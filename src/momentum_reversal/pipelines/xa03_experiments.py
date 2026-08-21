"""XA03 causal rolling cross-sectional aggregation experiment."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from momentum_reversal.backtest.calendar import rebalance_schedule
from momentum_reversal.pipelines.cross_sectional_database import DatabaseLayout


PROGRAM = Path("config/experiments/xa03/program.toml")
LOCK = Path("config/experiments/xa03/PREREG_LOCK.json")
RUN_IDS = {
    "XA03A": "xa03a-training-panel-20260821-v1",
    "XA03B": "xa03b-atomic-models-20260821-v1",
    "XA03C": "xa03c-factor-only-aggregation-20260821-v1",
    "XA03D": "xa03d-factor-state-aggregation-20260821-v1",
    "XA03E": "xa03e-paired-portfolio-comparison-20260821-v1",
}
FREQUENCIES = ("weekly", "monthly")


def run_xa03(project_root: str | Path, runtime_root: str | Path, batch: str) -> dict[str, Any]:
    project = Path(project_root).resolve()
    runtime = Path(runtime_root).resolve()
    batch = batch.upper()
    if batch not in RUN_IDS:
        raise ValueError(f"unknown XA03 batch: {batch}")
    program = _load_program(project)
    _verify_lock(project)
    commit = _require_clean_git(project)
    dependencies = _dependency_manifests(runtime, batch)
    root = _batch_root(runtime, batch)
    if root.exists():
        raise FileExistsError(f"XA03 run directory already exists: {root}")
    root.mkdir(parents=True)
    try:
        if batch == "XA03A":
            summary = _run_a(project, runtime, root, program)
        elif batch == "XA03B":
            summary = _run_model_batch(project, runtime, root, program, batch)
        elif batch == "XA03C":
            summary = _run_model_batch(project, runtime, root, program, batch)
        elif batch == "XA03D":
            summary = _run_model_batch(project, runtime, root, program, batch)
        else:
            summary = _run_e(project, runtime, root, program)
        _write_json(root / "summary.json", summary)
        _write_manifest(project, root, batch, commit, dependencies)
        return summary
    except Exception as exc:
        _write_json(root / "FAILED.json", {
            "batch": batch, "status": "failed", "exception_type": type(exc).__name__,
            "message": str(exc),
        })
        raise


def _run_a(project: Path, runtime: Path, root: Path, program: dict[str, Any]) -> dict[str, Any]:
    _verify_parent_inputs(project, runtime, program)
    xa01 = _xa01_root(runtime)
    factors = pd.read_parquet(xa01 / "factor_values_weekly_monthly.parquet")
    factor_ids = list(program["feature_sets"]["all14"])
    factors = factors.loc[factors["factor_id"].isin(factor_ids), [
        "signal_date", "sid", "factor_id", "eligible", "percentile",
    ]].copy()
    factors["signal_date"] = pd.to_datetime(factors["signal_date"]).dt.normalize()
    factors["centered_score"] = 2.0 * factors["percentile"].astype(float) - 1.0
    factors["finite_eligible"] = factors["eligible"].astype(bool) & np.isfinite(factors["centered_score"])
    wide = factors.pivot(index=["signal_date", "sid"], columns="factor_id", values="centered_score")
    avail = factors.pivot(index=["signal_date", "sid"], columns="factor_id", values="finite_eligible").fillna(False)
    wide = wide.reindex(columns=factor_ids)
    avail = avail.reindex(columns=factor_ids, fill_value=False)
    common = avail.sum(axis=1).ge(int(program["common_universe"]["minimum_available_factors"]))
    wide = wide.loc[common].copy()
    wide = wide.fillna(float(program["static_aggregation"]["missing_factor_contribution"]))
    wide.columns.name = None
    feature_panel = wide.reset_index()
    feature_panel["available_factor_count"] = avail.loc[common].sum(axis=1).to_numpy(dtype=np.int16)
    for factor_id in factor_ids:
        feature_panel[f"{factor_id}__available"] = avail.loc[common, factor_id].to_numpy(dtype=bool)

    layout = DatabaseLayout.load(project_root=project, runtime_root=runtime)
    market = layout.market_root
    calendar = pd.read_parquet(market / "calendar.parquet")
    calendar["session_date"] = pd.to_datetime(calendar["session_date"]).dt.normalize()
    prices = pd.read_parquet(market / "prices_daily.parquet", columns=["date", "sid", "tr_open"])
    prices["date"] = pd.to_datetime(prices["date"]).dt.normalize()
    rf = pd.read_parquet(market / "risk_free_daily.parquet")
    rf["date"] = pd.to_datetime(rf["date"]).dt.normalize()
    schedules = _build_schedules(calendar, program)
    frequency_panel = _replicate_features_by_frequency(feature_panel, schedules)
    target = _build_common_targets(frequency_panel, prices, rf, schedules, program)
    _verify_xa01_target_overlap(target, pd.read_parquet(xa01 / "target_ledger.parquet"), program)

    state_daily = pd.read_parquet(
        runtime / "results/experiments/xa02/XA02B/runs/xa02b-market-state-features-20260821-v1/market_state_daily.parquet"
    )
    state_panel = _build_state_panel(state_daily, schedules, program)
    _validate_training_readiness(target, state_panel, program)
    refits = _build_refit_ledger(schedules, target, program)
    inner = _build_inner_fold_ledger(target, schedules, program)

    _write_parquet(root / "extended_target_ledger.parquet", target)
    _write_parquet(root / "common_universe_ledger.parquet", frequency_panel[[
        "frequency", "signal_date", "sid", "available_factor_count"
    ]])
    _write_parquet(root / "model_feature_panel.parquet", frequency_panel)
    _write_parquet(root / "state_feature_panel.parquet", state_panel)
    _write_parquet(root / "refit_ledger.parquet", refits)
    _write_parquet(root / "inner_fold_ledger.parquet", inner)
    coverage = frequency_panel.groupby(["frequency", "signal_date"], sort=True).agg(
        common_names=("sid", "size"), median_available_factors=("available_factor_count", "median"),
        minimum_available_factors=("available_factor_count", "min"),
    ).reset_index()
    _write_csv(root / "common_universe_coverage.csv", coverage)
    audit = {
        "parent_hashes_passed": True,
        "xa01_overlap_identity_passed": True,
        "prediction_uses_target_valid": False,
        "minimum_common_names": int(coverage.loc[
            coverage["signal_date"].ge(pd.Timestamp(program["sample"]["first_oos_signal_close"])),
            "common_names",
        ].min()),
        "target_rank_range": [float(target["target_rank"].min()), float(target["target_rank"].max())],
        "future_perturbation_contract": "covered_by_causal_asof_inputs_and_target_availability",
    }
    if audit["minimum_common_names"] < int(program["common_universe"]["minimum_names_per_signal"]):
        raise ValueError("COMMON10 coverage fell below the frozen minimum")
    _write_json(root / "causality_and_identity_audit.json", audit)
    return {
        "batch": "XA03A", "status": "completed", "factor_count": len(factor_ids),
        "feature_rows": len(frequency_panel), "target_rows": len(target),
        "state_rows": len(state_panel), "refit_rows": len(refits), "inner_fold_rows": len(inner),
        "minimum_common_names": audit["minimum_common_names"],
        "xa01_overlap_identity_passed": True,
    }


def _build_schedules(calendar: pd.DataFrame, program: dict[str, Any]) -> pd.DataFrame:
    sessions = pd.DatetimeIndex(calendar["session_date"]).sort_values().unique()
    rows = []
    end = pd.Timestamp(program["sample"]["evaluation_end_close"])
    starts = {
        "weekly": pd.Timestamp(program["sample"]["supplemental_training_start_weekly_signal"]),
        "monthly": pd.Timestamp(program["sample"]["supplemental_training_start_monthly_signal"]),
    }
    for frequency in FREQUENCIES:
        schedule = rebalance_schedule(sessions, frequency).reset_index(drop=True)
        schedule["label_end_execution_date"] = schedule["execution_date"].shift(-1)
        schedule = schedule.loc[
            schedule["signal_date"].ge(starts[frequency])
            & schedule["execution_date"].le(end)
            & schedule["label_end_execution_date"].notna()
            & schedule["label_end_execution_date"].le(end)
        ].copy()
        schedule["frequency"] = frequency
        rows.append(schedule[["frequency", "signal_date", "execution_date", "label_end_execution_date"]])
    return pd.concat(rows, ignore_index=True).sort_values(["frequency", "signal_date"], ignore_index=True)


def _replicate_features_by_frequency(features: pd.DataFrame, schedules: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for frequency, dates in schedules.groupby("frequency", sort=True):
        keep = features.loc[features["signal_date"].isin(set(dates["signal_date"]))].copy()
        keep.insert(0, "frequency", frequency)
        parts.append(keep)
    out = pd.concat(parts, ignore_index=True)
    if out.duplicated(["frequency", "signal_date", "sid"]).any():
        raise ValueError("duplicate common feature key")
    return out.sort_values(["frequency", "signal_date", "sid"], ignore_index=True)


def _build_common_targets(features: pd.DataFrame, prices: pd.DataFrame, rf: pd.DataFrame,
                          schedules: pd.DataFrame, program: dict[str, Any]) -> pd.DataFrame:
    opens = prices.pivot(index="date", columns="sid", values="tr_open")
    rf_series = rf.set_index("date")["rf_return"].astype(float).sort_index()
    rows = []
    members = features.groupby(["frequency", "signal_date"], sort=False)["sid"].agg(list)
    for item in schedules.itertuples(index=False):
        key = (item.frequency, pd.Timestamp(item.signal_date))
        if key not in members.index:
            continue
        sids = pd.Index(members.loc[key], dtype=str)
        entry = pd.Timestamp(item.execution_date)
        exit_date = pd.Timestamp(item.label_end_execution_date)
        start = opens.loc[entry].reindex(sids).astype(float)
        finish = opens.loc[exit_date].reindex(sids).astype(float)
        total = finish / start - 1.0
        cash = float((1.0 + rf_series.loc[(rf_series.index >= entry) & (rf_series.index < exit_date)]).prod() - 1.0)
        one = pd.DataFrame({
            "frequency": item.frequency, "signal_date": pd.Timestamp(item.signal_date),
            "execution_date": entry, "label_end_execution_date": exit_date,
            "sid": sids, "forward_total_return": total.to_numpy(),
        })
        one["forward_cash_return"] = cash
        one["forward_excess_cash"] = one["forward_total_return"] - cash
        one["target_available_at"] = exit_date
        one["target_valid"] = np.isfinite(one["forward_excess_cash"])
        one["target_rank"] = centered_cross_sectional_rank(
            one["forward_excess_cash"], one["target_valid"]
        )
        rows.append(one)
    out = pd.concat(rows, ignore_index=True)
    return out.sort_values(["frequency", "signal_date", "sid"], ignore_index=True)


def centered_cross_sectional_rank(values: pd.Series, valid: pd.Series | None = None) -> pd.Series:
    x = pd.to_numeric(values, errors="coerce")
    mask = np.isfinite(x) if valid is None else (pd.Series(valid, index=x.index).astype(bool) & np.isfinite(x))
    out = pd.Series(np.nan, index=x.index, dtype=float)
    n = int(mask.sum())
    if n < 2:
        return out
    ranks = x.loc[mask].rank(method="average", ascending=True)
    out.loc[mask] = 2.0 * (ranks - 1.0) / (n - 1.0) - 1.0
    return out


def _verify_xa01_target_overlap(current: pd.DataFrame, parent: pd.DataFrame,
                                program: dict[str, Any]) -> None:
    parent = parent.copy()
    for col in ("signal_date", "execution_date", "label_end_execution_date", "target_available_at"):
        parent[col] = pd.to_datetime(parent[col]).dt.normalize()
    start = pd.Timestamp(program["sample"]["first_oos_signal_close"])
    cols = list(program["training_targets"]["parent_overlap_identity_columns"])
    left = current.loc[current["signal_date"].ge(start), ["frequency", "signal_date", "sid", *cols]]
    right = parent[["frequency", "signal_date", "sid", *cols]]
    merged = left.merge(right, on=["frequency", "signal_date", "sid"], suffixes=("_new", "_old"), validate="one_to_one")
    if len(merged) != len(left):
        raise ValueError("XA01 overlap target key mismatch")
    for col in cols:
        a, b = merged[f"{col}_new"], merged[f"{col}_old"]
        if pd.api.types.is_datetime64_any_dtype(a):
            ok = a.eq(b)
        elif a.dtype == bool:
            ok = a.eq(b)
        else:
            ok = np.isclose(a.astype(float), b.astype(float), rtol=0, atol=0, equal_nan=True)
        if not bool(np.asarray(ok).all()):
            raise ValueError(f"XA01 overlap drift: {col}")


def _build_state_panel(state_daily: pd.DataFrame, schedules: pd.DataFrame,
                       program: dict[str, Any]) -> pd.DataFrame:
    states = list(program["feature_sets"]["s6"])
    raw = state_daily.loc[state_daily["state_id"].isin(states), ["date", "state_id", "raw_value"]].copy()
    raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()
    wide = raw.pivot(index="date", columns="state_id", values="raw_value").reindex(columns=states)
    rows = []
    for frequency, group in schedules.groupby("frequency", sort=True):
        one = wide.reindex(pd.DatetimeIndex(group["signal_date"])).copy()
        one.index.name = "signal_date"
        one = one.reset_index()
        one.insert(0, "frequency", frequency)
        rows.append(one)
    out = pd.concat(rows, ignore_index=True)
    if out[states].isna().any(axis=1).any():
        bad = out.loc[out[states].isna().any(axis=1), ["frequency", "signal_date"]].head()
        raise ValueError(f"missing primary state values: {bad.to_dict('records')}")
    return out.sort_values(["frequency", "signal_date"], ignore_index=True)


def _validate_training_readiness(target: pd.DataFrame, states: pd.DataFrame,
                                 program: dict[str, Any]) -> None:
    first = pd.Timestamp(program["sample"]["first_oos_signal_close"])
    for frequency in FREQUENCIES:
        min_dates = int(program["walk_forward"][f"minimum_complete_training_dates_{frequency}"])
        cutoff = first
        dates = target.loc[
            target["frequency"].eq(frequency) & target["target_available_at"].le(cutoff)
            & target["target_valid"], "signal_date",
        ].drop_duplicates()
        if len(dates) < min_dates:
            raise ValueError(f"insufficient {frequency} training history before first OOS: {len(dates)}")
        available = states.loc[states["frequency"].eq(frequency) & states["signal_date"].le(cutoff)]
        if available.empty:
            raise ValueError(f"missing {frequency} state history")


def _build_refit_ledger(schedules: pd.DataFrame, target: pd.DataFrame,
                        program: dict[str, Any]) -> pd.DataFrame:
    first = pd.Timestamp(program["sample"]["first_oos_signal_close"])
    monthly = schedules.loc[schedules["frequency"].eq("monthly") & schedules["signal_date"].ge(first)]
    rows = []
    for frequency in FREQUENCIES:
        freq_dates = schedules.loc[schedules["frequency"].eq(frequency), "signal_date"]
        for anchor in monthly["signal_date"]:
            if frequency == "monthly":
                prediction_start = anchor
            else:
                later = freq_dates[freq_dates.ge(anchor)]
                if later.empty:
                    continue
                prediction_start = later.iloc[0]
            mature = target.loc[target["frequency"].eq(frequency) & target["target_available_at"].le(anchor), "signal_date"].drop_duplicates().sort_values()
            maximum = int(program["walk_forward"][f"maximum_training_dates_{frequency}"])
            rows.append({
                "frequency": frequency, "refit_signal_date": anchor,
                "prediction_start_signal_date": prediction_start,
                "training_start_signal_date": mature.iloc[-maximum] if len(mature) >= maximum else mature.iloc[0],
                "training_end_signal_date": mature.iloc[-1], "complete_training_dates": min(len(mature), maximum),
            })
    return pd.DataFrame(rows).sort_values(["frequency", "refit_signal_date"], ignore_index=True)


def _build_inner_fold_ledger(target: pd.DataFrame, schedules: pd.DataFrame,
                             program: dict[str, Any]) -> pd.DataFrame:
    first = pd.Timestamp(program["sample"]["first_oos_signal_close"])
    rows = []
    for frequency in FREQUENCIES:
        evaluation = schedules.loc[schedules["frequency"].eq(frequency) & schedules["signal_date"].ge(first)].copy()
        evaluation["execution_year"] = pd.to_datetime(evaluation["execution_date"]).dt.year
        block = int(program["annual_recipe_selection"][f"{frequency}_validation_block_dates"])
        minimum = int(program["annual_recipe_selection"][f"minimum_inner_training_dates_{frequency}"])
        for year, group in evaluation.groupby("execution_year", sort=True):
            cutoff = group["signal_date"].min()
            dates = target.loc[target["frequency"].eq(frequency) & target["target_available_at"].le(cutoff), "signal_date"].drop_duplicates().sort_values().tolist()
            fold = 0
            start = minimum
            while start + block <= len(dates):
                rows.append({
                    "frequency": frequency, "execution_year": int(year), "fold_id": fold,
                    "train_start": dates[0], "train_end": dates[start - 1],
                    "validation_start": dates[start], "validation_end": dates[start + block - 1],
                    "train_dates": start, "validation_dates": block,
                })
                fold += 1
                start += block
    return pd.DataFrame(rows).sort_values(["frequency", "execution_year", "fold_id"], ignore_index=True)


@dataclass(frozen=True)
class PanelData:
    features: pd.DataFrame
    targets: pd.DataFrame
    states: pd.DataFrame
    refits: pd.DataFrame


class _WeightedRidge:
    """Deterministic weighted Ridge with an unpenalized fitted intercept."""

    def __init__(self, alpha: float) -> None:
        self.alpha = float(alpha)

    def fit(self, x: pd.DataFrame, y: np.ndarray, sample_weight: np.ndarray) -> "_WeightedRidge":
        a = np.asarray(x, dtype=float); target = np.asarray(y, dtype=float)
        weight = np.asarray(sample_weight, dtype=float)
        total = float(weight.sum())
        x_mean = (a * weight[:, None]).sum(axis=0) / total
        y_mean = float(np.dot(target, weight) / total)
        xc = a - x_mean; yc = target - y_mean
        gram = xc.T @ (xc * weight[:, None])
        rhs = xc.T @ (yc * weight)
        self.coef_ = np.linalg.solve(gram + self.alpha * np.eye(a.shape[1]), rhs)
        self.intercept_ = y_mean - float(np.dot(x_mean, self.coef_))
        return self

    def predict(self, x: pd.DataFrame) -> np.ndarray:
        return np.asarray(x, dtype=float) @ self.coef_ + self.intercept_


def _load_panel(runtime: Path) -> PanelData:
    root = _batch_root(runtime, "XA03A")
    return PanelData(
        pd.read_parquet(root / "model_feature_panel.parquet"),
        pd.read_parquet(root / "extended_target_ledger.parquet"),
        pd.read_parquet(root / "state_feature_panel.parquet"),
        pd.read_parquet(root / "refit_ledger.parquet"),
    )


def _run_model_batch(project: Path, runtime: Path, root: Path, program: dict[str, Any],
                     batch: str) -> dict[str, Any]:
    panel = _load_panel(runtime)
    processes = pd.read_csv(project / program["registries"]["process_registry"])
    layers = {
        "XA03B": {"raw_control", "single_factor_model"},
        "XA03C": {"static_aggregation", "factor_only_model"},
        "XA03D": {"factor_state_model", "rsp_ablation"},
    }[batch]
    selected = processes.loc[processes["layer"].isin(layers) & processes["eligible"].astype(bool)].copy()
    bundles = pd.read_csv(project / program["registries"]["feature_bundles"]).set_index("bundle_id")
    recipes = pd.read_csv(project / program["registries"]["model_recipes"]).set_index("recipe_id")
    first = pd.Timestamp(program["sample"]["first_oos_signal_close"])
    predictions: list[pd.DataFrame] = []
    selections: list[pd.DataFrame] = []
    refit_rows: list[pd.DataFrame] = []
    importance_rows: list[pd.DataFrame] = []
    invalid_rows: list[dict[str, Any]] = []

    inherited: dict[tuple[str, str, int], str] = {}
    if batch == "XA03D":
        parent = _batch_root(runtime, "XA03C") / "model_selection_ledger.parquet"
        parent_sel = pd.read_parquet(parent)
        inherited = {
            (str(r.process_id), str(r.frequency), int(r.execution_year)): str(r.recipe_id)
            for r in parent_sel.itertuples(index=False)
        }

    for proc in selected.itertuples(index=False):
        bundle = bundles.loc[str(proc.bundle_id)]
        factor_ids = _split_pipe(bundle["factor_ids"])
        state_ids = _split_pipe(bundle["state_ids"])
        for frequency in FREQUENCIES:
            if str(proc.family) == "direct_rank":
                pred = _direct_predictions(panel, str(proc.process_id), frequency, factor_ids[0], first)
                predictions.append(pred)
                continue
            if str(proc.family).startswith("static_"):
                pred = _static_predictions(panel, str(proc.process_id), frequency, factor_ids,
                                           str(proc.family), program, first)
                predictions.append(pred)
                continue
            parent_process = str(proc.recipe_inherit_from) if pd.notna(proc.recipe_inherit_from) else ""
            try:
                result = _walk_forward_predictions(
                    panel, str(proc.process_id), frequency, str(proc.family), factor_ids, state_ids,
                    _split_pipe(proc.selector_recipe_ids), recipes, program,
                    inherited_parent=parent_process, inherited=inherited,
                )
            except (ValueError, np.linalg.LinAlgError) as exc:
                invalid_rows.append({
                    "process_id": str(proc.process_id), "frequency": frequency,
                    "invalid": True, "reason": str(exc),
                })
                continue
            predictions.append(result[0])
            selections.append(result[1])
            refit_rows.append(result[2])
            importance_rows.append(result[3])

    prediction = pd.concat(predictions, ignore_index=True).sort_values(
        ["process_id", "frequency", "signal_date", "sid"], ignore_index=True
    )
    _write_parquet(root / "prediction_ledger.parquet", prediction)
    selection = pd.concat(selections, ignore_index=True) if selections else pd.DataFrame(
        columns=["process_id", "frequency", "execution_year", "recipe_id"]
    )
    refit = pd.concat(refit_rows, ignore_index=True) if refit_rows else pd.DataFrame()
    importance = pd.concat(importance_rows, ignore_index=True) if importance_rows else pd.DataFrame()
    _write_parquet(root / "model_selection_ledger.parquet", selection)
    _write_parquet(root / "model_refit_ledger.parquet", refit)
    _write_parquet(root / "coefficient_and_importance_ledger.parquet", importance)
    invalid_frame = pd.DataFrame(invalid_rows, columns=["process_id", "frequency", "invalid", "reason"])
    _write_csv(root / "invalid_process_ledger.csv", invalid_frame)
    score_audit = _prediction_audit(prediction, selected, invalid_frame)
    _write_csv(root / "prediction_audit.csv", score_audit)
    if not bool(score_audit["passed"].all()):
        raise ValueError(f"{batch} prediction audit failed")
    return {
        "batch": batch, "status": "completed", "processes_per_frequency": len(selected),
        "prediction_rows": len(prediction), "selection_rows": len(selection),
        "refit_rows": len(refit), "importance_rows": len(importance),
        "invalid_process_frequency_cells": len(invalid_frame),
        "all_prediction_audits_passed": True,
    }


def _direct_predictions(panel: PanelData, process_id: str, frequency: str,
                        factor_id: str, first: pd.Timestamp) -> pd.DataFrame:
    one = panel.features.loc[
        panel.features["frequency"].eq(frequency) & panel.features["signal_date"].ge(first),
        ["frequency", "signal_date", "sid", factor_id, f"{factor_id}__available"],
    ].copy()
    one = one.loc[one[f"{factor_id}__available"].astype(bool)].copy()
    one = one.rename(columns={factor_id: "prediction"})
    one["process_id"] = process_id
    one["recipe_id"] = "DIRECT_RANK"
    one["fit_signal_date"] = pd.NaT
    return one[["process_id", "frequency", "signal_date", "sid", "prediction", "recipe_id", "fit_signal_date"]]


def _static_predictions(panel: PanelData, process_id: str, frequency: str,
                        factor_ids: list[str], family: str,
                        program: dict[str, Any], first: pd.Timestamp) -> pd.DataFrame:
    one = panel.features.loc[
        panel.features["frequency"].eq(frequency) & panel.features["signal_date"].ge(first),
        ["frequency", "signal_date", "sid", *factor_ids],
    ].copy()
    if family == "static_equal_rank":
        one["prediction"] = one[factor_ids].mean(axis=1)
        recipe = "STATIC_EQUAL_RANK"
    else:
        dimensions = {
            "trend_price_path": ["XS001_MOM_255_0", "XS002_MOM_12_1", "XS003_MOM_12_7", "XS004_HIGH_52W"],
            "reversal_calendar": ["XS007_ST_REV_21", "XS008_SAME_MONTH_5Y"],
            "low_risk_lottery": ["XS013_LOW_BETA_FP", "XS015_MAX_21"],
            "liquidity_attention": ["XS018_AMIHUD_252", "XS019_PRICE_DELAY_52W", "XS020_VOLUME_SHOCK_50D"],
            "operating_quality_cash": ["XS032_GROSS_PROFIT_AT", "XS056_CFO_ACCRUALS_PT"],
            "investment_conservatism": ["XS041_ASSET_GROWTH"],
        }
        dim_scores = [one[members].sum(axis=1) / len(members) for members in dimensions.values()]
        one["prediction"] = sum(dim_scores) / len(dim_scores)
        recipe = "STATIC_DIMENSION_EQUAL_RANK"
    one["process_id"] = process_id
    one["recipe_id"] = recipe
    one["fit_signal_date"] = pd.NaT
    return one[["process_id", "frequency", "signal_date", "sid", "prediction", "recipe_id", "fit_signal_date"]]


def _walk_forward_predictions(
    panel: PanelData, process_id: str, frequency: str, family: str,
    factor_ids: list[str], state_ids: list[str], selector_ids: list[str],
    recipes: pd.DataFrame, program: dict[str, Any], *, inherited_parent: str,
    inherited: dict[tuple[str, str, int], str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    first = pd.Timestamp(program["sample"]["first_oos_signal_close"])
    features = panel.features.loc[panel.features["frequency"].eq(frequency), [
        "frequency", "signal_date", "sid", *factor_ids,
        *[f"{factor_id}__available" for factor_id in factor_ids],
    ]].copy()
    target = panel.targets.loc[panel.targets["frequency"].eq(frequency), [
        "frequency", "signal_date", "execution_date", "sid", "target_rank",
        "target_valid", "target_available_at",
    ]].copy()
    data = features.merge(target, on=["frequency", "signal_date", "sid"], how="left", validate="one_to_one")
    states = panel.states.loc[panel.states["frequency"].eq(frequency), [
        "frequency", "signal_date", *state_ids,
    ]].copy()
    if state_ids:
        data = data.merge(states, on=["frequency", "signal_date"], how="left", validate="many_to_one")
    focal = factor_ids[0] if len(factor_ids) == 1 else None
    if focal is not None:
        data = data.loc[data[f"{focal}__available"].astype(bool)].copy()
    evaluation = data.loc[data["signal_date"].ge(first)].copy()
    eval_dates = evaluation[["signal_date", "execution_date"]].drop_duplicates().sort_values("signal_date")
    eval_dates["execution_year"] = pd.to_datetime(eval_dates["execution_date"]).dt.year

    selections = []
    selector_cache: dict[tuple[str, tuple[pd.Timestamp, ...]], pd.Series] = {}
    for year, group in eval_dates.groupby("execution_year", sort=True):
        cutoff = pd.Timestamp(group["signal_date"].min())
        if inherited_parent:
            key = (inherited_parent, frequency, int(year))
            if key not in inherited:
                raise ValueError(f"missing inherited recipe: {key}")
            recipe_id = inherited[key]
            diagnostics = {"validation_mean_ic": np.nan, "one_se_members": recipe_id, "legal_blocks": 0}
        else:
            recipe_id, diagnostics = _select_annual_recipe(
                data, cutoff, process_id, frequency, int(year), family, factor_ids,
                state_ids, selector_ids, recipes, program, selector_cache,
            )
        selections.append({
            "process_id": process_id, "frequency": frequency, "execution_year": int(year),
            "selection_signal_date": cutoff, "recipe_id": recipe_id, **diagnostics,
        })
    selection = pd.DataFrame(selections)

    refit_spec = panel.refits.loc[panel.refits["frequency"].eq(frequency)].copy()
    predictions = []
    refit_audit = []
    importances = []
    invalid = False
    for refit in refit_spec.itertuples(index=False):
        pred_dates = eval_dates.loc[eval_dates["signal_date"].ge(pd.Timestamp(refit.prediction_start_signal_date))]
        later_refits = refit_spec.loc[refit_spec["refit_signal_date"].gt(pd.Timestamp(refit.refit_signal_date)), "prediction_start_signal_date"]
        if not later_refits.empty:
            pred_dates = pred_dates.loc[pred_dates["signal_date"].lt(pd.Timestamp(later_refits.iloc[0]))]
        if pred_dates.empty:
            continue
        execution_year = int(pred_dates.iloc[0]["execution_year"])
        recipe_id = str(selection.loc[selection["execution_year"].eq(execution_year), "recipe_id"].iloc[0])
        train = _training_slice(data, pd.Timestamp(refit.refit_signal_date), frequency, program)
        if train.empty:
            invalid = True
            break
        try:
            model, design_names, transform, fit_importance = _fit_registered_model(
                train, family, factor_ids, state_ids, recipe_id, recipes.loc[recipe_id],
                process_id, frequency, program,
            )
        except Exception:
            invalid = True
            break
        for prediction_date in pred_dates["signal_date"]:
            current = evaluation.loc[evaluation["signal_date"].eq(prediction_date)].copy()
            if current.empty:
                continue
            matrix = _design_matrix(current, family, factor_ids, state_ids, transform,
                                    process_id, training=False)
            values = model.predict(matrix)
            predictions.append(pd.DataFrame({
                "process_id": process_id, "frequency": frequency,
                "signal_date": prediction_date, "sid": current["sid"].to_numpy(),
                "prediction": values, "recipe_id": recipe_id,
                "fit_signal_date": pd.Timestamp(refit.refit_signal_date),
            }))
        refit_audit.append({
            "process_id": process_id, "frequency": frequency, "refit_signal_date": refit.refit_signal_date,
            "recipe_id": recipe_id, "training_dates": train["signal_date"].nunique(),
            "training_rows": len(train), "training_start": train["signal_date"].min(),
            "training_end": train["signal_date"].max(), "latest_target_available_at": train["target_available_at"].max(),
            "design_columns": "|".join(design_names), "fit_valid": True,
        })
        fit_importance["process_id"] = process_id
        fit_importance["frequency"] = frequency
        fit_importance["refit_signal_date"] = pd.Timestamp(refit.refit_signal_date)
        fit_importance["recipe_id"] = recipe_id
        importances.append(fit_importance)
    if invalid or not predictions:
        raise ValueError(f"outer selected recipe failed for {process_id}/{frequency}")
    prediction = pd.concat(predictions, ignore_index=True)
    if prediction.duplicated(["signal_date", "sid"]).any():
        raise ValueError(f"duplicate prediction key: {process_id}/{frequency}")
    return prediction, selection, pd.DataFrame(refit_audit), pd.concat(importances, ignore_index=True)


def _training_slice(data: pd.DataFrame, cutoff: pd.Timestamp, frequency: str,
                    program: dict[str, Any]) -> pd.DataFrame:
    legal = data.loc[
        data["target_valid"].fillna(False)
        & data["target_rank"].notna()
        & pd.to_datetime(data["target_available_at"]).le(cutoff)
    ].copy()
    dates = legal["signal_date"].drop_duplicates().sort_values()
    maximum = int(program["walk_forward"][f"maximum_training_dates_{frequency}"])
    minimum = int(program["walk_forward"][f"minimum_complete_training_dates_{frequency}"])
    if len(dates) < minimum:
        return legal.iloc[0:0]
    keep = set(dates.iloc[-maximum:])
    return legal.loc[legal["signal_date"].isin(keep)].sort_values(["signal_date", "sid"], ignore_index=True)


def _select_annual_recipe(data: pd.DataFrame, cutoff: pd.Timestamp, process_id: str,
                          frequency: str, year: int, family: str, factor_ids: list[str],
                          state_ids: list[str], recipe_ids: list[str], recipes: pd.DataFrame,
                          program: dict[str, Any],
                          cache: dict[tuple[str, tuple[pd.Timestamp, ...]], pd.Series] | None = None,
                          ) -> tuple[str, dict[str, Any]]:
    legal = data.loc[
        data["target_valid"].fillna(False) & data["target_rank"].notna()
        & pd.to_datetime(data["target_available_at"]).le(cutoff)
    ].copy()
    dates = legal["signal_date"].drop_duplicates().sort_values().tolist()
    block = int(program["annual_recipe_selection"][f"{frequency}_validation_block_dates"])
    minimum = int(program["annual_recipe_selection"][f"minimum_inner_training_dates_{frequency}"])
    folds = []
    start = minimum
    while start + block <= len(dates):
        folds.append((dates[:start], dates[start:start + block]))
        start += block
    if len(folds) < int(program["annual_recipe_selection"]["minimum_legal_validation_blocks"]):
        raise ValueError(f"insufficient inner folds: {process_id}/{frequency}/{year}")
    values: dict[str, pd.Series] = {}
    valid_recipes = []
    cache = {} if cache is None else cache
    for recipe_id in recipe_ids:
        date_ic = []
        try:
            for train_dates, validation_dates in folds:
                fold_key = (recipe_id, tuple(pd.Timestamp(x) for x in validation_dates))
                if fold_key not in cache:
                    train = legal.loc[legal["signal_date"].isin(set(train_dates))]
                    valid = legal.loc[legal["signal_date"].isin(set(validation_dates))]
                    model, _, transform, _ = _fit_registered_model(
                        train, family, factor_ids, state_ids, recipe_id, recipes.loc[recipe_id],
                        process_id, frequency, program,
                    )
                    matrix = _design_matrix(valid, family, factor_ids, state_ids, transform,
                                            process_id, training=False)
                    pred = model.predict(matrix)
                    scored = valid[["signal_date", "target_rank"]].copy()
                    scored["prediction"] = pred
                    cache[fold_key] = scored.groupby("signal_date", sort=True).apply(
                        lambda g: _spearman(g["prediction"], g["target_rank"]),
                        include_groups=False,
                    ).reset_index(drop=True)
                date_ic.extend(cache[fold_key].tolist())
        except Exception:
            continue
        series = pd.Series(date_ic, dtype=float).dropna().reset_index(drop=True)
        if not series.empty:
            values[recipe_id] = series
            valid_recipes.append(recipe_id)
    if not valid_recipes:
        raise ValueError(f"all recipes invalid: {process_id}/{frequency}/{year}")
    means = {recipe: float(values[recipe].mean()) for recipe in valid_recipes}
    best = sorted(valid_recipes, key=lambda r: (-means[r], int(recipes.loc[r, "capacity_rank"]), r))[0]
    one_se = []
    for recipe in valid_recipes:
        n = min(len(values[best]), len(values[recipe]))
        diff = values[best].iloc[:n].to_numpy() - values[recipe].iloc[:n].to_numpy()
        se = _noncircular_mbb_se(
            diff,
            int(program["annual_recipe_selection"][f"one_se_{frequency}_block_dates"]),
            int(program["annual_recipe_selection"]["one_se_bootstrap_draws"]),
            _derived_seed(int(program["annual_recipe_selection"]["one_se_global_seed"]),
                          "inner_one_se", process_id, frequency, year),
        )
        if float(np.mean(diff)) <= se:
            one_se.append(recipe)
    chosen = sorted(one_se, key=lambda r: (int(recipes.loc[r, "capacity_rank"]), r))[0]
    return chosen, {
        "validation_mean_ic": means[chosen], "one_se_members": "|".join(sorted(one_se)),
        "legal_blocks": len(folds),
    }


def _fit_registered_model(train: pd.DataFrame, family: str, factor_ids: list[str],
                          state_ids: list[str], recipe_id: str, recipe: pd.Series,
                          process_id: str, frequency: str,
                          program: dict[str, Any]) -> tuple[Any, list[str], dict[str, Any], pd.DataFrame]:
    transform = _fit_state_transform(train, state_ids)
    matrix = _design_matrix(train, family, factor_ids, state_ids, transform, process_id, training=True)
    y = train["target_rank"].to_numpy(dtype=float)
    counts = train.groupby("signal_date")["sid"].transform("size").to_numpy(dtype=float)
    weights = 1.0 / counts
    if family == "ridge":
        model = _WeightedRidge(alpha=float(recipe["alpha"]))
        model.fit(matrix, y, sample_weight=weights)
        importance = pd.DataFrame({"feature": list(matrix.columns), "value": model.coef_.astype(float), "kind": "coefficient"})
    elif family == "lightgbm":
        model = LGBMRegressor(
            objective="regression", max_depth=int(recipe["max_depth"]), num_leaves=int(recipe["num_leaves"]),
            n_estimators=int(recipe["n_estimators"]), learning_rate=float(recipe["learning_rate"]),
            min_child_samples=int(recipe["min_child_samples"]), reg_lambda=float(recipe["reg_lambda"]),
            subsample=float(recipe["subsample"]), colsample_bytree=float(recipe["colsample_bytree"]),
            random_state=int(recipe["seed"]), n_jobs=1, deterministic=True, force_col_wise=True,
            verbosity=-1,
        )
        model.fit(matrix, y, sample_weight=weights)
        _audit_lgbm_leaf_support(model, matrix, train["signal_date"], frequency, program)
        importance = pd.DataFrame({"feature": list(matrix.columns), "value": model.feature_importances_.astype(float), "kind": "split_importance"})
    else:
        raise ValueError(f"unsupported fitted family: {family}")
    return model, list(matrix.columns), transform, importance


def _fit_state_transform(train: pd.DataFrame, state_ids: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not state_ids:
        return result
    unique = train[["signal_date", *state_ids]].drop_duplicates("signal_date").sort_values("signal_date")
    for state in state_ids:
        values = unique[state].to_numpy(dtype=float)
        q01, q99 = np.quantile(values, [0.01, 0.99], method="linear")
        clipped = np.clip(values, q01, q99)
        mean = float(np.mean(clipped)); std = float(np.std(clipped, ddof=0))
        if not np.isfinite(std) or std <= 0:
            raise ValueError(f"invalid state standard deviation: {state}")
        z = (clipped - mean) / std
        result[state] = {"q01": float(q01), "q99": float(q99), "mean": mean,
                         "std": std, "square_mean": float(np.mean(z * z))}
    return result


def _design_matrix(frame: pd.DataFrame, family: str, factor_ids: list[str], state_ids: list[str],
                   transform: dict[str, Any], process_id: str, *, training: bool) -> pd.DataFrame:
    out = frame[factor_ids].astype(float).copy()
    zstates: dict[str, np.ndarray] = {}
    for state in state_ids:
        spec = transform[state]
        values = np.clip(frame[state].to_numpy(dtype=float), spec["q01"], spec["q99"])
        zstates[state] = (values - spec["mean"]) / spec["std"]
    if family == "lightgbm":
        for state, values in zstates.items():
            out[state] = values
    elif family == "ridge" and state_ids:
        if "ROLE5" in process_id:
            if "MKT_BREADTH_RSP63" in zstates:
                z = zstates["MKT_BREADTH_RSP63"]
                out["XS002_x_BREADTH"] = out["XS002_MOM_12_1"].to_numpy() * z
                out["XS002_x_BREADTH2"] = out["XS002_MOM_12_1"].to_numpy() * (z * z - transform["MKT_BREADTH_RSP63"]["square_mean"])
            z = zstates["MKT_TREND126"]
            out["XS008_x_TREND"] = out["XS008_SAME_MONTH_5Y"].to_numpy() * z
            out["XS008_x_TREND2"] = out["XS008_SAME_MONTH_5Y"].to_numpy() * (z * z - transform["MKT_TREND126"]["square_mean"])
        else:
            for factor in factor_ids:
                for state, values in zstates.items():
                    out[f"{factor}_x_{state}"] = out[factor].to_numpy() * values
    if not np.isfinite(out.to_numpy(dtype=float)).all():
        raise ValueError("non-finite model matrix")
    return out


def _audit_lgbm_leaf_support(model: LGBMRegressor, matrix: pd.DataFrame,
                             dates: pd.Series, frequency: str,
                             program: dict[str, Any]) -> None:
    leaves = np.asarray(model.booster_.predict(matrix, pred_leaf=True))
    if leaves.ndim == 1:
        leaves = leaves[:, None]
    d = pd.to_datetime(dates).reset_index(drop=True)
    minimum = int(program["models"][f"lightgbm_minimum_independent_dates_per_leaf_{frequency}"])
    years_min = int(program["models"]["lightgbm_minimum_independent_calendar_years_per_leaf"])
    for tree in range(leaves.shape[1]):
        for leaf in np.unique(leaves[:, tree]):
            member_dates = d.loc[leaves[:, tree] == leaf]
            if member_dates.nunique() < minimum or member_dates.dt.year.nunique() < years_min:
                raise ValueError("LightGBM leaf lacks independent date/year support")


def _noncircular_mbb_se(values: np.ndarray, block: int, draws: int, seed: int) -> float:
    x = np.asarray(values, dtype=float)
    if len(x) < block or len(x) < 2:
        return math.inf
    starts = np.arange(len(x) - block + 1)
    rng = np.random.default_rng(seed)
    blocks = int(math.ceil(len(x) / block))
    chosen = rng.choice(starts, size=(draws, blocks), replace=True)
    indices = (chosen[:, :, None] + np.arange(block)[None, None, :]).reshape(draws, -1)[:, :len(x)]
    means = x[indices].mean(axis=1)
    return float(np.std(means, ddof=1))


def _prediction_audit(predictions: pd.DataFrame, processes: pd.DataFrame,
                      invalid: pd.DataFrame | None = None) -> pd.DataFrame:
    rows = []
    expected = set(processes["process_id"].astype(str))
    for (process_id, frequency), group in predictions.groupby(["process_id", "frequency"], sort=True):
        rows.append({
            "process_id": process_id, "frequency": frequency, "prediction_rows": len(group),
            "signal_dates": group["signal_date"].nunique(), "finite_predictions": bool(np.isfinite(group["prediction"]).all()),
            "unique_keys": not group.duplicated(["signal_date", "sid"]).any(),
            "passed": bool(np.isfinite(group["prediction"]).all()) and not group.duplicated(["signal_date", "sid"]).any(),
        })
    frame = pd.DataFrame(rows)
    observed = set(frame["process_id"])
    invalid = pd.DataFrame() if invalid is None else invalid
    invalid_cells = set(zip(invalid.get("process_id", []), invalid.get("frequency", [])))
    expected_cells = {(process, frequency) for process in expected for frequency in FREQUENCIES}
    observed_cells = set(zip(frame["process_id"], frame["frequency"]))
    if observed_cells | invalid_cells != expected_cells or observed_cells & invalid_cells:
        raise ValueError("prediction/invalid process-frequency partition mismatch")
    if not invalid.empty:
        extra = invalid[["process_id", "frequency"]].copy()
        extra["prediction_rows"] = 0; extra["signal_dates"] = 0
        extra["finite_predictions"] = False; extra["unique_keys"] = True; extra["passed"] = True
        frame = pd.concat([frame, extra], ignore_index=True)
    return frame


def _run_e(project: Path, runtime: Path, root: Path, program: dict[str, Any]) -> dict[str, Any]:
    process_registry = pd.read_csv(project / program["registries"]["process_registry"])
    comparison_registry = pd.read_csv(project / program["registries"]["comparison_registry"])
    target = pd.read_parquet(_batch_root(runtime, "XA03A") / "extended_target_ledger.parquet")
    first = pd.Timestamp(program["sample"]["first_oos_signal_close"])
    target = target.loc[target["signal_date"].ge(first)].copy()
    predictions = pd.concat([
        pd.read_parquet(_batch_root(runtime, batch) / "prediction_ledger.parquet")
        for batch in ("XA03B", "XA03C", "XA03D")
    ], ignore_index=True)
    invalid = pd.concat([
        pd.read_csv(_batch_root(runtime, batch) / "invalid_process_ledger.csv")
        for batch in ("XA03B", "XA03C", "XA03D")
    ], ignore_index=True)
    predicted_cells = set(map(tuple, predictions[["process_id", "frequency"]].drop_duplicates().to_numpy()))
    invalid_cells = set(map(tuple, invalid[["process_id", "frequency"]].drop_duplicates().to_numpy()))
    if len(predicted_cells | invalid_cells) != 114 or predicted_cells & invalid_cells:
        raise ValueError("XA03E requires an exact prediction/invalid partition of 114 cells")
    target_key = target[[
        "frequency", "signal_date", "execution_date", "label_end_execution_date", "sid",
        "forward_total_return", "forward_excess_cash", "target_rank", "target_valid",
    ]]
    scored = predictions.merge(
        target_key, on=["frequency", "signal_date", "sid"], how="left", validate="many_to_one"
    )
    rank_ic = _rank_ic_ledger(scored)
    _write_parquet(root / "prediction_ledger_all.parquet", predictions)
    _write_parquet(root / "rank_ic_ledger.parquet", rank_ic)

    widths = [int(x) for x in program["paths"]["top_k"]]
    costs = [int(x) for x in program["paths"]["cost_scenarios_bps"]]
    periods = []
    holdings = []
    path_summaries = []
    for (process_id, frequency), group in scored.groupby(["process_id", "frequency"], sort=True):
        for top_k in widths:
            gross, held = _portfolio_period_ledger(group, top_k)
            holdings.append(held.assign(process_id=process_id, frequency=frequency, top_k=top_k))
            for cost in costs:
                one = gross.copy()
                one["net_return"] = one["gross_return"] - one["l1_turnover"] * cost / 10000.0
                one["process_id"] = process_id; one["frequency"] = frequency
                one["top_k"] = top_k; one["cost_bps"] = cost
                periods.append(one)

    common_periods = []
    for frequency, group in target.groupby("frequency", sort=True):
        gross, _ = _common_ew_period_ledger(group)
        for cost in costs:
            one = gross.copy()
            one["control_return"] = one["gross_return"] - one["l1_turnover"] * cost / 10000.0
            one["frequency"] = frequency; one["cost_bps"] = cost
            common_periods.append(one[["frequency", "signal_date", "cost_bps", "control_return"]])
    common = pd.concat(common_periods, ignore_index=True)
    period = pd.concat(periods, ignore_index=True)
    period = period.merge(common, on=["frequency", "signal_date", "cost_bps"], how="left", validate="many_to_one")
    period["active_return"] = period["net_return"] - period["control_return"]
    if (period[["net_return", "control_return"]] <= -1.0).any(axis=None):
        raise ValueError("return at or below -100%")
    period["relative_log_return"] = np.log1p(period["net_return"]) - np.log1p(period["control_return"])
    period["path_id"] = (
        period["process_id"] + "__" + period["frequency"] + "__top"
        + period["top_k"].astype(str) + "__" + period["cost_bps"].astype(str) + "bps"
    )
    _write_parquet(root / "period_return_ledger.parquet", period)
    _write_parquet(root / "topk_holdings.parquet", pd.concat(holdings, ignore_index=True))
    _write_parquet(root / "common_ew_period_returns.parquet", common)

    for key, group in period.groupby(["process_id", "frequency", "top_k", "cost_bps"], sort=True):
        path_summaries.append(_path_summary_record(group, *key))
    path_summary = pd.DataFrame(path_summaries)
    placeholder = []
    for item in invalid.itertuples(index=False):
        for top_k in widths:
            for cost in costs:
                placeholder.append({
                    "process_id": item.process_id, "frequency": item.frequency,
                    "top_k": top_k, "cost_bps": cost, "periods": 0,
                    "total_return": np.nan, "cagr": np.nan, "annualized_mean": np.nan,
                    "annualized_volatility": np.nan, "sharpe_zero": np.nan,
                    "max_drawdown": np.nan, "terminal_relative_wealth": np.nan,
                    "annualized_relative_log": np.nan, "active_ir": np.nan,
                    "mean_turnover": np.nan, "total_cost_return": np.nan,
                    "invalid": True, "invalid_reason": item.reason,
                })
    if placeholder:
        path_summary["invalid"] = False; path_summary["invalid_reason"] = ""
        path_summary = pd.concat([path_summary, pd.DataFrame(placeholder)], ignore_index=True)
    _write_csv(root / "path_cost_summary.csv", path_summary)
    primary_path = path_summary.loc[
        path_summary.apply(
            lambda r: int(r.cost_bps) == int(program["paths"][f"{r.frequency}_primary_cost_bps"]), axis=1
        )
    ].copy()
    _write_csv(root / "path_summary.csv", primary_path)

    absolute = _absolute_assessment(period, rank_ic, process_registry, program)
    paired = _paired_assessment(period, rank_ic, comparison_registry, "paired_promotion", program)
    rsp = _paired_assessment(period, rank_ic, comparison_registry, "rsp_ablation", program)
    _write_csv(root / "absolute_assessment.csv", absolute)
    _write_csv(root / "parent_child_incremental_assessment.csv", paired)
    _write_csv(root / "rsp_incremental_assessment.csv", rsp)
    roles = _qualification_roles(absolute, paired, rsp, process_registry, period, program, invalid)
    _write_csv(root / "qualification_role_ledger.csv", roles)
    subperiod = _subperiod_summary(period, program)
    calendar = _calendar_summary(period, program)
    _write_csv(root / "subperiod_and_mature_slice.csv", subperiod)
    _write_csv(root / "calendar_and_rolling_performance.csv", calendar)
    importance = pd.concat([
        pd.read_parquet(_batch_root(runtime, batch) / "coefficient_and_importance_ledger.parquet")
        for batch in ("XA03B", "XA03C", "XA03D")
    ], ignore_index=True)
    _write_csv(root / "coefficient_and_importance_stability.csv", _importance_summary(importance))
    coverage = _coverage_and_concentration(period, predictions)
    _write_csv(root / "coverage_and_concentration_audit.csv", coverage)
    resolved = process_registry.copy()
    resolved["weekly_primary_status"] = resolved["process_id"].map(
        roles.loc[roles["frequency"].eq("weekly")].set_index("process_id")["primary_status"]
    )
    resolved["monthly_primary_status"] = resolved["process_id"].map(
        roles.loc[roles["frequency"].eq("monthly")].set_index("process_id")["primary_status"]
    )
    _write_csv(root / "process_registry_resolved.csv", resolved)
    decision = {
        "schema_version": "xa03.decision.v1", "status": "completed_hard_stop",
        "formal_eligible": False, "qualified_process_frequency_cells": int(roles["primary_status"].str.startswith("qualified").sum()),
        "rsp_incremental_supported_cells": int(roles["tags"].str.contains("rsp_incremental_supported", na=False).sum()),
        "p00_run": False, "bagging_run": False, "stacking_run": False,
        "automatic_champion_selected": False, "hard_stop": "XA03E",
    }
    _write_json(root / "decision.json", decision)
    return {
        "batch": "XA03E", "status": "completed_hard_stop", "process_frequency_cells": 114,
        "topk_paths": 456, "cost_paths": 1824, "period_rows": len(period),
        "absolute_rows": len(absolute), "paired_rows": len(paired), "rsp_rows": len(rsp),
        "qualified_cells": decision["qualified_process_frequency_cells"], "p00_run": False,
    }


def _rank_ic_ledger(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (process_id, frequency, date), group in scored.groupby(
        ["process_id", "frequency", "signal_date"], sort=True
    ):
        valid = group["target_valid"].fillna(False) & np.isfinite(group["prediction"]) & np.isfinite(group["target_rank"])
        rows.append({
            "process_id": process_id, "frequency": frequency, "signal_date": date,
            "rank_ic": _spearman(group.loc[valid, "prediction"], group.loc[valid, "target_rank"]),
            "rank_ic_names": int(valid.sum()),
        })
    return pd.DataFrame(rows)


def _portfolio_period_ledger(group: pd.DataFrame, top_k: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []; holdings = []; pretrade: dict[str, float] = {}
    for date, one in group.groupby("signal_date", sort=True):
        ordered = one.sort_values(["prediction", "sid"], ascending=[False, True], kind="mergesort")
        selected = ordered.head(top_k).copy()
        target_weight = 1.0 / top_k
        target = {str(sid): target_weight for sid in selected["sid"]}
        turnover = sum(abs(target.get(sid, 0.0) - pretrade.get(sid, 0.0)) for sid in set(target) | set(pretrade))
        returns = selected.set_index("sid")["forward_total_return"].astype(float)
        valid = selected.set_index("sid")["target_valid"].fillna(False)
        realized = {str(sid): float(returns.loc[sid]) if bool(valid.loc[sid]) and np.isfinite(returns.loc[sid]) else 0.0 for sid in returns.index}
        gross = sum(target[sid] * realized[sid] for sid in target)
        denom = 1.0 + gross
        pretrade = {sid: target[sid] * (1.0 + realized[sid]) / denom for sid in target} if denom > 0 else {}
        rows.append({
            "signal_date": date, "execution_date": selected["execution_date"].iloc[0],
            "label_end_execution_date": selected["label_end_execution_date"].iloc[0],
            "gross_return": gross, "l1_turnover": turnover,
            "selected_count": len(selected), "invalid_selected_count": int((~selected["target_valid"].fillna(False)).sum()),
        })
        for rank, item in enumerate(selected.itertuples(index=False), start=1):
            holdings.append({"signal_date": date, "sid": str(item.sid), "rank": rank, "weight": target_weight,
                             "prediction": float(item.prediction)})
    return pd.DataFrame(rows), pd.DataFrame(holdings)


def _common_ew_period_ledger(group: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []; held_rows = []; pretrade: dict[str, float] = {}
    for date, one in group.groupby("signal_date", sort=True):
        n = len(one); target = {str(sid): 1.0 / n for sid in one["sid"]}
        turnover = sum(abs(target.get(sid, 0.0) - pretrade.get(sid, 0.0)) for sid in set(target) | set(pretrade))
        vals = one.set_index("sid")
        realized = {str(sid): float(vals.loc[sid, "forward_total_return"])
                    if bool(vals.loc[sid, "target_valid"]) and np.isfinite(vals.loc[sid, "forward_total_return"]) else 0.0
                    for sid in vals.index}
        gross = sum(target[sid] * realized[sid] for sid in target)
        denom = 1.0 + gross
        pretrade = {sid: target[sid] * (1.0 + realized[sid]) / denom for sid in target} if denom > 0 else {}
        rows.append({"signal_date": date, "gross_return": gross, "l1_turnover": turnover})
    return pd.DataFrame(rows), pd.DataFrame(held_rows)


def _path_summary_record(group: pd.DataFrame, process_id: str, frequency: str,
                         top_k: int, cost_bps: int) -> dict[str, Any]:
    ann = 52 if frequency == "weekly" else 12
    r = group["net_return"].astype(float)
    active = group["active_return"].astype(float)
    rel = group["relative_log_return"].astype(float)
    wealth = (1.0 + r).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0
    years = max(len(r) / ann, 1.0 / ann)
    return {
        "process_id": process_id, "frequency": frequency, "top_k": int(top_k), "cost_bps": int(cost_bps),
        "periods": len(r), "total_return": float(wealth.iloc[-1] - 1.0),
        "cagr": float(wealth.iloc[-1] ** (1.0 / years) - 1.0),
        "annualized_mean": float(r.mean() * ann), "annualized_volatility": float(r.std(ddof=1) * math.sqrt(ann)),
        "sharpe_zero": float(r.mean() / r.std(ddof=1) * math.sqrt(ann)) if r.std(ddof=1) > 0 else np.nan,
        "max_drawdown": float(drawdown.min()), "terminal_relative_wealth": float(np.exp(rel.sum()) - 1.0),
        "annualized_relative_log": float(rel.mean() * ann),
        "active_ir": float(active.mean() / active.std(ddof=1) * math.sqrt(ann)) if active.std(ddof=1) > 0 else np.nan,
        "mean_turnover": float(group["l1_turnover"].mean()), "total_cost_return": float((group["l1_turnover"] * cost_bps / 10000.0).sum()),
    }


def _absolute_assessment(period: pd.DataFrame, rank_ic: pd.DataFrame,
                         processes: pd.DataFrame, program: dict[str, Any]) -> pd.DataFrame:
    candidates = processes.loc[processes["primary_candidate"].astype(bool), "process_id"].astype(str).tolist()
    rows = []
    for frequency in FREQUENCIES:
        cost = int(program["paths"][f"{frequency}_primary_cost_bps"])
        for process_id in candidates:
            econ = period.loc[
                period["process_id"].eq(process_id) & period["frequency"].eq(frequency)
                & period["top_k"].eq(20) & period["cost_bps"].eq(cost)
            ]
            ic = rank_ic.loc[rank_ic["process_id"].eq(process_id) & rank_ic["frequency"].eq(frequency)]
            rows.append({
                "process_id": process_id, "frequency": frequency,
                "economic_mean": float(econ["relative_log_return"].mean()),
                "economic_p": _outer_p(econ.set_index("signal_date")["relative_log_return"], frequency, "economic", "absolute", program),
                "mean_rank_ic": float(ic["rank_ic"].mean()),
                "rank_ic_p": _outer_p(ic.set_index("signal_date")["rank_ic"], frequency, "rank_ic", "absolute", program),
            })
    frame = pd.DataFrame(rows)
    frame["economic_q"] = frame.groupby("frequency")["economic_p"].transform(_bh)
    frame["rank_ic_q"] = frame.groupby("frequency")["rank_ic_p"].transform(_bh)
    return frame


def _paired_assessment(period: pd.DataFrame, rank_ic: pd.DataFrame, comparisons: pd.DataFrame,
                       comparison_type: str, program: dict[str, Any]) -> pd.DataFrame:
    registry = comparisons.loc[comparisons["family"].eq(comparison_type)]
    rows = []
    for frequency in FREQUENCIES:
        cost = int(program["paths"][f"{frequency}_primary_cost_bps"])
        for row in registry.itertuples(index=False):
            child = period.loc[
                period["process_id"].eq(row.candidate_process_id) & period["frequency"].eq(frequency)
                & period["top_k"].eq(20) & period["cost_bps"].eq(cost),
                ["signal_date", "relative_log_return"],
            ].rename(columns={"relative_log_return": "child"})
            parent = period.loc[
                period["process_id"].eq(row.parent_process_id) & period["frequency"].eq(frequency)
                & period["top_k"].eq(20) & period["cost_bps"].eq(cost),
                ["signal_date", "relative_log_return"],
            ].rename(columns={"relative_log_return": "parent"})
            econ = child.merge(parent, on="signal_date", how="inner", validate="one_to_one")
            econ["increment"] = econ["child"] - econ["parent"]
            ci = rank_ic.loc[rank_ic["process_id"].eq(row.candidate_process_id) & rank_ic["frequency"].eq(frequency), ["signal_date", "rank_ic"]].rename(columns={"rank_ic": "child"})
            pi = rank_ic.loc[rank_ic["process_id"].eq(row.parent_process_id) & rank_ic["frequency"].eq(frequency), ["signal_date", "rank_ic"]].rename(columns={"rank_ic": "parent"})
            ic = ci.merge(pi, on="signal_date", how="inner", validate="one_to_one")
            ic["increment"] = ic["child"] - ic["parent"]
            family = "paired" if comparison_type == "paired_promotion" else "rsp"
            rows.append({
                "comparison_id": row.comparison_id, "candidate_process_id": row.candidate_process_id,
                "parent_process_id": row.parent_process_id, "frequency": frequency,
                "economic_mean_increment": float(econ["increment"].mean()),
                "economic_p": _outer_p(econ.set_index("signal_date")["increment"], frequency, "economic", family, program),
                "mean_rank_ic_increment": float(ic["increment"].mean()),
                "rank_ic_p": _outer_p(ic.set_index("signal_date")["increment"], frequency, "rank_ic", family, program),
            })
    frame = pd.DataFrame(rows)
    frame["economic_q"] = frame.groupby("frequency")["economic_p"].transform(_bh)
    frame["rank_ic_q"] = frame.groupby("frequency")["rank_ic_p"].transform(_bh)
    return frame


def _outer_p(series: pd.Series, frequency: str, outcome: str, family: str,
             program: dict[str, Any]) -> float:
    x = series.astype(float).dropna().to_numpy()
    if x.size == 0:
        return 1.0
    block = int(program["inference"][f"{frequency}_moving_block_periods"])
    draws = int(program["inference"]["bootstrap_draws"])
    seed = _derived_seed(int(program["inference"]["bootstrap_seed"]), "outer_inference", frequency, outcome, family)
    rng = np.random.default_rng(seed)
    blocks = int(math.ceil(len(x) / block))
    starts = rng.integers(0, len(x), size=(draws, blocks))
    indices = (starts[:, :, None] + np.arange(block)[None, None, :]) % len(x)
    means = x[indices.reshape(draws, -1)[:, :len(x)]].mean(axis=1)
    count = int((means <= 0.0).sum())
    return float((1 + count) / (draws + 1))


def _bh(series: pd.Series) -> pd.Series:
    p = pd.Series(series, dtype=float)
    order = np.argsort(p.to_numpy(), kind="mergesort")
    ranked = p.to_numpy()[order]
    m = len(ranked)
    q = np.minimum.accumulate((ranked * m / np.arange(1, m + 1))[::-1])[::-1]
    result = np.empty(m); result[order] = np.minimum(q, 1.0)
    return pd.Series(result, index=p.index)


def _qualification_roles(absolute: pd.DataFrame, paired: pd.DataFrame, rsp: pd.DataFrame,
                         processes: pd.DataFrame, period: pd.DataFrame,
                         program: dict[str, Any], invalid: pd.DataFrame | None = None) -> pd.DataFrame:
    pair_by_child = paired.set_index(["candidate_process_id", "frequency"])
    rsp_by_child = rsp.set_index(["candidate_process_id", "frequency"])
    rows = []
    invalid_cells = set() if invalid is None else set(zip(invalid["process_id"], invalid["frequency"]))
    for item in absolute.itertuples(index=False):
        process = str(item.process_id); frequency = str(item.frequency)
        if (process, frequency) in invalid_cells:
            rows.append({"process_id": process, "frequency": frequency,
                         "primary_status": "invalid", "tags": "invalid"})
            continue
        ann = 52 if frequency == "weekly" else 12
        absolute_pass = (
            item.economic_mean * ann >= 0.02 and item.economic_q <= 0.10
            and item.mean_rank_ic > 0 and _stability_gate(period, process, frequency, program)
        )
        pair_pass = False; rsp_pass = False
        if (process, frequency) in pair_by_child.index:
            p = pair_by_child.loc[(process, frequency)]
            pair_pass = bool(
                absolute_pass and p.economic_mean_increment * ann >= 0.02
                and p.economic_q <= 0.10 and p.mean_rank_ic_increment >= -0.005
            )
        if (process, frequency) in rsp_by_child.index:
            r = rsp_by_child.loc[(process, frequency)]
            rsp_pass = bool(r.economic_mean_increment > 0 and r.economic_q <= 0.10 and r.mean_rank_ic_increment > 0)
        status = "qualified_incremental" if pair_pass else ("qualified_absolute_only" if absolute_pass else "not_qualified")
        tags = []
        if absolute_pass: tags.append("absolute_qualified")
        if pair_pass: tags.append("state_incremental_qualified" if process.startswith("FS_") else "incremental_qualified")
        if rsp_pass: tags.append("rsp_incremental_supported")
        if item.rank_ic_q <= 0.10 and item.mean_rank_ic > 0 and not absolute_pass: tags.append("predictive_only")
        if (item.economic_p <= 0.05 or item.rank_ic_p <= 0.05) and not absolute_pass: tags.append("exploratory_unstable")
        sub = _fixed_subperiod_signs(period, process, frequency, program)
        if absolute_pass and all(x > 0 for x in sub): tags.append("broadly_robust")
        if absolute_pass and np.prod(np.sign(sub)) < 0: tags.append("conditional_specialist")
        rows.append({"process_id": process, "frequency": frequency, "primary_status": status,
                     "tags": "|".join(tags) if tags else "not_qualified"})
    # no-RSP controls are present but cannot qualify.
    no_rsp = processes.loc[processes["no_rsp_diagnostic"].astype(bool), "process_id"]
    for process in no_rsp:
        for frequency in FREQUENCIES:
            if (process, frequency) in invalid_cells:
                rows.append({"process_id": process, "frequency": frequency,
                             "primary_status": "invalid", "tags": "invalid"})
                continue
            rows.append({"process_id": process, "frequency": frequency,
                         "primary_status": "not_qualified", "tags": "no_rsp_mechanism_control"})
    return pd.DataFrame(rows).sort_values(["frequency", "process_id"], ignore_index=True)


def _stability_gate(period: pd.DataFrame, process: str, frequency: str,
                    program: dict[str, Any]) -> bool:
    cost = int(program["paths"][f"{frequency}_primary_cost_bps"])
    cell = period.loc[period["process_id"].eq(process) & period["frequency"].eq(frequency)]
    main = cell.loc[cell["cost_bps"].eq(cost)]
    widths = main.groupby("top_k")["relative_log_return"].mean()
    if 20 not in widths or widths.loc[20] <= 0 or sum(widths.get(k, -np.inf) > 0 for k in (10, 20, 50)) < 2:
        return False
    stress = cell.loc[cell["top_k"].eq(20) & cell["cost_bps"].eq(20), "relative_log_return"].mean()
    if not stress > 0:
        return False
    primary = main.loc[main["top_k"].eq(20)].copy()
    primary["year"] = pd.to_datetime(primary["execution_date"]).dt.year
    sums = primary.groupby("year")["relative_log_return"].sum()
    if len(sums) == 0 or (sums > 0).mean() < 0.75:
        return False
    denom = sums.abs().sum()
    return bool(denom > 0 and sums.abs().max() / denom <= 0.50)


def _fixed_subperiod_signs(period: pd.DataFrame, process: str, frequency: str,
                           program: dict[str, Any]) -> tuple[float, float]:
    cost = int(program["paths"][f"{frequency}_primary_cost_bps"])
    cell = period.loc[
        period["process_id"].eq(process) & period["frequency"].eq(frequency)
        & period["top_k"].eq(20) & period["cost_bps"].eq(cost)
    ].copy()
    date = pd.to_datetime(cell["execution_date"])
    return (
        float(cell.loc[date.le(pd.Timestamp("2021-12-31")), "relative_log_return"].sum()),
        float(cell.loc[date.ge(pd.Timestamp("2022-01-01")), "relative_log_return"].sum()),
    )


def _subperiod_summary(period: pd.DataFrame, program: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for (process, frequency), group in period.groupby(["process_id", "frequency"], sort=True):
        cost = int(program["paths"][f"{frequency}_primary_cost_bps"])
        group = group.loc[group["top_k"].eq(20) & group["cost_bps"].eq(cost)].copy()
        execution = pd.to_datetime(group["execution_date"])
        for label, mask in (
            ("2018_2021", execution.le(pd.Timestamp("2021-12-31"))),
            ("2022_2026h1", execution.ge(pd.Timestamp("2022-01-01"))),
            ("full", pd.Series(True, index=group.index)),
        ):
            one = group.loc[mask]
            rows.append({"process_id": process, "frequency": frequency, "period": label,
                         "periods": len(one), "relative_log_sum": float(one["relative_log_return"].sum()),
                         "active_mean": float(one["active_return"].mean())})
    return pd.DataFrame(rows)


def _calendar_summary(period: pd.DataFrame, program: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for (process, frequency), group in period.groupby(["process_id", "frequency"], sort=True):
        cost = int(program["paths"][f"{frequency}_primary_cost_bps"])
        one = group.loc[group["top_k"].eq(20) & group["cost_bps"].eq(cost)].copy()
        one["year"] = pd.to_datetime(one["execution_date"]).dt.year
        for year, cell in one.groupby("year", sort=True):
            rows.append({"process_id": process, "frequency": frequency, "window_type": "calendar_year",
                         "window_id": str(year), "periods": len(cell),
                         "relative_log_sum": float(cell["relative_log_return"].sum()),
                         "active_mean": float(cell["active_return"].mean())})
    return pd.DataFrame(rows)


def _importance_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["process_id", "frequency", "feature", "mean_value", "std_value", "refits"])
    return frame.groupby(["process_id", "frequency", "feature", "kind"], sort=True)["value"].agg(
        mean_value="mean", std_value="std", refits="size"
    ).reset_index()


def _coverage_and_concentration(period: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    pred = predictions.groupby(["process_id", "frequency"], sort=True).agg(
        prediction_rows=("sid", "size"), signal_dates=("signal_date", "nunique"),
        median_names=("sid", lambda x: float(x.groupby(predictions.loc[x.index, "signal_date"]).size().median())),
    ).reset_index()
    invalid = period.groupby(["process_id", "frequency"], sort=True)["invalid_selected_count"].sum().rename("invalid_selected_total").reset_index()
    return pred.merge(invalid, on=["process_id", "frequency"], how="left")


def audit_xa03(project_root: str | Path, runtime_root: str | Path) -> dict[str, Any]:
    project = Path(project_root).resolve(); runtime = Path(runtime_root).resolve()
    _verify_lock(project)
    manifests = {}
    for batch in RUN_IDS:
        root = _batch_root(runtime, batch)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        _verify_manifest(root, manifest)
        manifests[batch] = _sha(root / "manifest.json")
    e = _batch_root(runtime, "XA03E")
    summary = json.loads((e / "summary.json").read_text(encoding="utf-8"))
    decision = json.loads((e / "decision.json").read_text(encoding="utf-8"))
    if summary["process_frequency_cells"] != 114 or summary["topk_paths"] != 456 or summary["cost_paths"] != 1824:
        raise ValueError("XA03 closure cardinality mismatch")
    if any(decision[key] for key in ("p00_run", "bagging_run", "stacking_run", "automatic_champion_selected")):
        raise ValueError("unauthorized XA03 continuation detected")
    return {"status": "passed", "manifests": manifests, "decision": decision,
            "process_frequency_cells": 114, "topk_paths": 456, "cost_paths": 1824}


def _split_pipe(value: Any) -> list[str]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    text = str(value).strip()
    return [] if not text or text.upper() in {"NONE", "INHERIT"} else text.split("|")


def _spearman(left: Iterable[float], right: Iterable[float]) -> float:
    a = pd.Series(left, dtype=float); b = pd.Series(right, dtype=float)
    valid = np.isfinite(a) & np.isfinite(b)
    if valid.sum() < 2:
        return np.nan
    return float(a.loc[valid].corr(b.loc[valid], method="spearman"))


def _derived_seed(global_seed: int, *parts: object) -> int:
    text = "|".join([str(global_seed), *map(str, parts)])
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def _load_program(project: Path) -> dict[str, Any]:
    with (project / PROGRAM).open("rb") as handle:
        return tomllib.load(handle)


def _batch_root(runtime: Path, batch: str) -> Path:
    return runtime / "results" / "experiments" / "xa03" / batch / "runs" / RUN_IDS[batch]


def _xa01_root(runtime: Path) -> Path:
    return runtime / "results/experiments/xa01/xa01-atomic-factor-walkforward-20260820-v1"


def _verify_lock(project: Path) -> None:
    lock_path = project / LOCK
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    for item in lock["files"]:
        path = project / item["path"]
        if _sha(path) != item["sha256"] or path.stat().st_size != int(item["size_bytes"]):
            raise ValueError(f"XA03 preregistration drift: {path}")


def _require_clean_git(project: Path) -> str:
    status = subprocess.run(["git", "status", "--porcelain"], cwd=project, check=True, capture_output=True, text=True).stdout
    if status.strip():
        raise ValueError("XA03 execution requires a clean Git worktree")
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, check=True, capture_output=True, text=True).stdout.strip()


def _dependency_manifests(runtime: Path, batch: str) -> dict[str, str]:
    order = list(RUN_IDS)
    dependencies = {}
    for dep in order[:order.index(batch)]:
        path = _batch_root(runtime, dep) / "manifest.json"
        if not path.is_file():
            raise FileNotFoundError(f"missing dependency manifest: {path}")
        manifest = json.loads(path.read_text(encoding="utf-8")); _verify_manifest(path.parent, manifest)
        dependencies[dep] = _sha(path)
    return dependencies


def _verify_parent_inputs(project: Path, runtime: Path, program: dict[str, Any]) -> None:
    parent = program["parent"]
    xa01 = _xa01_root(runtime)
    checks = {
        project / "config/experiments/xa01/PREREG_LOCK.json": parent["xa01_prereg_lock_sha256"],
        xa01 / "manifest.json": parent["xa01_runtime_manifest_sha256"],
        xa01 / "factor_values_weekly_monthly.parquet": parent["xa01_factor_values_sha256"],
        xa01 / "target_ledger.parquet": parent["xa01_target_ledger_sha256"],
        project / "config/experiments/xa02/PREREG_LOCK.json": parent["xa02_prereg_lock_sha256"],
        project / "results/published/cross_sectional_alpha/XA02/publication_manifest.json": parent["xa02_publication_manifest_sha256"],
    }
    for path, expected in checks.items():
        if not path.is_file() or _sha(path) != expected:
            raise ValueError(f"XA03 parent hash mismatch: {path}")


def _write_manifest(project: Path, root: Path, batch: str, commit: str,
                    dependencies: dict[str, str]) -> None:
    files = {p.name: {"sha256": _sha(p), "size_bytes": p.stat().st_size}
             for p in sorted(root.iterdir()) if p.is_file() and p.name != "manifest.json"}
    payload = {
        "schema_version": "xa03.runtime_manifest.v1", "batch": batch,
        "run_id": RUN_IDS[batch], "git_commit": commit, "git_dirty": False,
        "prereg_lock_sha256": _sha(project / LOCK), "dependency_manifests": dependencies,
        "python": platform.python_version(), "files": files,
    }
    _write_json(root / "manifest.json", payload)


def _verify_manifest(root: Path, manifest: dict[str, Any]) -> None:
    expected = set(manifest["files"])
    actual = {p.name for p in root.iterdir() if p.is_file() and p.name != "manifest.json"}
    if expected != actual:
        raise ValueError(f"manifest member mismatch: {root}")
    for name, meta in manifest["files"].items():
        path = root / name
        if _sha(path) != meta["sha256"] or path.stat().st_size != int(meta["size_bytes"]):
            raise ValueError(f"manifest hash mismatch: {path}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    frame.to_parquet(path, index=False)


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    frame.to_csv(path, index=False, lineterminator="\n")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
