"""Preregistered Round 5 MAE13 single-factor experiments."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.round2_market import sha256_file
from momentum_reversal.pipelines.round4_diagnostics import build_drawdown_episodes
from momentum_reversal.pipelines.round4_experiments import (
    _held_daily_target,
    _performance,
    replay_spy_cash,
)


PROGRAM_ID = "defense_mae13_single_factor_round5_v1"
LOCKBOX_START = pd.Timestamp("2022-01-03")
MAX_TARGET_SIGNAL = pd.Timestamp("2021-09-24")
MAX_POLICY_SIGNAL = pd.Timestamp("2021-12-23")
NAV_END = pd.Timestamp("2021-12-31")


@dataclass(frozen=True, slots=True)
class Round5BatchResult:
    output_dir: Path
    manifest_path: Path
    status: str


def build_mae13_targets(
    market_daily: pd.DataFrame,
    risk_free_daily: pd.DataFrame,
    decision_calendar: pd.DataFrame,
) -> pd.DataFrame:
    """Build entry-anchored 13-week SPY-vs-cash adverse excursion targets."""

    market = market_daily.copy()
    market["session_date"] = pd.to_datetime(market["session_date"]).dt.normalize()
    market = market.sort_values("session_date", kind="mergesort").set_index("session_date")
    rf = risk_free_daily.copy()
    rf["session_date"] = pd.to_datetime(rf["session_date"]).dt.normalize()
    rf = rf.sort_values("session_date", kind="mergesort").set_index("session_date")
    if not market.index.is_unique or not rf.index.is_unique:
        raise DataQualityError("Round5 market/RF sessions must be unique")
    sessions = market.index
    rf = rf.reindex(sessions)
    if rf["rf_log"].isna().any():
        raise DataQualityError("Round5 RF does not cover market sessions")
    position = {date: index for index, date in enumerate(sessions)}
    opens = pd.to_numeric(market["tr_open"], errors="coerce").to_numpy(float)
    closes = pd.to_numeric(market["tr_close"], errors="coerce").to_numpy(float)
    rf_log = pd.to_numeric(rf["rf_log"], errors="coerce").to_numpy(float)
    cumulative_rf = np.r_[0.0, np.cumsum(rf_log)]

    calendar = decision_calendar.copy().sort_values("execution_session", kind="mergesort")
    for column in ("signal_session", "execution_session"):
        calendar[column] = pd.to_datetime(calendar[column]).dt.normalize()
    calendar["terminal_execution"] = calendar["execution_session"].shift(-13)
    rows: list[dict[str, Any]] = []
    for row in calendar.itertuples(index=False):
        signal = pd.Timestamp(row.signal_session)
        if signal > MAX_POLICY_SIGNAL:
            continue
        start = pd.Timestamp(row.execution_session)
        terminal = pd.Timestamp(row.terminal_execution) if pd.notna(row.terminal_execution) else pd.NaT
        available = (
            signal <= MAX_TARGET_SIGNAL
            and pd.notna(terminal)
            and start in position
            and terminal in position
            and terminal < LOCKBOX_START
        )
        raw_log = raw_simple = y5 = y10 = np.nan
        worst_date = pd.NaT
        if available:
            i0, i1 = position[start], position[terminal]
            if i1 <= i0:
                raise DataQualityError("Round5 terminal precedes target start")
            close_log = np.log(closes[i0:i1] / opens[i0]) - (
                cumulative_rf[i0 + 1 : i1 + 1] - cumulative_rf[i0]
            )
            terminal_log = np.log(opens[i1] / opens[i0]) - (
                cumulative_rf[i1] - cumulative_rf[i0]
            )
            path = np.r_[close_log, terminal_log]
            if not np.isfinite(path).all():
                raise DataQualityError("Round5 target path contains non-finite values")
            location = int(np.argmin(path))
            minimum = min(0.0, float(path[location]))
            raw_log = -minimum
            raw_simple = 1.0 - float(np.exp(-raw_log))
            y5 = max(raw_simple - 0.05, 0.0)
            y10 = max(raw_simple - 0.10, 0.0)
            worst_date = sessions[i0 + location] if location < (i1 - i0) else terminal
        rows.append(
            {
                "week_id": row.week_id,
                "signal_session": signal,
                "execution_session": start,
                "terminal_execution": terminal,
                "target_available_at": terminal,
                "target_available": bool(available),
                "worst_path_date": worst_date,
                "raw_mae13_log": raw_log,
                "raw_mae13": raw_simple,
                "excess_mae13_deadzone5": y5,
                "excess_mae13_deadzone10": y10,
            }
        )
    result = pd.DataFrame(rows)
    forbidden = result["execution_session"].ge(LOCKBOX_START) & result["target_available"]
    if forbidden.any():
        raise DataQualityError("Round5 materialized lockbox targets")
    available = result.loc[result["target_available"]]
    calendar_reaches_boundary = calendar["signal_session"].max() >= MAX_TARGET_SIGNAL
    if calendar_reaches_boundary and available["signal_session"].max() != MAX_TARGET_SIGNAL:
        raise DataQualityError("Round5 maximum mature target signal drifted")
    return result


def run_r5a(
    *, project_root: str | Path, runtime_root: str | Path, run_id: str
) -> Round5BatchResult:
    root, runtime, prereg, _, parent = _load_inputs(project_root, runtime_root)
    output = runtime / "results/experiments/round5/R5A_MAE13_TARGET/runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    market, rf, calendar = _load_parent_tables(parent)
    targets = build_mae13_targets(market, rf, calendar)
    targets.to_parquet(output / "targets_weekly.parquet", index=False, compression="zstd")
    valid = targets.loc[targets["target_available"]].copy()
    summary = pd.DataFrame(
        [
            {
                "available_weeks": len(valid),
                "first_signal": valid["signal_session"].min(),
                "last_signal": valid["signal_session"].max(),
                "zero_y5_fraction": float(valid["excess_mae13_deadzone5"].eq(0).mean()),
                "raw_mae_ge_2_fraction": float(valid["raw_mae13"].ge(0.02).mean()),
                "raw_mae_ge_5_fraction": float(valid["raw_mae13"].ge(0.05).mean()),
                "raw_mae_ge_10_fraction": float(valid["raw_mae13"].ge(0.10).mean()),
                "mean_raw_mae": float(valid["raw_mae13"].mean()),
                "median_raw_mae": float(valid["raw_mae13"].median()),
                "mean_y5": float(valid["excess_mae13_deadzone5"].mean()),
                "lag1_raw_mae_autocorr": float(valid["raw_mae13"].autocorr(1)),
            }
        ]
    )
    summary.to_csv(output / "target_summary.csv", index=False, lineterminator="\n")
    yearly = valid.assign(execution_year=valid["execution_session"].dt.year).groupby(
        "execution_year", as_index=False
    ).agg(
        weeks=("week_id", "size"),
        mean_raw_mae=("raw_mae13", "mean"),
        mean_y5=("excess_mae13_deadzone5", "mean"),
        mae5_rate=("raw_mae13", lambda x: float((x >= 0.05).mean())),
        mae10_rate=("raw_mae13", lambda x: float((x >= 0.10).mean())),
    )
    yearly.to_csv(output / "target_yearly.csv", index=False, lineterminator="\n")
    manifest = _write_manifest(
        output,
        root,
        prereg,
        "R5A_MAE13_TARGET",
        run_id,
        counts={"target_rows": len(targets), "available_targets": len(valid)},
    )
    return Round5BatchResult(output, output / "manifest.json", manifest["status"])


def run_r5b(
    *, project_root: str | Path, runtime_root: str | Path, run_id: str
) -> Round5BatchResult:
    root, runtime, prereg, r4a, _ = _load_inputs(project_root, runtime_root)
    run_ids = _run_ids(root)
    target_root = _batch_root(runtime, "R5A_MAE13_TARGET", run_ids["r5a"])
    _validate_bundle(target_root, "R5A_MAE13_TARGET")
    output = runtime / "results/experiments/round5/R5B_MAE13_SINGLE_FACTOR/runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    targets = pd.read_parquet(target_root / "targets_weekly.parquet")
    targets["signal_session"] = pd.to_datetime(targets["signal_session"]).dt.normalize()
    features = pd.read_parquet(r4a / "feature_inputs_weekly.parquet")
    features["signal_session"] = pd.to_datetime(features["signal_session"]).dt.normalize()
    registry = pd.read_csv(root / "config/experiments/round5/factor_registry.csv")
    summary_rows: list[dict[str, Any]] = []
    quintile_parts: list[pd.DataFrame] = []
    yearly_parts: list[pd.DataFrame] = []
    for arm_id in registry["arm_id"]:
        arm = features.loc[features["arm_id"].eq(arm_id), ["week_id", "signal_session", "defense_score"]]
        joined = arm.merge(targets, on=["week_id", "signal_session"], how="inner", validate="one_to_one")
        execution_year = pd.to_datetime(joined["execution_session"]).dt.year
        diag = joined.loc[
            joined["target_available"]
            & joined["defense_score"].notna()
            & execution_year.between(2005, 2021)
        ].copy()
        score = pd.to_numeric(diag["defense_score"], errors="coerce")
        y5 = pd.to_numeric(diag["excess_mae13_deadzone5"], errors="coerce")
        rho = _rho(score, y5)
        ci_low, p_value = _block_bootstrap_rho(score, y5, 13, 2000, 20260817)
        threshold = float(score.quantile(0.75, interpolation="linear"))
        top = score > threshold
        capture = float(y5[top].sum() / y5.sum()) if y5.sum() > 0 else np.nan
        top_mean = float(y5[top].mean()) if top.any() else np.nan
        rest_mean = float(y5[~top].mean()) if (~top).any() else np.nan
        ratio = top_mean / rest_mean if rest_mean > 0 else np.nan
        event5 = diag["raw_mae13"].ge(0.05)
        event10 = diag["raw_mae13"].ge(0.10)
        summary_rows.append(
            {
                "arm_id": arm_id,
                "native_target_weeks": len(diag),
                "native_start": diag["signal_session"].min(),
                "native_end": diag["signal_session"].max(),
                "spearman_y5": rho,
                "block95_lower_spearman": ci_low,
                "one_sided_block_p": p_value,
                "pooled_q75": threshold,
                "top_score_fraction": float(top.mean()),
                "y5_loss_capture": capture,
                "top_mean_y5": top_mean,
                "rest_mean_y5": rest_mean,
                "top_rest_mean_ratio": ratio,
                "mae5_precision": float(event5[top].mean()) if top.any() else np.nan,
                "mae5_recall": float(top[event5].mean()) if event5.any() else np.nan,
                "mae5_lift": float(event5[top].mean() / event5.mean()) if top.any() and event5.mean() > 0 else np.nan,
                "mae10_precision": float(event10[top].mean()) if top.any() else np.nan,
                "mae10_recall": float(top[event10].mean()) if event10.any() else np.nan,
                "mae10_lift": float(event10[top].mean() / event10.mean()) if top.any() and event10.mean() > 0 else np.nan,
                "false_alert_y5_zero": float(y5[top].eq(0).mean()) if top.any() else np.nan,
            }
        )
        diag["quintile"] = pd.qcut(score, 5, labels=False, duplicates="drop") + 1
        q = diag.groupby("quintile", as_index=False).agg(
            weeks=("week_id", "size"),
            mean_y5=("excess_mae13_deadzone5", "mean"),
            median_y5=("excess_mae13_deadzone5", "median"),
            mean_raw_mae=("raw_mae13", "mean"),
            mae10_rate=("raw_mae13", lambda x: float((x >= 0.10).mean())),
        )
        q.insert(0, "arm_id", arm_id)
        quintile_parts.append(q)
        diag["execution_year"] = pd.to_datetime(diag["execution_session"]).dt.year
        for year, part in diag.groupby("execution_year"):
            yearly_parts.append(
                pd.DataFrame(
                    [{
                        "arm_id": arm_id,
                        "execution_year": int(year),
                        "weeks": len(part),
                        "spearman_y5": _rho(part["defense_score"], part["excess_mae13_deadzone5"]),
                        "mean_y5": float(part["excess_mae13_deadzone5"].mean()),
                    }]
                )
            )
    summary = pd.DataFrame(summary_rows)
    summary["bh_q_value"] = _bh_adjust(summary["one_sided_block_p"].to_numpy(float))
    summary.to_csv(output / "signal_summary.csv", index=False, lineterminator="\n")
    pd.concat(quintile_parts, ignore_index=True).to_csv(output / "quintiles.csv", index=False, lineterminator="\n")
    pd.concat(yearly_parts, ignore_index=True).to_csv(output / "yearly_signal.csv", index=False, lineterminator="\n")
    manifest = _write_manifest(
        output,
        root,
        prereg,
        "R5B_MAE13_SINGLE_FACTOR",
        run_id,
        counts={"eligible_arms": len(summary), "summary_rows": len(summary)},
        parent_manifests={"r5a": sha256_file(target_root / "manifest.json")},
    )
    return Round5BatchResult(output, output / "manifest.json", manifest["status"])


def run_r5c(
    *, project_root: str | Path, runtime_root: str | Path, run_id: str
) -> Round5BatchResult:
    root, runtime, prereg, r4a, parent = _load_inputs(project_root, runtime_root)
    output = runtime / "results/experiments/round5/R5C_SPY_CASH_PROXY/runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    features = pd.read_parquet(r4a / "feature_inputs_weekly.parquet")
    features["signal_session"] = pd.to_datetime(features["signal_session"]).dt.normalize()
    registry = pd.read_csv(root / "config/experiments/round5/factor_registry.csv")
    market, rf, calendar = _load_parent_tables(parent)
    signals, thresholds = _build_policy_signals(features, calendar, registry["arm_id"].tolist())
    signals.to_parquet(output / "signals_weekly.parquet", index=False, compression="zstd")
    thresholds.to_parquet(output / "annual_thresholds.parquet", index=False, compression="zstd")
    nav_parts: list[pd.DataFrame] = []
    economic_rows: list[dict[str, Any]] = []
    yearly_parts: list[pd.DataFrame] = []
    for arm_id in registry["arm_id"]:
        arm_signal = signals.loc[signals["arm_id"].eq(arm_id)].copy()
        first = arm_signal.loc[arm_signal["signal_valid"], "execution_session"].min()
        if pd.isna(first):
            continue
        valid_targets = arm_signal.loc[
            arm_signal["signal_valid"] & arm_signal["execution_session"].ge(first),
            ["execution_session", "target_spy_weight"],
        ]
        daily_target = _held_daily_target(valid_targets, market, start=first, end=NAV_END)
        static_weight = float(daily_target.mean())
        static_schedule = valid_targets.assign(target_spy_weight=static_weight)
        always_schedule = valid_targets.assign(target_spy_weight=1.0)
        for cost_bps in (0, 5, 10, 20):
            dynamic = replay_spy_cash(market, rf, valid_targets, start=first, end=NAV_END, cost_bps=cost_bps)
            static = replay_spy_cash(market, rf, static_schedule, start=first, end=NAV_END, cost_bps=cost_bps)
            always = replay_spy_cash(market, rf, always_schedule, start=first, end=NAV_END, cost_bps=cost_bps)
            for kind, frame in (("dynamic", dynamic), ("matched_static", static), ("always_spy", always)):
                part = frame.copy()
                part.insert(0, "arm_id", arm_id)
                part.insert(1, "path_type", kind)
                part.insert(2, "cost_bps", cost_bps)
                nav_parts.append(part)
            dyn_metrics, stat_metrics, always_metrics = _performance(dynamic), _performance(static), _performance(always)
            active = dynamic.set_index("date")["nav"] / static.set_index("date")["nav"]
            economic_rows.append(
                {
                    "arm_id": arm_id,
                    "cost_bps": cost_bps,
                    "native_start": first,
                    "mean_daily_target_weight": static_weight,
                    "dynamic_cagr": dyn_metrics["cagr"],
                    "dynamic_sharpe": dyn_metrics["sharpe"],
                    "dynamic_mdd": dyn_metrics["mdd"],
                    "dynamic_turnover": float(dynamic["turnover"].sum()),
                    "static_cagr": stat_metrics["cagr"],
                    "static_mdd": stat_metrics["mdd"],
                    "always_spy_mdd": always_metrics["mdd"],
                    "active_terminal_wealth": float(active.iloc[-1] - 1.0),
                }
            )
            if cost_bps == 10:
                active_log = np.log(active).diff().fillna(np.log(active.iloc[0]))
                yearly = pd.DataFrame({"date": active.index, "active_log_return": active_log.to_numpy()})
                yearly["execution_year"] = pd.DatetimeIndex(yearly["date"]).year
                yearly = yearly.groupby("execution_year", as_index=False)["active_log_return"].sum()
                yearly.insert(0, "arm_id", arm_id)
                yearly["positive"] = yearly["active_log_return"] > 0
                yearly_parts.append(yearly)
    nav = pd.concat(nav_parts, ignore_index=True)
    nav.to_parquet(output / "nav_daily.parquet", index=False, compression="zstd")
    pd.DataFrame(economic_rows).to_csv(output / "economic_summary.csv", index=False, lineterminator="\n")
    pd.concat(yearly_parts, ignore_index=True).to_csv(output / "yearly_active.csv", index=False, lineterminator="\n")
    manifest = _write_manifest(
        output,
        root,
        prereg,
        "R5C_SPY_CASH_PROXY",
        run_id,
        counts={"eligible_arms": len(registry), "nav_rows": len(nav), "signal_rows": len(signals)},
    )
    return Round5BatchResult(output, output / "manifest.json", manifest["status"])


def run_r5d(
    *, project_root: str | Path, runtime_root: str | Path, run_id: str
) -> Round5BatchResult:
    root, runtime, prereg, r4a, parent = _load_inputs(project_root, runtime_root)
    run_ids = _run_ids(root)
    roots = {
        "r5a": _batch_root(runtime, "R5A_MAE13_TARGET", run_ids["r5a"]),
        "r5b": _batch_root(runtime, "R5B_MAE13_SINGLE_FACTOR", run_ids["r5b"]),
        "r5c": _batch_root(runtime, "R5C_SPY_CASH_PROXY", run_ids["r5c"]),
    }
    for key, batch in (("r5a", "R5A_MAE13_TARGET"), ("r5b", "R5B_MAE13_SINGLE_FACTOR"), ("r5c", "R5C_SPY_CASH_PROXY")):
        _validate_bundle(roots[key], batch)
    output = runtime / "results/experiments/round5/R5D_MAE13_ROBUSTNESS/runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    targets = pd.read_parquet(roots["r5a"] / "targets_weekly.parquet")
    targets["signal_session"] = pd.to_datetime(targets["signal_session"]).dt.normalize()
    features = pd.read_parquet(r4a / "feature_inputs_weekly.parquet")
    features["signal_session"] = pd.to_datetime(features["signal_session"]).dt.normalize()
    registry = pd.read_csv(root / "config/experiments/round5/factor_registry.csv")
    pivot = features.loc[features["arm_id"].isin(registry["arm_id"])].pivot(
        index="signal_session", columns="arm_id", values="defense_score"
    )
    target_execution_year = pd.to_datetime(targets["execution_session"]).dt.year
    common_dates = pivot.dropna().index.intersection(
        targets.loc[
            targets["target_available"] & target_execution_year.between(2005, 2021),
            "signal_session",
        ]
    )
    common_rows: list[dict[str, Any]] = []
    leaveout_rows: list[dict[str, Any]] = []
    market, _, _ = _load_parent_tables(parent)
    episodes = build_drawdown_episodes(market)
    major = episodes.loc[episodes["severity_10"]].copy()
    major["peak_date"] = pd.to_datetime(major["peak_date"]).dt.normalize()
    major["recovery_date"] = pd.to_datetime(major["recovery_date"]).dt.normalize()
    major = major.loc[
        major["recovery_date"].ge(pd.Timestamp("2005-01-01"))
        & major["peak_date"].le(NAV_END)
    ].copy()
    signal_summary = pd.read_csv(roots["r5b"] / "signal_summary.csv")
    economic = pd.read_csv(roots["r5c"] / "economic_summary.csv")
    yearly = pd.read_csv(roots["r5c"] / "yearly_active.csv")
    for arm_id in registry["arm_id"]:
        arm = features.loc[features["arm_id"].eq(arm_id), ["signal_session", "defense_score"]]
        joined = arm.merge(targets, on="signal_session", how="inner", validate="one_to_one")
        diag = joined.loc[joined["signal_session"].isin(common_dates)].copy()
        common_rho = _rho(diag["defense_score"], diag["excess_mae13_deadzone5"])
        threshold = float(diag["defense_score"].quantile(0.75, interpolation="linear"))
        top = diag["defense_score"] > threshold
        y5 = diag["excess_mae13_deadzone5"]
        capture = float(y5[top].sum() / y5.sum()) if y5.sum() > 0 else np.nan
        ratio = float(y5[top].mean() / y5[~top].mean()) if y5[~top].mean() > 0 else np.nan
        common_rows.append(
            {
                "arm_id": arm_id,
                "common_weeks": len(diag),
                "common_spearman_y5": common_rho,
                "common_y5_capture": capture,
                "common_top_rest_ratio": ratio,
            }
        )
        for episode in major.itertuples(index=False):
            recovery = pd.Timestamp(episode.recovery_date) if pd.notna(episode.recovery_date) else NAV_END
            overlaps = diag["execution_session"].le(recovery) & diag["terminal_execution"].ge(pd.Timestamp(episode.peak_date))
            kept = diag.loc[~overlaps]
            leaveout_rows.append(
                {
                    "arm_id": arm_id,
                    "episode_id": episode.episode_id,
                    "removed_weeks": int(overlaps.sum()),
                    "remaining_weeks": len(kept),
                    "spearman_y5_without_event": _rho(kept["defense_score"], kept["excess_mae13_deadzone5"]),
                }
            )
    common = pd.DataFrame(common_rows)
    leaveout = pd.DataFrame(leaveout_rows)
    min_leaveout = leaveout.groupby("arm_id", as_index=False)["spearman_y5_without_event"].min().rename(
        columns={"spearman_y5_without_event": "minimum_leaveout_spearman"}
    )
    econ10 = economic.loc[economic["cost_bps"].eq(10)].copy()
    positive_years = yearly.groupby("arm_id", as_index=False)["positive"].mean().rename(
        columns={"positive": "positive_year_fraction"}
    )
    final = signal_summary.merge(common, on="arm_id", validate="one_to_one")
    final = final.merge(min_leaveout, on="arm_id", validate="one_to_one")
    final = final.merge(econ10, on="arm_id", validate="one_to_one")
    final = final.merge(positive_years, on="arm_id", validate="one_to_one")
    final["reference_positive"] = (
        final["spearman_y5"].gt(0)
        & final["y5_loss_capture"].gt(0.25)
        & final["top_mean_y5"].gt(final["rest_mean_y5"])
        & final["active_terminal_wealth"].gt(0)
        & final["positive_year_fraction"].ge(0.60)
    )
    final["robust_reference_positive"] = (
        final["reference_positive"]
        & final["block95_lower_spearman"].gt(0)
        & final["bh_q_value"].le(0.10)
        & final["y5_loss_capture"].ge(0.35)
        & final["top_rest_mean_ratio"].ge(1.25)
        & final["common_spearman_y5"].gt(0)
        & final["minimum_leaveout_spearman"].gt(0)
        & final["dynamic_mdd"].gt(final["always_spy_mdd"])
    )
    final.to_csv(output / "final_assessment.csv", index=False, lineterminator="\n")
    common.to_csv(output / "common_intersection.csv", index=False, lineterminator="\n")
    leaveout.to_csv(output / "leave_one_event_out.csv", index=False, lineterminator="\n")
    cost = economic[["arm_id", "cost_bps", "active_terminal_wealth", "dynamic_mdd"]]
    cost.to_csv(output / "cost_robustness.csv", index=False, lineterminator="\n")
    manifest = _write_manifest(
        output,
        root,
        prereg,
        "R5D_MAE13_ROBUSTNESS",
        run_id,
        counts={
            "eligible_arms": len(final),
            "reference_positive": int(final["reference_positive"].sum()),
            "robust_reference_positive": int(final["robust_reference_positive"].sum()),
            "common_weeks": len(common_dates),
            "major_events": len(major),
        },
        parent_manifests={key: sha256_file(path / "manifest.json") for key, path in roots.items()},
    )
    return Round5BatchResult(output, output / "manifest.json", manifest["status"])


def _build_policy_signals(
    features: pd.DataFrame, calendar: pd.DataFrame, arms: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    cal = calendar[["week_id", "signal_session", "execution_session"]].copy()
    for column in ("signal_session", "execution_session"):
        cal[column] = pd.to_datetime(cal[column]).dt.normalize()
    signal_parts: list[pd.DataFrame] = []
    threshold_rows: list[dict[str, Any]] = []
    for arm_id in arms:
        arm = features.loc[features["arm_id"].eq(arm_id)].merge(
            cal, on=["week_id", "signal_session"], how="left", validate="one_to_one"
        ).sort_values("execution_session", kind="mergesort")
        arm = arm.loc[arm["signal_session"] <= MAX_POLICY_SIGNAL].copy()
        arm["execution_year"] = arm["execution_session"].dt.year
        arm["target_spy_weight"] = np.nan
        arm["threshold_q75"] = np.nan
        arm["signal_valid"] = False
        state, opened = 1.0, False
        for year in range(2005, 2022):
            test = arm["execution_year"].eq(year)
            history = pd.to_numeric(
                arm.loc[arm["execution_session"] < pd.Timestamp(year=year, month=1, day=1), "defense_score"],
                errors="coerce",
            ).dropna()
            valid = len(history) >= 260
            threshold = float(history.quantile(0.75, interpolation="linear")) if valid else np.nan
            threshold_rows.append(
                {"arm_id": arm_id, "execution_year": year, "history_weeks": len(history), "threshold_q75": threshold, "year_valid": valid}
            )
            for index in arm.index[test]:
                score = float(arm.at[index, "defense_score"])
                score_valid = valid and np.isfinite(score)
                if score_valid:
                    state = 0.5 if score > threshold else 1.0
                    opened = True
                    arm.at[index, "signal_valid"] = True
                arm.at[index, "target_spy_weight"] = state if opened else 1.0
                arm.at[index, "threshold_q75"] = threshold
        arm = arm.loc[arm["execution_year"].between(2005, 2021)].copy()
        arm["alert"] = arm["target_spy_weight"].eq(0.5) & arm["signal_valid"]
        signal_parts.append(
            arm[["arm_id", "week_id", "signal_session", "execution_session", "defense_score", "threshold_q75", "signal_valid", "alert", "target_spy_weight"]]
        )
    return pd.concat(signal_parts, ignore_index=True), pd.DataFrame(threshold_rows)


def _rho(x: pd.Series, y: pd.Series) -> float:
    result = spearmanr(pd.to_numeric(x, errors="coerce"), pd.to_numeric(y, errors="coerce"))
    return float(result.statistic) if np.isfinite(result.statistic) else np.nan


def _block_bootstrap_rho(
    score: pd.Series, target: pd.Series, block: int, repetitions: int, seed: int
) -> tuple[float, float]:
    x, y = np.asarray(score, float), np.asarray(target, float)
    if len(x) < block or not np.isfinite(x).all() or not np.isfinite(y).all():
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    starts = np.arange(len(x) - block + 1)
    estimates = np.empty(repetitions)
    for index in range(repetitions):
        chosen: list[int] = []
        while len(chosen) < len(x):
            start = int(rng.choice(starts))
            chosen.extend(range(start, start + block))
        ids = np.asarray(chosen[: len(x)])
        estimates[index] = _rho(pd.Series(x[ids]), pd.Series(y[ids]))
    return float(np.nanquantile(estimates, 0.05)), float(np.nanmean(estimates <= 0))


def _bh_adjust(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, float)
    order = np.argsort(p)
    ranked = p[order]
    adjusted = np.minimum.accumulate((ranked * len(p) / np.arange(1, len(p) + 1))[::-1])[::-1]
    result = np.empty_like(adjusted)
    result[order] = np.minimum(adjusted, 1.0)
    return result


def _load_parent_tables(parent: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return (
        pd.read_parquet(parent / "curated/market_daily.parquet"),
        pd.read_parquet(parent / "curated/risk_free_daily.parquet"),
        pd.read_parquet(parent / "curated/decision_calendar.parquet"),
    )


def _load_inputs(
    project_root: str | Path, runtime_root: str | Path
) -> tuple[Path, Path, dict[str, Any], Path, Path]:
    root, runtime = Path(project_root).resolve(), Path(runtime_root).resolve()
    lock_path = root / "config/experiments/round5/PREREG_LOCK.json"
    prereg = json.loads(lock_path.read_text(encoding="utf-8"))
    for relative, expected in prereg["files"].items():
        if sha256_file(root / relative) != expected:
            raise DataQualityError(f"Round5 prereg hash mismatch: {relative}")
    if prereg["authorization"]["lockbox"] is not False:
        raise DataQualityError("Round5 lockbox firewall is not closed")
    r4a = runtime / "data/round4/staging/R4A_FREE_FACTOR_DATA" / prereg["r4a"]["run_id"]
    if sha256_file(r4a / "manifest.json") != prereg["r4a"]["manifest_sha256"]:
        raise DataQualityError("Round5 R4A anchor mismatch")
    parent = runtime / "data/round2/staging/R2A_DATA/r2a-long-free-20260816-v1"
    return root, runtime, prereg, r4a, parent


def _batch_root(runtime: Path, batch: str, run_id: str) -> Path:
    return runtime / f"results/experiments/round5/{batch}/runs" / run_id


def _run_ids(root: Path) -> dict[str, str]:
    program = tomllib.loads(
        (root / "config/experiments/round5/program.toml").read_text(encoding="utf-8")
    )
    values = program["run_ids"]
    if len(values) != 4:
        raise DataQualityError("Round5 frozen run-id count drifted")
    return {"r5a": values[0], "r5b": values[1], "r5c": values[2], "r5d": values[3]}


def _validate_bundle(path: Path, expected_batch: str) -> None:
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest["program_id"] != PROGRAM_ID or manifest["batch_id"] != expected_batch:
        raise DataQualityError(f"Round5 parent bundle identity mismatch: {path}")
    if manifest["lockbox_read"] is not False:
        raise DataQualityError("Round5 parent bundle read lockbox")
    for record in manifest["files"]:
        file_path = path / record["path"]
        if file_path.stat().st_size != record["size_bytes"] or sha256_file(file_path) != record["sha256"]:
            raise DataQualityError(f"Round5 parent bundle file mismatch: {file_path}")


def _write_manifest(
    output: Path,
    root: Path,
    prereg: dict[str, Any],
    batch_id: str,
    run_id: str,
    *,
    counts: dict[str, int],
    parent_manifests: dict[str, str] | None = None,
) -> dict[str, Any]:
    files = []
    for path in sorted((p for p in output.rglob("*") if p.is_file()), key=lambda p: p.relative_to(output).as_posix()):
        files.append({"path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "batch_id": batch_id,
        "run_id": run_id,
        "status": "completed_development",
        "formal_eligible": False,
        "maximum_target_signal": str(MAX_TARGET_SIGNAL.date()),
        "maximum_policy_signal": str(MAX_POLICY_SIGNAL.date()),
        "lockbox_read": False,
        "models_run": False,
        "factor_additions_run": False,
        "window_search_run": False,
        "position_search_run": False,
        "prereg_lock_sha256": sha256_file(root / "config/experiments/round5/PREREG_LOCK.json"),
        "r4a_manifest_sha256": prereg["r4a"]["manifest_sha256"],
        "parent_manifests": parent_manifests or {},
        "counts": counts,
        "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip(),
        "files": files,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return manifest
