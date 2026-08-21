"""XA04 unified complete-case cross-sectional model experiment."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from momentum_reversal.pipelines import xa03_experiments as x3


PROGRAM = Path("config/experiments/xa04/program.toml")
LOCK = Path("config/experiments/xa04/PREREG_LOCK.json")
RUN_IDS = {
    "XA04A": "xa04a-core10-panel-20260821-v1",
    "XA04B": "xa04b-unified-models-20260821-v1",
    "XA04C": "xa04c-portfolios-inference-20260821-v1",
    "XA04D": "xa04d-audit-decision-20260821-v1",
}
FREQUENCIES = ("weekly", "monthly")


def run_xa04(project_root: str | Path, runtime_root: str | Path, batch: str) -> dict[str, Any]:
    project = Path(project_root).resolve(); runtime = Path(runtime_root).resolve()
    batch = batch.upper()
    if batch not in RUN_IDS:
        raise ValueError(f"unknown XA04 batch: {batch}")
    program = _program(project)
    _verify_lock(project)
    commit = _require_clean(project)
    dependencies = _dependencies(runtime, batch)
    root = _root(runtime, batch)
    if root.exists():
        raise FileExistsError(f"XA04 run already exists: {root}")
    root.mkdir(parents=True)
    try:
        result = {"XA04A": _run_a, "XA04B": _run_b, "XA04C": _run_c, "XA04D": _run_d}[batch](
            project, runtime, root, program
        )
        _write_json(root / "summary.json", result)
        _write_manifest(project, root, batch, commit, dependencies)
        return result
    except Exception as exc:
        _write_json(root / "FAILED.json", {"batch": batch, "type": type(exc).__name__, "message": str(exc)})
        raise


def _run_a(project: Path, runtime: Path, root: Path, p: dict[str, Any]) -> dict[str, Any]:
    parent = _xa03a(runtime)
    parent_checks = {
        project / p["parent"]["xa03_publication_manifest_path"]: p["parent"]["xa03_publication_manifest_sha256"],
        parent / "manifest.json": p["parent"]["xa03a_runtime_manifest_sha256"],
        parent / "model_feature_panel.parquet": p["parent"]["xa03_factor_panel_sha256"],
        parent / "extended_target_ledger.parquet": p["parent"]["xa03_target_sha256"],
        parent / "state_feature_panel.parquet": p["parent"]["xa03_state_sha256"],
        parent / "refit_ledger.parquet": p["parent"]["xa03_refit_sha256"],
    }
    for path, expected in parent_checks.items():
        if not path.is_file() or _sha(path) != expected:
            raise ValueError(f"XA04 parent drift: {path}")
    features = pd.read_parquet(parent / "model_feature_panel.parquet")
    targets = pd.read_parquet(parent / "extended_target_ledger.parquet")
    states = pd.read_parquet(parent / "state_feature_panel.parquet")
    refits = pd.read_parquet(parent / "refit_ledger.parquet")
    factors = list(p["factors"]["core10"])
    start = pd.Timestamp(p["sample"]["screen_start_signal"])
    flags = [f"{f}__available" for f in factors]
    screen = features.loc[features["signal_date"].ge(start), ["frequency", "signal_date", "sid", *flags]].copy()
    coverage_rows = []
    for factor in factors:
        flag = f"{factor}__available"
        by_date = screen.groupby(["frequency", "signal_date"], sort=True)[flag].mean()
        coverage_rows.append({"factor_id": factor, "minimum_coverage": float(by_date.min()),
                              "weekly_minimum": float(by_date.xs("weekly").min()),
                              "monthly_minimum": float(by_date.xs("monthly").min()), "passed": bool(by_date.min() >= .95)})
    coverage = pd.DataFrame(coverage_rows)
    if not bool(coverage["passed"].all()):
        raise ValueError(f"CORE10 factor coverage gate failed: {coverage.loc[~coverage.passed, 'factor_id'].tolist()}")
    complete = features[flags].astype(bool).all(axis=1) & np.isfinite(features[factors].to_numpy(dtype=float)).all(axis=1)
    panel = features.loc[complete, ["frequency", "signal_date", "sid", *factors]].copy()
    counts = panel.groupby(["frequency", "signal_date"], sort=True).size().rename("complete_names").reset_index()
    base_counts = features.groupby(["frequency", "signal_date"], sort=True).size().rename("base_names").reset_index()
    counts = counts.merge(base_counts, on=["frequency", "signal_date"], validate="one_to_one")
    counts["coverage"] = counts["complete_names"] / counts["base_names"]
    oos = counts["signal_date"].ge(pd.Timestamp(p["sample"]["first_oos_signal_close"]))
    if counts.loc[oos, "complete_names"].min() < int(p["sample"]["minimum_names_per_date"]):
        raise ValueError("CORE10 complete-case names fell below 350")
    if counts.loc[oos, "coverage"].min() < float(p["sample"]["complete_case_minimum_coverage"]):
        raise ValueError("CORE10 complete-case coverage fell below 95%")
    keys = panel[["frequency", "signal_date", "sid"]]
    target = keys.merge(targets.drop(columns=["target_rank"]), on=["frequency", "signal_date", "sid"], how="left", validate="one_to_one")
    target["target_rank"] = target.groupby(["frequency", "signal_date"], sort=False)["forward_excess_cash"].transform(
        lambda values: x3.centered_cross_sectional_rank(values, target.loc[values.index, "target_valid"])
    )
    target = target.sort_values(["frequency", "signal_date", "sid"], ignore_index=True)
    if target.duplicated(["frequency", "signal_date", "sid"]).any():
        raise ValueError("duplicate CORE10 target key")
    state_dates = panel[["frequency", "signal_date"]].drop_duplicates()
    state = state_dates.merge(states, on=["frequency", "signal_date"], how="left", validate="one_to_one")
    state_ids = list(p["states"]["s6"])
    if state[state_ids].isna().any(axis=None):
        raise ValueError("state coverage is incomplete")
    _write_parquet(root / "model_feature_panel.parquet", panel)
    _write_parquet(root / "target_ledger.parquet", target)
    _write_parquet(root / "state_feature_panel.parquet", state)
    _write_parquet(root / "refit_ledger.parquet", refits)
    _write_csv(root / "factor_coverage_gate.csv", coverage)
    _write_csv(root / "complete_case_coverage.csv", counts)
    _write_json(root / "panel_audit.json", {
        "factor_count": 10, "all_missing_forbidden": True, "same_keyspace_for_all_models": True,
        "oos_minimum_names": int(counts.loc[oos, "complete_names"].min()),
        "oos_minimum_coverage": float(counts.loc[oos, "coverage"].min()),
    })
    return {"batch": "XA04A", "status": "completed", "panel_rows": len(panel), "target_rows": len(target),
            "minimum_oos_names": int(counts.loc[oos, "complete_names"].min()),
            "minimum_oos_coverage": float(counts.loc[oos, "coverage"].min())}


def _run_b(project: Path, runtime: Path, root: Path, p: dict[str, Any]) -> dict[str, Any]:
    a = _root(runtime, "XA04A")
    panel = pd.read_parquet(a / "model_feature_panel.parquet")
    target = pd.read_parquet(a / "target_ledger.parquet")
    states = pd.read_parquet(a / "state_feature_panel.parquet")
    refits = pd.read_parquet(a / "refit_ledger.parquet")
    registry = pd.read_csv(project / "config/experiments/xa04/process_registry.csv", skipinitialspace=True).fillna("")
    recipes = pd.read_csv(project / "config/experiments/xa04/model_recipes.csv").set_index("recipe_id")
    factors = list(p["factors"]["core10"]); first = pd.Timestamp(p["sample"]["first_oos_signal_close"])
    predictions = []; refit_audits = []; importance = []; leaf_audits = []; invalid = []
    for proc in registry.itertuples(index=False):
        for frequency in FREQUENCIES:
            try:
                if proc.family.startswith("static"):
                    pred = _static_prediction(panel, proc.process_id, proc.family, frequency, factors, first)
                    predictions.append(pred); continue
                state_ids = list(p["states"][str(proc.state_bundle).lower()]) if proc.state_bundle else []
                result = _fixed_walk_forward(panel, target, states, refits, proc.process_id, proc.family,
                                             proc.recipe_id, recipes.loc[proc.recipe_id], frequency,
                                             factors, state_ids, first, p)
                predictions.append(result[0]); refit_audits.append(result[1]); importance.append(result[2]); leaf_audits.append(result[3])
            except Exception as exc:
                invalid.append({"process_id": proc.process_id, "frequency": frequency, "reason": str(exc)})
    for frequency in FREQUENCIES:
        raw = panel.loc[panel["frequency"].eq(frequency) & panel["signal_date"].ge(first),
                        ["frequency", "signal_date", "sid", "XS003_MOM_12_7"]].copy()
        raw = raw.rename(columns={"XS003_MOM_12_7": "prediction"})
        raw["process_id"] = "RAW_XS003_CORE10"; raw["recipe_id"] = "DIRECT_RANK"; raw["fit_signal_date"] = pd.NaT
        predictions.append(raw[["process_id", "frequency", "signal_date", "sid", "prediction", "recipe_id", "fit_signal_date"]])
    pred = pd.concat(predictions, ignore_index=True).sort_values(["process_id", "frequency", "signal_date", "sid"], ignore_index=True)
    invalid_frame = pd.DataFrame(invalid, columns=["process_id", "frequency", "reason"])
    observed = set(map(tuple, pred[["process_id", "frequency"]].drop_duplicates().to_numpy()))
    invalid_cells = set(map(tuple, invalid_frame[["process_id", "frequency"]].to_numpy()))
    expected = {(pid, f) for pid in registry["process_id"] for f in FREQUENCIES}
    benchmark_cells = {("RAW_XS003_CORE10", f) for f in FREQUENCIES}
    if (observed - benchmark_cells) | invalid_cells != expected or (observed - benchmark_cells) & invalid_cells:
        raise ValueError("prediction/invalid partition mismatch")
    _write_parquet(root / "prediction_ledger.parquet", pred)
    _write_parquet(root / "model_refit_ledger.parquet", pd.concat(refit_audits, ignore_index=True) if refit_audits else pd.DataFrame())
    _write_parquet(root / "coefficient_and_importance_ledger.parquet", pd.concat(importance, ignore_index=True) if importance else pd.DataFrame())
    _write_parquet(root / "leaf_support_audit.parquet", pd.concat(leaf_audits, ignore_index=True) if leaf_audits else pd.DataFrame())
    _write_csv(root / "invalid_process_ledger.csv", invalid_frame)
    return {"batch": "XA04B", "status": "completed", "registered_cells": 68,
            "valid_cells": len(observed), "invalid_cells": len(invalid_cells), "prediction_rows": len(pred)}


def _static_prediction(panel: pd.DataFrame, pid: str, family: str, frequency: str,
                       factors: list[str], first: pd.Timestamp) -> pd.DataFrame:
    one = panel.loc[panel["frequency"].eq(frequency) & panel["signal_date"].ge(first),
                    ["frequency", "signal_date", "sid", *factors]].copy()
    if family == "static_equal":
        one["prediction"] = one[factors].mean(axis=1)
    else:
        dims = [factors[:4], [factors[4]], [factors[5]], factors[6:9], [factors[9]]]
        one["prediction"] = sum(one[d].mean(axis=1) for d in dims) / 5.0
    one["process_id"] = pid; one["recipe_id"] = pid; one["fit_signal_date"] = pd.NaT
    return one[["process_id", "frequency", "signal_date", "sid", "prediction", "recipe_id", "fit_signal_date"]]


def _fixed_walk_forward(panel: pd.DataFrame, target: pd.DataFrame, states: pd.DataFrame,
                        refits: pd.DataFrame, pid: str, family: str, recipe_id: str, recipe: pd.Series,
                        frequency: str, factors: list[str], state_ids: list[str], first: pd.Timestamp,
                        p: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    data = panel.loc[panel["frequency"].eq(frequency)].merge(
        target.loc[target["frequency"].eq(frequency), ["frequency", "signal_date", "sid", "target_rank", "target_valid", "target_available_at"]],
        on=["frequency", "signal_date", "sid"], validate="one_to_one")
    if state_ids:
        data = data.merge(states.loc[states["frequency"].eq(frequency), ["frequency", "signal_date", *state_ids]],
                          on=["frequency", "signal_date"], validate="many_to_one")
    eval_dates = data.loc[data["signal_date"].ge(first), "signal_date"].drop_duplicates().sort_values()
    specs = refits.loc[refits["frequency"].eq(frequency)].sort_values("prediction_start_signal_date")
    preds = []; audits = []; importances = []; leaves = []
    for i, spec in enumerate(specs.itertuples(index=False)):
        begin = pd.Timestamp(spec.prediction_start_signal_date)
        end = pd.Timestamp(specs.iloc[i + 1]["prediction_start_signal_date"]) if i + 1 < len(specs) else pd.Timestamp.max
        dates = eval_dates.loc[eval_dates.ge(begin) & eval_dates.lt(end)]
        if len(dates) == 0:
            continue
        train = data.loc[data["target_valid"].fillna(False) & data["target_rank"].notna()
                         & pd.to_datetime(data["target_available_at"]).le(pd.Timestamp(spec.refit_signal_date))].copy()
        legal_dates = train["signal_date"].drop_duplicates().sort_values()
        maximum = int(p["walk_forward"][f"maximum_training_dates_{frequency}"])
        minimum = int(p["walk_forward"][f"minimum_training_dates_{frequency}"])
        if len(legal_dates) < minimum:
            raise ValueError(f"insufficient training dates at {spec.refit_signal_date}: {len(legal_dates)}")
        train = train.loc[train["signal_date"].isin(set(legal_dates.iloc[-maximum:]))].sort_values(["signal_date", "sid"])
        model, transform, imp, leaf = _fit(train, family, recipe, factors, state_ids, frequency, p)
        for date in dates:
            current = data.loc[data["signal_date"].eq(date)].copy()
            matrix = _matrix(current, family, factors, state_ids, transform)
            preds.append(pd.DataFrame({"process_id": pid, "frequency": frequency, "signal_date": date,
                                       "sid": current["sid"].to_numpy(), "prediction": model.predict(matrix),
                                       "recipe_id": recipe_id, "fit_signal_date": pd.Timestamp(spec.refit_signal_date)}))
        audits.append({"process_id": pid, "frequency": frequency, "refit_signal_date": spec.refit_signal_date,
                       "recipe_id": recipe_id, "training_dates": train["signal_date"].nunique(), "training_rows": len(train)})
        imp["process_id"] = pid; imp["frequency"] = frequency; imp["refit_signal_date"] = spec.refit_signal_date; imp["recipe_id"] = recipe_id
        importances.append(imp)
        if not leaf.empty:
            leaf["process_id"] = pid; leaf["frequency"] = frequency; leaf["refit_signal_date"] = spec.refit_signal_date; leaf["recipe_id"] = recipe_id
            leaves.append(leaf)
    if not preds:
        raise ValueError("no walk-forward predictions")
    return pd.concat(preds, ignore_index=True), pd.DataFrame(audits), pd.concat(importances, ignore_index=True), (pd.concat(leaves, ignore_index=True) if leaves else pd.DataFrame())


def _fit(train: pd.DataFrame, family: str, recipe: pd.Series, factors: list[str], state_ids: list[str],
         frequency: str, p: dict[str, Any]) -> tuple[Any, dict[str, Any], pd.DataFrame, pd.DataFrame]:
    transform = x3._fit_state_transform(train, state_ids)
    matrix = _matrix(train, family, factors, state_ids, transform)
    y = train["target_rank"].to_numpy(dtype=float)
    weights = 1.0 / train.groupby("signal_date")["sid"].transform("size").to_numpy(dtype=float)
    if family == "ridge":
        model = x3._WeightedRidge(float(recipe["alpha"])).fit(matrix, y, weights)
        imp = pd.DataFrame({"feature": matrix.columns, "value": model.coef_, "kind": "coefficient"})
        return model, transform, imp, pd.DataFrame()
    model = LGBMRegressor(objective="regression", max_depth=int(recipe["max_depth"]),
                          num_leaves=int(recipe["num_leaves"]), n_estimators=int(recipe["n_estimators"]),
                          learning_rate=float(p["models"]["lightgbm_learning_rate"]),
                          min_child_samples=int(p["models"]["lightgbm_min_child_samples"]),
                          min_child_weight=float(p["models"][f"lightgbm_min_sum_hessian_in_leaf_{frequency}"]),
                          reg_lambda=float(p["models"]["lightgbm_reg_lambda"]), subsample=1.0,
                          colsample_bytree=1.0, random_state=int(p["models"]["lightgbm_seed"]),
                          n_jobs=1, deterministic=True, force_col_wise=True, verbosity=-1)
    model.fit(matrix, y, sample_weight=weights)
    leaf = _leaf_audit(model, matrix, train["signal_date"], weights, frequency, p)
    if not bool(leaf["passed"].all()):
        failed = leaf.loc[~leaf["passed"]].sort_values(["unique_dates", "neff_dates"]).iloc[0]
        raise ValueError(
            "registered LightGBM leaf support gate failed: "
            f"tree={int(failed.tree)},leaf={int(failed.leaf)},dates={int(failed.unique_dates)},"
            f"neff={float(failed.neff_dates):.9f},year_mass={float(failed.max_single_year_mass):.12f}"
        )
    imp = pd.DataFrame({"feature": matrix.columns, "value": model.feature_importances_, "kind": "split_importance"})
    return model, transform, imp, leaf


def _matrix(frame: pd.DataFrame, family: str, factors: list[str], state_ids: list[str], transform: dict[str, Any]) -> pd.DataFrame:
    out = frame[factors].astype(float).copy(); zstates = {}
    for state in state_ids:
        spec = transform[state]; raw = np.clip(frame[state].to_numpy(dtype=float), spec["q01"], spec["q99"])
        zstates[state] = (raw - spec["mean"]) / spec["std"]
    if family == "lightgbm":
        for state, values in zstates.items(): out[state] = values
    elif state_ids:
        for factor in factors:
            for state, values in zstates.items(): out[f"{factor}_x_{state}"] = out[factor].to_numpy() * values
    if not np.isfinite(out.to_numpy()).all(): raise ValueError("non-finite model matrix")
    return out


def _leaf_audit(model: LGBMRegressor, matrix: pd.DataFrame, dates: pd.Series, weights: np.ndarray,
                frequency: str, p: dict[str, Any]) -> pd.DataFrame:
    assigned = np.asarray(model.booster_.predict(matrix, pred_leaf=True)); assigned = assigned[:, None] if assigned.ndim == 1 else assigned
    d = pd.to_datetime(dates).reset_index(drop=True); w = np.asarray(weights, dtype=float); rows = []
    min_dates = int(p["models"][f"lightgbm_leaf_min_unique_dates_{frequency}"])
    min_neff = float(p["models"][f"lightgbm_leaf_min_neff_dates_{frequency}"])
    max_year = float(p["models"]["lightgbm_leaf_max_single_year_mass"])
    for tree in range(assigned.shape[1]):
        for leaf_id in np.unique(assigned[:, tree]):
            mask = assigned[:, tree] == leaf_id
            masses = pd.Series(w[mask], index=d.loc[mask]).groupby(level=0).sum()
            neff = float(masses.sum() ** 2 / np.square(masses).sum())
            year_mass = masses.groupby(masses.index.year).sum(); concentration = float(year_mass.max() / year_mass.sum())
            passed = masses.size >= min_dates and neff + 1e-9 >= min_neff and concentration <= max_year + 1e-9
            rows.append({"tree": tree, "leaf": int(leaf_id), "unique_dates": masses.size, "neff_dates": neff,
                         "calendar_years": len(year_mass), "max_single_year_mass": concentration, "passed": passed})
    return pd.DataFrame(rows)


def _run_c(project: Path, runtime: Path, root: Path, p: dict[str, Any]) -> dict[str, Any]:
    registry = pd.read_csv(project / "config/experiments/xa04/process_registry.csv", skipinitialspace=True).fillna("")
    pred = pd.read_parquet(_root(runtime, "XA04B") / "prediction_ledger.parquet")
    invalid = pd.read_csv(_root(runtime, "XA04B") / "invalid_process_ledger.csv")
    target = pd.read_parquet(_root(runtime, "XA04A") / "target_ledger.parquet")
    first = pd.Timestamp(p["sample"]["first_oos_signal_close"]); target = target.loc[target["signal_date"].ge(first)]
    scored = pred.merge(target[["frequency", "signal_date", "execution_date", "label_end_execution_date", "sid", "forward_total_return", "target_rank", "target_valid"]],
                        on=["frequency", "signal_date", "sid"], validate="many_to_one")
    rank_ic = x3._rank_ic_ledger(scored); _write_parquet(root / "rank_ic_ledger.parquet", rank_ic)
    holdings = []; proxies = []
    for (pid, frequency), group in scored.groupby(["process_id", "frequency"], sort=True):
        for k in p["portfolio"]["top_k"]:
            gross, held = x3._portfolio_period_ledger(group, int(k))
            holdings.append(held.assign(process_id=pid, frequency=frequency, top_k=int(k)))
            proxies.append(gross.assign(process_id=pid, frequency=frequency, top_k=int(k)))
    held = pd.concat(holdings, ignore_index=True); proxy = pd.concat(proxies, ignore_index=True)
    _write_parquet(root / "topk_holdings.parquet", held); _write_parquet(root / "label_based_portfolio_proxy.parquet", proxy)
    costs = [int(x) for x in p["portfolio"]["cost_bps"]]
    x3._init_portfolio_worker(str(_market_root(project, runtime)), str(root / "topk_holdings.parquet"),
                              str(root / "label_based_portfolio_proxy.parquet"), str(_root(runtime, "XA04A") / "target_ledger.parquet"), tuple(costs))
    tasks = sorted(map(tuple, held[["process_id", "frequency"]].drop_duplicates().to_numpy()))
    results = [x3._portfolio_worker_task(task) for task in tasks]
    controls = [x3._portfolio_worker_task(("__COMMON_EW__", f)) for f in FREQUENCIES]
    period = pd.concat([r[0] for r in results], ignore_index=True); common = pd.concat([r[0] for r in controls], ignore_index=True)
    daily = pd.concat([r[1] for r in results] + [r[1] for r in controls], ignore_index=True)
    accounting = pd.DataFrame([row for r in results for row in r[2]])
    if not bool(accounting["identity_passed"].all()): raise ValueError("event-driven accounting failed")
    period = period.merge(common, on=["frequency", "signal_date", "cost_bps"], validate="many_to_one")
    period["active_return"] = period["net_return"] - period["control_return"]
    period["relative_log_return"] = np.log1p(period["net_return"]) - np.log1p(period["control_return"])
    _write_parquet(root / "period_return_ledger.parquet", period); _write_parquet(root / "daily_nav_paths.parquet", daily)
    _write_csv(root / "portfolio_accounting_identity.csv", accounting)
    absolute = _absolute(period, rank_ic, registry, invalid, p); paired = _paired(period, rank_ic, registry, invalid, p, "parent")
    rsp = _paired(period, rank_ic, registry, invalid, p, "rsp"); raw = _raw_anchor(period, registry, invalid, p)
    _write_csv(root / "absolute_assessment.csv", absolute); _write_csv(root / "parent_increment_assessment.csv", paired)
    _write_csv(root / "rsp_ablation_assessment.csv", rsp); _write_csv(root / "raw_xs003_assessment.csv", raw)
    roles = _roles(period, absolute, paired, raw, registry, invalid, p); _write_csv(root / "qualification_role_ledger.csv", roles)
    return {"batch": "XA04C", "status": "completed", "valid_process_frequency_cells": len(tasks),
            "portfolio_paths": len(tasks) * 4, "cost_paths": len(tasks) * 16,
            "qualified_incremental_cells": int(roles["qualified_incremental"].sum())}


def _absolute(period: pd.DataFrame, ic: pd.DataFrame, reg: pd.DataFrame, invalid: pd.DataFrame, p: dict[str, Any]) -> pd.DataFrame:
    rows = []; bad = set(map(tuple, invalid[["process_id", "frequency"]].to_numpy())) if not invalid.empty else set()
    for frequency in FREQUENCIES:
        cost = int(p["portfolio"][f"primary_cost_bps_{frequency}"])
        for pid in reg["process_id"]:
            if (pid, frequency) in bad:
                rows.append({"process_id": pid, "frequency": frequency, "economic_mean": np.nan, "economic_p": 1.0, "mean_rank_ic": np.nan, "rank_ic_p": 1.0}); continue
            econ = period.loc[period.process_id.eq(pid) & period.frequency.eq(frequency) & period.top_k.eq(20) & period.cost_bps.eq(cost)]
            one_ic = ic.loc[ic.process_id.eq(pid) & ic.frequency.eq(frequency)]
            rows.append({"process_id": pid, "frequency": frequency, "economic_mean": econ.relative_log_return.mean(),
                         "economic_p": _pvalue(econ.relative_log_return, frequency, "absolute", p),
                         "mean_rank_ic": one_ic.rank_ic.mean(), "rank_ic_p": _pvalue(one_ic.rank_ic, frequency, "absolute_ic", p)})
    out = pd.DataFrame(rows); out["economic_q"] = out.groupby("frequency").economic_p.transform(_bh); out["rank_ic_q"] = out.groupby("frequency").rank_ic_p.transform(_bh)
    return out


def _paired(period: pd.DataFrame, ic: pd.DataFrame, reg: pd.DataFrame, invalid: pd.DataFrame, p: dict[str, Any], kind: str) -> pd.DataFrame:
    rows = []; bad = set(map(tuple, invalid[["process_id", "frequency"]].to_numpy())) if not invalid.empty else set()
    children = reg.loc[reg.layer.ne("static")].copy() if kind == "parent" else reg.loc[reg.rsp_twin.ne("") & reg.state_bundle.isin(["S2", "S6"])].copy()
    for frequency in FREQUENCIES:
        cost = int(p["portfolio"][f"primary_cost_bps_{frequency}"])
        for row in children.itertuples(index=False):
            parent = row.parent_process if kind == "parent" else row.rsp_twin
            if (row.process_id, frequency) in bad or (parent, frequency) in bad:
                rows.append({"process_id": row.process_id, "parent_process": parent, "frequency": frequency, "economic_mean_increment": np.nan, "economic_p": 1.0, "mean_rank_ic_increment": np.nan}); continue
            keys = ["signal_date"]
            c = period.loc[period.process_id.eq(row.process_id) & period.frequency.eq(frequency) & period.top_k.eq(20) & period.cost_bps.eq(cost), keys + ["relative_log_return"]].rename(columns={"relative_log_return":"c"})
            par = period.loc[period.process_id.eq(parent) & period.frequency.eq(frequency) & period.top_k.eq(20) & period.cost_bps.eq(cost), keys + ["relative_log_return"]].rename(columns={"relative_log_return":"p"})
            diff = c.merge(par, on=keys, validate="one_to_one"); increment = diff.c - diff.p
            ci = ic.loc[ic.process_id.eq(row.process_id) & ic.frequency.eq(frequency), ["signal_date","rank_ic"]].rename(columns={"rank_ic":"c"})
            pi = ic.loc[ic.process_id.eq(parent) & ic.frequency.eq(frequency), ["signal_date","rank_ic"]].rename(columns={"rank_ic":"p"})
            di = ci.merge(pi,on="signal_date",validate="one_to_one")
            rows.append({"process_id": row.process_id, "parent_process": parent, "frequency": frequency,
                         "economic_mean_increment": increment.mean(), "economic_p": _pvalue(increment, frequency, kind, p),
                         "mean_rank_ic_increment": (di.c-di.p).mean()})
    out = pd.DataFrame(rows); out["economic_q"] = out.groupby("frequency").economic_p.transform(_bh); return out


def _raw_anchor(period: pd.DataFrame, reg: pd.DataFrame, invalid: pd.DataFrame, p: dict[str, Any]) -> pd.DataFrame:
    rows=[];bad=set(map(tuple,invalid[["process_id","frequency"]].to_numpy())) if not invalid.empty else set()
    for frequency in FREQUENCIES:
        cost=int(p["portfolio"][f"primary_cost_bps_{frequency}"])
        parent=period.loc[period.process_id.eq("RAW_XS003_CORE10")&period.frequency.eq(frequency)&period.top_k.eq(20)&period.cost_bps.eq(cost),["signal_date","relative_log_return"]].rename(columns={"relative_log_return":"parent"})
        for pid in reg.process_id:
            if (pid,frequency) in bad:
                rows.append({"process_id":pid,"frequency":frequency,"economic_mean_increment":np.nan,"economic_p":1.0});continue
            child=period.loc[period.process_id.eq(pid)&period.frequency.eq(frequency)&period.top_k.eq(20)&period.cost_bps.eq(cost),["signal_date","relative_log_return"]].rename(columns={"relative_log_return":"child"})
            merged=child.merge(parent,on="signal_date",validate="one_to_one");inc=merged.child-merged.parent
            rows.append({"process_id":pid,"frequency":frequency,"economic_mean_increment":inc.mean(),"economic_p":_pvalue(inc,frequency,"raw_anchor",p)})
    out=pd.DataFrame(rows);out["economic_q"]=out.groupby("frequency").economic_p.transform(_bh);out["beats_raw_XS003"]=(out.economic_mean_increment>0)&(out.economic_q<=.10);return out


def _roles(period: pd.DataFrame, absolute: pd.DataFrame, paired: pd.DataFrame, raw: pd.DataFrame,
           reg: pd.DataFrame, invalid: pd.DataFrame, p: dict[str, Any]) -> pd.DataFrame:
    pair = paired.set_index(["process_id", "frequency"]); raw_idx=raw.set_index(["process_id","frequency"]); rows=[]; bad=set(map(tuple, invalid[["process_id","frequency"]].to_numpy())) if not invalid.empty else set()
    for a in absolute.itertuples(index=False):
        pid=a.process_id; f=a.frequency; ann=52 if f=="weekly" else 12; cost=int(p["portfolio"][f"primary_cost_bps_{f}"])
        cell=period.loc[period.process_id.eq(pid)&period.frequency.eq(f)]; main=cell.loc[cell.cost_bps.eq(cost)]
        width=main.groupby("top_k").relative_log_return.sum(); stress=cell.loc[cell.top_k.eq(20)&cell.cost_bps.eq(20)].relative_log_return.sum()
        one=main.loc[main.top_k.eq(20)].copy(); one["year"]=pd.to_datetime(one.execution_date).dt.year; yearly=one.groupby("year").relative_log_return.sum()
        stable=(20 in width and width[20]>0 and sum(width.get(k,-1)>0 for k in (10,20,50))>=2 and stress>0
                and len(yearly)>0 and (yearly>0).mean()>=.75 and yearly.abs().max()/yearly.abs().sum()<=.50)
        absolute_pass=((pid,f) not in bad and a.economic_mean*ann>=.02 and a.economic_q<=.10 and a.mean_rank_ic>0
                       and float(np.exp(one.relative_log_return.sum()))>1 and one.active_return.mean()/(one.active_return.std(ddof=1) or np.nan)>0 and stable)
        pair_pass=False
        if (pid,f) in pair.index:
            q=pair.loc[(pid,f)]; pair_pass=bool(absolute_pass and q.economic_mean_increment*ann>=.02 and q.economic_q<=.10 and q.mean_rank_ic_increment>=-.005)
        rows.append({"process_id":pid,"frequency":f,"valid":(pid,f) not in bad,"absolute_qualified":absolute_pass,
                     "qualified_incremental":pair_pass,"beats_raw_XS003":bool(raw_idx.loc[(pid,f),"beats_raw_XS003"]),
                     "primary_status":"qualified_incremental" if pair_pass else ("qualified_absolute_only" if absolute_pass else ("invalid" if (pid,f) in bad else "not_qualified"))})
    return pd.DataFrame(rows)


def _pvalue(values: pd.Series, frequency: str, family: str, p: dict[str, Any]) -> float:
    x=pd.Series(values,dtype=float).dropna().to_numpy();
    if len(x)==0:return 1.0
    block=int(p["inference"][f"block_dates_{frequency}"]); draws=int(p["inference"]["draws"])
    seed=int(hashlib.sha256(f"20260821|{frequency}|{family}".encode()).hexdigest()[:8],16); rng=np.random.default_rng(seed)
    starts=rng.integers(0,len(x),size=(draws,math.ceil(len(x)/block))); idx=(starts[:,:,None]+np.arange(block)[None,None,:])%len(x)
    means=x[idx.reshape(draws,-1)[:,:len(x)]].mean(axis=1); return float((1+(means<=0).sum())/(draws+1))


def _bh(values: pd.Series) -> pd.Series:
    p=np.asarray(values,float); order=np.argsort(p,kind="mergesort"); ranked=p[order]; m=len(p)
    q=np.minimum.accumulate((ranked*m/np.arange(1,m+1))[::-1])[::-1]; out=np.empty(m);out[order]=np.minimum(q,1);return pd.Series(out,index=values.index)


def _run_d(project: Path, runtime: Path, root: Path, p: dict[str, Any]) -> dict[str, Any]:
    summaries={}; manifests={}
    for batch in ("XA04A","XA04B","XA04C"):
        r=_root(runtime,batch); m=json.loads((r/"manifest.json").read_text(encoding="utf-8")); _verify_manifest(r,m)
        summaries[batch]=json.loads((r/"summary.json").read_text(encoding="utf-8")); manifests[batch]=_sha(r/"manifest.json")
    roles=pd.read_csv(_root(runtime,"XA04C")/"qualification_role_ledger.csv")
    registry=pd.read_csv(project/"config/experiments/xa04/process_registry.csv",skipinitialspace=True).fillna("")
    trees=set(registry.loc[registry.family.eq("lightgbm"),"process_id"])
    qualified=roles.loc[roles.qualified_incremental.astype(bool)&roles.process_id.isin(trees),["process_id","frequency","primary_status"]]
    _write_csv(root/"qualified_tree_candidate_ledger.csv",qualified)
    decision={"schema_version":"xa04.decision.v1","status":"completed_hard_stop","formal_eligible":False,
              "qualified_tree_cells":len(qualified),"best_loser_substitution":False,"p00_run":False,"xa05_started":False,
              "next_branch":"RAW_XS003_ONLY" if qualified.empty else "RAW_XS003_PLUS_ALL_QUALIFIED_TREES"}
    _write_json(root/"decision.json",decision)
    return {"batch":"XA04D","status":"completed_hard_stop","dependency_manifests":manifests,**decision}


def audit_xa04(project_root: str|Path,runtime_root:str|Path)->dict[str,Any]:
    project=Path(project_root).resolve();runtime=Path(runtime_root).resolve();_verify_lock(project)
    manifests={}
    for batch in RUN_IDS:
        root=_root(runtime,batch);m=json.loads((root/"manifest.json").read_text(encoding="utf-8"));_verify_manifest(root,m);manifests[batch]=_sha(root/"manifest.json")
    decision=json.loads((_root(runtime,"XA04D")/"decision.json").read_text(encoding="utf-8"))
    if decision["p00_run"] or decision["xa05_started"]:raise ValueError("unauthorized continuation")
    return {"status":"passed","manifests":manifests,"decision":decision}


def _program(project:Path)->dict[str,Any]:
    with (project/PROGRAM).open("rb") as h:return tomllib.load(h)
def _root(runtime:Path,batch:str)->Path:return runtime/"results"/"experiments"/"xa04"/batch/"runs"/RUN_IDS[batch]
def _xa03a(runtime:Path)->Path:return runtime/"results/experiments/xa03/XA03A/runs/xa03a-training-panel-20260821-v1"
def _market_root(project:Path,runtime:Path)->Path:
    from momentum_reversal.pipelines.cross_sectional_database import DatabaseLayout
    return DatabaseLayout.load(project_root=project,runtime_root=runtime).market_root
def _sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def _verify_lock(project:Path)->None:
    lock=json.loads((project/LOCK).read_text(encoding="utf-8"))
    for rel,item in lock["files"].items():
        path=project/rel
        if _sha(path)!=item["sha256"] or path.stat().st_size!=int(item["size_bytes"]):raise ValueError(f"XA04 prereg drift: {rel}")
def _require_clean(project:Path)->str:
    if subprocess.run(["git","status","--porcelain"],cwd=project,capture_output=True,text=True,check=True).stdout.strip():raise ValueError("XA04 requires clean Git")
    return subprocess.run(["git","rev-parse","HEAD"],cwd=project,capture_output=True,text=True,check=True).stdout.strip()
def _dependencies(runtime:Path,batch:str)->dict[str,str]:
    deps={}
    for prior in list(RUN_IDS)[:list(RUN_IDS).index(batch)]:
        path=_root(runtime,prior)/"manifest.json"
        if not path.exists():raise FileNotFoundError(path)
        m=json.loads(path.read_text(encoding="utf-8"));_verify_manifest(path.parent,m);deps[prior]=_sha(path)
    return deps
def _write_json(path:Path,payload:dict)->None:path.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n")
def _write_csv(path:Path,frame:pd.DataFrame)->None:frame.to_csv(path,index=False,lineterminator="\n")
def _write_parquet(path:Path,frame:pd.DataFrame)->None:frame.to_parquet(path,index=False)
def _write_manifest(project:Path,root:Path,batch:str,commit:str,deps:dict[str,str])->None:
    files={p.name:{"sha256":_sha(p),"size_bytes":p.stat().st_size} for p in sorted(root.iterdir()) if p.is_file() and p.name!="manifest.json"}
    _write_json(root/"manifest.json",{"schema_version":"xa04.runtime_manifest.v1","batch":batch,"run_id":RUN_IDS[batch],"git_commit":commit,"git_dirty":False,"prereg_lock_sha256":_sha(project/LOCK),"dependencies":deps,"python":platform.python_version(),"files":files})
def _verify_manifest(root:Path,m:dict)->None:
    actual={p.name for p in root.iterdir() if p.is_file() and p.name!="manifest.json"}
    if actual!=set(m["files"]):raise ValueError(f"manifest members mismatch: {root}")
    for name,item in m["files"].items():
        path=root/name
        if _sha(path)!=item["sha256"] or path.stat().st_size!=int(item["size_bytes"]):raise ValueError(f"manifest drift: {path}")
