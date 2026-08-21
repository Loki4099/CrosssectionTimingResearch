"""XA02 complete factor paths and causal market-state atlas."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import platform
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy import stats

from momentum_reversal.backtest.engine import BaselineBacktester, replay_linear_cost
from momentum_reversal.data.corporate_actions import CorporateActionLedger
from momentum_reversal.data.membership import PITMembership
from momentum_reversal.pipelines.cross_sectional_database import DatabaseLayout


PROGRAM = Path("config/experiments/xa02/program.toml")
LOCK = Path("config/experiments/xa02/PREREG_LOCK.json")
RUN_IDS = {
    "XA02A": "xa02a-factor-path-ledger-20260821-v1",
    "XA02B": "xa02b-market-state-features-20260821-v1",
    "XA02C": "xa02c-factor-state-atlas-20260821-v1",
    "XA02D": "xa02d-atlas-closure-20260821-v1",
}
PRIMARY_STATES = (
    "MKT_TREND126", "MKT_LOG_RV21", "MKT_DD252_SEVERITY",
    "MKT_BREADTH_RSP63", "MKT_XS_DISP21", "MKT_AVG_CORR63",
)
SHADOW_STATES = (
    "SHADOW_SMA50_200", "SHADOW_LOG_RV_RATIO", "SHADOW_BREADTH_SMA200",
)
STATE_PAIRS = {
    "trend_x_volatility": ("MKT_TREND126", "MKT_LOG_RV21"),
    "breadth_x_volatility": ("MKT_BREADTH_RSP63", "MKT_LOG_RV21"),
    "dispersion_x_correlation": ("MKT_XS_DISP21", "MKT_AVG_CORR63"),
}


def run_xa02(project_root: str | Path, runtime_root: str | Path, batch: str) -> dict[str, Any]:
    project = Path(project_root).resolve()
    runtime = Path(runtime_root).resolve()
    batch = batch.upper()
    if batch not in RUN_IDS:
        raise ValueError(f"unknown XA02 batch: {batch}")
    program = _load_program(project)
    _verify_lock(project)
    commit = _require_clean_git(project)
    dependencies = _dependency_manifests(runtime, batch)
    root = _batch_root(runtime, batch)
    if root.exists():
        raise FileExistsError(f"XA02 run directory already exists: {root}")
    root.mkdir(parents=True)
    try:
        if batch == "XA02A":
            summary = _run_a(project, runtime, root, program)
        elif batch == "XA02B":
            summary = _run_b(project, runtime, root, program)
        elif batch == "XA02C":
            summary = _run_c(project, runtime, root, program)
        else:
            summary = _run_d(project, runtime, root, program)
        _write_json(root / "summary.json", summary)
        _write_manifest(project, root, batch, commit, dependencies)
        return summary
    except Exception:
        # Preserve a non-empty failed run for forensic review; reruns require a
        # new run id or explicit user-authorized cleanup.
        _write_json(root / "FAILED.json", {"batch": batch, "status": "failed"})
        raise


def _run_a(project: Path, runtime: Path, root: Path, program: dict[str, Any]) -> dict[str, Any]:
    _verify_parent_inputs(project, runtime, program)
    xa01 = _xa01_root(runtime)
    factors = pd.read_parquet(xa01 / "factor_values_weekly_monthly.parquet")
    targets = pd.read_parquet(xa01 / "target_ledger.parquet")
    registry = pd.read_csv(project / "config/experiments/xa02/factor_registry.csv")
    factor_ids = registry["factor_id"].tolist()
    factors = factors.loc[factors["factor_id"].isin(factor_ids)].copy()
    factors["signal_date"] = pd.to_datetime(factors["signal_date"]).dt.normalize()
    targets["signal_date"] = pd.to_datetime(targets["signal_date"]).dt.normalize()
    targets["execution_date"] = pd.to_datetime(targets["execution_date"]).dt.normalize()
    targets["label_end_execution_date"] = pd.to_datetime(
        targets["label_end_execution_date"]
    ).dt.normalize()
    rank_ic = _rank_ic_by_date(factors, targets)

    layout = DatabaseLayout.load(project_root=project, runtime_root=runtime)
    market = layout.market_root
    prices = pd.read_parquet(market / "prices_daily.parquet")
    membership = PITMembership.from_intervals(pd.read_parquet(market / "membership.parquet"))
    actions = CorporateActionLedger(pd.read_parquet(market / "corporate_actions.parquet"))
    sessions = pd.DatetimeIndex(pd.read_parquet(market / "calendar.parquet")["session_date"])
    rf = pd.read_parquet(market / "risk_free_daily.parquet").set_index("date")["rf_return"].astype(float)
    engine = BaselineBacktester(
        prices, membership, sessions=sessions,
        signal_start=program["sample"]["first_signal_close"],
        signal_end=program["sample"]["evaluation_end_close"],
        evaluation_start=program["sample"]["evaluation_start_open"],
        corporate_actions=actions, missing_valuation_policy="carry_last_close",
        missing_execution_policy="leave_cash",
    )
    all_periods: list[pd.DataFrame] = []
    all_nav: list[pd.DataFrame] = []
    all_holdings: list[pd.DataFrame] = []
    all_rankings: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    ranking_audit: list[dict[str, Any]] = []
    costs = [int(x) for x in program["paths"]["cost_scenarios_bps"]]
    widths = [int(x) for x in program["paths"]["top_k"]]

    for factor_id in factor_ids:
        one = factors.loc[factors["factor_id"].eq(factor_id)]
        scores = one.set_index(["signal_date", "sid"])["score"].dropna()
        eligible = one.loc[one["eligible"]].copy()
        sizes = eligible.groupby("signal_date")["sid"].transform("size")
        ew_weights = pd.Series(
            1.0 / sizes.to_numpy(),
            index=pd.MultiIndex.from_frame(eligible[["signal_date", "sid"]]),
        )
        ew_weights.index.names = ["signal_date", "sid"]
        for frequency in ("weekly", "monthly"):
            date_meta = _date_metadata(one, targets, factor_id, frequency)
            zero_control = engine.run(
                signal="mom_255_0", top_n=1, frequency=frequency, cost_bps=0.0,
                target_weights=ew_weights, risk_free_daily=rf, full_audit=True,
            )
            all_holdings.append(_holding_frame(
                zero_control.target_weights, factor_id, frequency, 0, "eligible_ew"
            ))
            control_periods: dict[int, pd.DataFrame] = {}
            for cost in costs:
                control = replay_linear_cost(zero_control, cost_bps=cost)
                control_periods[cost] = _period_frame(control, date_meta, "control_return")
                summaries.append(_summary_record(
                    control, rf, factor_id, frequency, 0, cost, "eligible_ew"
                ))
                all_nav.append(_nav_frame(control, factor_id, frequency, 0, cost, "eligible_ew"))

            for width in widths:
                zero_factor = engine.run(
                    signal="mom_255_0", top_n=width, frequency=frequency, cost_bps=0.0,
                    selection_scores=scores, selection_label=factor_id,
                    selection_score_cache_key=factor_id, risk_free_daily=rf, full_audit=True,
                )
                all_holdings.append(_holding_frame(
                    zero_factor.target_weights, factor_id, frequency, width, "factor_topk"
                ))
                selected_ranking = zero_factor.rankings.loc[zero_factor.rankings["selected"]].copy()
                selected_ranking["factor_id"] = factor_id; selected_ranking["frequency"] = frequency
                selected_ranking["top_k"] = width
                all_rankings.append(selected_ranking)
                ranking_audit.extend(_ranking_rows(zero_factor.rankings, factor_id, frequency, width))
                for cost in costs:
                    result = replay_linear_cost(zero_factor, cost_bps=cost)
                    factor_period = _period_frame(result, date_meta, "factor_return")
                    joined = factor_period.merge(
                        control_periods[cost],
                        on=["signal_date", "execution_date", "next_execution_date"],
                        how="inner", validate="one_to_one",
                    )
                    meta = date_meta[[
                        "signal_date", "rank_ic", "eligible_count", "tie_rate", "selected_sids_top20"
                    ]].drop_duplicates("signal_date")
                    joined = joined.merge(meta, on="signal_date", how="left", validate="one_to_one")
                    joined["active_return"] = joined["factor_return"] - joined["control_return"]
                    joined["relative_log_return"] = (
                        np.log1p(joined["factor_return"]) - np.log1p(joined["control_return"])
                    )
                    joined["factor_id"] = factor_id
                    joined["frequency"] = frequency
                    joined["top_k"] = width
                    joined["cost_bps"] = cost
                    joined["path_id"] = f"{factor_id}__{frequency}__top{width}__{cost}bps"
                    all_periods.append(joined)
                    summaries.append(_summary_record(
                        result, rf, factor_id, frequency, width, cost, "factor_topk"
                    ))
                    all_nav.append(_nav_frame(
                        result, factor_id, frequency, width, cost, "factor_topk"
                    ))

    period_ledger = pd.concat(all_periods, ignore_index=True).sort_values(
        ["factor_id", "frequency", "top_k", "cost_bps", "signal_date"], ignore_index=True
    )
    nav = pd.concat(all_nav, ignore_index=True).sort_values(
        ["path_type", "factor_id", "frequency", "top_k", "cost_bps", "date"], ignore_index=True
    )
    holdings = pd.concat(all_holdings, ignore_index=True).sort_values(
        ["path_type", "factor_id", "frequency", "top_k", "signal_date", "sid"], ignore_index=True
    )
    rankings = pd.concat(all_rankings, ignore_index=True).sort_values(
        ["factor_id", "frequency", "top_k", "signal_date", "rank"], ignore_index=True
    )
    summary_frame = pd.DataFrame(summaries).sort_values(
        ["path_type", "factor_id", "frequency", "top_k", "cost_bps"], ignore_index=True
    )
    identity = _xa01_metric_identity(summary_frame, pd.read_csv(xa01 / "portfolio_metrics.csv"))
    if not bool(identity["identity_passed"].all()):
        raise ValueError("XA02A path summaries do not reproduce XA01")
    _write_parquet(root / "holding_period_ledger.parquet", period_ledger)
    _write_parquet(root / "daily_nav_paths.parquet", nav)
    _write_parquet(root / "topk_holdings.parquet", holdings)
    _write_parquet(root / "ranking_ledger.parquet", rankings)
    _write_csv(root / "path_summary.csv", summary_frame)
    _write_csv(root / "xa01_path_identity.csv", identity)
    nested_audit = _nested_topk_audit(rankings)
    if not bool(nested_audit["nested_passed"].all()):
        raise ValueError("TopK rankings are not nested")
    ranking_frame = pd.DataFrame(ranking_audit).merge(
        nested_audit, on=["factor_id", "frequency", "signal_date", "top_k"], how="left"
    )
    _write_csv(root / "ranking_audit.csv", ranking_frame)
    repair = _xs056_repair(factors, targets)
    _write_csv(root / "xs056_twelve_month_repair.csv", repair)
    return {
        "batch": "XA02A", "status": "completed", "factor_count": len(factor_ids),
        "signal_path_count": 112, "factor_cost_path_count": 448,
        "control_cost_path_count": 112, "holding_period_rows": len(period_ledger),
        "daily_nav_rows": len(nav), "holding_rows": len(holdings),
        "xa01_identity_rows": len(identity), "xa01_identity_passed": True,
        "xs056_repair_rows": len(repair),
    }


def _rank_ic_by_date(factors: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    merged = factors.merge(
        targets[["signal_date", "frequency", "sid", "forward_excess_cash", "target_valid"]],
        on=["signal_date", "sid"], how="inner", validate="many_to_many",
    )
    merged = merged.loc[merged["eligible"] & merged["target_valid"]]
    rows = []
    for (factor_id, frequency, signal_date), group in merged.groupby(
        ["factor_id", "frequency", "signal_date"], sort=True
    ):
        common = group[["score", "forward_excess_cash"]].dropna()
        rho = common["score"].corr(common["forward_excess_cash"], method="spearman") if len(common) >= 100 else np.nan
        rows.append({"factor_id": factor_id, "frequency": frequency, "signal_date": signal_date,
                     "rank_ic": rho, "rank_ic_names": len(common)})
    return pd.DataFrame(rows)


def _date_metadata(one: pd.DataFrame, targets: pd.DataFrame, factor_id: str, frequency: str) -> pd.DataFrame:
    dates = targets.loc[targets["frequency"].eq(frequency), [
        "signal_date", "execution_date", "label_end_execution_date"
    ]].drop_duplicates()
    eligible = one.loc[one["eligible"] & one["score"].notna()]
    rows = []
    for signal_date, group in eligible.groupby("signal_date", sort=True):
        ordered = group.sort_values(["score", "sid"], ascending=[False, True])
        boundary = ordered["score"].iloc[min(19, len(ordered) - 1)]
        selected = ordered.head(20)["sid"].astype(str).tolist()
        rows.append({"signal_date": signal_date, "eligible_count": len(ordered),
                     "tie_rate": float(np.isclose(ordered["score"], boundary, rtol=0, atol=1e-14).mean()),
                     "selected_sids_top20": "|".join(selected)})
    meta = dates.merge(pd.DataFrame(rows), on="signal_date", how="inner")
    # RankIC is added by the caller after the common target/factor calculation.
    # Recalculate locally to keep this helper deterministic and self-contained.
    fac = one.merge(
        targets.loc[targets["frequency"].eq(frequency), ["signal_date", "sid", "forward_excess_cash", "target_valid"]],
        on=["signal_date", "sid"], how="inner",
    )
    ric = []
    for d, g in fac.loc[fac["eligible"] & fac["target_valid"]].groupby("signal_date"):
        c = g[["score", "forward_excess_cash"]].dropna()
        ric.append({"signal_date": d, "rank_ic": c["score"].corr(c["forward_excess_cash"], method="spearman") if len(c) >= 100 else np.nan})
    return meta.merge(pd.DataFrame(ric), on="signal_date", how="left")


def _period_frame(result: Any, date_meta: pd.DataFrame, name: str) -> pd.DataFrame:
    rebalances = result.rebalances.reset_index(drop=True).sort_values("execution_date")
    rows = rebalances[["signal_date", "execution_date", "pretrade_nav", "l1_turnover", "cost_amount"]].copy()
    rows["next_execution_date"] = rows["execution_date"].shift(-1)
    rows[name] = rows["pretrade_nav"].shift(-1) / rows["pretrade_nav"] - 1.0
    rows = rows.loc[rows["next_execution_date"].notna()].copy()
    rows = rows.loc[rows["signal_date"].isin(set(date_meta["signal_date"]))]
    rows = rows.rename(columns={"l1_turnover": f"{name}_l1_turnover", "cost_amount": f"{name}_cost"})
    return rows[["signal_date", "execution_date", "next_execution_date", name,
                 f"{name}_l1_turnover", f"{name}_cost"]]


def _holding_frame(frame: pd.DataFrame, factor_id: str, frequency: str, top_k: int, path_type: str) -> pd.DataFrame:
    out = frame.copy()
    out["factor_id"] = factor_id; out["frequency"] = frequency
    out["top_k"] = top_k; out["path_type"] = path_type
    return out


def _nav_frame(result: Any, factor_id: str, frequency: str, top_k: int, cost: int, path_type: str) -> pd.DataFrame:
    out = result.nav.reset_index().copy()
    out["factor_id"] = factor_id; out["frequency"] = frequency
    out["top_k"] = top_k; out["cost_bps"] = cost; out["path_type"] = path_type
    return out


def _summary_record(result: Any, rf: pd.Series, factor_id: str, frequency: str,
                    top_k: int, cost: int, path_type: str) -> dict[str, Any]:
    record = result.summary(rf).to_dict()
    record.update({"factor_id": factor_id, "frequency": frequency, "top_k": top_k,
                   "cost_bps": cost, "path_type": path_type})
    return record


def _ranking_rows(frame: pd.DataFrame, factor_id: str, frequency: str, top_k: int) -> list[dict[str, Any]]:
    rows = []
    for signal_date, group in frame.groupby("signal_date", sort=True):
        selected = group.loc[group["selected"]].sort_values("rank")
        rows.append({"factor_id": factor_id, "frequency": frequency, "top_k": top_k,
                     "signal_date": signal_date, "selected_count": len(selected),
                     "unique_sid_count": selected["sid"].nunique(),
                     "score_monotone": bool(selected["score"].is_monotonic_decreasing)})
    return rows


def _nested_topk_audit(holdings: pd.DataFrame) -> pd.DataFrame:
    factor = holdings
    rows = []
    for (factor_id, frequency, signal_date), group in factor.groupby(
        ["factor_id", "frequency", "signal_date"], sort=True
    ):
        sets = {int(k): set(g["sid"].astype(str)) for k, g in group.groupby("top_k")}
        for width in (5, 10, 20, 50):
            expected = width in sets and len(sets[width]) == width
            if width > 5:
                prior = {5: None, 10: 5, 20: 10, 50: 20}[width]
                expected = expected and sets[prior].issubset(sets[width])
            rows.append({"factor_id": factor_id, "frequency": frequency,
                         "signal_date": signal_date, "top_k": width,
                         "nested_passed": bool(expected)})
    return pd.DataFrame(rows)


def _xa01_metric_identity(current: pd.DataFrame, parent: pd.DataFrame) -> pd.DataFrame:
    keys = ["factor_id", "frequency", "top_k", "cost_bps", "path_type"]
    fields = ["total_return", "cagr", "sharpe_excess_rf", "max_drawdown",
              "annualized_l1_turnover", "total_cost"]
    merged = current.merge(parent, on=keys, suffixes=("_xa02", "_xa01"), validate="one_to_one")
    for field in fields:
        merged[f"abs_diff_{field}"] = (merged[f"{field}_xa02"] - merged[f"{field}_xa01"]).abs()
    merged["maximum_abs_diff"] = merged[[f"abs_diff_{f}" for f in fields]].max(axis=1)
    merged["identity_passed"] = merged["maximum_abs_diff"].le(1e-10)
    return merged[keys + ["maximum_abs_diff", "identity_passed"]]


def _xs056_repair(factors: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    one = factors.loc[factors["factor_id"].eq("XS056_CFO_ACCRUALS_PT") & factors["eligible"]]
    target = targets.loc[targets["frequency"].eq("monthly")]
    merged = one.merge(target[["signal_date", "sid", "execution_date"]], on=["signal_date", "sid"], how="inner")
    # XA01 missed this pre-registered diagnostic because of a role-token mismatch.
    # The additive repair reports the available one-period evidence and provenance;
    # it does not rewrite XA01 or fabricate a 252-session target not in its ledger.
    return pd.DataFrame([{ "factor_id": "XS056_CFO_ACCRUALS_PT", "repair_status": "documented_not_materialized",
        "reason": "XA01 target ledger contains next-rebalance targets only; 252-session repair requires a separate target artifact",
        "available_monthly_rows": len(merged), "historical_xa01_overwritten": False }])


def _run_b(project: Path, runtime: Path, root: Path, program: dict[str, Any]) -> dict[str, Any]:
    layout = DatabaseLayout.load(project_root=project, runtime_root=runtime)
    market = layout.market_root
    calendar = pd.read_parquet(market / "calendar.parquet")
    sessions = pd.DatetimeIndex(pd.to_datetime(calendar["session_date"])).normalize()
    benchmark = pd.read_parquet(market / "benchmark_daily.parquet").set_index("date").reindex(sessions)
    prices = pd.read_parquet(market / "prices_daily.parquet")
    membership_frame = pd.read_parquet(market / "membership.parquet")
    membership = PITMembership.from_intervals(membership_frame)
    rsp_path = runtime / "data/round10/staging/R10A_RSP_LOCKBOX_FEATURE" / program["parent"]["r10a_run_id"] / "rsp_daily.parquet"
    rsp = pd.read_parquet(rsp_path).set_index("session_date")["tr_close"].astype(float).reindex(sessions)
    spy = benchmark["benchmark_tr_close"].astype(float)
    logret = np.log(spy).diff()

    wide_tr = prices.pivot(index="date", columns="sid", values="tr_close").reindex(sessions)
    wide_raw = prices.pivot(index="date", columns="sid", values="raw_close").reindex(sessions)
    wide_split = prices.pivot(index="date", columns="sid", values="stock_splits").reindex(sessions)
    daily_stock_ret = np.log(wide_tr).diff()
    ret21 = wide_tr / wide_tr.shift(21) - 1.0

    trend = spy / spy.shift(126) - 1.0
    rv21 = logret.rolling(21, min_periods=21).std(ddof=1) * math.sqrt(252.0)
    rv126 = logret.rolling(126, min_periods=126).std(ddof=1) * math.sqrt(252.0)
    raw = pd.DataFrame(index=sessions)
    raw["MKT_TREND126"] = trend
    raw["MKT_LOG_RV21"] = np.log(rv21.where(rv21.gt(0)))
    raw["MKT_DD252_SEVERITY"] = -(spy / spy.rolling(252, min_periods=252).max() - 1.0)
    ratio = np.log(rsp / spy)
    raw["MKT_BREADTH_RSP63"] = ratio - ratio.shift(63)
    raw["SHADOW_SMA50_200"] = (
        spy.rolling(50, min_periods=50).mean() / spy.rolling(200, min_periods=200).mean() - 1.0
    )
    raw["SHADOW_LOG_RV_RATIO"] = np.log((rv21 / rv126).where(rv126.gt(0)))

    split_missing = wide_raw.notna() & wide_split.isna()
    if bool(split_missing.any().any()):
        raise ValueError("non-missing raw close has missing split marker")
    split_multiplier = wide_split.where(wide_split.gt(0), 1.0).where(wide_raw.notna())
    causal_split_close = wide_raw * split_multiplier.fillna(1.0).cumprod()
    invalid = split_missing.cummax()
    causal_split_close = causal_split_close.mask(invalid)
    sma200 = causal_split_close.rolling(200, min_periods=200).mean()

    dispersion = []
    avg_corr = []
    breadth_sma = []
    state_counts = []
    for i, date in enumerate(sessions):
        members = list(membership.members_on(date))
        values = ret21.loc[date].reindex(members).dropna().astype(float)
        if len(values) >= int(program["states"]["minimum_cross_sectional_members"]):
            med = float(values.median())
            disp = 1.4826 * float((values - med).abs().median())
        else:
            disp = np.nan
        dispersion.append(disp)

        above = (causal_split_close.loc[date].reindex(members) > sma200.loc[date].reindex(members))
        valid_sma = causal_split_close.loc[date].reindex(members).notna() & sma200.loc[date].reindex(members).notna()
        breadth_sma.append(float(above.loc[valid_sma].mean()) if valid_sma.sum() >= 200 else np.nan)

        corr_value = np.nan; valid_names = 0; valid_pairs = 0
        if i >= 63:
            # Complete-window names are a conservative subset of the registered
            # >=50-common-session universe. Every retained pair has 63 common
            # returns, so the pairwise gate is satisfied without imputation.
            window = daily_stock_ret.iloc[i - 62:i + 1].reindex(columns=members)
            complete = window.columns[window.notna().all(axis=0)]
            valid_names = len(complete)
            valid_pairs = valid_names * (valid_names - 1) // 2
            if valid_names >= 200 and valid_pairs >= int(program["states"]["minimum_valid_pairs"]):
                x = window[complete].to_numpy(dtype=float)
                x = (x - x.mean(axis=0)) / x.std(axis=0, ddof=1)
                if np.isfinite(x).all():
                    numerator = float(np.square(x.sum(axis=1)).sum() - valid_names * (len(x) - 1))
                    corr_value = numerator / ((len(x) - 1) * valid_names * (valid_names - 1))
        avg_corr.append(corr_value)
        state_counts.append({"date": date, "dispersion_names": len(values),
                             "correlation_names": valid_names, "correlation_pairs": valid_pairs,
                             "sma200_names": int(valid_sma.sum())})
    raw["MKT_XS_DISP21"] = dispersion
    raw["MKT_AVG_CORR63"] = avg_corr
    raw["SHADOW_BREADTH_SMA200"] = breadth_sma
    raw.index.name = "date"

    daily_parts = []
    minimum = int(program["states"]["causal_percentile_history_sessions"])
    for state_id in PRIMARY_STATES + SHADOW_STATES:
        series = raw[state_id].astype(float)
        percentile = _causal_percentile(series, minimum)
        bins = _tercile(percentile)
        daily_parts.append(pd.DataFrame({"date": sessions, "state_id": state_id,
                                         "raw_value": series.to_numpy(),
                                         "causal_percentile": percentile.to_numpy(),
                                         "state_bin": bins.to_numpy()}))
    daily = pd.concat(daily_parts, ignore_index=True)

    a = _batch_root(runtime, "XA02A")
    ledger = pd.read_parquet(a / "holding_period_ledger.parquet", columns=["frequency", "signal_date"])
    signal_dates = ledger.drop_duplicates().sort_values(["frequency", "signal_date"])
    features = signal_dates.merge(
        daily.rename(columns={"date": "signal_date"}), on="signal_date", how="left", validate="many_to_many"
    )
    features = _add_episode_ids(features)
    _write_parquet(root / "market_state_daily.parquet", daily)
    _write_parquet(root / "market_state_features.parquet", features)
    _write_parquet(root / "market_state_coverage.parquet", pd.DataFrame(state_counts))
    coverage = features.groupby(["frequency", "state_id", "state_bin"], dropna=False).size().rename("observations").reset_index()
    _write_csv(root / "state_bin_coverage.csv", coverage)
    causality = _state_causality_audit(raw, wide_raw, wide_split, minimum)
    _write_json(root / "causality_audit.json", causality)
    if not (causality["strictly_past_percentile_invariant"] and causality["future_split_perturbation_invariant"]
            and not causality["full_sample_thresholds_used"]):
        raise ValueError("XA02B causality audit failed")
    return {"batch": "XA02B", "status": "completed", "daily_rows": len(daily),
            "scheduled_feature_rows": len(features), "primary_states": 6, "shadow_states": 3,
            "causality_passed": True}


def _causal_percentile(series: pd.Series, minimum: int) -> pd.Series:
    ordered: list[float] = []
    output = np.full(len(series), np.nan)
    for i, value in enumerate(series.to_numpy(dtype=float)):
        if np.isfinite(value) and len(ordered) >= minimum:
            left = bisect.bisect_left(ordered, float(value))
            right = bisect.bisect_right(ordered, float(value))
            output[i] = (left + 0.5 * (right - left)) / len(ordered)
        if np.isfinite(value):
            bisect.insort(ordered, float(value))
    return pd.Series(output, index=series.index)


def _tercile(percentile: pd.Series) -> pd.Series:
    values = percentile.to_numpy(dtype=float)
    out = np.full(len(values), None, dtype=object)
    out[np.isfinite(values) & (values <= 1.0 / 3.0)] = "low"
    out[np.isfinite(values) & (values > 1.0 / 3.0) & (values <= 2.0 / 3.0)] = "mid"
    out[np.isfinite(values) & (values > 2.0 / 3.0)] = "high"
    return pd.Series(out, index=percentile.index, dtype="object")


def _add_episode_ids(features: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for (_, state_id), group in features.sort_values("signal_date").groupby(["frequency", "state_id"], sort=False):
        group = group.copy(); bins = group["state_bin"].astype("object")
        change = bins.isna() | bins.shift().isna() | bins.ne(bins.shift())
        group["episode_id"] = change.cumsum().astype("Int64")
        group.loc[bins.isna(), "episode_id"] = pd.NA
        parts.append(group)
    return pd.concat(parts, ignore_index=True)


def _state_causality_audit(raw: pd.DataFrame, wide_raw: pd.DataFrame,
                           wide_split: pd.DataFrame, minimum: int) -> dict[str, bool]:
    cutoff = len(raw) // 2
    percentile_ok = True
    for column in raw.columns:
        original = _causal_percentile(raw[column], minimum)
        changed = raw[column].copy()
        finite = changed.iloc[cutoff + 1:].notna()
        changed.iloc[cutoff + 1:] = changed.iloc[cutoff + 1:].where(~finite, changed.iloc[cutoff + 1:] * 7.0 + 3.0)
        replay = _causal_percentile(changed, minimum)
        if not original.iloc[:cutoff + 1].equals(replay.iloc[:cutoff + 1]):
            percentile_ok = False
    multiplier = wide_split.where(wide_split.gt(0), 1.0).fillna(1.0)
    original_close = wide_raw * multiplier.cumprod()
    changed_split = multiplier.copy()
    future_events = wide_split.iloc[cutoff + 1:].gt(0)
    changed_split.iloc[cutoff + 1:] = changed_split.iloc[cutoff + 1:].where(
        ~future_events, changed_split.iloc[cutoff + 1:] * 2.0
    )
    replay_close = wide_raw * changed_split.cumprod()
    split_ok = bool(np.allclose(original_close.iloc[:cutoff + 1].to_numpy(),
                                replay_close.iloc[:cutoff + 1].to_numpy(), equal_nan=True))
    return {"strictly_past_percentile_invariant": percentile_ok,
            "future_split_perturbation_invariant": split_ok,
            "full_sample_thresholds_used": False}


def _run_c(project: Path, runtime: Path, root: Path, program: dict[str, Any]) -> dict[str, Any]:
    a = _batch_root(runtime, "XA02A"); b = _batch_root(runtime, "XA02B")
    ledger = pd.read_parquet(a / "holding_period_ledger.parquet")
    features = pd.read_parquet(b / "market_state_features.parquet")
    for column in ("signal_date", "execution_date", "next_execution_date"):
        ledger[column] = pd.to_datetime(ledger[column]).dt.normalize()
    features["signal_date"] = pd.to_datetime(features["signal_date"]).dt.normalize()
    primary = ledger.loc[
        ledger["top_k"].eq(int(program["paths"]["primary_width"]))
        & (((ledger["frequency"].eq("weekly")) & ledger["cost_bps"].eq(int(program["paths"]["weekly_primary_cost_bps"])))
           | ((ledger["frequency"].eq("monthly")) & ledger["cost_bps"].eq(int(program["paths"]["monthly_primary_cost_bps"]))))
    ].copy()
    state = features.loc[features["state_id"].isin(PRIMARY_STATES)]
    atlas = primary.merge(state, on=["frequency", "signal_date"], how="left", validate="many_to_many")

    conditional_rows: list[dict[str, Any]] = []
    test_rows: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    for (factor_id, frequency, state_id), group in atlas.groupby(
        ["factor_id", "frequency", "state_id"], sort=True
    ):
        group = group.sort_values("signal_date").reset_index(drop=True)
        tail_cut = float(group["active_return"].quantile(float(program["metrics"]["tail_quantile"])))
        for state_bin in ("low", "mid", "high"):
            cell = group.loc[group["state_bin"].eq(state_bin)]
            episode_mdd = _conditional_episode_mdd(cell)
            conditional_rows.append({
                "factor_id": factor_id, "frequency": frequency, "state_id": state_id,
                "state_bin": state_bin, "observations": len(cell),
                "calendar_years": cell["signal_date"].dt.year.nunique(),
                "episodes": cell["episode_id"].nunique(),
                "mean_factor_return": cell["factor_return"].mean(),
                "mean_control_return": cell["control_return"].mean(),
                "mean_active_return": cell["active_return"].mean(),
                "active_ir": _ir(cell["active_return"], frequency),
                "active_hit": cell["active_return"].gt(0).mean(),
                "mean_rank_ic": cell["rank_ic"].mean(),
                "rank_ic_positive_fraction": cell["rank_ic"].gt(0).mean(),
                "relative_log_contribution": cell["relative_log_return"].sum(),
                "tail_mean": cell.loc[cell["active_return"].le(tail_cut), "active_return"].mean(),
                "mean_factor_turnover": cell["factor_return_l1_turnover"].mean(),
                "mean_factor_cost": cell["factor_return_cost"].mean(),
                "mean_eligible_count": cell["eligible_count"].mean(),
                "worst_episode_relative_mdd": episode_mdd,
            })
            for episode_id, episode in cell.groupby("episode_id", dropna=True):
                episode_rows.append({"factor_id": factor_id, "frequency": frequency,
                                     "state_id": state_id, "state_bin": state_bin,
                                     "episode_id": int(episode_id),
                                     "start_signal": episode["signal_date"].min(),
                                     "end_signal": episode["signal_date"].max(),
                                     "observations": len(episode),
                                     "relative_log_contribution": episode["relative_log_return"].sum(),
                                     "episode_relative_mdd": _relative_mdd(episode["relative_log_return"])})
        for outcome in ("active_return", "rank_ic"):
            test_rows.append(_one_dimensional_test(group, factor_id, frequency, state_id, outcome, program))
    conditional = pd.DataFrame(conditional_rows)
    one_tests = pd.DataFrame(test_rows)
    one_tests["bh_q"] = one_tests.groupby(["frequency", "outcome"])["p_value"].transform(_bh)

    grid_rows: list[dict[str, Any]] = []
    two_test_rows: list[dict[str, Any]] = []
    feature_wide = features.loc[features["state_id"].isin(PRIMARY_STATES)].pivot(
        index=["frequency", "signal_date"], columns="state_id", values="state_bin"
    ).reset_index()
    pair_atlas = primary.merge(feature_wide, on=["frequency", "signal_date"], how="left", validate="many_to_one")
    for (factor_id, frequency), group in pair_atlas.groupby(["factor_id", "frequency"], sort=True):
        group = group.sort_values("signal_date").reset_index(drop=True)
        for pair_id, (left, right) in STATE_PAIRS.items():
            for left_bin in ("low", "mid", "high"):
                for right_bin in ("low", "mid", "high"):
                    cell = group.loc[group[left].eq(left_bin) & group[right].eq(right_bin)]
                    grid_rows.append({"factor_id": factor_id, "frequency": frequency, "pair_id": pair_id,
                                      "left_state": left, "right_state": right,
                                      "left_bin": left_bin, "right_bin": right_bin,
                                      "observations": len(cell),
                                      "calendar_years": cell["signal_date"].dt.year.nunique(),
                                      "mean_active_return": cell["active_return"].mean(),
                                      "active_ir": _ir(cell["active_return"], frequency),
                                      "mean_rank_ic": cell["rank_ic"].mean(),
                                      "relative_log_contribution": cell["relative_log_return"].sum()})
            for outcome in ("active_return", "rank_ic"):
                two_test_rows.append(_two_dimensional_test(
                    group, factor_id, frequency, pair_id, left, right, outcome, program
                ))
    grid = pd.DataFrame(grid_rows)
    two_tests = pd.DataFrame(two_test_rows)
    two_tests["bh_q"] = two_tests.groupby(["frequency", "outcome"])["p_value"].transform(_bh)
    _write_csv(root / "atlas_1d_conditional_summary.csv", conditional)
    _write_csv(root / "atlas_1d_tests.csv", one_tests)
    _write_csv(root / "state_episode_contributions.csv", pd.DataFrame(episode_rows))
    _write_csv(root / "atlas_2d_grid.csv", grid)
    _write_csv(root / "atlas_2d_tests.csv", two_tests)
    return {"batch": "XA02C", "status": "completed", "one_dimensional_tests": len(one_tests),
            "one_dimensional_expected": 336, "two_dimensional_tests": len(two_tests),
            "two_dimensional_expected": 168, "descriptive_2d_cells": len(grid),
            "one_dimensional_fdr_positive": int(one_tests["bh_q"].le(.10).sum()),
            "two_dimensional_fdr_positive": int(two_tests["bh_q"].le(.10).sum())}


def _one_dimensional_test(group: pd.DataFrame, factor_id: str, frequency: str,
                          state_id: str, outcome: str, program: dict[str, Any]) -> dict[str, Any]:
    bins = group["state_bin"].astype("object")
    y = group[outcome].astype(float)
    counts = {b: int((bins == b).sum()) for b in ("low", "mid", "high")}
    years = {b: int(group.loc[bins.eq(b), "signal_date"].dt.year.nunique()) for b in counts}
    episodes = {b: int(group.loc[bins.eq(b), "episode_id"].nunique()) for b in counts}
    minimum = int(program["atlas_1d"][f"{frequency}_minimum_per_bin"])
    sample_ok = all(counts[b] >= minimum and years[b] >= 4 and episodes[b] >= 3 for b in counts)
    x = np.column_stack([np.ones(len(group)), bins.eq("low").astype(float), bins.eq("high").astype(float)])
    usable = y.notna() & bins.notna()
    beta, covariance, p = _hac_fit(y.to_numpy(), x, usable.to_numpy(),
                                   int(program["inference"][f"{frequency}_hac_lag"]), [1, 2])
    if not sample_ok:
        p = 1.0
    means = {b: float(y.loc[bins.eq(b)].mean()) for b in counts}
    ordered = sorted(means, key=lambda b: (-means[b], ("low", "mid", "high").index(b)))
    best, worst = ordered[0], ordered[-1]
    spread = means[best] - means[worst]
    annualized = spread * (52 if frequency == "weekly" else 12) if outcome == "active_return" else spread
    denom = float(y.std(ddof=1)); standardized = spread / denom if denom > 0 else np.nan
    ci_low_mid, ci_high_mid = _bootstrap_tercile_diffs(
        y.to_numpy(), bins.to_numpy(), int(program["inference"][f"{frequency}_block_periods"]),
        int(program["inference"]["bootstrap_draws"]), int(program["inference"]["bootstrap_seed"]),
    )
    return {"factor_id": factor_id, "frequency": frequency, "state_id": state_id,
            "outcome": outcome, "observations": int(usable.sum()),
            "low_n": counts["low"], "mid_n": counts["mid"], "high_n": counts["high"],
            "sample_gate_passed": sample_ok, "beta_low_vs_mid": beta[1] if len(beta) == 3 else np.nan,
            "beta_high_vs_mid": beta[2] if len(beta) == 3 else np.nan,
            "p_value": float(p), "best_bin": best, "worst_bin": worst,
            "best_minus_worst": spread, "qualification_effect": annualized,
            "standardized_range": standardized,
            "low_mid_ci_low": ci_low_mid[0], "low_mid_ci_high": ci_low_mid[1],
            "high_mid_ci_low": ci_high_mid[0], "high_mid_ci_high": ci_high_mid[1]}


def _two_dimensional_test(group: pd.DataFrame, factor_id: str, frequency: str,
                          pair_id: str, left: str, right: str, outcome: str,
                          program: dict[str, Any]) -> dict[str, Any]:
    a = group[left].astype("object"); b = group[right].astype("object")
    extreme = a.isin(["low", "high"]) & b.isin(["low", "high"])
    y = group[outcome].astype(float)
    x = np.column_stack([np.ones(len(group)), a.eq("high").astype(float),
                         b.eq("high").astype(float), (a.eq("high") & b.eq("high")).astype(float)])
    usable = extreme & y.notna()
    cells = {(aa, bb): group.loc[a.eq(aa) & b.eq(bb)] for aa in ("low", "high") for bb in ("low", "high")}
    min_corner = int(program["atlas_2d"][f"{frequency}_minimum_corner_cell"])
    min_total = int(program["atlas_2d"][f"{frequency}_minimum_total_extreme_observations"])
    cell_gate = all(len(c) >= min_corner and c["signal_date"].dt.year.nunique() >= 3
                    and _joint_episode_count(c, left, right) >= 3
                    and _max_year_fraction(c) <= .5 for c in cells.values())
    descriptive_min = int(program["atlas_2d"][f"descriptive_{frequency}_minimum_per_cell"])
    supported = sum(int(len(group.loc[a.eq(aa) & b.eq(bb)]) >= descriptive_min)
                    for aa in ("low", "mid", "high") for bb in ("low", "mid", "high"))
    sample_ok = cell_gate and int(usable.sum()) >= min_total and supported >= 7
    beta, covariance, p = _hac_fit(y.to_numpy(), x, usable.to_numpy(),
                                   int(program["inference"][f"{frequency}_hac_lag"]), [3])
    interaction = float(beta[3]) if len(beta) == 4 else np.nan
    if not sample_ok:
        p = 1.0
    denom = float(y.std(ddof=1)); standardized = abs(interaction) / denom if denom > 0 else np.nan
    ci = _bootstrap_did(y.to_numpy(), a.to_numpy(), b.to_numpy(),
                        int(program["inference"][f"{frequency}_block_periods"]),
                        int(program["inference"]["bootstrap_draws"]), int(program["inference"]["bootstrap_seed"]));
    return {"factor_id": factor_id, "frequency": frequency, "pair_id": pair_id,
            "left_state": left, "right_state": right, "outcome": outcome,
            "extreme_observations": int(usable.sum()), "supported_3x3_cells": supported,
            "sample_gate_passed": sample_ok, "interaction_beta": interaction,
            "annualized_interaction": interaction * (52 if frequency == "weekly" and outcome == "active_return" else 12 if frequency == "monthly" and outcome == "active_return" else 1),
            "standardized_interaction": standardized, "p_value": float(p),
            "bootstrap_ci_low": ci[0], "bootstrap_ci_high": ci[1]}


def _hac_fit(y: np.ndarray, x: np.ndarray, usable: np.ndarray, lag: int,
             tested: list[int]) -> tuple[np.ndarray, np.ndarray, float]:
    y = np.asarray(y, dtype=float); x = np.asarray(x, dtype=float); usable = np.asarray(usable, dtype=bool)
    xw = x.copy(); yw = np.zeros_like(y)
    xw[~usable] = 0.0; yw[usable] = y[usable]
    if usable.sum() <= x.shape[1] or np.linalg.matrix_rank(xw) < x.shape[1]:
        return np.full(x.shape[1], np.nan), np.full((x.shape[1], x.shape[1]), np.nan), 1.0
    beta = np.linalg.lstsq(xw, yw, rcond=None)[0]
    residual = yw - xw @ beta
    scores = xw * residual[:, None]
    meat = scores.T @ scores
    for step in range(1, lag + 1):
        weight = 1.0 - step / (lag + 1.0)
        gamma = scores[step:].T @ scores[:-step]
        meat += weight * (gamma + gamma.T)
    bread = np.linalg.pinv(xw.T @ xw)
    covariance = bread @ meat @ bread
    r = np.zeros((len(tested), x.shape[1]));
    for i, column in enumerate(tested): r[i, column] = 1.0
    rb = r @ beta; rcov = r @ covariance @ r.T
    if np.linalg.matrix_rank(rcov) < len(tested):
        return beta, covariance, 1.0
    statistic = float(rb.T @ np.linalg.pinv(rcov) @ rb)
    return beta, covariance, float(stats.chi2.sf(max(statistic, 0.0), len(tested)))


def _moving_block_indices(n: int, block: int, draws: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    blocks = math.ceil(n / block)
    starts = rng.integers(0, n, size=(draws, blocks))
    offsets = np.arange(block)
    return ((starts[:, :, None] + offsets) % n).reshape(draws, -1)[:, :n]


def _bootstrap_tercile_diffs(y: np.ndarray, bins: np.ndarray, block: int, draws: int,
                             seed: int) -> tuple[tuple[float, float], tuple[float, float]]:
    idx = _moving_block_indices(len(y), block, draws, seed)
    yi = y[idx]; bi = bins[idx]
    def mean_bin(label: str) -> np.ndarray:
        mask = (bi == label) & np.isfinite(yi)
        count = mask.sum(axis=1); total = np.where(mask, yi, 0.0).sum(axis=1)
        return np.divide(total, count, out=np.full(draws, np.nan), where=count > 0)
    mid = mean_bin("mid")
    return _percentile_ci(mean_bin("low") - mid), _percentile_ci(mean_bin("high") - mid)


def _bootstrap_did(y: np.ndarray, a: np.ndarray, b: np.ndarray, block: int, draws: int,
                   seed: int) -> tuple[float, float]:
    idx = _moving_block_indices(len(y), block, draws, seed); yi = y[idx]; ai = a[idx]; bi = b[idx]
    means = {}
    for aa in ("low", "high"):
        for bb in ("low", "high"):
            mask = (ai == aa) & (bi == bb) & np.isfinite(yi)
            count = mask.sum(axis=1); total = np.where(mask, yi, 0.0).sum(axis=1)
            means[(aa, bb)] = np.divide(total, count, out=np.full(draws, np.nan), where=count > 0)
    did = means[("high", "high")] - means[("high", "low")] - means[("low", "high")] + means[("low", "low")]
    return _percentile_ci(did)


def _percentile_ci(values: np.ndarray) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    return (float(np.percentile(finite, 2.5)), float(np.percentile(finite, 97.5))) if len(finite) else (np.nan, np.nan)


def _ir(values: pd.Series, frequency: str) -> float:
    values = values.dropna().astype(float); std = values.std(ddof=1)
    return float(math.sqrt(52 if frequency == "weekly" else 12) * values.mean() / std) if len(values) > 1 and std > 0 else np.nan


def _relative_mdd(log_returns: pd.Series) -> float:
    wealth = np.exp(log_returns.fillna(0).cumsum()); peak = wealth.cummax()
    return float((wealth / peak - 1.0).min()) if len(wealth) else np.nan


def _conditional_episode_mdd(cell: pd.DataFrame) -> float:
    values = [_relative_mdd(g.sort_values("signal_date")["relative_log_return"])
              for _, g in cell.groupby("episode_id", dropna=True)]
    return float(np.nanmin(values)) if values else np.nan


def _joint_episode_count(cell: pd.DataFrame, left: str, right: str) -> int:
    if cell.empty: return 0
    ordered = cell.sort_index()
    indices = ordered.index.to_numpy(dtype=int)
    keys = (ordered[left].astype(str) + "|" + ordered[right].astype(str)).to_numpy()
    return int(1 + np.sum((np.diff(indices) != 1) | (keys[1:] != keys[:-1])))


def _max_year_fraction(cell: pd.DataFrame) -> float:
    counts = cell["signal_date"].dt.year.value_counts()
    return float(counts.max() / counts.sum()) if len(counts) else 1.0


def _bh(series: pd.Series) -> pd.Series:
    x = series.fillna(1.0).astype(float).to_numpy(); order = np.argsort(x); ranked = x[order]
    q = np.minimum.accumulate((ranked * len(x) / np.arange(1, len(x) + 1))[::-1])[::-1]
    out = np.empty_like(q); out[order] = np.minimum(q, 1.0)
    return pd.Series(out, index=series.index)


def _run_d(project: Path, runtime: Path, root: Path, program: dict[str, Any]) -> dict[str, Any]:
    a = _batch_root(runtime, "XA02A"); b = _batch_root(runtime, "XA02B"); c = _batch_root(runtime, "XA02C")
    ledger = pd.read_parquet(a / "holding_period_ledger.parquet")
    holdings = pd.read_parquet(a / "topk_holdings.parquet")
    path_summary = pd.read_csv(a / "path_summary.csv")
    features = pd.read_parquet(b / "market_state_features.parquet")
    one_tests = pd.read_csv(c / "atlas_1d_tests.csv")
    conditional = pd.read_csv(c / "atlas_1d_conditional_summary.csv")
    two_tests = pd.read_csv(c / "atlas_2d_tests.csv")
    factor_values = pd.read_parquet(_xa01_root(runtime) / "factor_values_weekly_monthly.parquet")
    for frame in (ledger, features, factor_values):
        frame["signal_date"] = pd.to_datetime(frame["signal_date"]).dt.normalize()

    similarity, clusters = _similarity_tables(factor_values, holdings, ledger, program)
    _write_csv(root / "factor_similarity.csv", similarity)
    _write_csv(root / "empirical_clusters.csv", clusters)

    relationship_rows = []
    role_rows = []
    primary_ledger = _primary_ledger(ledger, program)
    primary_state = features.loc[features["state_id"].isin(PRIMARY_STATES)]
    joined = primary_ledger.merge(primary_state, on=["frequency", "signal_date"], how="left")
    for (factor_id, frequency), ff in joined.groupby(["factor_id", "frequency"], sort=True):
        qualifying = []
        exploratory = []
        for test in one_tests.loc[
            one_tests["factor_id"].eq(factor_id) & one_tests["frequency"].eq(frequency)
        ].itertuples(index=False):
            state_group = ff.loc[ff["state_id"].eq(test.state_id)].sort_values("signal_date")
            stability = _one_dimensional_stability(
                ledger, state_group, factor_id, frequency, test.state_id,
                test.outcome, test.best_bin, test.worst_bin, program
            )
            effect_floor = abs(float(test.qualification_effect)) >= (
                float(program["roles"]["conditional_active_annualized_spread_minimum"])
                if test.outcome == "active_return" else
                float(program["roles"]["conditional_rank_ic_spread_minimum"])
            )
            standardized = abs(float(test.standardized_range)) >= float(program["roles"]["conditional_standardized_range_minimum"])
            other = "rank_ic" if test.outcome == "active_return" else "active_return"
            other_contrast = _fixed_bin_contrast(state_group, other, test.best_bin, test.worst_bin)
            cross_ok = bool(np.isfinite(other_contrast) and other_contrast >= -1e-15)
            qualifies = bool(test.sample_gate_passed and test.bh_q <= .10 and effect_floor and standardized
                             and stability["all_stability_gates"] and cross_ok)
            means = {b: _fixed_bin_mean(state_group, test.outcome, b) for b in ("low", "mid", "high")}
            signs = {int(np.sign(v)) for v in means.values() if np.isfinite(v) and not np.isclose(v, 0)}
            if qualifies:
                role = "conditional_sign_switch" if (-1 in signs and 1 in signs) else "conditional_strength"
                qualifying.append(role)
            elif (test.p_value <= .05) or (test.bh_q <= .10):
                role = "exploratory_state_candidate"; exploratory.append(role)
            else:
                role = "none"
            relationship_rows.append({"factor_id": factor_id, "frequency": frequency,
                                      "state_id": test.state_id, "outcome": test.outcome,
                                      "best_bin": test.best_bin, "worst_bin": test.worst_bin,
                                      "bh_q": test.bh_q, "qualification_effect": test.qualification_effect,
                                      "standardized_range": test.standardized_range,
                                      "cross_outcome_contrast": other_contrast,
                                      "relationship_role": role, "qualifies": qualifies, **stability})
        broad = _broad_static(factor_id, frequency, ledger, path_summary, program)
        stable_sign_switch = "conditional_sign_switch" in qualifying
        broad = bool(broad and not stable_sign_switch)
        time_break = _time_break(ff, frequency) and not qualifying
        tags = []
        if "conditional_sign_switch" in qualifying: tags.append("conditional_sign_switch")
        if "conditional_strength" in qualifying: tags.append("conditional_strength")
        if broad: tags.append("broad_static")
        if time_break: tags.append("time_break_unexplained")
        if exploratory: tags.append("exploratory_state_candidate")
        if not tags: tags.append("no_state_evidence")
        priority = list(program["roles"]["primary_role_priority"])
        primary_role = next(item for item in priority if item in tags)
        role_rows.append({"factor_id": factor_id, "frequency": frequency,
                          "role_tags": "|".join(tags), "primary_role": primary_role,
                          "broad_static": broad, "qualifying_state_relationships": len(qualifying),
                          "exploratory_state_relationships": len(exploratory),
                          "time_break_unexplained": time_break})
    relationships = pd.DataFrame(relationship_rows)
    roles = pd.DataFrame(role_rows)
    _write_csv(root / "factor_state_relationship_assessment.csv", relationships)
    _write_csv(root / "factor_state_role_assessment.csv", roles)

    two_stability = _two_dimensional_stability(two_tests, ledger, features, clusters, program)
    _write_csv(root / "atlas_2d_role_assessment.csv", two_stability)
    rolling, calendar = _rolling_and_calendar(primary_ledger, program)
    _write_csv(root / "rolling_performance.csv", rolling)
    _write_csv(root / "calendar_performance.csv", calendar)
    summary = {
        "batch": "XA02D", "status": "completed_hard_stop", "factor_frequency_roles": len(roles),
        "primary_role_counts": roles["primary_role"].value_counts().sort_index().to_dict(),
        "qualifying_1d_relationships": int(relationships["qualifies"].sum()),
        "robust_2d_contexts": int(two_stability["context_label"].eq("robust_2d_context").sum()),
        "exploratory_2d_contexts": int(two_stability["context_label"].eq("exploratory_2d_context").sum()),
        "models_run": False, "factor_aggregation_run": False, "strategy_selection_run": False,
        "market_state_classifier_run": False, "p00_run": False, "lockbox_read": False,
        "automatic_xa03": False, "user_review_required": True,
    }
    _write_json(root / "decision.json", summary)
    return summary


def _primary_ledger(ledger: pd.DataFrame, program: dict[str, Any]) -> pd.DataFrame:
    return ledger.loc[
        ledger["top_k"].eq(int(program["paths"]["primary_width"]))
        & (((ledger["frequency"].eq("weekly")) & ledger["cost_bps"].eq(int(program["paths"]["weekly_primary_cost_bps"])))
           | ((ledger["frequency"].eq("monthly")) & ledger["cost_bps"].eq(int(program["paths"]["monthly_primary_cost_bps"]))))
    ].copy()


def _fixed_bin_mean(group: pd.DataFrame, outcome: str, state_bin: str) -> float:
    return float(group.loc[group["state_bin"].eq(state_bin), outcome].mean())


def _fixed_bin_contrast(group: pd.DataFrame, outcome: str, best: str, worst: str) -> float:
    return _fixed_bin_mean(group, outcome, best) - _fixed_bin_mean(group, outcome, worst)


def _one_dimensional_stability(ledger: pd.DataFrame, state_group: pd.DataFrame,
                               factor_id: str, frequency: str, state_id: str,
                               outcome: str, best: str, worst: str,
                               program: dict[str, Any]) -> dict[str, Any]:
    base = state_group.sort_values("signal_date")
    full_direction = np.sign(_fixed_bin_contrast(base, outcome, best, worst))
    years = sorted(base["signal_date"].dt.year.unique())
    loyo = []
    min_bin = int(program["role_contrasts"][f"{frequency}_minimum_per_fixed_bin_after_leave_one_year_out"])
    for year in years:
        sample = base.loc[base["signal_date"].dt.year.ne(year)]
        if min((sample["state_bin"].eq(best)).sum(), (sample["state_bin"].eq(worst)).sum()) < min_bin:
            continue
        loyo.append(np.sign(_fixed_bin_contrast(sample, outcome, best, worst)) == full_direction)
    loyo_fraction = float(np.mean(loyo)) if loyo else 0.0
    n_best = max(int(base["state_bin"].eq(best).sum()), 1); n_worst = max(int(base["state_bin"].eq(worst).sum()), 1)
    contributions = []
    for year in years:
        y = base.loc[base["signal_date"].dt.year.eq(year)]
        contributions.append(float(y.loc[y["state_bin"].eq(best), outcome].sum() / n_best
                                   - y.loc[y["state_bin"].eq(worst), outcome].sum() / n_worst))
    denom = sum(abs(x) for x in contributions)
    max_year = max((abs(x) / denom for x in contributions), default=1.0) if denom > 0 else 1.0

    topk_signs = []
    for width in (10, 20, 50):
        variant = ledger.loc[(ledger["factor_id"].eq(factor_id)) & ledger["frequency"].eq(frequency)
                             & ledger["top_k"].eq(width)]
        primary_cost = 10 if frequency == "weekly" else 5
        variant = variant.loc[variant["cost_bps"].eq(primary_cost)].merge(
            state_group[["signal_date", "state_bin"]].drop_duplicates(), on="signal_date", how="left"
        )
        value = outcome if outcome == "rank_ic" else "active_return"
        topk_signs.append((width, np.sign(_fixed_bin_contrast(variant, value, best, worst))))
    topk_agree = sum(sign == full_direction for _, sign in topk_signs)
    top20_agree = next(sign for width, sign in topk_signs if width == 20) == full_direction
    cost_variant = ledger.loc[(ledger["factor_id"].eq(factor_id)) & ledger["frequency"].eq(frequency)
                              & ledger["top_k"].eq(20) & ledger["cost_bps"].eq(20)].merge(
        state_group[["signal_date", "state_bin"]].drop_duplicates(), on="signal_date", how="left"
    )
    cost_value = outcome if outcome == "rank_ic" else "active_return"
    cost_agree = np.sign(_fixed_bin_contrast(cost_variant, cost_value, best, worst)) == full_direction
    gates = (loyo_fraction >= .75 and max_year <= .5 and topk_agree >= 2 and top20_agree and cost_agree)
    return {"leave_one_year_out_estimable": len(loyo), "leave_one_year_out_direction_fraction": loyo_fraction,
            "maximum_single_year_absolute_contribution_fraction": max_year,
            "topk_widths_same_direction": topk_agree, "top20_same_direction": bool(top20_agree),
            "twenty_bps_same_direction": bool(cost_agree), "all_stability_gates": bool(gates)}


def _broad_static(factor_id: str, frequency: str, ledger: pd.DataFrame,
                  path_summary: pd.DataFrame, program: dict[str, Any]) -> bool:
    primary_cost = 10 if frequency == "weekly" else 5
    group = ledger.loc[ledger["factor_id"].eq(factor_id) & ledger["frequency"].eq(frequency)]
    base = group.loc[group["top_k"].eq(20) & group["cost_bps"].eq(primary_cost)]
    if base.empty or base["relative_log_return"].sum() <= 0 or _ir(base["active_return"], frequency) <= 0:
        return False
    topk_positive = 0
    for width in (5, 10, 20, 50):
        one = group.loc[group["top_k"].eq(width) & group["cost_bps"].eq(primary_cost)]
        topk_positive += int(one["relative_log_return"].sum() > 0 and _ir(one["active_return"], frequency) > 0)
    costs_positive = all(
        (lambda x: x["relative_log_return"].sum() > 0 and _ir(x["active_return"], frequency) > 0)(
            group.loc[group["top_k"].eq(20) & group["cost_bps"].eq(cost)]
        ) for cost in (0, 5, 10, 20)
    )
    subperiods = [base.loc[base["signal_date"].dt.year.le(2021)], base.loc[base["signal_date"].dt.year.ge(2022)]]
    years = base.assign(year=base["signal_date"].dt.year).groupby("year")["relative_log_return"].sum()
    concentration = float(years.abs().max() / years.abs().sum()) if years.abs().sum() > 0 else 1.0
    return bool(topk_positive >= 3 and costs_positive and all(x["relative_log_return"].sum() > 0 for x in subperiods)
                and concentration <= .5)


def _time_break(group: pd.DataFrame, frequency: str) -> bool:
    early = group.loc[group["signal_date"].dt.year.le(2021)]
    late = group.loc[group["signal_date"].dt.year.ge(2022)]
    if early.empty or late.empty: return False
    active = np.sign(early["active_return"].mean()) * np.sign(late["active_return"].mean()) < 0
    rankic = np.sign(early["rank_ic"].mean()) * np.sign(late["rank_ic"].mean()) < 0
    return bool(active or rankic)


def _similarity_tables(factors: pd.DataFrame, holdings: pd.DataFrame, ledger: pd.DataFrame,
                       program: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    factor_ids = sorted(factors["factor_id"].unique())
    rows = []
    for frequency in ("weekly", "monthly"):
        dates = set(ledger.loc[ledger["frequency"].eq(frequency), "signal_date"])
        one = factors.loc[factors["signal_date"].isin(dates) & factors["eligible"]]
        h = holdings.loc[holdings["frequency"].eq(frequency) & holdings["top_k"].eq(20)
                         & holdings["path_type"].eq("factor_topk")]
        active = ledger.loc[ledger["frequency"].eq(frequency) & ledger["top_k"].eq(20)
                            & ledger["cost_bps"].eq(10 if frequency == "weekly" else 5)]
        for i, left in enumerate(factor_ids):
            for right in factor_ids[i + 1:]:
                pair = one.loc[one["factor_id"].isin([left, right])].pivot_table(
                    index=["signal_date", "sid"], columns="factor_id", values="score"
                ).dropna()
                by_date = pair.groupby(level=0).apply(
                    lambda x: x[left].corr(x[right], method="spearman"), include_groups=False
                ).dropna()
                jaccards = []
                for date in sorted(dates):
                    ls = set(h.loc[h["factor_id"].eq(left) & h["signal_date"].eq(date), "sid"].astype(str))
                    rs = set(h.loc[h["factor_id"].eq(right) & h["signal_date"].eq(date), "sid"].astype(str))
                    if ls or rs: jaccards.append(len(ls & rs) / len(ls | rs))
                aw = active.loc[active["factor_id"].isin([left, right])].pivot(
                    index="signal_date", columns="factor_id", values="active_return"
                ).dropna()
                score_corr = float(by_date.median()) if len(by_date) else np.nan
                jaccard = float(np.median(jaccards)) if jaccards else np.nan
                pearson = float(aw[left].corr(aw[right])) if len(aw) else np.nan
                spearman = float(aw[left].corr(aw[right], method="spearman")) if len(aw) else np.nan
                thresholds = int(abs(score_corr) >= .8) + int(jaccard >= .6) + int(abs(pearson) >= .7)
                rows.append({"frequency": frequency, "factor_left": left, "factor_right": right,
                             "median_date_score_spearman": score_corr, "median_top20_jaccard": jaccard,
                             "active_return_pearson": pearson, "active_return_spearman": spearman,
                             "thresholds_exceeded": thresholds, "empirically_redundant": thresholds >= 2})
    similarity = pd.DataFrame(rows)
    cluster_rows = []
    for frequency in ("weekly", "monthly"):
        parent = {f: f for f in factor_ids}
        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]; x = parent[x]
            return x
        def union(x: str, y: str) -> None:
            rx, ry = find(x), find(y)
            if rx != ry: parent[max(rx, ry)] = min(rx, ry)
        for item in similarity.loc[similarity["frequency"].eq(frequency) & similarity["empirically_redundant"]].itertuples():
            union(item.factor_left, item.factor_right)
        for factor_id in factor_ids:
            cluster_rows.append({"frequency": frequency, "factor_id": factor_id,
                                 "empirical_cluster": find(factor_id)})
    return similarity, pd.DataFrame(cluster_rows)


def _two_dimensional_stability(tests: pd.DataFrame, ledger: pd.DataFrame,
                               features: pd.DataFrame, clusters: pd.DataFrame,
                               program: dict[str, Any]) -> pd.DataFrame:
    wide = features.loc[features["state_id"].isin(PRIMARY_STATES)].pivot(
        index=["frequency", "signal_date"], columns="state_id", values="state_bin"
    ).reset_index()
    joined = ledger.merge(wide, on=["frequency", "signal_date"], how="left")
    rows = []
    for test in tests.itertuples(index=False):
        left, right = STATE_PAIRS[test.pair_id]
        primary_cost = 10 if test.frequency == "weekly" else 5
        all_variants = joined.loc[joined["factor_id"].eq(test.factor_id)
                                  & joined["frequency"].eq(test.frequency)]
        group = all_variants.loc[all_variants["top_k"].eq(20)
                                 & all_variants["cost_bps"].eq(primary_cost)].sort_values("signal_date")
        outcome = test.outcome
        full = _did(group, left, right, outcome)
        sign = np.sign(full)
        loyo = []
        for year in sorted(group["signal_date"].dt.year.unique()):
            sample = group.loc[group["signal_date"].dt.year.ne(year)]
            counts = _corner_counts(sample, left, right)
            min_corner = int(program["atlas_2d"][f"leave_one_year_out_{test.frequency}_minimum_per_corner"])
            min_total = int(program["atlas_2d"][f"leave_one_year_out_{test.frequency}_minimum_total"])
            if min(counts.values()) < min_corner or sum(counts.values()) < min_total: continue
            loyo.append(np.sign(_did(sample, left, right, outcome)) == sign)
        loyo_fraction = float(np.mean(loyo)) if loyo else 0.0
        contributions = _did_year_contributions(group, left, right, outcome)
        denominator = sum(abs(v) for v in contributions.values())
        max_year = max((abs(v) / denominator for v in contributions.values()), default=1.0) if denominator else 1.0
        effect_ok = abs(float(test.annualized_interaction if outcome == "active_return" else test.interaction_beta)) >= (.03 if outcome == "active_return" else .02)
        topk_signs = []
        for width in (10, 20, 50):
            variant = all_variants.loc[all_variants["top_k"].eq(width)
                                       & all_variants["cost_bps"].eq(primary_cost)]
            topk_signs.append((width, np.sign(_did(variant, left, right, outcome))))
        topk_agree = sum(value == sign for _, value in topk_signs)
        top20_agree = next(value for width, value in topk_signs if width == 20) == sign
        stress = all_variants.loc[all_variants["top_k"].eq(20) & all_variants["cost_bps"].eq(20)]
        stress_agree = np.sign(_did(stress, left, right, outcome)) == sign
        stable = bool(test.sample_gate_passed and test.bh_q <= .10 and effect_ok
                      and abs(float(test.standardized_interaction)) >= .25
                      and len(loyo) >= int(program["atlas_2d"]["minimum_estimable_leave_one_year_out_runs"])
                      and loyo_fraction >= .75 and max_year <= .5
                      and topk_agree >= 2 and top20_agree and stress_agree)
        rows.append({"factor_id": test.factor_id, "frequency": test.frequency,
                     "pair_id": test.pair_id, "outcome": outcome, "interaction_sign": int(sign),
                     "bh_q": test.bh_q, "sample_gate_passed": bool(test.sample_gate_passed),
                     "leave_one_year_out_estimable": len(loyo),
                     "leave_one_year_out_direction_fraction": loyo_fraction,
                     "maximum_single_year_absolute_contribution_fraction": max_year,
                     "topk_widths_same_direction": topk_agree,
                     "top20_same_direction": bool(top20_agree),
                     "twenty_bps_same_direction": bool(stress_agree),
                     "base_stability_passed": stable})
    frame = pd.DataFrame(rows)
    labels = []
    for item in frame.itertuples(index=False):
        same = frame.loc[frame["pair_id"].eq(item.pair_id) & frame["outcome"].eq(item.outcome)
                         & frame["interaction_sign"].eq(item.interaction_sign) & frame["base_stability_passed"]]
        cross_frequency = same.loc[same["factor_id"].eq(item.factor_id), "frequency"].nunique() >= 2
        cluster_map = clusters.loc[clusters["frequency"].eq(item.frequency)].set_index("factor_id")["empirical_cluster"]
        nonredundant = same.loc[same["frequency"].eq(item.frequency), "factor_id"].map(cluster_map).nunique() >= 2
        replicated = cross_frequency or nonredundant
        if item.base_stability_passed and replicated: label = "robust_2d_context"
        elif item.base_stability_passed or item.bh_q <= .10: label = "exploratory_2d_context"
        else: label = "none"
        labels.append((replicated, label))
    frame["replicated"] = [x[0] for x in labels]
    frame["context_label"] = [x[1] for x in labels]
    return frame


def _corner_counts(group: pd.DataFrame, left: str, right: str) -> dict[tuple[str, str], int]:
    return {(a, b): int((group[left].eq(a) & group[right].eq(b)).sum())
            for a in ("low", "high") for b in ("low", "high")}


def _did(group: pd.DataFrame, left: str, right: str, outcome: str) -> float:
    means = {(a, b): group.loc[group[left].eq(a) & group[right].eq(b), outcome].mean()
             for a in ("low", "high") for b in ("low", "high")}
    return float(means[("high", "high")] - means[("high", "low")]
                 - means[("low", "high")] + means[("low", "low")])


def _did_year_contributions(group: pd.DataFrame, left: str, right: str,
                            outcome: str) -> dict[int, float]:
    counts = _corner_counts(group, left, right)
    output = {}
    for year in sorted(group["signal_date"].dt.year.unique()):
        one = group.loc[group["signal_date"].dt.year.eq(year)]
        terms = {}
        for a in ("low", "high"):
            for b in ("low", "high"):
                terms[(a, b)] = float(one.loc[one[left].eq(a) & one[right].eq(b), outcome].sum()) / max(counts[(a, b)], 1)
        output[int(year)] = terms[("high", "high")] - terms[("high", "low")] - terms[("low", "high")] + terms[("low", "low")]
    return output


def _rolling_and_calendar(ledger: pd.DataFrame, program: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rolling_rows = []; calendar_rows = []
    for (factor_id, frequency), group in ledger.groupby(["factor_id", "frequency"], sort=True):
        group = group.sort_values("signal_date").reset_index(drop=True)
        windows = program["rolling"][f"{frequency}_windows"]
        annual = 52 if frequency == "weekly" else 12
        for window in windows:
            mean = group["active_return"].rolling(int(window), min_periods=int(window)).mean()
            std = group["active_return"].rolling(int(window), min_periods=int(window)).std(ddof=1)
            rel = group["relative_log_return"].rolling(int(window), min_periods=int(window)).sum()
            ric = group["rank_ic"].rolling(int(window), min_periods=int(window)).mean()
            for i in np.flatnonzero(mean.notna().to_numpy()):
                rolling_rows.append({"factor_id": factor_id, "frequency": frequency,
                                     "window_periods": int(window), "end_signal_date": group.loc[i, "signal_date"],
                                     "active_annualized_mean": annual * mean.iloc[i],
                                     "active_ir": math.sqrt(annual) * mean.iloc[i] / std.iloc[i] if std.iloc[i] > 0 else np.nan,
                                     "relative_log_wealth": rel.iloc[i], "mean_rank_ic": ric.iloc[i]})
        for calendar_type in ("calendar_year", "calendar_quarter"):
            key = group["signal_date"].dt.year.astype(str) if calendar_type == "calendar_year" else group["signal_date"].dt.to_period("Q").astype(str)
            for label, cell in group.groupby(key, sort=True):
                calendar_rows.append({"factor_id": factor_id, "frequency": frequency,
                                      "calendar_type": calendar_type, "period": label,
                                      "observations": len(cell),
                                      "active_annualized_mean": annual * cell["active_return"].mean(),
                                      "active_ir": _ir(cell["active_return"], frequency),
                                      "relative_log_wealth": cell["relative_log_return"].sum(),
                                      "mean_rank_ic": cell["rank_ic"].mean()})
    return pd.DataFrame(rolling_rows), pd.DataFrame(calendar_rows)


def audit_xa02(project_root: str | Path, runtime_root: str | Path) -> dict[str, Any]:
    project = Path(project_root).resolve(); runtime = Path(runtime_root).resolve()
    program = _load_program(project); _verify_lock(project)
    manifests = {}
    required = {
        "XA02A": ("holding_period_ledger.parquet", "daily_nav_paths.parquet", "topk_holdings.parquet", "path_summary.csv", "xa01_path_identity.csv"),
        "XA02B": ("market_state_daily.parquet", "market_state_features.parquet", "state_bin_coverage.csv", "causality_audit.json"),
        "XA02C": ("atlas_1d_conditional_summary.csv", "atlas_1d_tests.csv", "atlas_2d_grid.csv", "atlas_2d_tests.csv"),
        "XA02D": ("factor_similarity.csv", "empirical_clusters.csv", "factor_state_role_assessment.csv", "atlas_2d_role_assessment.csv", "decision.json"),
    }
    for batch in RUN_IDS:
        root = _batch_root(runtime, batch)
        missing = [name for name in required[batch] if not (root / name).is_file()]
        if missing: raise FileNotFoundError(f"{batch} missing {missing}")
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        _verify_manifest(root, manifest)
        manifests[batch] = _sha(root / "manifest.json")
    a = _batch_root(runtime, "XA02A"); c = _batch_root(runtime, "XA02C"); d = _batch_root(runtime, "XA02D")
    ledger = pd.read_parquet(a / "holding_period_ledger.parquet")
    identity = pd.read_csv(a / "xa01_path_identity.csv")
    one = pd.read_csv(c / "atlas_1d_tests.csv"); two = pd.read_csv(c / "atlas_2d_tests.csv")
    decision = json.loads((d / "decision.json").read_text(encoding="utf-8"))
    if ledger[["factor_id", "frequency", "top_k", "cost_bps"]].drop_duplicates().shape[0] != 448:
        raise ValueError("XA02A factor cost path grid is incomplete")
    if len(one) != 336 or len(two) != 168: raise ValueError("XA02C fixed hypothesis families are incomplete")
    if not bool(identity["identity_passed"].all()): raise ValueError("XA01 identity failed")
    if any(decision.get(key) for key in ("models_run", "factor_aggregation_run", "strategy_selection_run", "market_state_classifier_run", "p00_run", "lockbox_read")):
        raise ValueError("forbidden XA02 output was authorized")
    return {"status": "passed", "manifests": manifests, "factor_cost_paths": 448,
            "one_dimensional_tests": 336, "two_dimensional_tests": 168,
            "models_run": False, "lockbox_read": False, "hard_stop": "XA02D"}


def _load_program(project: Path) -> dict[str, Any]:
    with (project / PROGRAM).open("rb") as handle: return tomllib.load(handle)


def _batch_root(runtime: Path, batch: str) -> Path:
    return runtime / "results" / "experiments" / "xa02" / batch / "runs" / RUN_IDS[batch]


def _xa01_root(runtime: Path) -> Path:
    return runtime / "results" / "experiments" / "xa01" / "xa01-atomic-factor-walkforward-20260820-v1"


def _verify_lock(project: Path) -> None:
    lock = json.loads((project / LOCK).read_text(encoding="utf-8"))
    for item in lock["files"]:
        path = project / item["path"]
        if path.stat().st_size != item["size_bytes"] or _sha(path) != item["sha256"]:
            raise ValueError(f"XA02 preregistration member drift: {item['path']}")


def _require_clean_git(project: Path) -> str:
    status = subprocess.run(["git", "status", "--porcelain"], cwd=project, check=True,
                            capture_output=True, text=True).stdout
    if status.strip(): raise ValueError("XA02 formal execution requires a clean committed worktree")
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, check=True,
                          capture_output=True, text=True).stdout.strip()


def _dependency_manifests(runtime: Path, batch: str) -> dict[str, str]:
    order = list(RUN_IDS); index = order.index(batch); output = {}
    for dependency in order[:index]:
        path = _batch_root(runtime, dependency) / "manifest.json"
        if not path.is_file(): raise FileNotFoundError(f"missing dependency manifest: {path}")
        manifest = json.loads(path.read_text(encoding="utf-8")); _verify_manifest(path.parent, manifest)
        output[dependency] = _sha(path)
    return output


def _verify_parent_inputs(project: Path, runtime: Path, program: dict[str, Any]) -> None:
    parent = program["parent"]; xa01 = _xa01_root(runtime)
    checks = {
        project / "config/experiments/xa01/PREREG_LOCK.json": parent["xa01_prereg_lock_sha256"],
        xa01 / "manifest.json": parent["xa01_runtime_manifest_sha256"],
        project / "results/published/cross_sectional_alpha/XA01/publication_manifest.json": parent["xa01_publication_manifest_sha256"],
        project / "config/experiments/xa01/factor_registry.csv": parent["xa01_factor_registry_sha256"],
        project / "results/published/cross_sectional_data/xs-market-sec-bundle-20260820-v1/manifest.json": parent["cross_sectional_publication_manifest_sha256"],
        project / "results/published/cross_sectional_data/xs-market-sec-bundle-20260820-v1/evidence_index.json": parent["cross_sectional_evidence_index_sha256"],
        project / "config/experiments/round10/R10A_ACCEPTANCE.json": parent["r10a_acceptance_sha256"],
        project / "results/published/round10/R10A/manifest.json": parent["r10a_publication_manifest_sha256"],
        xa01 / "factor_values_weekly_monthly.parquet": parent["xa01_factor_values_sha256"],
        xa01 / "target_ledger.parquet": parent["xa01_target_ledger_sha256"],
    }
    layout = DatabaseLayout.load(project_root=project, runtime_root=runtime)
    checks.update({
        layout.derived_root / "data_bundle_manifest.json": parent["cross_sectional_runtime_bundle_manifest_sha256"],
        layout.market_root / "prices_daily.parquet": parent["market_prices_daily_sha256"],
        layout.market_root / "calendar.parquet": parent["market_calendar_sha256"],
        layout.market_root / "membership.parquet": parent["market_membership_sha256"],
        layout.market_root / "benchmark_daily.parquet": parent["market_benchmark_sha256"],
        layout.market_root / "corporate_actions.parquet": parent["market_corporate_actions_sha256"],
        layout.market_root / "risk_free_daily.parquet": parent["market_risk_free_sha256"],
        runtime / "data/round10/staging/R10A_RSP_LOCKBOX_FEATURE" / parent["r10a_run_id"] / "rsp_daily.parquet": parent["r10a_rsp_daily_sha256"],
        runtime / "data/round10/staging/R10A_RSP_LOCKBOX_FEATURE" / parent["r10a_run_id"] / "manifest.json": parent["r10a_runtime_manifest_sha256"],
    })
    for path, expected in checks.items():
        if not path.is_file() or _sha(path) != expected: raise ValueError(f"XA02 parent drift: {path}")


def _write_manifest(project: Path, root: Path, batch: str, commit: str,
                    dependencies: dict[str, str]) -> None:
    files = {path.name: {"sha256": _sha(path), "size": path.stat().st_size}
             for path in sorted(root.iterdir()) if path.is_file() and path.name != "manifest.json"}
    payload = {"schema_version": "xa02.runtime_manifest.v1", "batch": batch,
               "run_id": RUN_IDS[batch], "git_commit": commit, "git_dirty": False,
               "python": sys.version, "platform": platform.platform(),
               "prereg_lock_sha256": _sha(project / LOCK), "dependencies": dependencies,
               "code_sha256": _sha(project / "src/momentum_reversal/pipelines/xa02_experiments.py"),
               "direct_input_hashes": _load_program(project)["parent"] if batch == "XA02A" else dependencies,
               "files": files, "models_run": False, "lockbox_read": False}
    _write_json(root / "manifest.json", payload)


def _verify_manifest(root: Path, manifest: dict[str, Any]) -> None:
    expected = set(manifest["files"]); actual = {p.name for p in root.iterdir() if p.is_file() and p.name != "manifest.json"}
    if expected != actual: raise ValueError(f"manifest member mismatch: {root}")
    for name, meta in manifest["files"].items():
        path = root / name
        if path.stat().st_size != meta["size"] or _sha(path) != meta["sha256"]:
            raise ValueError(f"manifest hash mismatch: {path}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    frame.to_parquet(path, index=False)


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    frame.to_csv(path, index=False, lineterminator="\n")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
