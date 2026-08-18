"""Preregistered Round 6 Attack4 single-factor role experiments."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tomllib
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr
from sklearn.metrics import roc_auc_score

from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.round2_market import sha256_file
from momentum_reversal.pipelines.round4_diagnostics import build_drawdown_episodes
from momentum_reversal.pipelines.round4_experiments import _held_daily_target, _performance, replay_spy_cash


PROGRAM_ID = "attack4_single_factor_round6_v1"
MAX_TARGET_SIGNAL = pd.Timestamp("2021-11-26")
MAX_PROXY_SIGNAL = pd.Timestamp("2021-12-23")
NAV_END = pd.Timestamp("2021-12-31")
LOCKBOX_EXECUTION = pd.Timestamp("2022-01-03")
BOOTSTRAP_REPETITIONS = 2000
BOOTSTRAP_SEED = 20260818
MAJOR_EVENTS = ("E014", "E017", "E022", "E024", "E025", "E028")


@dataclass(frozen=True, slots=True)
class Round6BatchResult:
    output_dir: Path
    manifest_path: Path
    status: str


def run_r6a(*, project_root: str | Path, runtime_root: str | Path, run_id: str) -> Round6BatchResult:
    root, runtime, lock, program, parents = _load_inputs(project_root, runtime_root)
    _require_run_id(root, 0, run_id)
    output = _batch_root(runtime, "R6A_ATTACK4_TARGET", run_id)
    output.mkdir(parents=True, exist_ok=False)

    r3 = pd.read_parquet(parents["r3b"] / "targets_weekly.parquet")
    r2 = pd.read_parquet(parents["r2b"] / "targets_weekly.parquet")
    for frame in (r3, r2):
        for column in ("signal_session", "execution_session", "next_4w_execution"):
            frame[column] = pd.to_datetime(frame[column]).dt.normalize()
    w4 = r2[["week_id", "signal_session", "execution_session", "next_4w_execution", "t3_available", "fwd_worst_excess_4w"]]
    targets = r3.merge(
        w4,
        on=["week_id", "signal_session", "execution_session", "next_4w_execution"],
        how="left",
        validate="one_to_one",
    ).rename(columns={"next_4w_execution": "terminal_execution"})
    targets["target_available_at"] = pd.to_datetime(targets["target_available_at"]).dt.normalize()
    targets["target_available"] = targets["target_available"].astype(bool) & targets["t3_available"].astype(bool)
    targets["severe_w4"] = np.where(
        targets["target_available"], targets["fwd_worst_excess_4w"].le(np.log(0.95)), np.nan
    )
    outcome_columns = ["fwd_excess_logret_4w", "sustainable_attack_4w", "fwd_worst_excess_4w", "severe_w4"]
    forbidden = targets["execution_session"].ge(LOCKBOX_EXECUTION)
    targets.loc[forbidden, outcome_columns] = np.nan
    targets.loc[forbidden, "target_available"] = False
    valid = targets.loc[
        targets["target_available"]
        & targets["execution_session"].dt.year.between(2005, 2021)
        & targets["signal_session"].le(MAX_TARGET_SIGNAL)
    ].copy()
    if valid.empty or valid["signal_session"].max() != MAX_TARGET_SIGNAL:
        raise DataQualityError("Round6 A4 mature development boundary drifted")
    if not np.array_equal(
        valid["sustainable_attack_4w"].to_numpy(float),
        valid["fwd_excess_logret_4w"].gt(0).astype(float).to_numpy(),
    ):
        raise DataQualityError("Round6 A4/B4 identity failed")
    columns = [
        "week_id", "signal_session", "execution_session", "terminal_execution", "target_available_at",
        "withheld_lockbox", "target_available", "fwd_excess_logret_4w", "sustainable_attack_4w",
        "fwd_worst_excess_4w", "severe_w4",
    ]
    targets[columns].to_parquet(output / "targets_weekly.parquet", index=False, compression="zstd")
    summary = pd.DataFrame([{
        "development_weeks": len(valid),
        "first_signal": valid["signal_session"].min(),
        "last_signal": valid["signal_session"].max(),
        "mean_a4": float(valid["fwd_excess_logret_4w"].mean()),
        "median_a4": float(valid["fwd_excess_logret_4w"].median()),
        "positive_a4_fraction": float(valid["sustainable_attack_4w"].mean()),
        "mean_w4": float(valid["fwd_worst_excess_4w"].mean()),
        "severe_w4_fraction": float(valid["severe_w4"].mean()),
        "lag1_a4_autocorr": float(valid["fwd_excess_logret_4w"].autocorr(1)),
    }])
    summary.to_csv(output / "target_summary.csv", index=False, lineterminator="\n")
    yearly = valid.assign(execution_year=valid["execution_session"].dt.year).groupby("execution_year", as_index=False).agg(
        weeks=("week_id", "size"), mean_a4=("fwd_excess_logret_4w", "mean"),
        median_a4=("fwd_excess_logret_4w", "median"), positive_a4_fraction=("sustainable_attack_4w", "mean"),
        mean_w4=("fwd_worst_excess_4w", "mean"), severe_w4_fraction=("severe_w4", "mean"),
    )
    yearly.to_csv(output / "target_yearly.csv", index=False, lineterminator="\n")
    manifest = _write_manifest(output, root, lock, "R6A_ATTACK4_TARGET", run_id,
        counts={"target_rows": len(targets), "development_targets": len(valid)},
        parent_manifests={"r3b": sha256_file(parents["r3b"] / "manifest.json"), "r2b": sha256_file(parents["r2b"] / "manifest.json")})
    return Round6BatchResult(output, output / "manifest.json", manifest["status"])


def build_attack_scores(features: pd.DataFrame, registry: pd.DataFrame) -> pd.DataFrame:
    """Build all registered scores; delta lags are applied before missing filtering."""
    base = features[["week_id", "signal_session", "source_arm_id", "source_defense_score"]].copy()
    base["signal_session"] = pd.to_datetime(base["signal_session"]).dt.normalize()
    rows: list[pd.DataFrame] = []
    for spec in registry.itertuples(index=False):
        arm = base.loc[base["source_arm_id"].eq(spec.source_arm_id)].sort_values("signal_session", kind="mergesort").copy()
        if spec.transform_kind == "negate_level":
            arm["attack_score"] = -arm["source_defense_score"]
        elif spec.transform_kind == "calendar_delta":
            lag = int(spec.lag_scheduled_weeks)
            arm["attack_score"] = arm["source_defense_score"].shift(lag) - arm["source_defense_score"]
        else:
            raise DataQualityError(f"Unknown Round6 transform: {spec.transform_kind}")
        arm.insert(0, "attack_arm_id", spec.attack_arm_id)
        rows.append(arm[["attack_arm_id", "source_arm_id", "week_id", "signal_session", "source_defense_score", "attack_score"]])
    result = pd.concat(rows, ignore_index=True)
    if result.groupby("attack_arm_id")["week_id"].nunique().nunique() != 1:
        raise DataQualityError("Round6 score calendars are not identical")
    return result


def run_r6b(*, project_root: str | Path, runtime_root: str | Path, run_id: str) -> Round6BatchResult:
    root, runtime, lock, _, parents = _load_inputs(project_root, runtime_root)
    _require_run_id(root, 1, run_id)
    rids = _run_ids(root)
    r6a = _batch_root(runtime, "R6A_ATTACK4_TARGET", rids[0])
    _validate_bundle(r6a, "R6A_ATTACK4_TARGET")
    output = _batch_root(runtime, "R6B_ATTACK4_SINGLE_FACTOR", run_id)
    output.mkdir(parents=True, exist_ok=False)
    targets = pd.read_parquet(r6a / "targets_weekly.parquet")
    for column in ("signal_session", "execution_session", "terminal_execution"):
        targets[column] = pd.to_datetime(targets[column]).dt.normalize()
    raw = pd.read_parquet(parents["r4a"] / "feature_inputs_weekly.parquet").rename(
        columns={"arm_id": "source_arm_id", "defense_score": "source_defense_score"}
    )
    registry = _registry(root)
    scores = build_attack_scores(raw, registry)
    scores = scores.merge(targets[["week_id", "signal_session", "execution_session"]], on=["week_id", "signal_session"], how="left", validate="many_to_one")
    scores = scores.loc[scores["signal_session"].le(MAX_PROXY_SIGNAL)].copy()
    scores.to_parquet(output / "scores_weekly.parquet", index=False, compression="zstd")
    joined = scores.merge(targets, on=["week_id", "signal_session", "execution_session"], how="left", validate="many_to_one")
    valid_base = joined["target_available"] & joined["execution_session"].dt.year.between(2005, 2021)
    pivot = joined.loc[valid_base].pivot(index="week_id", columns="attack_arm_id", values="attack_score")
    common_weeks = set(pivot.dropna().index)
    summary_rows: list[dict[str, Any]] = []
    yearly_rows: list[dict[str, Any]] = []
    quintile_rows: list[pd.DataFrame] = []
    for arm_id in registry["attack_arm_id"]:
        arm = joined.loc[joined["attack_arm_id"].eq(arm_id) & valid_base & joined["attack_score"].notna()].copy()
        common = arm.loc[arm["week_id"].isin(common_weeks)].copy()
        native = _signal_metrics(arm)
        metrics = _signal_metrics(common)
        low4, p4 = _block_bootstrap_rho(common["attack_score"], common["fwd_excess_logret_4w"], 4)
        low8, p8 = _block_bootstrap_rho(common["attack_score"], common["fwd_excess_logret_4w"], 8)
        row = {"attack_arm_id": arm_id, "native_weeks": len(arm), "common_weeks": len(common),
               "native_start": arm["signal_session"].min(), "native_end": arm["signal_session"].max(),
               "native_spearman_a4": native["spearman_a4"], **metrics,
               "block4_95_lower_spearman": low4, "block4_one_sided_p": p4,
               "block8_spearman_a4": metrics["spearman_a4"], "block8_95_lower_spearman": low8, "block8_one_sided_p_diagnostic": p8}
        summary_rows.append(row)
        full_arm = joined.loc[joined["attack_arm_id"].eq(arm_id)].copy()
        causal = _causal_threshold_state(full_arm)
        causal_diag = common.merge(causal[["week_id", "threshold_q75", "signal_valid", "attack_high"]], on="week_id", validate="one_to_one")
        # Replace pooled diagnostics with the preregistered causal alert budget diagnostics.
        summary_rows[-1].update(_top_metrics(causal_diag, causal_diag["attack_high"] & causal_diag["signal_valid"]))
        for year, part in common.groupby(common["execution_session"].dt.year):
            yearly_rows.append({"attack_arm_id": arm_id, "execution_year": int(year), "weeks": len(part),
                                "spearman_a4": _rho(part["attack_score"], part["fwd_excess_logret_4w"]),
                                "mean_a4": float(part["fwd_excess_logret_4w"].mean())})
        q = common.copy()
        q["quintile"] = pd.qcut(q["attack_score"], 5, labels=False, duplicates="drop") + 1
        q = q.groupby("quintile", as_index=False).agg(weeks=("week_id", "size"), mean_a4=("fwd_excess_logret_4w", "mean"),
            median_a4=("fwd_excess_logret_4w", "median"), positive_rate=("sustainable_attack_4w", "mean"),
            median_w4=("fwd_worst_excess_4w", "median"), severe_w4_rate=("severe_w4", "mean"))
        q.insert(0, "attack_arm_id", arm_id)
        quintile_rows.append(q)
    summary = pd.DataFrame(summary_rows)
    summary["bh_q_value"] = _bh_adjust(summary["block4_one_sided_p"].to_numpy(float))
    yearly = pd.DataFrame(yearly_rows)
    positive_years = yearly.groupby("attack_arm_id")["spearman_a4"].apply(lambda x: float((x.dropna() > 0).mean())).rename("positive_rankic_year_fraction")
    summary = summary.merge(positive_years, on="attack_arm_id", validate="one_to_one")
    summary.to_csv(output / "signal_summary.csv", index=False, lineterminator="\n")
    yearly.to_csv(output / "yearly_signal.csv", index=False, lineterminator="\n")
    pd.concat(quintile_rows, ignore_index=True).to_csv(output / "quintiles.csv", index=False, lineterminator="\n")
    manifest = _write_manifest(output, root, lock, "R6B_ATTACK4_SINGLE_FACTOR", run_id,
        counts={"registered_arms": len(registry), "score_rows": len(scores), "common_weeks": len(common_weeks)},
        parent_manifests={"r6a": sha256_file(r6a / "manifest.json"), "r4a": sha256_file(parents["r4a"] / "manifest.json")})
    return Round6BatchResult(output, output / "manifest.json", manifest["status"])


def run_r6c(*, project_root: str | Path, runtime_root: str | Path, run_id: str) -> Round6BatchResult:
    root, runtime, lock, _, parents = _load_inputs(project_root, runtime_root)
    _require_run_id(root, 2, run_id)
    rids = _run_ids(root)
    r6a = _batch_root(runtime, "R6A_ATTACK4_TARGET", rids[0])
    r6b = _batch_root(runtime, "R6B_ATTACK4_SINGLE_FACTOR", rids[1])
    _validate_bundle(r6a, "R6A_ATTACK4_TARGET"); _validate_bundle(r6b, "R6B_ATTACK4_SINGLE_FACTOR")
    output = _batch_root(runtime, "R6C_ATTACK4_ROLE_PROXY", run_id)
    output.mkdir(parents=True, exist_ok=False)
    scores = pd.read_parquet(r6b / "scores_weekly.parquet")
    targets = pd.read_parquet(r6a / "targets_weekly.parquet")
    for frame in (scores, targets):
        for column in set(("signal_session", "execution_session")) & set(frame.columns):
            frame[column] = pd.to_datetime(frame[column]).dt.normalize()
    registry = _registry(root)
    state_parts = []
    for arm_id in registry["attack_arm_id"]:
        arm = scores.loc[scores["attack_arm_id"].eq(arm_id)].copy()
        state_parts.append(_causal_threshold_state(arm))
    states = pd.concat(state_parts, ignore_index=True)
    first_common = states.loc[states["signal_valid"]].groupby("attack_arm_id")["execution_session"].min().max()
    if pd.isna(first_common):
        raise DataQualityError("Round6 has no common legal proxy start")
    states["common_proxy_period"] = states["execution_session"].ge(first_common)
    states.to_parquet(output / "states_weekly.parquet", index=False, compression="zstd")
    market = pd.read_parquet(parents["r2a"] / "curated/market_daily.parquet")
    rf = pd.read_parquet(parents["r2a"] / "curated/risk_free_daily.parquet")
    nav_parts: list[pd.DataFrame] = []
    economic_rows: list[dict[str, Any]] = []
    yearly_parts: list[pd.DataFrame] = []
    for arm_id in registry["attack_arm_id"]:
        arm = states.loc[states["attack_arm_id"].eq(arm_id) & states["common_proxy_period"]].copy()
        schedule = arm[["execution_session", "target_spy_weight"]]
        daily_target = _held_daily_target(schedule, market, start=first_common, end=NAV_END)
        static_weight = float(daily_target.mean())
        static_schedule = schedule.assign(target_spy_weight=static_weight)
        always_schedule = schedule.assign(target_spy_weight=1.0)
        main_dynamic = main_static = None
        for cost in (0, 5, 10, 20):
            dynamic = replay_spy_cash(market, rf, schedule, start=first_common, end=NAV_END, cost_bps=cost)
            static = replay_spy_cash(market, rf, static_schedule, start=first_common, end=NAV_END, cost_bps=cost)
            always = replay_spy_cash(market, rf, always_schedule, start=first_common, end=NAV_END, cost_bps=cost)
            for kind, frame in (("dynamic", dynamic), ("matched_static", static), ("always_spy", always)):
                part = frame.copy(); part.insert(0, "attack_arm_id", arm_id); part.insert(1, "path_type", kind); part.insert(2, "cost_bps", cost)
                nav_parts.append(part)
            dm, sm = _performance_extended(dynamic, always), _performance_extended(static, always)
            active = dynamic.set_index("date")["nav"] / static.set_index("date")["nav"]
            economic_rows.append({"attack_arm_id": arm_id, "cost_bps": cost, "common_start": first_common,
                "mean_daily_target_weight": static_weight, "dynamic_cagr": dm["cagr"], "dynamic_sharpe": dm["sharpe"],
                "dynamic_mdd": dm["mdd"], "dynamic_ann_vol": dm["ann_vol"], "dynamic_beta": dm["beta"],
                "dynamic_turnover": float(dynamic["turnover"].sum()), "dynamic_cost": float(dynamic["cost_amount"].sum()),
                "static_cagr": sm["cagr"], "static_mdd": sm["mdd"], "active_terminal_wealth": float(active.iloc[-1] - 1)})
            if cost == 10:
                main_dynamic, main_static = dynamic, static
        assert main_dynamic is not None and main_static is not None
        active = main_dynamic.set_index("date")["nav"] / main_static.set_index("date")["nav"]
        active_log = np.log(active).diff().fillna(np.log(active.iloc[0]))
        annual = pd.DataFrame({"date": active.index, "active_log_return": active_log.to_numpy()})
        annual["execution_year"] = pd.DatetimeIndex(annual["date"]).year
        annual = annual.groupby("execution_year", as_index=False)["active_log_return"].sum()
        annual.insert(0, "attack_arm_id", arm_id); annual["positive"] = annual["active_log_return"] > 0
        yearly_parts.append(annual)
    pd.concat(nav_parts, ignore_index=True).to_parquet(output / "nav_daily.parquet", index=False, compression="zstd")
    economic = pd.DataFrame(economic_rows)
    economic.to_csv(output / "economic_summary.csv", index=False, lineterminator="\n")
    yearly = pd.concat(yearly_parts, ignore_index=True); yearly.to_csv(output / "yearly_active.csv", index=False, lineterminator="\n")

    conditional, conditional_summary = _conditional_audit(states, targets, registry, scores)
    conditional.to_csv(output / "conditional_cells.csv", index=False, lineterminator="\n")
    conditional_summary.to_csv(output / "conditional_summary.csv", index=False, lineterminator="\n")
    mechanism = _mechanism_attribution(states, targets)
    mechanism.to_csv(output / "mechanism_attribution.csv", index=False, lineterminator="\n")
    manifest = _write_manifest(output, root, lock, "R6C_ATTACK4_ROLE_PROXY", run_id,
        counts={"registered_arms": len(registry), "state_rows": len(states), "nav_rows": sum(len(x) for x in nav_parts),
                "conditional_arms": int(registry["conditional_eligible"].sum())},
        parent_manifests={"r6a": sha256_file(r6a / "manifest.json"), "r6b": sha256_file(r6b / "manifest.json")})
    return Round6BatchResult(output, output / "manifest.json", manifest["status"])


def run_r6d(*, project_root: str | Path, runtime_root: str | Path, run_id: str) -> Round6BatchResult:
    root, runtime, lock, _, parents = _load_inputs(project_root, runtime_root)
    _require_run_id(root, 3, run_id)
    rids = _run_ids(root)
    roots = {"r6a": _batch_root(runtime, "R6A_ATTACK4_TARGET", rids[0]),
             "r6b": _batch_root(runtime, "R6B_ATTACK4_SINGLE_FACTOR", rids[1]),
             "r6c": _batch_root(runtime, "R6C_ATTACK4_ROLE_PROXY", rids[2])}
    for key, batch in (("r6a", "R6A_ATTACK4_TARGET"), ("r6b", "R6B_ATTACK4_SINGLE_FACTOR"), ("r6c", "R6C_ATTACK4_ROLE_PROXY")):
        _validate_bundle(roots[key], batch)
    output = _batch_root(runtime, "R6D_ATTACK4_ROBUSTNESS", run_id)
    output.mkdir(parents=True, exist_ok=False)
    summary = pd.read_csv(roots["r6b"] / "signal_summary.csv")
    scores = pd.read_parquet(roots["r6b"] / "scores_weekly.parquet")
    targets = pd.read_parquet(roots["r6a"] / "targets_weekly.parquet")
    for frame in (scores, targets):
        for column in set(("signal_session", "execution_session", "terminal_execution")) & set(frame.columns):
            frame[column] = pd.to_datetime(frame[column]).dt.normalize()
    pivot = scores.pivot(index="week_id", columns="attack_arm_id", values="attack_score")
    common = set(pivot.dropna().index) & set(targets.loc[targets["target_available"] & targets["execution_session"].dt.year.between(2005, 2021), "week_id"])
    market = pd.read_parquet(parents["r2a"] / "curated/market_daily.parquet")
    episodes = build_drawdown_episodes(market)
    episodes["peak_date"] = pd.to_datetime(episodes["peak_date"]).dt.normalize(); episodes["recovery_date"] = pd.to_datetime(episodes["recovery_date"]).dt.normalize()
    episodes = episodes.loc[episodes["episode_id"].isin(MAJOR_EVENTS)]
    if set(episodes["episode_id"]) != set(MAJOR_EVENTS):
        raise DataQualityError("Round6 fixed major event registry unavailable")
    leave_rows = []
    for arm_id in summary["attack_arm_id"]:
        arm = scores.loc[scores["attack_arm_id"].eq(arm_id) & scores["week_id"].isin(common)].merge(targets, on=["week_id", "signal_session", "execution_session"], validate="one_to_one")
        for event in episodes.itertuples(index=False):
            recovery = min(pd.Timestamp(event.recovery_date), NAV_END)
            remove = arm["execution_session"].le(recovery) & arm["terminal_execution"].ge(pd.Timestamp(event.peak_date))
            kept = arm.loc[~remove]
            leave_rows.append({"attack_arm_id": arm_id, "episode_id": event.episode_id, "removed_weeks": int(remove.sum()),
                               "remaining_weeks": len(kept), "spearman_a4_without_event": _rho(kept["attack_score"], kept["fwd_excess_logret_4w"])})
    leaveout = pd.DataFrame(leave_rows)
    min_leave = leaveout.groupby("attack_arm_id", as_index=False)["spearman_a4_without_event"].min().rename(columns={"spearman_a4_without_event": "minimum_leaveout_spearman"})
    econ = pd.read_csv(roots["r6c"] / "economic_summary.csv")
    yearly = pd.read_csv(roots["r6c"] / "yearly_active.csv")
    cond = pd.read_csv(roots["r6c"] / "conditional_summary.csv")
    registry = _registry(root)
    e10 = econ.loc[econ["cost_bps"].eq(10)].add_suffix("_10").rename(columns={"attack_arm_id_10": "attack_arm_id"})
    e20 = econ.loc[econ["cost_bps"].eq(20), ["attack_arm_id", "active_terminal_wealth"]].rename(columns={"active_terminal_wealth": "active_terminal_wealth_20"})
    py = yearly.groupby("attack_arm_id", as_index=False)["positive"].mean().rename(columns={"positive": "positive_active_year_fraction"})
    final = summary.merge(registry[["attack_arm_id", "family", "display_name", "preregistered_role", "direct_eligible", "conditional_eligible", "context_only"]], on="attack_arm_id", validate="one_to_one")
    final = final.merge(min_leave, on="attack_arm_id", validate="one_to_one").merge(e10, on="attack_arm_id", validate="one_to_one").merge(e20, on="attack_arm_id", validate="one_to_one").merge(py, on="attack_arm_id", validate="one_to_one").merge(cond, on="attack_arm_id", how="left", validate="one_to_one")
    final["binary_guardrail_pass"] = final["auc_b4"].gt(0.5) & final["top_positive_rate"].gt(final["rest_positive_rate"])
    final["worst_path_harm_veto"] = final["top_median_w4"].lt(final["rest_median_w4"]) | final["top_severe_w4_rate"].gt(final["rest_severe_w4_rate"])
    final["reference_attack_positive"] = (final["spearman_a4"].gt(0) & final["positive_upside_capture"].gt(.25)
        & final["top_mean_a4"].gt(final["rest_mean_a4"]) & final["b4_lift"].gt(1) & final["positive_rankic_year_fraction"].ge(.60))
    final["robust_direct_attack"] = (final["direct_eligible"] & final["reference_attack_positive"]
        & final["block4_95_lower_spearman"].gt(0) & final["bh_q_value"].le(.10) & final["positive_upside_capture"].ge(.35)
        & final["top_mean_a4"].gt(0) & final["b4_lift"].ge(1.10) & final["binary_guardrail_pass"]
        & ~final["worst_path_harm_veto"] & final["native_spearman_a4"].gt(0) & final["minimum_leaveout_spearman"].gt(0)
        & final["block8_spearman_a4"].ge(0))
    final["economic_reference"] = (~final["context_only"] & final["active_terminal_wealth_10"].gt(0)
        & final["active_terminal_wealth_20"].gt(0) & final["positive_active_year_fraction"].ge(.60)
        & final["dynamic_mdd_10"].ge(final["static_mdd_10"]) & ~final["worst_path_harm_veto"])
    final["conditional_role_pass"] = (final["conditional_eligible"] & final["conditional_cells_valid"].fillna(False)
        & final["conditional_block95_lower"].fillna(-np.inf).gt(0) & final["conditional_bh_q"].fillna(1).le(.10)
        & ~final["conditional_w4_harm_veto"].fillna(True))
    final["model_input_eligible"] = (final["robust_direct_attack"] | final["economic_reference"] | final["conditional_role_pass"]) & ~final["context_only"]
    final["failed_routes"] = final.apply(_failed_routes, axis=1)
    final.to_csv(output / "final_assessment.csv", index=False, lineterminator="\n")
    leaveout.to_csv(output / "leave_one_event_out.csv", index=False, lineterminator="\n")
    econ[["attack_arm_id", "cost_bps", "active_terminal_wealth", "dynamic_mdd"]].to_csv(output / "cost_robustness.csv", index=False, lineterminator="\n")
    qualification = final[["attack_arm_id", "robust_direct_attack", "economic_reference", "conditional_role_pass", "model_input_eligible", "failed_routes"]]
    qualification.to_csv(output / "qualification_ledger.csv", index=False, lineterminator="\n")
    assessment = "completed_attack_role_candidates_development_only" if final["model_input_eligible"].any() else "completed_no_attack_role_candidate"
    manifest = _write_manifest(output, root, lock, "R6D_ATTACK4_ROBUSTNESS", run_id,
        counts={"registered_arms": len(final), "robust_direct_attack": int(final["robust_direct_attack"].sum()),
                "economic_reference": int(final["economic_reference"].sum()), "conditional_role_pass": int(final["conditional_role_pass"].sum()),
                "model_input_eligible": int(final["model_input_eligible"].sum()), "major_events": len(episodes)},
        parent_manifests={key: sha256_file(path / "manifest.json") for key, path in roots.items()}, assessment=assessment)
    return Round6BatchResult(output, output / "manifest.json", manifest["status"])


def _signal_metrics(frame: pd.DataFrame) -> dict[str, float]:
    score, a4 = frame["attack_score"], frame["fwd_excess_logret_4w"]
    return {"spearman_a4": _rho(score, a4)}


def _top_metrics(frame: pd.DataFrame, top: pd.Series) -> dict[str, float]:
    valid = frame.loc[frame.get("signal_valid", pd.Series(True, index=frame.index)).astype(bool)].copy()
    top = top.reindex(valid.index).fillna(False).astype(bool)
    rest = ~top
    a4 = valid["fwd_excess_logret_4w"].astype(float); b4 = valid["sustainable_attack_4w"].astype(float)
    w4 = valid["fwd_worst_excess_4w"].astype(float); severe = valid["severe_w4"].astype(float)
    positive = a4.clip(lower=0)
    auc = float(roc_auc_score(b4, valid["attack_score"])) if b4.nunique() == 2 else np.nan
    return {
        "causal_diagnostic_weeks": len(valid), "top_fraction": float(top.mean()),
        "positive_upside_capture": float(positive[top].sum() / positive.sum()) if positive.sum() > 0 else np.nan,
        "top_mean_a4": float(a4[top].mean()), "rest_mean_a4": float(a4[rest].mean()),
        "top_median_a4": float(a4[top].median()), "rest_median_a4": float(a4[rest].median()),
        "top_positive_rate": float(b4[top].mean()), "rest_positive_rate": float(b4[rest].mean()),
        "b4_lift": float(b4[top].mean() / b4.mean()) if b4.mean() > 0 else np.nan, "auc_b4": auc,
        "top_median_w4": float(w4[top].median()), "rest_median_w4": float(w4[rest].median()),
        "top_severe_w4_rate": float(severe[top].mean()), "rest_severe_w4_rate": float(severe[rest].mean()),
    }


def _causal_threshold_state(arm: pd.DataFrame) -> pd.DataFrame:
    arm = arm.sort_values("execution_session", kind="mergesort").copy()
    arm = arm.loc[arm["signal_session"].le(MAX_PROXY_SIGNAL)].copy()
    arm["execution_year"] = arm["execution_session"].dt.year
    arm["threshold_q75"] = np.nan; arm["signal_valid"] = False; arm["attack_high"] = False; arm["target_spy_weight"] = np.nan
    state, opened = 1.0, False
    for year in range(2005, 2022):
        test = arm["execution_year"].eq(year)
        history = arm.loc[arm["execution_session"].lt(pd.Timestamp(year, 1, 1)), "attack_score"].dropna().astype(float)
        valid_year = len(history) >= 260
        threshold = float(history.quantile(.75, interpolation="linear")) if valid_year else np.nan
        for index in arm.index[test]:
            score = arm.at[index, "attack_score"]
            valid = valid_year and np.isfinite(score)
            if valid:
                high = bool(float(score) > threshold); state = 1.0 if high else .5; opened = True
                arm.at[index, "signal_valid"] = True; arm.at[index, "attack_high"] = high
            arm.at[index, "threshold_q75"] = threshold
            arm.at[index, "target_spy_weight"] = state if opened else 1.0
    return arm.loc[arm["execution_year"].between(2005, 2021), ["attack_arm_id", "source_arm_id", "week_id", "signal_session", "execution_session", "attack_score", "threshold_q75", "signal_valid", "attack_high", "target_spy_weight"]]


def _conditional_audit(states: pd.DataFrame, targets: pd.DataFrame, registry: pd.DataFrame, scores: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    # risk-low iff defense <= q75. Since attack=-defense, compute the causal defense q75 directly.
    rsp_full = scores.loc[scores["attack_arm_id"].eq("A4__RSP_SPY63_LVL")].sort_values("execution_session", kind="mergesort").copy()
    rsp_full["defense_score"] = -rsp_full["attack_score"]
    rsp_full["risk_low"] = False
    for year in range(2005, 2022):
        idx = rsp_full["execution_session"].dt.year.eq(year)
        hist = rsp_full.loc[rsp_full["execution_session"].lt(pd.Timestamp(year, 1, 1)), "defense_score"].dropna()
        if len(hist) >= 260:
            threshold = float(hist.quantile(.75, interpolation="linear"))
            rsp_full.loc[idx, "risk_low"] = rsp_full.loc[idx, "defense_score"].le(threshold)
    risk = rsp_full.loc[rsp_full["execution_session"].dt.year.between(2005, 2021), ["week_id", "risk_low"]]
    cell_rows: list[dict[str, Any]] = []; summary_rows: list[dict[str, Any]] = []
    cond_ids = set(registry.loc[registry["conditional_eligible"], "attack_arm_id"])
    p_values: list[float] = []; positions: list[int] = []
    for arm_id in registry["attack_arm_id"]:
        arm = states.loc[states["attack_arm_id"].eq(arm_id)].merge(risk, on="week_id", validate="one_to_one").merge(targets, on=["week_id", "signal_session", "execution_session"], validate="one_to_one")
        diag = arm.loc[arm["signal_valid"] & arm["risk_low"] & arm["target_available"]].copy()
        total_common = len(arm.loc[arm["signal_valid"] & arm["target_available"]])
        for high, part in diag.groupby("attack_high"):
            cell_rows.append({"attack_arm_id": arm_id, "risk_low": True, "attack_high": bool(high), "weeks": len(part),
                "fraction_of_common": len(part) / total_common if total_common else np.nan, "mean_a4": float(part["fwd_excess_logret_4w"].mean()),
                "median_a4": float(part["fwd_excess_logret_4w"].median()), "positive_rate": float(part["sustainable_attack_4w"].mean()),
                "median_w4": float(part["fwd_worst_excess_4w"].median()), "severe_w4_rate": float(part["severe_w4"].mean())})
        high = diag.loc[diag["attack_high"]]; low = diag.loc[~diag["attack_high"]]
        contrast = float(high["fwd_excess_logret_4w"].mean() - low["fwd_excess_logret_4w"].mean()) if len(high) and len(low) else np.nan
        lower, p = _block_bootstrap_contrast(diag)
        cells_valid = len(high) >= 44 and len(low) >= 44 and len(high) / max(total_common, 1) >= .05 and len(low) / max(total_common, 1) >= .05
        harm = bool(high["fwd_worst_excess_4w"].median() < low["fwd_worst_excess_4w"].median() or high["severe_w4"].mean() > low["severe_w4"].mean()) if len(high) and len(low) else True
        summary_rows.append({"attack_arm_id": arm_id, "conditional_registered": arm_id in cond_ids, "conditional_common_weeks": total_common,
            "conditional_high_weeks": len(high), "conditional_low_weeks": len(low), "conditional_cells_valid": cells_valid,
            "conditional_a4_contrast": contrast, "conditional_block95_lower": lower, "conditional_one_sided_p": p,
            "conditional_w4_harm_veto": harm})
        if arm_id in cond_ids:
            positions.append(len(summary_rows) - 1); p_values.append(p)
    summaries = pd.DataFrame(summary_rows); summaries["conditional_bh_q"] = np.nan
    if p_values:
        summaries.loc[positions, "conditional_bh_q"] = _bh_adjust(np.asarray(p_values, float))
    return pd.DataFrame(cell_rows), summaries


def _block_bootstrap_contrast(frame: pd.DataFrame) -> tuple[float, float]:
    if len(frame) < 8:
        return np.nan, np.nan
    x = frame["attack_high"].to_numpy(bool); y = frame["fwd_excess_logret_4w"].to_numpy(float)
    rng = np.random.default_rng(BOOTSTRAP_SEED); starts = np.arange(len(frame) - 4 + 1); estimates = []
    for _ in range(BOOTSTRAP_REPETITIONS):
        ids: list[int] = []
        while len(ids) < len(frame):
            start = int(rng.choice(starts)); ids.extend(range(start, start + 4))
        ids = np.asarray(ids[:len(frame)]); hi, lo = y[ids][x[ids]], y[ids][~x[ids]]
        estimates.append(float(hi.mean() - lo.mean()) if len(hi) and len(lo) else np.nan)
    values = np.asarray(estimates, float)
    return float(np.nanquantile(values, .05)), float(np.nanmean(values <= 0))


def _mechanism_attribution(states: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for arm_id, arm in states.groupby("attack_arm_id"):
        joined = arm.merge(targets, on=["week_id", "signal_session", "execution_session"], validate="one_to_one")
        diag = joined.loc[joined["signal_valid"] & joined["target_available"]]
        under = 1 - diag["target_spy_weight"].astype(float); a4 = diag["fwd_excess_logret_4w"].astype(float)
        defense = float((under * (-a4).clip(lower=0)).sum()); missed = float((under * a4.clip(lower=0)).sum())
        rows.append({"attack_arm_id": arm_id, "weeks": len(diag), "defense_benefit_proxy": defense,
                     "missed_upside_proxy": missed, "net_timing_proxy": defense - missed})
    return pd.DataFrame(rows)


def _performance_extended(nav: pd.DataFrame, spy: pd.DataFrame) -> dict[str, float]:
    result = _performance(nav); returns = nav["daily_return"].to_numpy(float); benchmark = spy["daily_return"].to_numpy(float)
    variance = np.var(benchmark, ddof=1)
    result["ann_vol"] = float(np.std(returns, ddof=1) * np.sqrt(252))
    result["beta"] = float(np.cov(returns, benchmark, ddof=1)[0, 1] / variance) if variance > 0 else np.nan
    return result


def _rho(x: Iterable[float], y: Iterable[float]) -> float:
    result = spearmanr(np.asarray(x, float), np.asarray(y, float), nan_policy="omit")
    return float(result.statistic) if np.isfinite(result.statistic) else np.nan


def _block_bootstrap_rho(score: pd.Series, target: pd.Series, block: int) -> tuple[float, float]:
    x, y = score.to_numpy(float), target.to_numpy(float)
    if len(x) < block or not np.isfinite(x).all() or not np.isfinite(y).all():
        return np.nan, np.nan
    rng = np.random.default_rng(BOOTSTRAP_SEED); starts = np.arange(len(x) - block + 1); values = np.empty(BOOTSTRAP_REPETITIONS)
    for i in range(BOOTSTRAP_REPETITIONS):
        ids: list[int] = []
        while len(ids) < len(x):
            start = int(rng.choice(starts)); ids.extend(range(start, start + block))
        pick = np.asarray(ids[:len(x)]); values[i] = _rho(x[pick], y[pick])
    return float(np.nanquantile(values, .05)), float(np.nanmean(values <= 0))


def _bh_adjust(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, float); order = np.argsort(p); ranked = p[order]
    adjusted = np.minimum.accumulate((ranked * len(p) / np.arange(1, len(p) + 1))[::-1])[::-1]
    result = np.empty_like(adjusted); result[order] = np.minimum(adjusted, 1); return result


def _failed_routes(row: pd.Series) -> str:
    failed = []
    if not row["robust_direct_attack"]: failed.append("robust_direct")
    if not row["economic_reference"]: failed.append("economic")
    if not row["conditional_role_pass"]: failed.append("conditional")
    return "|".join(failed)


def _registry(root: Path) -> pd.DataFrame:
    registry = pd.read_csv(root / "config/experiments/round6/factor_registry.csv")
    for column in ("direct_eligible", "conditional_eligible", "context_only"):
        registry[column] = registry[column].astype(bool)
    if len(registry) != 20 or registry["attack_arm_id"].nunique() != 20:
        raise DataQualityError("Round6 registry cardinality drifted")
    return registry


def _load_inputs(project_root: str | Path, runtime_root: str | Path) -> tuple[Path, Path, dict[str, Any], dict[str, Any], dict[str, Path]]:
    root, runtime = Path(project_root).resolve(), Path(runtime_root).resolve()
    lock_path = root / "config/experiments/round6/PREREG_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    for relative, expected in lock["files"].items():
        if sha256_file(root / relative) != expected:
            raise DataQualityError(f"Round6 prereg hash mismatch: {relative}")
    program = tomllib.loads((root / "config/experiments/round6/program.toml").read_text(encoding="utf-8"))
    auth = program["authorization"]
    if auth["models"] or auth["final_state_machine"] or auth["lockbox"] or auth["mom255_transfer"]:
        raise DataQualityError("Round6 forbidden authorization opened")
    parents = {
        "r2a": runtime / "data/round2/staging/R2A_DATA/r2a-long-free-20260816-v1",
        "r2b": runtime / "results/experiments/round2/R2B_SIGNAL_DIAGNOSTICS/runs" / program["parent"]["r2b_run_id"],
        "r3b": runtime / "results/experiments/round3/R3B_RECOVERY_PERSISTENCE/runs" / program["parent"]["r3b_run_id"],
        "r4a": runtime / "data/round4/staging/R4A_FREE_FACTOR_DATA" / program["parent"]["r4a_run_id"],
    }
    checks = (("r2b", "r2b_manifest_sha256"), ("r3b", "r3b_manifest_sha256"), ("r4a", "r4a_manifest_sha256"))
    for key, field in checks:
        if sha256_file(parents[key] / "manifest.json") != program["parent"][field]:
            raise DataQualityError(f"Round6 {key} parent manifest drifted")
    if sha256_file(parents["r3b"] / "targets_weekly.parquet") != program["parent"]["r3b_targets_weekly_sha256"]:
        raise DataQualityError("Round6 R3B target bytes drifted")
    if sha256_file(parents["r2b"] / "targets_weekly.parquet") != program["parent"]["r2b_targets_weekly_sha256"]:
        raise DataQualityError("Round6 R2B target bytes drifted")
    return root, runtime, lock, program, parents


def _run_ids(root: Path) -> list[str]:
    values = tomllib.loads((root / "config/experiments/round6/program.toml").read_text(encoding="utf-8"))["run_ids"]
    if len(values) != 4: raise DataQualityError("Round6 run-id count drifted")
    return list(values)


def _require_run_id(root: Path, index: int, run_id: str) -> None:
    if _run_ids(root)[index] != run_id: raise DataQualityError("Round6 run-id differs from preregistration")


def _batch_root(runtime: Path, batch: str, run_id: str) -> Path:
    return runtime / "results/experiments/round6" / batch / "runs" / run_id


def _validate_bundle(path: Path, expected_batch: str) -> None:
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest["program_id"] != PROGRAM_ID or manifest["batch_id"] != expected_batch or manifest["lockbox_read"] is not False:
        raise DataQualityError(f"Round6 parent identity/firewall failed: {path}")
    for record in manifest["files"]:
        member = path / record["path"]
        if member.stat().st_size != record["size_bytes"] or sha256_file(member) != record["sha256"]:
            raise DataQualityError(f"Round6 immutable bundle mismatch: {member}")


def _write_manifest(output: Path, root: Path, lock: dict[str, Any], batch_id: str, run_id: str, *, counts: dict[str, int], parent_manifests: dict[str, str], assessment: str | None = None) -> dict[str, Any]:
    files = [{"path": p.relative_to(output).as_posix(), "size_bytes": p.stat().st_size, "sha256": sha256_file(p)}
             for p in sorted((x for x in output.rglob("*") if x.is_file()), key=lambda x: x.relative_to(output).as_posix())]
    manifest = {"schema_version": 1, "program_id": PROGRAM_ID, "batch_id": batch_id, "run_id": run_id,
        "status": "completed_development", "assessment": assessment or "completed_development", "formal_eligible": False,
        "maximum_target_signal": str(MAX_TARGET_SIGNAL.date()), "maximum_proxy_signal": str(MAX_PROXY_SIGNAL.date()),
        "maximum_nav_date": str(NAV_END.date()), "lockbox_read": False, "lockbox_predictions_generated": False,
        "models_run": False, "final_state_machine_run": False, "mom255_transfer_run": False,
        "factor_additions_run": False, "window_search_run": False, "position_search_run": False,
        "prereg_lock_sha256": sha256_file(root / "config/experiments/round6/PREREG_LOCK.json"),
        "parent_manifests": parent_manifests, "counts": counts, "files": files}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
