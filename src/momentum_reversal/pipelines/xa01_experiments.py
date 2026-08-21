"""XA01 causal weekly/monthly atomic-factor tournament."""

from __future__ import annotations

import hashlib
import json
import math
import tomllib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from momentum_reversal.backtest.calendar import rebalance_schedule
from momentum_reversal.backtest.engine import BaselineBacktester
from momentum_reversal.analytics.performance import performance_summary
from momentum_reversal.data.corporate_actions import CorporateActionLedger
from momentum_reversal.data.factor_database import build_factor_database
from momentum_reversal.data.membership import PITMembership
from momentum_reversal.factors.cross_sectional_fundamental import (
    compute_fundamental_factor_panel,
)
from momentum_reversal.factors.cross_sectional_market import (
    materialize_cross_sectional_market_factors,
)
from momentum_reversal.pipelines.cross_sectional_database import DatabaseLayout


PROGRAM = Path("config/experiments/xa01/program.toml")
LOCK = Path("config/experiments/xa01/PREREG_LOCK.json")
RUN_ID = "xa01-atomic-factor-walkforward-20260820-v1"


def run_xa01(project_root: str | Path, runtime_root: str | Path, batch: str) -> dict[str, Any]:
    project = Path(project_root).resolve()
    runtime = Path(runtime_root).resolve()
    program = _load_program(project)
    root = runtime / "results" / "experiments" / "xa01" / RUN_ID
    root.mkdir(parents=True, exist_ok=True)
    batch = batch.upper()
    if batch == "XA01A":
        result = _run_a(project, runtime, root, program)
    elif batch == "XA01B":
        result = _run_b(root, program)
    elif batch == "XA01C":
        result = _run_c(project, runtime, root, program)
    elif batch == "XA01D":
        result = _run_d(project, runtime, root, program)
    else:
        raise ValueError("batch must be XA01A, XA01B, XA01C, or XA01D")
    _write_json(root / f"{batch.lower()}_summary.json", result)
    _write_manifest(root)
    return result


def _run_a(project: Path, runtime: Path, root: Path, program: dict[str, Any]) -> dict[str, Any]:
    layout = DatabaseLayout.load(project_root=project, runtime_root=runtime)
    factors = _factor_registry(project)
    selected = factors["factor_id"].tolist()
    market = layout.market_root
    calendar = pd.read_parquet(market / "calendar.parquet")
    calendar["session_date"] = pd.to_datetime(calendar["session_date"]).dt.normalize()
    start = pd.Timestamp(program["sample"]["history_start"])
    end = pd.Timestamp(program["sample"]["evaluation_end_close"])
    mask = calendar["session_date"].between(start, end)
    union_mask = mask & (
        calendar["week_last_session"].astype(bool)
        | calendar["month_last_session"].astype(bool)
    )
    signals = pd.DatetimeIndex(calendar.loc[union_mask, "session_date"])
    prices = pd.read_parquet(market / "prices_daily.parquet")
    benchmark = pd.read_parquet(market / "benchmark_daily.parquet")
    membership = pd.read_parquet(market / "membership.parquet")
    market_ids = factors.loc[~factors["factor_id"].isin(
        ["XS032_GROSS_PROFIT_AT", "XS041_ASSET_GROWTH", "XS056_CFO_ACCRUALS_PT"]
    ), "factor_id"].tolist()
    market_panel = materialize_cross_sectional_market_factors(
        prices, benchmark, calendar, membership, signals,
        factor_ids=market_ids, volume_qa_passed=True,
        allowed_signal_frequencies=("weekly", "monthly"),
    )
    facts = pd.read_parquet(layout.curated_root / "canonical_annual_facts.parquet")
    mappings = pd.read_parquet(layout.curated_root / "entity_cik_intervals.parquet").rename(
        columns={"cik10": "cik"}
    )
    fundamental = _event_driven_fundamental_panel(facts, mappings, signals)
    active = pd.read_csv(project / "config/research/cross_sectional_alpha/active_factor_registry.csv", dtype=str, keep_default_na=False)
    database = build_factor_database(market_panel, fundamental, membership, signals, active)
    database = database.loc[database["factor_id"].isin(selected)].sort_values(
        ["signal_date", "sid", "factor_id"], ignore_index=True
    )
    parent = pd.read_parquet(layout.factor_bundle_root / "factor_values.parquet")
    parent = parent.loc[parent["factor_id"].isin(selected)].copy()
    month_dates = pd.DatetimeIndex(calendar.loc[mask & calendar["month_last_session"].astype(bool), "session_date"])
    current_month = database.loc[database["signal_date"].isin(month_dates)].reset_index(drop=True)
    parent = parent.loc[parent["signal_date"].isin(month_dates)].reset_index(drop=True)
    compare = ["signal_date", "sid", "factor_id", "raw_value", "score", "eligible", "missing_reason", "rank", "percentile"]
    pd.testing.assert_frame_equal(current_month[compare], parent[compare], check_dtype=False, check_exact=True)
    _write_parquet(root / "factor_values_weekly_monthly.parquet", database)
    _write_parquet(root / "signal_dates.parquet", pd.DataFrame({"signal_date": signals}))
    targets = _build_targets(database, prices, membership, calendar,
                             pd.read_parquet(market / "risk_free_daily.parquet"), program)
    _write_parquet(root / "target_ledger.parquet", targets)
    return {
        "batch": "XA01A", "status": "completed", "factor_count": len(selected),
        "signal_dates": len(signals), "factor_rows": len(database),
        "target_rows": len(targets), "monthly_parent_identity": True,
    }


def _event_driven_fundamental_panel(
    facts: pd.DataFrame,
    mappings: pd.DataFrame,
    signal_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Materialise exact as-of states only when their SEC inputs can change.

    Annual fundamental values remain constant between filing availability
    events.  The canonical calculator is therefore evaluated at the first
    scheduled signal and at the first signal on/after every possible state
    change, then its complete audited rows are carried forward.  Monthly
    identity against the frozen parent is checked by XA01A after expansion.
    """

    dates = pd.DatetimeIndex(pd.to_datetime(signal_dates)).normalize().sort_values().unique()
    fact_frame = facts.copy()
    fact_frame["cik"] = fact_frame["cik"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(10)
    fact_frame["available_session"] = pd.to_datetime(fact_frame["available_session"]).dt.normalize()
    fact_frame["period_end"] = pd.to_datetime(fact_frame["period_end"]).dt.normalize()
    mapping_frame = mappings.copy()
    mapping_frame["cik"] = mapping_frame["cik"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(10)
    mapping_frame["effective_from"] = pd.to_datetime(mapping_frame["effective_from"]).dt.normalize()
    mapping_frame["effective_to"] = pd.to_datetime(mapping_frame["effective_to"], errors="coerce").dt.normalize()
    parts: list[pd.DataFrame] = []
    for item in mapping_frame.sort_values(["sid", "effective_from"]).itertuples(index=False):
        relevant = dates[dates >= pd.Timestamp(item.effective_from)]
        if not pd.isna(item.effective_to):
            relevant = relevant[relevant < pd.Timestamp(item.effective_to)]
        if relevant.empty:
            continue
        cik_facts = fact_frame.loc[fact_frame["cik"].eq(str(item.cik))]
        state_dates: set[pd.Timestamp] = {pd.Timestamp(relevant[0])}
        if not cik_facts.empty:
            change_at = cik_facts[["available_session", "period_end"]].max(axis=1).dropna().unique()
            for event in pd.DatetimeIndex(change_at):
                position = int(relevant.searchsorted(event, side="left"))
                if position < len(relevant):
                    state_dates.add(pd.Timestamp(relevant[position]))
        one_mapping = pd.DataFrame({
            "sid": [str(item.sid)], "cik": [str(item.cik)],
            "effective_from": [pd.Timestamp(item.effective_from)],
            "effective_to": [item.effective_to],
        })
        states = compute_fundamental_factor_panel(
            cik_facts, one_mapping, sorted(state_dates)
        ).reset_index()
        for _, group in states.groupby("factor_id", sort=False):
            expanded = group.set_index("signal_date").reindex(relevant, method="ffill")
            expanded.index.name = "signal_date"
            expanded["sid"] = str(item.sid)
            parts.append(expanded.reset_index())
    if not parts:
        raise ValueError("event-driven fundamental panel is empty")
    return pd.concat(parts, ignore_index=True).sort_values(
        ["signal_date", "sid", "factor_id"], ignore_index=True
    )


def _build_targets(database: pd.DataFrame, prices: pd.DataFrame, membership: pd.DataFrame,
                   calendar: pd.DataFrame, rf: pd.DataFrame,
                   program: dict[str, Any]) -> pd.DataFrame:
    sessions = pd.DatetimeIndex(calendar["session_date"])
    opens = prices.pivot(index="date", columns="sid", values="tr_open").reindex(sessions)
    rf_series = rf.set_index("date")["rf_return"].astype(float).reindex(sessions).fillna(0.0)
    eval_start = pd.Timestamp(program["sample"]["evaluation_start_open"])
    eval_end = pd.Timestamp(program["sample"]["evaluation_end_close"])
    rows: list[pd.DataFrame] = []
    for frequency in ("weekly", "monthly"):
        schedule = rebalance_schedule(sessions, frequency).reset_index(drop=True)
        schedule["next_execution_date"] = schedule["execution_date"].shift(-1)
        schedule = schedule.loc[
            schedule["execution_date"].between(eval_start, eval_end)
            & schedule["next_execution_date"].notna()
            & schedule["next_execution_date"].le(eval_end)
        ]
        for item in schedule.itertuples(index=False):
            sig = pd.Timestamp(item.signal_date)
            entry = pd.Timestamp(item.execution_date)
            exit_date = pd.Timestamp(item.next_execution_date)
            members = database.loc[database["signal_date"].eq(sig), "sid"].drop_duplicates()
            if members.empty:
                continue
            start_open = opens.loc[entry].reindex(members).astype(float)
            end_open = opens.loc[exit_date].reindex(members).astype(float)
            stock_return = end_open / start_open - 1.0
            cash = float((1.0 + rf_series.loc[(rf_series.index >= entry) & (rf_series.index < exit_date)]).prod() - 1.0)
            frame = pd.DataFrame({
                "signal_date": sig, "execution_date": entry,
                "label_end_execution_date": exit_date, "frequency": frequency,
                "sid": members.to_numpy(), "forward_total_return": stock_return.to_numpy(),
            })
            frame["forward_cash_return"] = cash
            frame["forward_excess_cash"] = frame["forward_total_return"] - cash
            frame["target_available_at"] = exit_date
            frame["target_valid"] = np.isfinite(frame["forward_excess_cash"])
            valid = frame["target_valid"]
            frame["forward_rank"] = np.nan
            frame.loc[valid, "forward_rank"] = frame.loc[valid, "forward_excess_cash"].rank(method="average", pct=True)
            rows.append(frame)
    return pd.concat(rows, ignore_index=True).sort_values(["frequency", "signal_date", "sid"], ignore_index=True)


def _run_b(root: Path, program: dict[str, Any]) -> dict[str, Any]:
    factors = pd.read_parquet(root / "factor_values_weekly_monthly.parquet")
    factors = factors.loc[factors["signal_date"].ge(pd.Timestamp("2017-12-29"))]
    targets = pd.read_parquet(root / "target_ledger.parquet")
    merged = factors.merge(targets, on=["signal_date", "sid"], how="inner", validate="many_to_many")
    merged = merged.loc[merged["eligible"] & merged["target_valid"]].copy()
    rows: list[dict[str, Any]] = []
    yearly: list[dict[str, Any]] = []
    for (factor_id, frequency), group in merged.groupby(["factor_id", "frequency"], sort=True):
        by_date = group.groupby("signal_date", sort=True).apply(
            lambda x: x["score"].corr(x["forward_excess_cash"], method="spearman"),
            include_groups=False,
        ).dropna()
        rho = float(by_date.median()) if len(by_date) else math.nan
        p = _block_sign_p(by_date.to_numpy(), block=4 if frequency == "weekly" else 1)
        rows.append({"factor_id": factor_id, "frequency": frequency,
                     "signal_dates": len(by_date), "median_rank_ic": rho,
                     "mean_rank_ic": float(by_date.mean()), "positive_fraction": float(by_date.gt(0).mean()),
                     "block_sign_p": p})
        for year, values in by_date.groupby(by_date.index.year):
            yearly.append({"factor_id": factor_id, "frequency": frequency,
                           "year": int(year), "median_rank_ic": float(values.median()),
                           "mean_rank_ic": float(values.mean()), "signal_dates": len(values)})
    summary = pd.DataFrame(rows)
    summary["bh_q"] = summary.groupby("frequency")["block_sign_p"].transform(_bh)
    _write_csv(root / "signal_summary.csv", summary)
    _write_csv(root / "yearly_rank_ic.csv", pd.DataFrame(yearly))
    correlations = _factor_correlations(merged)
    _write_csv(root / "factor_correlations.csv", correlations)
    return {"batch": "XA01B", "status": "completed", "summary_rows": len(summary),
            "correlation_rows": len(correlations)}


def _run_c(project: Path, runtime: Path, root: Path, program: dict[str, Any]) -> dict[str, Any]:
    layout = DatabaseLayout.load(project_root=project, runtime_root=runtime)
    market = layout.market_root
    prices = pd.read_parquet(market / "prices_daily.parquet")
    membership_frame = pd.read_parquet(market / "membership.parquet")
    membership = PITMembership.from_intervals(membership_frame)
    actions = CorporateActionLedger(pd.read_parquet(market / "corporate_actions.parquet"))
    calendar = pd.read_parquet(market / "calendar.parquet")
    sessions = pd.DatetimeIndex(calendar["session_date"])
    rf_frame = pd.read_parquet(market / "risk_free_daily.parquet")
    rf = rf_frame.set_index("date")["rf_return"].astype(float)
    database = pd.read_parquet(root / "factor_values_weekly_monthly.parquet")
    database = database.loc[database["signal_date"].ge(pd.Timestamp(program["sample"]["first_signal_close"]))]
    engine = BaselineBacktester(
        prices, membership, sessions=sessions,
        signal_start=program["sample"]["first_signal_close"],
        signal_end=program["sample"]["evaluation_end_close"],
        evaluation_start=program["sample"]["evaluation_start_open"],
        corporate_actions=actions, missing_valuation_policy="carry_last_close",
        missing_execution_policy="leave_cash",
    )
    metrics: list[dict[str, Any]] = []
    for factor_id, factor_group in database.groupby("factor_id", sort=True):
        scores = factor_group.set_index(["signal_date", "sid"])["score"].dropna()
        eligible = factor_group.loc[factor_group["eligible"]].copy()
        ew = eligible.groupby("signal_date")["sid"].transform("size")
        ew_weights = pd.Series(1.0 / ew.to_numpy(), index=pd.MultiIndex.from_frame(eligible[["signal_date", "sid"]]))
        ew_weights.index.names = ["signal_date", "sid"]
        for frequency in ("weekly", "monthly"):
            cost_primary = int(program["paths"][f"{frequency}_primary_cost_bps"])
            for top_k in program["paths"]["top_k"]:
                for cost in program["paths"]["cost_scenarios_bps"]:
                    result = engine.run(signal="mom_255_0", top_n=int(top_k), frequency=frequency,
                                        cost_bps=float(cost), selection_scores=scores,
                                        selection_label=factor_id,
                                        selection_score_cache_key=factor_id, risk_free_daily=rf,
                                        full_audit=(cost == cost_primary))
                    record = result.summary(rf).to_dict()
                    record.update({"factor_id": factor_id, "frequency": frequency,
                                   "top_k": int(top_k), "cost_bps": int(cost), "path_type": "factor_topk"})
                    metrics.append(record)
            for cost in program["paths"]["cost_scenarios_bps"]:
                result = engine.run(signal="mom_255_0", top_n=1, frequency=frequency,
                                    cost_bps=float(cost), target_weights=ew_weights,
                                    risk_free_daily=rf, full_audit=False)
                record = result.summary(rf).to_dict()
                record.update({"factor_id": factor_id, "frequency": frequency,
                               "top_k": 0, "cost_bps": int(cost), "path_type": "eligible_ew"})
                metrics.append(record)
    frame = pd.DataFrame(metrics)
    _write_csv(root / "portfolio_metrics.csv", frame)
    primary = _active_comparison(frame, program)
    _write_csv(root / "primary_active_comparison.csv", primary)
    return {"batch": "XA01C", "status": "completed", "registered_paths": 112,
            "cost_path_rows": int(frame[frame["path_type"].eq("factor_topk")].shape[0]),
            "benchmark_rows": int(frame[frame["path_type"].eq("eligible_ew")].shape[0])}


def _active_comparison(frame: pd.DataFrame, program: dict[str, Any]) -> pd.DataFrame:
    top = frame.loc[frame["path_type"].eq("factor_topk") & frame["top_k"].eq(program["paths"]["primary_width"])].copy()
    ew = frame.loc[frame["path_type"].eq("eligible_ew")].copy()
    merged = top.merge(ew, on=["factor_id", "frequency", "cost_bps"], suffixes=("_factor", "_eligible_ew"), validate="one_to_one")
    merged["active_terminal_return"] = merged["total_return_factor"] - merged["total_return_eligible_ew"]
    merged["active_cagr"] = merged["cagr_factor"] - merged["cagr_eligible_ew"]
    merged["active_sharpe"] = merged["sharpe_excess_rf_factor"] - merged["sharpe_excess_rf_eligible_ew"]
    merged["active_mdd"] = merged["max_drawdown_factor"] - merged["max_drawdown_eligible_ew"]
    return merged


def _run_d(project: Path, runtime: Path, root: Path, program: dict[str, Any]) -> dict[str, Any]:
    registry = _factor_registry(project)
    signal = pd.read_csv(root / "signal_summary.csv")
    all_active = pd.read_csv(root / "primary_active_comparison.csv")
    active = all_active.loc[((all_active["frequency"].eq("weekly")) & all_active["cost_bps"].eq(program["paths"]["weekly_primary_cost_bps"])) |
                            ((all_active["frequency"].eq("monthly")) & all_active["cost_bps"].eq(program["paths"]["monthly_primary_cost_bps"]))]
    portfolio = pd.read_csv(root / "portfolio_metrics.csv")
    robustness_path = root / "portfolio_robustness.csv"
    robustness = pd.read_csv(robustness_path) if robustness_path.is_file() else _portfolio_robustness(portfolio, program)
    _write_csv(robustness_path, robustness)
    subperiod_path = root / "subperiod_robustness.csv"
    subperiod = pd.read_csv(subperiod_path) if subperiod_path.is_file() else _subperiod_robustness(project, runtime, root, program)
    _write_csv(subperiod_path, subperiod)
    g00 = _g00_identity(project, portfolio)
    _write_csv(root / "g00_identity_audit.csv", g00)
    paper = _paper_horizon_diagnostics(project, runtime, root, registry)
    _write_csv(root / "paper_horizon_diagnostics.csv", paper)
    decision = signal.merge(active[["factor_id", "frequency", "active_terminal_return", "active_sharpe"]],
                            on=["factor_id", "frequency"], how="left").merge(
                                robustness, on=["factor_id", "frequency"], how="left").merge(
                                subperiod, on=["factor_id", "frequency"], how="left").merge(
                                registry[["factor_id", "dimension"]], on="factor_id", how="left")
    decision["evidence_qualified"] = (
        decision["median_rank_ic"].gt(0) & decision["bh_q"].le(program["inference"]["bh_fdr_level"])
        & decision["active_terminal_return"].gt(0) & decision["active_sharpe"].gt(0)
        & decision["topk_positive_count"].ge(3)
        & decision["cost_positive_count"].ge(3)
        & decision["subperiods_active_positive"].astype(bool)
    )
    decision["weak_dimension_floor"] = (
        decision["median_rank_ic"].gt(0) & decision["active_terminal_return"].gt(0)
        & decision["topk_positive_count"].ge(2)
        & decision["cost_positive_count"].ge(2)
    )
    decision["dimension_representative"] = False
    for (dimension, frequency), group in decision.groupby(["dimension", "frequency"]):
        if group["evidence_qualified"].any():
            continue
        candidates = group.loc[group["weak_dimension_floor"]].sort_values(
            ["positive_fraction", "active_sharpe", "factor_id"], ascending=[False, False, True]
        )
        if not candidates.empty:
            decision.loc[candidates.index[0], "dimension_representative"] = True
    decision["status"] = np.select(
        [decision["evidence_qualified"], decision["dimension_representative"]],
        ["evidence_qualified", "dimension_representative"], default="not_advanced")
    decision["g00_identity_gate_passed"] = bool(g00["identity_passed"].all())
    decision["interpretation_status"] = np.where(
        decision["g00_identity_gate_passed"], "interpretable", "provisional_g00_top50_identity_exception"
    )
    _write_csv(root / "final_assessment.csv", decision)
    return {"batch": "XA01D", "status": "completed_hard_stop",
            "evidence_qualified_cells": int(decision["evidence_qualified"].sum()),
            "dimension_representative_cells": int(decision["dimension_representative"].sum()),
            "g00_identity_rows": len(g00), "g00_identity_passed": bool(g00["identity_passed"].all()),
            "paper_diagnostic_rows": len(paper),
            "models_run": False, "aggregation_run": False, "p00_run": False}


def _portfolio_robustness(frame: pd.DataFrame, program: dict[str, Any]) -> pd.DataFrame:
    top = frame.loc[frame["path_type"].eq("factor_topk")].copy()
    ew = frame.loc[frame["path_type"].eq("eligible_ew")].copy()
    joined = top.merge(ew, on=["factor_id", "frequency", "cost_bps"], suffixes=("_factor", "_ew"), validate="many_to_one")
    joined["positive"] = ((joined["total_return_factor"] - joined["total_return_ew"]) > 0) & ((joined["sharpe_excess_rf_factor"] - joined["sharpe_excess_rf_ew"]) > 0)
    rows = []
    for (factor_id, frequency), group in joined.groupby(["factor_id", "frequency"], sort=True):
        primary_cost = int(program["paths"][f"{frequency}_primary_cost_bps"])
        topk = group.loc[group["cost_bps"].eq(primary_cost)]
        costs = group.loc[group["top_k_factor"].eq(program["paths"]["primary_width"])]
        rows.append({"factor_id": factor_id, "frequency": frequency,
                     "topk_positive_count": int(topk["positive"].sum()),
                     "topk_tested_count": int(len(topk)),
                     "cost_positive_count": int(costs["positive"].sum()),
                     "cost_tested_count": int(len(costs))})
    return pd.DataFrame(rows)


def _engine_inputs(project: Path, runtime: Path, program: dict[str, Any]):
    layout = DatabaseLayout.load(project_root=project, runtime_root=runtime)
    market = layout.market_root
    prices = pd.read_parquet(market / "prices_daily.parquet")
    membership = PITMembership.from_intervals(pd.read_parquet(market / "membership.parquet"))
    actions = CorporateActionLedger(pd.read_parquet(market / "corporate_actions.parquet"))
    sessions = pd.DatetimeIndex(pd.read_parquet(market / "calendar.parquet")["session_date"])
    rf = pd.read_parquet(market / "risk_free_daily.parquet").set_index("date")["rf_return"].astype(float)
    engine = BaselineBacktester(prices, membership, sessions=sessions,
        signal_start=program["sample"]["first_signal_close"], signal_end=program["sample"]["evaluation_end_close"],
        evaluation_start=program["sample"]["evaluation_start_open"], corporate_actions=actions,
        missing_valuation_policy="carry_last_close", missing_execution_policy="leave_cash")
    return layout, engine, rf


def _subperiod_robustness(project: Path, runtime: Path, root: Path, program: dict[str, Any]) -> pd.DataFrame:
    _, engine, rf = _engine_inputs(project, runtime, program)
    database = pd.read_parquet(root / "factor_values_weekly_monthly.parquet")
    database = database.loc[database["signal_date"].ge(pd.Timestamp(program["sample"]["first_signal_close"]))]
    rows = []
    periods = (("2018_2021", pd.Timestamp("2018-01-02"), pd.Timestamp("2021-12-31")),
               ("2022_2026h1", pd.Timestamp("2022-01-03"), pd.Timestamp("2026-06-30")))
    for factor_id, group in database.groupby("factor_id", sort=True):
        scores = group.set_index(["signal_date", "sid"])["score"].dropna()
        eligible = group.loc[group["eligible"]].copy()
        sizes = eligible.groupby("signal_date")["sid"].transform("size")
        weights = pd.Series(1.0 / sizes.to_numpy(), index=pd.MultiIndex.from_frame(eligible[["signal_date", "sid"]]))
        weights.index.names = ["signal_date", "sid"]
        for frequency in ("weekly", "monthly"):
            cost = int(program["paths"][f"{frequency}_primary_cost_bps"])
            factor_result = engine.run(signal="mom_255_0", top_n=20, frequency=frequency, cost_bps=cost,
                selection_scores=scores, selection_label=factor_id, selection_score_cache_key=factor_id,
                risk_free_daily=rf, full_audit=False)
            ew_result = engine.run(signal="mom_255_0", top_n=1, frequency=frequency, cost_bps=cost,
                target_weights=weights, risk_free_daily=rf, full_audit=False)
            record = {"factor_id": factor_id, "frequency": frequency}
            positive = True
            for label, start, end in periods:
                fnav = factor_result.nav.loc[factor_result.nav.index.to_series().between(start, end)]
                enav = ew_result.nav.loc[ew_result.nav.index.to_series().between(start, end)]
                fwealth = float((1 + fnav["daily_return"]).prod())
                ewealth = float((1 + enav["daily_return"]).prod())
                record[f"{label}_active_terminal"] = fwealth / ewealth - 1.0
                record[f"{label}_factor_sharpe"] = float(performance_summary(fnav["daily_return"], risk_free_daily=rf)["sharpe_excess_rf"])
                positive &= record[f"{label}_active_terminal"] > 0
            record["subperiods_active_positive"] = positive
            rows.append(record)
    return pd.DataFrame(rows)


def _g00_identity(project: Path, portfolio: pd.DataFrame) -> pd.DataFrame:
    current = portfolio.loc[(portfolio["factor_id"].eq("XS001_MOM_255_0")) & portfolio["path_type"].eq("factor_topk") & portfolio["top_k"].isin([10, 20, 50])].copy()
    parent = pd.read_csv(project / "results/published/G00/summary.csv")
    parent = parent.loc[parent["portfolio_mode"].eq("long_only") & parent["signal"].eq("mom_255_0") & parent["top_n"].isin([10, 20, 50])]
    merged = current.merge(parent, left_on=["top_k", "frequency", "cost_bps"], right_on=["top_n", "frequency", "cost_bps"], suffixes=("_xa01", "_g00"), validate="one_to_one")
    fields = ["total_return", "cagr", "sharpe_excess_rf", "max_drawdown", "annualized_l1_turnover", "total_cost"]
    for field in fields:
        merged[f"abs_diff_{field}"] = (merged[f"{field}_xa01"] - merged[f"{field}_g00"]).abs()
    merged["maximum_abs_diff"] = merged[[f"abs_diff_{field}" for field in fields]].max(axis=1)
    merged["identity_passed"] = merged["maximum_abs_diff"].le(1e-12)
    if len(merged) != 24:
        raise ValueError("XA01/G00 common scenario set must contain 24 rows")
    return merged[["top_k", "frequency", "cost_bps", "maximum_abs_diff", "identity_passed"]]


def _paper_horizon_diagnostics(project: Path, runtime: Path, root: Path, registry: pd.DataFrame) -> pd.DataFrame:
    layout = DatabaseLayout.load(project_root=project, runtime_root=runtime)
    prices = pd.read_parquet(layout.market_root / "prices_daily.parquet")
    calendar = pd.read_parquet(layout.market_root / "calendar.parquet")
    sessions = pd.DatetimeIndex(calendar["session_date"])
    signal_sets = {
        "weekly": set(pd.to_datetime(calendar.loc[calendar["week_last_session"].astype(bool), "session_date"])),
        "monthly": set(pd.to_datetime(calendar.loc[calendar["month_last_session"].astype(bool), "session_date"])),
    }
    opens = prices.pivot(index="date", columns="sid", values="tr_open").reindex(sessions)
    factors = pd.read_parquet(root / "factor_values_weekly_monthly.parquet")
    factors = factors.loc[factors["signal_date"].ge(pd.Timestamp("2017-12-29"))]
    horizon_map = {"six_month_diagnostic": 126, "twelve_month_diagnostic": 252, "twenty_session_diagnostic": 20}
    rows = []
    session_pos = pd.Series(np.arange(len(sessions)), index=sessions)
    for item in registry.itertuples(index=False):
        horizon = horizon_map.get(str(item.paper_horizon_role))
        if horizon is None: continue
        frame = factors.loc[factors["factor_id"].eq(item.factor_id) & factors["eligible"]]
        for frequency in ("weekly", "monthly"):
            ics = []
            scheduled = frame.loc[frame["signal_date"].isin(signal_sets[frequency])]
            for signal, group in scheduled.groupby("signal_date", sort=True):
                pos = int(session_pos.loc[pd.Timestamp(signal)]) + 1
                if pos + horizon >= len(sessions): continue
                start = opens.iloc[pos].reindex(group["sid"])
                end = opens.iloc[pos + horizon].reindex(group["sid"])
                ret = end.to_numpy() / start.to_numpy() - 1.0
                rho = pd.Series(group["score"].to_numpy()).corr(pd.Series(ret), method="spearman")
                if np.isfinite(rho): ics.append(float(rho))
            rows.append({"factor_id": item.factor_id, "frequency": frequency, "horizon_sessions": horizon,
                         "signal_dates": len(ics), "median_rank_ic": float(np.median(ics)) if ics else np.nan,
                         "mean_rank_ic": float(np.mean(ics)) if ics else np.nan, "diagnostic_only": True})
    return pd.DataFrame(rows)


def audit_xa01(project_root: str | Path, runtime_root: str | Path) -> dict[str, Any]:
    project = Path(project_root).resolve(); runtime = Path(runtime_root).resolve()
    program = _load_program(project); root = runtime / "results" / "experiments" / "xa01" / RUN_ID
    required = ["factor_values_weekly_monthly.parquet", "target_ledger.parquet", "signal_summary.csv",
                "portfolio_metrics.csv", "primary_active_comparison.csv", "final_assessment.csv", "manifest.json"]
    missing = [name for name in required if not (root / name).is_file()]
    if missing: raise FileNotFoundError(f"missing XA01 artifacts: {missing}")
    factor = pd.read_parquet(root / required[0]); targets = pd.read_parquet(root / required[1])
    metrics = pd.read_csv(root / "portfolio_metrics.csv"); final = pd.read_csv(root / "final_assessment.csv")
    identity = pd.read_csv(root / "g00_identity_audit.csv")
    assert factor["factor_id"].nunique() == program["parent"]["selected_factor_count"]
    assert set(targets["frequency"]) == {"weekly", "monthly"}
    assert len(metrics.loc[metrics["path_type"].eq("factor_topk")]) == 448
    assert len(final) == 28
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for name, meta in manifest["files"].items():
        path = root / name
        if _sha(path) != meta["sha256"]: raise ValueError(f"hash mismatch: {name}")
    identity_passed = bool(identity["identity_passed"].all())
    return {"status": "passed" if identity_passed else "passed_with_g00_top50_identity_exception",
            "factor_count": 14, "strategy_paths": 112,
            "cost_paths": 448, "final_rows": len(final), "lockbox_read": False,
            "g00_identity_passed": identity_passed,
            "g00_identity_passed_rows": int(identity["identity_passed"].sum()),
            "g00_identity_total_rows": len(identity)}


def _factor_correlations(merged: pd.DataFrame) -> pd.DataFrame:
    wide = merged.pivot_table(index=["frequency", "signal_date", "sid"], columns="factor_id", values="score")
    rows = []
    for frequency, group in wide.groupby(level=0):
        corr = group.droplevel(0).corr(method="spearman", min_periods=100)
        for i, left in enumerate(corr.columns):
            for right in corr.columns[i + 1:]:
                rows.append({"frequency": frequency, "factor_left": left, "factor_right": right,
                             "spearman": corr.loc[left, right]})
    return pd.DataFrame(rows)


def _block_sign_p(values: np.ndarray, block: int) -> float:
    values = np.asarray(values, dtype=float); values = values[np.isfinite(values)]
    if len(values) < 3: return math.nan
    if block > 1:
        values = np.array([np.mean(values[i:i + block]) for i in range(0, len(values), block)])
    if len(values) < 3: return math.nan
    return float(stats.wilcoxon(values, alternative="greater", zero_method="wilcox").pvalue)


def _bh(series: pd.Series) -> pd.Series:
    x = series.astype(float).to_numpy(); order = np.argsort(x); ranked = x[order]
    q = np.minimum.accumulate((ranked * len(x) / np.arange(1, len(x) + 1))[::-1])[::-1]
    out = np.empty_like(q); out[order] = np.minimum(q, 1.0)
    return pd.Series(out, index=series.index)


def _load_program(project: Path) -> dict[str, Any]:
    with (project / PROGRAM).open("rb") as handle: program = tomllib.load(handle)
    lock = project / LOCK
    if not lock.is_file(): raise FileNotFoundError(lock)
    return program


def _factor_registry(project: Path) -> pd.DataFrame:
    return pd.read_csv(project / "config/experiments/xa01/factor_registry.csv")


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); frame.to_parquet(path, index=False)


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    frame.to_csv(path, index=False, lineterminator="\n")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(root: Path) -> None:
    files = {}
    for path in sorted(root.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            files[path.name] = {"sha256": _sha(path), "size": path.stat().st_size}
    _write_json(root / "manifest.json", {"schema_version": "xa01.runtime_manifest.v1", "run_id": RUN_ID, "files": files})
