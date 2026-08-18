"""Preregistered Round 9 P00-to-mom255 long-only transfer experiments."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tomllib
from typing import Any

import numpy as np
import pandas as pd

from momentum_reversal.backtest import BaselineBacktester
from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.round2_market import sha256_file
from momentum_reversal.pipelines.run_context import load_experiment_data, prepare_experiment_run


PROGRAM_ID = "p00_mom255_long_only_transfer_round9_v1"


@dataclass(frozen=True, slots=True)
class Round9BatchResult:
    output_dir: Path
    manifest_path: Path
    status: str


def simulate_union_event_book(
    *,
    engine: BaselineBacktester,
    base_targets: pd.DataFrame,
    overlay_schedule: pd.Series,
    risk_free_daily: pd.Series,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cost_bps: float,
    path_type: str,
    static_allocation: float | None = None,
    full_audit: bool = True,
) -> dict[str, pd.DataFrame]:
    """Replay frozen TopK targets on a base/weekly union event calendar.

    Base events replace the relative stock book with the frozen G00 target.
    Overlay-only events preserve the current relative composition and change
    only total stock exposure.  Corporate actions occur before either event and
    the final target vector is charged exactly once.
    """
    if path_type not in {"naked", "p00_overlay", "matched_static"}:
        raise ValueError(f"unsupported path_type: {path_type}")
    start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    base = base_targets.copy()
    base["execution_date"] = pd.to_datetime(base["execution_date"]).dt.normalize()
    base = base[(base.execution_date >= start) & (base.execution_date <= end)]
    required = {"execution_date", "sid", "target_weight"}
    if not required.issubset(base.columns) or base.empty:
        raise DataQualityError("Round9 base target ledger is missing or empty")
    if base.duplicated(["execution_date", "sid"]).any():
        raise DataQualityError("Round9 base target ledger has duplicate date/SID rows")
    base_map = {
        pd.Timestamp(date): group.set_index("sid").target_weight.astype(float).sort_index()
        for date, group in base.groupby("execution_date", sort=True)
    }
    overlay = pd.to_numeric(overlay_schedule.copy(), errors="coerce")
    overlay.index = pd.DatetimeIndex(pd.to_datetime(overlay.index)).normalize()
    overlay = overlay[~overlay.index.duplicated(keep="last")].sort_index()
    overlay = overlay[(overlay.index >= start) & (overlay.index <= end)]
    if path_type != "naked" and overlay.empty:
        raise DataQualityError("Round9 overlay schedule is empty")
    if path_type == "matched_static":
        if static_allocation is None or not 0 <= static_allocation <= 1:
            raise ValueError("matched_static requires a finite allocation in [0,1]")
    event_dates = pd.DatetimeIndex(sorted(base_map))
    if path_type != "naked":
        event_dates = event_dates.union(overlay.index).sort_values()
    sessions = engine.sessions[(engine.sessions >= start) & (engine.sessions <= end)]
    if sessions.empty or event_dates.difference(sessions).size:
        raise DataQualityError("Round9 event/session calendar drifted")
    rf = pd.to_numeric(risk_free_daily, errors="coerce").reindex(sessions)
    if rf.isna().any() or not np.isfinite(rf).all():
        raise DataQualityError("Round9 risk-free calendar is incomplete")
    held_overlay = overlay.reindex(sessions).ffill()
    if path_type != "naked" and held_overlay.isna().any():
        raise DataQualityError("Round9 has no P00 state at the sample start")

    shares = pd.Series(dtype=float)
    cash = float(engine.initial_capital)
    previous_close_nav = float(engine.initial_capital)
    cost_rate = float(cost_bps) / 10_000.0
    nav_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    action_rows: list[dict[str, Any]] = []

    for date in sessions:
        date = pd.Timestamp(date)
        shares, cash, actions = engine._apply_corporate_actions(date=date, shares=shares, cash=cash)
        for row in actions:
            action_rows.append({"date": date, **row})
        if date in event_dates:
            is_base = date in base_map
            is_overlay = date in overlay.index
            if path_type == "naked":
                allocation = 1.0
            elif path_type == "matched_static":
                allocation = float(static_allocation)
            else:
                allocation = float(held_overlay.loc[date])
            existing = pd.Index(shares.index, dtype="object")
            existing_open = (
                engine._price_vector(date, existing, "tr_open", execution=False)
                if len(existing)
                else pd.Series(dtype=float)
            )
            old_values = shares * existing_open if len(existing) else pd.Series(dtype=float)
            pretrade_nav = float(old_values.sum() + cash)
            if not np.isfinite(pretrade_nav) or pretrade_nav <= 0:
                raise DataQualityError(f"Round9 non-positive pretrade NAV on {date.date()}")
            if is_base:
                requested = base_map[date] * allocation
                selected_open = engine._price_vector(
                    date, pd.Index(requested.index, dtype="object"), "tr_open", execution=True
                )
                filled = pd.Index(selected_open.index, dtype="object")
                targets = requested.reindex(filled).astype(float)
                missing_targets = pd.Index(requested.index).difference(filled, sort=False)
            else:
                risky = float(old_values.sum())
                if risky > 0:
                    targets = (old_values / risky * allocation).astype(float)
                    selected_open = existing_open.reindex(targets.index)
                else:
                    targets = pd.Series(dtype=float)
                    selected_open = pd.Series(dtype=float)
                filled = pd.Index(targets.index, dtype="object")
                missing_targets = pd.Index([], dtype="object")
            union = existing.union(pd.Index(targets.index, dtype="object"), sort=False)
            preweights = old_values.reindex(union, fill_value=0.0) / pretrade_nav
            targetweights = targets.reindex(union, fill_value=0.0)
            l1 = float((targetweights - preweights).abs().sum())
            cost_amount = pretrade_nav * cost_rate * l1
            postcost_nav = pretrade_nav - cost_amount
            if postcost_nav <= 0:
                raise DataQualityError("Round9 transaction costs exhausted capital")
            shares = (postcost_nav * targets / selected_open).dropna()
            shares = shares[shares.ne(0.0)].astype(float)
            cash = postcost_nav * (1.0 - float(targets.sum()))
            event_rows.append(
                {
                    "execution_date": date,
                    "event_kind": "base_and_overlay" if is_base and is_overlay else ("base" if is_base else "overlay"),
                    "base_reranked": bool(is_base),
                    "overlay_event": bool(is_overlay),
                    "target_allocation": allocation,
                    "pretrade_nav": pretrade_nav,
                    "pretrade_long_exposure": float(preweights.sum()),
                    "target_long_exposure": float(targets.sum()),
                    "l1_turnover": l1,
                    "cost_bps": float(cost_bps),
                    "cost_amount": cost_amount,
                    "selected_count": len(filled),
                    "missing_target_count": len(missing_targets),
                    "missing_target_sids": "|".join(map(str, missing_targets)),
                    "corporate_actions_applied_pre_open": sum(row["status"] == "applied" for row in actions),
                }
            )
            if full_audit:
                for sid in union:
                    trade_rows.append(
                        {
                            "execution_date": date,
                            "sid": sid,
                            "pretrade_weight": float(preweights.loc[sid]),
                            "target_weight": float(targetweights.loc[sid]),
                            "trade_weight": float(targetweights.loc[sid] - preweights.loc[sid]),
                        }
                    )
                for sid, value in targets.items():
                    target_rows.append({"execution_date": date, "sid": sid, "target_weight": float(value)})

        close_prices = engine._price_vector(date, pd.Index(shares.index), "tr_close", execution=False)
        cash *= 1.0 + float(rf.loc[date])
        position_values = shares * close_prices
        long_value = float(position_values.sum())
        close_nav = long_value + cash
        if not np.isfinite(close_nav) or close_nav <= 0:
            raise DataQualityError(f"Round9 non-positive close NAV on {date.date()}")
        nav_rows.append(
            {
                "date": date,
                "nav": close_nav,
                "daily_return": close_nav / previous_close_nav - 1.0,
                "long_value": long_value,
                "cash_value": cash,
                "long_exposure": long_value / close_nav,
                "cash_weight": cash / close_nav,
                "invested_count": int(position_values.ne(0).sum()),
                "rf_return": float(rf.loc[date]),
            }
        )
        previous_close_nav = close_nav
    return {
        "nav": pd.DataFrame(nav_rows),
        "events": pd.DataFrame(event_rows),
        "targets": pd.DataFrame(target_rows),
        "trades": pd.DataFrame(trade_rows),
        "corporate_actions": pd.DataFrame(action_rows),
    }


def run_r9a(*, project_root: str | Path, runtime_root: str | Path, run_id: str) -> Round9BatchResult:
    root, runtime, lock, program, parents, data = _load_inputs(project_root, runtime_root)
    _require_run_id(root, 0, run_id)
    output = _batch_root(runtime, "R9A_MOM255_UNION_LEDGER", run_id)
    output.mkdir(parents=True, exist_ok=False)
    start, end = pd.Timestamp(program["sample"]["first_execution"]), pd.Timestamp(program["sample"]["last_execution"])
    engine = BaselineBacktester(
        data.prices,
        data.membership,
        sessions=data.sessions,
        evaluation_start=start,
        signal_end=end,
        corporate_actions=data.corporate_actions,
        missing_valuation_policy=data.missing_valuation_policy,
        missing_execution_policy=data.legacy_missing_execution_policy,
    )
    states = pd.read_parquet(parents["r8a"] / "policy_states_weekly.parquet")
    _dates(states)
    states = states[states.policy_id.eq(program["transfer"]["overlay_policy_id"])]
    if len(states) != 404 or not states.week_id.is_unique:
        raise DataQualityError("Round9 P00 parent state coverage drifted")
    overlay = states.set_index("execution_session").target_spy_weight.astype(float)
    holdings = pd.read_parquet(parents["g00"] / "artifacts/holdings.parquet")
    g00_nav = pd.read_parquet(parents["g00"] / "artifacts/nav.parquet")
    _dates(holdings); _dates(g00_nav)
    registry = pd.read_csv(root / program["transfer"]["registry"])
    all_nav, all_events, all_targets, all_trades, all_actions = [], [], [], [], []
    static_rows, identity_rows = [], []
    for spec in registry.itertuples(index=False):
        base = holdings[holdings.strategy_id.eq(spec.g00_strategy_id)][["signal_date", "execution_date", "sid", "target_weight"]].copy()
        if base.empty:
            raise DataQualityError(f"Round9 missing G00 targets for {spec.g00_strategy_id}")
        base = base[(base.execution_date >= start) & (base.execution_date <= end)]
        dynamic_zero = simulate_union_event_book(
            engine=engine, base_targets=base, overlay_schedule=overlay, risk_free_daily=data.risk_free_daily,
            start=start, end=end, cost_bps=0, path_type="p00_overlay", full_audit=False,
        )
        target_mean = float(dynamic_zero["nav"].long_exposure.mean())
        lo, hi = float(program["static_control"]["lower"]), float(program["static_control"]["upper"])
        for _ in range(int(program["static_control"]["iterations"])):
            mid = (lo + hi) / 2.0
            trial = simulate_union_event_book(
                engine=engine, base_targets=base, overlay_schedule=overlay, risk_free_daily=data.risk_free_daily,
                start=start, end=end, cost_bps=0, path_type="matched_static", static_allocation=mid, full_audit=False,
            )
            if float(trial["nav"].long_exposure.mean()) < target_mean:
                lo = mid
            else:
                hi = mid
        static_allocation = (lo + hi) / 2.0
        static_check = simulate_union_event_book(
            engine=engine, base_targets=base, overlay_schedule=overlay, risk_free_daily=data.risk_free_daily,
            start=start, end=end, cost_bps=0, path_type="matched_static", static_allocation=static_allocation, full_audit=False,
        )
        static_mean = float(static_check["nav"].long_exposure.mean())
        if abs(static_mean - target_mean) > float(program["static_control"]["tolerance"]):
            raise DataQualityError("Round9 matched-static exposure solver failed")
        static_rows.append({"transfer_id": spec.transfer_id, "dynamic_mean_actual_long_exposure_0bp": target_mean, "static_allocation": static_allocation, "static_mean_actual_long_exposure_0bp": static_mean, "absolute_match_error": abs(static_mean-target_mean)})
        for cost in program["transfer"]["cost_bps"]:
            for path_type in ("naked", "p00_overlay", "matched_static"):
                result = simulate_union_event_book(
                    engine=engine, base_targets=base, overlay_schedule=overlay, risk_free_daily=data.risk_free_daily,
                    start=start, end=end, cost_bps=cost, path_type=path_type,
                    static_allocation=static_allocation if path_type == "matched_static" else None, full_audit=True,
                )
                for name, collection in (("nav", all_nav), ("events", all_events), ("targets", all_targets), ("trades", all_trades), ("corporate_actions", all_actions)):
                    frame = result[name]
                    if frame.empty:
                        continue
                    saved = frame.copy()
                    saved.insert(0, "transfer_id", spec.transfer_id)
                    saved.insert(1, "path_type", path_type)
                    if "cost_bps" in saved.columns:
                        saved["cost_bps"] = float(cost)
                    else:
                        saved.insert(2, "cost_bps", float(cost))
                    collection.append(saved)
                if path_type == "naked":
                    frozen = g00_nav[(g00_nav.strategy_id.eq(spec.g00_strategy_id)) & g00_nav.cost_bps.eq(float(cost)) & (g00_nav.date >= start) & (g00_nav.date <= end)].sort_values("date")
                    replay = result["nav"].sort_values("date")
                    if len(frozen) != len(replay) or not frozen.date.reset_index(drop=True).equals(replay.date.reset_index(drop=True)):
                        raise DataQualityError("Round9 naked/G00 calendar identity failed")
                    nav_error = float(np.max(np.abs(frozen.nav.to_numpy(float) - replay.nav.to_numpy(float))))
                    return_error = float(np.max(np.abs(frozen.daily_return.to_numpy(float) - replay.daily_return.to_numpy(float))))
                    passed = nav_error <= float(program["identity"]["nav_tolerance"]) and return_error <= float(program["identity"]["daily_return_tolerance"])
                    identity_rows.append({"transfer_id": spec.transfer_id, "cost_bps": float(cost), "observations": len(replay), "maximum_nav_absolute_error": nav_error, "maximum_daily_return_absolute_error": return_error, "identity_passed": passed})
                    if not passed:
                        raise DataQualityError(f"Round9 naked replay failed G00 identity: {spec.transfer_id}, {cost}bp")
    pd.concat(all_nav, ignore_index=True).to_parquet(output / "nav_daily.parquet", index=False, compression="zstd")
    pd.concat(all_events, ignore_index=True).to_parquet(output / "event_ledger.parquet", index=False, compression="zstd")
    pd.concat(all_targets, ignore_index=True).to_parquet(output / "target_ledger.parquet", index=False, compression="zstd")
    pd.concat(all_trades, ignore_index=True).to_parquet(output / "trade_ledger.parquet", index=False, compression="zstd")
    if all_actions:
        pd.concat(all_actions, ignore_index=True).to_parquet(output / "corporate_action_audit.parquet", index=False, compression="zstd")
    pd.DataFrame(static_rows).to_csv(output / "static_allocations.csv", index=False, lineterminator="\n")
    pd.DataFrame(identity_rows).to_csv(output / "g00_identity_audit.csv", index=False, lineterminator="\n")
    counts = {"transfer_cells": 6, "paths": 3, "cost_scenarios": 4, "nav_rows": sum(len(x) for x in all_nav), "identity_checks": len(identity_rows)}
    manifest = _manifest(output, root, "R9A_MOM255_UNION_LEDGER", run_id, counts, {"g00": sha256_file(parents["g00"] / "manifest.json"), "r8a": sha256_file(parents["r8a"] / "manifest.json")}, strategy_nav=True)
    return Round9BatchResult(output, output / "manifest.json", manifest["status"])


def run_r9b(*, project_root: str | Path, runtime_root: str | Path, run_id: str) -> Round9BatchResult:
    root, runtime, _, program, _, _ = _load_inputs(project_root, runtime_root)
    _require_run_id(root, 1, run_id)
    r9a = _batch_root(runtime, "R9A_MOM255_UNION_LEDGER", _run_ids(root)[0])
    _validate_bundle(r9a, "R9A_MOM255_UNION_LEDGER")
    output = _batch_root(runtime, "R9B_MOM255_TRANSFER_ECONOMICS", run_id)
    output.mkdir(parents=True, exist_ok=False)
    nav = pd.read_parquet(r9a / "nav_daily.parquet")
    events = pd.read_parquet(r9a / "event_ledger.parquet")
    _dates(nav); _dates(events)
    metrics = []
    for keys, part in nav.groupby(["transfer_id", "path_type", "cost_bps"], sort=True):
        transfer_id, path_type, cost = keys
        perf = _performance(part.sort_values("date"))
        ev = events[(events.transfer_id.eq(transfer_id)) & events.path_type.eq(path_type) & events.cost_bps.eq(cost)]
        metrics.append({"transfer_id": transfer_id, "path_type": path_type, "cost_bps": cost, **perf, "cumulative_l1_turnover": float(ev.l1_turnover.sum()), "mean_actual_long_exposure": float(part.long_exposure.mean()), "event_count": len(ev)})
    metrics_frame = pd.DataFrame(metrics)
    wide = metrics_frame.pivot(index=["transfer_id", "cost_bps"], columns="path_type", values=["terminal", "cagr", "sharpe", "mdd", "cumulative_l1_turnover", "mean_actual_long_exposure"]).reset_index()
    wide.columns = ["_".join(filter(None, map(str, col))).rstrip("_") if isinstance(col, tuple) else col for col in wide.columns]
    comparisons = pd.DataFrame({"transfer_id": wide.transfer_id, "cost_bps": wide.cost_bps})
    comparisons["overlay_to_naked_terminal_ratio"] = wide.terminal_p00_overlay / wide.terminal_naked
    comparisons["timing_value_vs_static"] = wide.terminal_p00_overlay / wide.terminal_matched_static - 1.0
    comparisons["delta_sharpe_vs_naked"] = wide.sharpe_p00_overlay - wide.sharpe_naked
    comparisons["delta_mdd_vs_naked"] = wide.mdd_p00_overlay - wide.mdd_naked
    comparisons["overlay_terminal"] = wide.terminal_p00_overlay
    comparisons["naked_terminal"] = wide.terminal_naked
    comparisons["static_terminal"] = wide.terminal_matched_static
    comparisons["overlay_cagr"] = wide.cagr_p00_overlay
    comparisons["naked_cagr"] = wide.cagr_naked
    comparisons["overlay_sharpe"] = wide.sharpe_p00_overlay
    comparisons["naked_sharpe"] = wide.sharpe_naked
    comparisons["overlay_mdd"] = wide.mdd_p00_overlay
    comparisons["naked_mdd"] = wide.mdd_naked
    comparisons["overlay_turnover"] = wide.cumulative_l1_turnover_p00_overlay
    comparisons["naked_turnover"] = wide.cumulative_l1_turnover_naked
    comparisons["overlay_mean_actual_long_exposure"] = wide.mean_actual_long_exposure_p00_overlay
    comparisons["static_mean_actual_long_exposure"] = wide.mean_actual_long_exposure_matched_static
    comparisons["four_metric_gate"] = comparisons.overlay_to_naked_terminal_ratio.gt(1.0) & comparisons.timing_value_vs_static.gt(0) & comparisons.delta_sharpe_vs_naked.gt(0) & comparisons.delta_mdd_vs_naked.gt(0)
    yearly = []
    for transfer_id in nav.transfer_id.unique():
        for cost in program["transfer"]["cost_bps"]:
            overlay = nav[(nav.transfer_id.eq(transfer_id)) & nav.path_type.eq("p00_overlay") & nav.cost_bps.eq(cost)].set_index("date").nav
            static = nav[(nav.transfer_id.eq(transfer_id)) & nav.path_type.eq("matched_static") & nav.cost_bps.eq(cost)].set_index("date").nav
            active = np.log(overlay / static).diff().fillna(np.log((overlay / static).iloc[0]))
            frame = active.rename("active_log_return").reset_index()
            frame["year"] = frame.date.dt.year
            frame = frame.groupby("year", as_index=False).active_log_return.sum()
            frame.insert(0, "cost_bps", float(cost)); frame.insert(0, "transfer_id", transfer_id)
            frame["positive"] = frame.active_log_return > 0
            yearly.append(frame)
    metrics_frame.to_csv(output / "path_metrics.csv", index=False, lineterminator="\n")
    comparisons.to_csv(output / "transfer_comparisons.csv", index=False, lineterminator="\n")
    pd.concat(yearly, ignore_index=True).to_csv(output / "yearly_timing.csv", index=False, lineterminator="\n")
    manifest = _manifest(output, root, "R9B_MOM255_TRANSFER_ECONOMICS", run_id, {"path_metrics": len(metrics_frame), "comparisons": len(comparisons), "year_rows": sum(len(x) for x in yearly)}, {"r9a": sha256_file(r9a / "manifest.json")}, strategy_nav=True)
    return Round9BatchResult(output, output / "manifest.json", manifest["status"])


def run_r9c(*, project_root: str | Path, runtime_root: str | Path, run_id: str) -> Round9BatchResult:
    root, runtime, _, program, _, _ = _load_inputs(project_root, runtime_root)
    _require_run_id(root, 2, run_id)
    r9a = _batch_root(runtime, "R9A_MOM255_UNION_LEDGER", _run_ids(root)[0])
    r9b = _batch_root(runtime, "R9B_MOM255_TRANSFER_ECONOMICS", _run_ids(root)[1])
    _validate_bundle(r9a, "R9A_MOM255_UNION_LEDGER"); _validate_bundle(r9b, "R9B_MOM255_TRANSFER_ECONOMICS")
    output = _batch_root(runtime, "R9C_MOM255_TRANSFER_ASSESSMENT", run_id)
    output.mkdir(parents=True, exist_ok=False)
    nav = pd.read_parquet(r9a / "nav_daily.parquet"); comparisons = pd.read_csv(r9b / "transfer_comparisons.csv")
    _dates(nav)
    registry = pd.read_csv(root / program["transfer"]["registry"])
    primary_id = program["transfer"]["primary_transfer_id"]
    cost = float(program["transfer"]["primary_cost_bps"])
    primary = comparisons[(comparisons.transfer_id.eq(primary_id)) & comparisons.cost_bps.eq(cost)]
    if len(primary) != 1:
        raise DataQualityError("Round9 primary comparison is not unique")
    active = _active_log_returns(nav, primary_id, cost)
    weekly = active.resample("W-FRI").sum()
    lower, pvalue = _block_mean(weekly.to_numpy(float), int(program["inference"]["block_weeks"]), int(program["inference"]["bootstrap_repetitions"]), int(program["inference"]["seed"]))
    events = pd.read_csv(root / program["inference"]["event_registry"], parse_dates=["peak_date", "recovery_date"])
    leave_rows = []
    for event in events.itertuples(index=False):
        keep = ~((active.index >= event.peak_date) & (active.index <= event.recovery_date))
        leave_rows.append({"episode_id": event.episode_id, "removed_days": int((~keep).sum()), "timing_value_without_event": float(np.exp(active.loc[keep].sum()) - 1.0)})
    leave = pd.DataFrame(leave_rows)
    c10 = comparisons[comparisons.cost_bps.eq(cost)].merge(registry[["transfer_id", "frequency", "primary"]], on="transfer_id", validate="one_to_one")
    passed = int(c10.four_metric_gate.sum())
    weekly_passed = int(c10.loc[c10.frequency.eq("weekly"), "four_metric_gate"].sum())
    monthly_passed = int(c10.loc[c10.frequency.eq("monthly"), "four_metric_gate"].sum())
    deltas = pd.DataFrame({
        "metric": ["overlay_to_naked_terminal_increment", "timing_value_vs_static", "delta_sharpe_vs_naked", "delta_mdd_vs_naked"],
        "median": [float((c10.overlay_to_naked_terminal_ratio - 1).median()), float(c10.timing_value_vs_static.median()), float(c10.delta_sharpe_vs_naked.median()), float(c10.delta_mdd_vs_naked.median())],
    })
    c20 = comparisons[comparisons.cost_bps.eq(20.0)]
    primary_four = bool(primary.four_metric_gate.iloc[0])
    family_gate = passed >= int(program["gates"]["family_minimum_passed"]) and weekly_passed >= int(program["gates"]["family_minimum_weekly_passed"]) and monthly_passed >= int(program["gates"]["family_minimum_monthly_passed"]) and bool(deltas["median"].gt(float(program["gates"]["family_all_metric_medians_gt"])).all())
    cost20_gate = bool(c20.timing_value_vs_static.gt(0).all())
    inference_gate = lower > float(program["gates"]["primary_block_lower_gt"]) and pvalue <= float(program["gates"]["primary_p_le"])
    leaveout_gate = bool(leave.timing_value_without_event.gt(float(program["gates"]["minimum_leaveout_timing_gt"])).all())
    eligible = primary_four and family_gate and cost20_gate and inference_gate and leaveout_gate
    assessment = c10.copy()
    assessment["primary_four_metric_gate"] = assessment.transfer_id.eq(primary_id) & assessment.four_metric_gate
    assessment["primary_block13_lower"] = np.where(assessment.transfer_id.eq(primary_id), lower, np.nan)
    assessment["primary_one_sided_p"] = np.where(assessment.transfer_id.eq(primary_id), pvalue, np.nan)
    assessment.to_csv(output / "cell_assessment.csv", index=False, lineterminator="\n")
    deltas.to_csv(output / "family_medians.csv", index=False, lineterminator="\n")
    leave.to_csv(output / "leave_one_event_out.csv", index=False, lineterminator="\n")
    family = {"primary_transfer_id": primary_id, "primary_four_metric_gate": primary_four, "primary_block13_95_lower": lower, "primary_one_sided_p": pvalue, "passed_cells": passed, "weekly_passed": weekly_passed, "monthly_passed": monthly_passed, "family_gate": family_gate, "cost20_direction_gate": cost20_gate, "leaveout_gate": leaveout_gate, "development_transfer_eligible": eligible}
    (output / "family_assessment.json").write_text(json.dumps(family, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    decision = {"program_id": PROGRAM_ID, "status": "completed_pending_user_lockbox_decision", "development_transfer_eligible": eligible, "eligible_policy_id": "P00_RSP_Y5_CLEAR" if eligible else None, "primary_transfer_id": primary_id, "lockbox_authorized": False, "lockbox_read": False, "automatic_revision": False}
    (output / "decision.json").write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = _manifest(output, root, "R9C_MOM255_TRANSFER_ASSESSMENT", run_id, {"cells": 6, "passed_cells": passed, "events": len(leave), "eligible": int(eligible)}, {"r9a": sha256_file(r9a / "manifest.json"), "r9b": sha256_file(r9b / "manifest.json")}, strategy_nav=False, assessment="completed_pending_user_lockbox_decision")
    return Round9BatchResult(output, output / "manifest.json", manifest["status"])


def _performance(frame: pd.DataFrame) -> dict[str, float]:
    ordered = frame.sort_values("date")
    nav = ordered.nav.astype(float)
    ret = ordered.daily_return.astype(float)
    years = len(ordered) / 252.0
    std = float(ret.std(ddof=1))
    return {"terminal": float(nav.iloc[-1]), "cagr": float(nav.iloc[-1] ** (1 / years) - 1), "sharpe": float(ret.mean() / std * np.sqrt(252)) if std > 0 else np.nan, "mdd": float((nav / nav.cummax() - 1).min())}


def _active_log_returns(nav: pd.DataFrame, transfer_id: str, cost: float) -> pd.Series:
    overlay = nav[(nav.transfer_id.eq(transfer_id)) & nav.path_type.eq("p00_overlay") & nav.cost_bps.eq(cost)].set_index("date").nav.sort_index()
    static = nav[(nav.transfer_id.eq(transfer_id)) & nav.path_type.eq("matched_static") & nav.cost_bps.eq(cost)].set_index("date").nav.sort_index()
    ratio = overlay / static
    return np.log(ratio).diff().fillna(np.log(ratio.iloc[0])).rename("active_log_return")


def _block_mean(x: np.ndarray, block: int, reps: int, seed: int) -> tuple[float, float]:
    rng = np.random.default_rng(seed); starts = np.arange(len(x) - block + 1); estimates = np.empty(reps)
    for i in range(reps):
        ids: list[int] = []
        while len(ids) < len(x):
            start = int(rng.choice(starts)); ids.extend(range(start, start + block))
        estimates[i] = float(np.mean(x[np.asarray(ids[: len(x)])]))
    return float(np.quantile(estimates, 0.05)), float(np.mean(estimates <= 0))


def _dates(frame: pd.DataFrame) -> None:
    for column in frame.columns:
        if column in {"date", "signal_date", "execution_date"} or column.endswith("session"):
            try:
                frame[column] = pd.to_datetime(frame[column]).dt.normalize()
            except (ValueError, TypeError):
                pass


def _load_inputs(project_root: str | Path, runtime_root: str | Path):
    root, runtime = Path(project_root).resolve(), Path(runtime_root).resolve()
    lock_path = root / "config/experiments/round9/PREREG_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    for relative, expected in lock["files"].items():
        if sha256_file(root / relative) != expected:
            raise DataQualityError(f"Round9 prereg mismatch: {relative}")
    program = tomllib.loads((root / "config/experiments/round9/program.toml").read_text(encoding="utf-8"))
    if program["program_id"] != PROGRAM_ID:
        raise DataQualityError("Round9 program identity failed")
    auth = program["authorization"]
    if not auth["union_event_ledger"] or not auth["development_mom255_nav"] or auth["lockbox"] or auth["model_search"] or auth["policy_search"] or auth["short_books"]:
        raise DataQualityError("Round9 authorization failed")
    parents = {
        "g00": runtime / "results/experiments/G00/runs" / program["parent"]["g00_run_id"],
        "r8a": runtime / "results/experiments/round8/R8A_RSP_POLICY_SIGNALS/runs" / program["parent"]["r8a_run_id"],
        "r8c": runtime / "results/experiments/round8/R8C_RSP_POLICY_ASSESSMENT/runs" / program["parent"]["r8c_run_id"],
    }
    checks = {
        parents["g00"] / "manifest.json": program["parent"]["g00_manifest_sha256"],
        parents["g00"] / "artifacts/holdings.parquet": program["parent"]["g00_holdings_sha256"],
        parents["g00"] / "artifacts/nav.parquet": program["parent"]["g00_nav_sha256"],
        parents["r8a"] / "manifest.json": program["parent"]["r8a_manifest_sha256"],
        parents["r8a"] / "policy_states_weekly.parquet": program["parent"]["r8a_states_sha256"],
        parents["r8c"] / "manifest.json": program["parent"]["r8c_manifest_sha256"],
        parents["r8c"] / "decision.json": program["parent"]["r8c_decision_sha256"],
    }
    for path, expected in checks.items():
        if sha256_file(path) != expected:
            raise DataQualityError(f"Round9 parent drift: {path}")
    decision = json.loads((parents["r8c"] / "decision.json").read_text(encoding="utf-8"))
    if decision.get("development_policy_eligible") != ["P00_RSP_Y5_CLEAR"] or decision.get("lockbox_read") is not False:
        raise DataQualityError("Round9 parent decision is not the unique P00 acceptance")
    dataset_dir = runtime / "data/curated" / program["parent"]["dataset_version"]
    dataset_checks = {"FROZEN.json": "dataset_freeze_sha256", "prices_daily.parquet": "prices_sha256", "corporate_actions.parquet": "corporate_actions_sha256", "risk_free_daily.parquet": "risk_free_sha256", "calendar.parquet": "calendar_sha256"}
    for filename, key in dataset_checks.items():
        if sha256_file(dataset_dir / filename) != program["parent"][key]:
            raise DataQualityError(f"Round9 dataset parent drift: {filename}")
    context = prepare_experiment_run(root / "config/experiments/G00.toml", run_id="round9-readonly-context", dataset_version=program["parent"]["dataset_version"], data_root=runtime / "data", output_root=runtime / "results")
    data = load_experiment_data(context, allow_review_dataset=True)
    return root, runtime, lock, program, parents, data


def _run_ids(root: Path) -> list[str]:
    return list(tomllib.loads((root / "config/experiments/round9/program.toml").read_text(encoding="utf-8"))["run_ids"])


def _require_run_id(root: Path, index: int, run_id: str) -> None:
    if _run_ids(root)[index] != run_id:
        raise DataQualityError("Round9 run-id mismatch")


def _batch_root(runtime: Path, batch: str, run_id: str) -> Path:
    return runtime / "results/experiments/round9" / batch / "runs" / run_id


def _validate_bundle(path: Path, batch: str) -> None:
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest["program_id"] != PROGRAM_ID or manifest["batch_id"] != batch or manifest["lockbox_read"] is not False:
        raise DataQualityError("Round9 bundle identity failed")
    for record in manifest["files"]:
        artifact = path / record["path"]
        if artifact.stat().st_size != record["size_bytes"] or sha256_file(artifact) != record["sha256"]:
            raise DataQualityError("Round9 bundle mutated")


def _manifest(output: Path, root: Path, batch: str, run_id: str, counts: dict[str, Any], parents: dict[str, str], *, strategy_nav: bool, assessment: str = "completed_development") -> dict[str, Any]:
    files = [{"path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in sorted(output.rglob("*")) if path.is_file()]
    manifest = {"schema_version": 1, "program_id": PROGRAM_ID, "batch_id": batch, "run_id": run_id, "status": "completed_development", "assessment": assessment, "formal_eligible": False, "lockbox_read": False, "lockbox_predictions_generated": False, "models_run": False, "state_machine_run": False, "strategy_nav_run": strategy_nav, "mom255_transfer_run": True, "prereg_lock_sha256": sha256_file(root / "config/experiments/round9/PREREG_LOCK.json"), "parent_manifests": parents, "counts": counts, "files": files}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
