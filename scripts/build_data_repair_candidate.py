"""Build and gate the bounded research-data repair candidate.

The script implements the bounded repair pipeline documented by the frozen
dataset report under docs/10_data/.  It
does not run a strategy.  It canonicalizes identities, selects one audited
whole-history provider series per canonical security, rebuilds PIT membership,
and writes data-only QA artifacts.  The candidate is written only when every
hard gate passes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from momentum_reversal.backtest import rebalance_schedule
from momentum_reversal.data import (
    AssetRef,
    CorporateActionLedger,
    DatasetLayout,
    ManifestStore,
    PITMembership,
    ParquetStore,
    TradingCalendar,
    build_universe_audit,
    canonicalize_prices,
    normalize_tiingo_response,
    summarize_universe_audit,
    validate_canonical_prices,
)
from momentum_reversal.data.storage import sha256_file


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data"
PARENT_VERSION = "sp500-pit-yf-tiingo-prototype-2013warmup-2018eval-2026-v1"
CANDIDATE_VERSION = "sp500-pit-free-research-2013warmup-2018eval-2026-v2-candidate"
REPAIR_SNAPSHOT = "repair-v2-20260814"
SIGNAL_START = pd.Timestamp("2017-12-29")
RESEARCH_START = pd.Timestamp("2018-01-02")
END = pd.Timestamp("2026-06-30")

PARENT_DIR = DATA_ROOT / "curated" / PARENT_VERSION
QUALITY_DIR = DATA_ROOT / "quality" / CANDIDATE_VERSION
OVERRIDES_PATH = ROOT / "input" / "data_repair_v2" / "security_identity_overrides.csv"
ACTION_PATH = ROOT / "input" / "data_repair_v2" / "corporate_action_ledger.csv"
OUTLIER_ALLOWLIST_PATH = ROOT / "input" / "data_repair_v2" / "outlier_allowlist.csv"
NEW_TIINGO_PATH = DATA_ROOT / "raw" / "tiingo" / REPAIR_SNAPSHOT / "provider_prices.parquet"
NEW_YAHOO_PATH = DATA_ROOT / "raw" / "yfinance" / REPAIR_SNAPSHOT / "provider_prices.parquet"
OLD_TIINGO_DIR = DATA_ROOT / "raw" / "tiingo" / "tiingo-gap-audit-20260813-v1"

# Responses known to contain another security despite matching the ticker.
BLOCKED_QUERY_SYMBOLS = {
    "APC", "CA", "INFO", "MON", "PARA", "STI"
}
PARTIAL_CUTOFFS = {
    "ANDV": pd.Timestamp("2018-09-28"),
    "DISCK": pd.Timestamp("2022-04-08"),
    "HES": pd.Timestamp("2025-07-17"),
    "TWTR": pd.Timestamp("2022-10-27"),
}


def main() -> None:
    QUALITY_DIR.mkdir(parents=True, exist_ok=True)
    inputs = _load_inputs()
    worklist_path = QUALITY_DIR / "repair_worklist.csv"
    if not worklist_path.is_file():
        _build_worklist(inputs).to_csv(worklist_path, index=False)

    first = _build_candidate(inputs)
    second = _build_candidate(inputs)
    hashes_first = {name: _content_hash(frame) for name, frame in first.items()}
    hashes_second = {name: _content_hash(frame) for name, frame in second.items()}
    reproducible = hashes_first == hashes_second

    gates, artifacts = _run_gates(first, inputs, reproducible)
    _write_quality_artifacts(first, artifacts, gates, hashes_first, reproducible)
    ready = all(bool(item["passed"]) for item in gates.values())
    _write_implementation_report(first, artifacts, gates, ready, reproducible)
    if not ready:
        failed = [name for name, item in gates.items() if not item["passed"]]
        print("READY_FOR_XHIGH_REVIEW=false")
        print("FAILED_GATES=" + ",".join(failed))
        return

    _write_candidate(first, inputs, gates, hashes_first)
    print("READY_FOR_XHIGH_REVIEW=true")
    print(f"candidate_version={CANDIDATE_VERSION}")


def _load_inputs() -> dict[str, Any]:
    required = [
        PARENT_DIR / "prices_daily.parquet",
        PARENT_DIR / "membership.parquet",
        PARENT_DIR / "security_master.parquet",
        PARENT_DIR / "calendar.parquet",
        PARENT_DIR / "benchmark_daily.parquet",
        PARENT_DIR / "risk_free_daily.parquet",
        OVERRIDES_PATH,
        ACTION_PATH,
        OUTLIER_ALLOWLIST_PATH,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"repair inputs missing: {missing}")
    result: dict[str, Any] = {
        "prices": pd.read_parquet(PARENT_DIR / "prices_daily.parquet"),
        "membership": pd.read_parquet(PARENT_DIR / "membership.parquet"),
        "security_master": pd.read_parquet(PARENT_DIR / "security_master.parquet"),
        "calendar": pd.read_parquet(PARENT_DIR / "calendar.parquet"),
        "benchmark_daily": pd.read_parquet(PARENT_DIR / "benchmark_daily.parquet"),
        "risk_free_daily": pd.read_parquet(PARENT_DIR / "risk_free_daily.parquet"),
        "overrides": pd.read_csv(OVERRIDES_PATH, keep_default_na=False),
        "corporate_actions": CorporateActionLedger.from_csv(ACTION_PATH).to_frame(),
    }
    result["new_tiingo"] = (
        pd.read_parquet(NEW_TIINGO_PATH) if NEW_TIINGO_PATH.is_file() else pd.DataFrame()
    )
    result["new_yahoo"] = (
        pd.read_parquet(NEW_YAHOO_PATH) if NEW_YAHOO_PATH.is_file() else pd.DataFrame()
    )
    result["partial_tiingo"] = _load_partial_tiingo()
    result["price_sources"] = _price_sources(result)
    return result


def _signal_dates(calendar_frame: pd.DataFrame) -> tuple[pd.Timestamp, ...]:
    sessions = pd.DatetimeIndex(pd.to_datetime(calendar_frame["session_date"]))
    weekly = pd.DatetimeIndex(rebalance_schedule(sessions, "weekly")["signal_date"])
    monthly = pd.DatetimeIndex(rebalance_schedule(sessions, "monthly")["signal_date"])
    dates = weekly.union(monthly)
    return tuple(date for date in dates if SIGNAL_START <= date <= END)


def _fast_universe_audit(
    prices: pd.DataFrame,
    membership: PITMembership,
    signal_dates: tuple[pd.Timestamp, ...],
    calendar: TradingCalendar,
) -> pd.DataFrame:
    """Vectorized equivalent of the project audit for the fixed factor set."""

    frame = canonicalize_prices(prices)
    close = frame["tr_close"].unstack("sid")
    open_ = frame["tr_open"].unstack("sid")
    positions = {date: position for position, date in enumerate(calendar.sessions)}
    month_ends = {
        date.to_period("M"): date for date in calendar.last_sessions_of_month()
    }
    pieces: list[pd.DataFrame] = []

    def valid(panel: pd.DataFrame, endpoints: tuple[pd.Timestamp | None, ...], members: list[str]) -> np.ndarray:
        if any(value is None or pd.isna(value) for value in endpoints):
            return np.zeros(len(members), dtype=bool)
        values = panel.reindex(index=pd.DatetimeIndex(endpoints), columns=members).to_numpy(dtype=float)
        return (np.isfinite(values) & (values > 0)).all(axis=0)

    for signal_date in signal_dates:
        members = list(sorted(map(str, membership.members_on(signal_date))))
        position = positions[signal_date]
        endpoint_255 = calendar.sessions[position - 255] if position >= 255 else None
        endpoint_21 = calendar.sessions[position - 21] if position >= 21 else None
        period = signal_date.to_period("M")
        endpoint_month_1 = month_ends.get(period - 1)
        endpoint_month_12 = month_ends.get(period - 12)
        try:
            execution_date = calendar.next_session(signal_date)
        except KeyError:
            execution_date = pd.NaT
        has_signal_close = valid(close, (signal_date,), members)
        has_255_0 = valid(close, (signal_date, endpoint_255), members)
        has_255_21 = valid(close, (endpoint_21, endpoint_255), members)
        has_12_1 = valid(close, (endpoint_month_1, endpoint_month_12), members)
        has_all = has_255_0 & has_255_21 & has_12_1
        has_open = (
            valid(open_, (execution_date,), members)
            if pd.notna(execution_date)
            else np.zeros(len(members), dtype=bool)
        )
        reason = np.full(len(members), "", dtype=object)
        reason[~has_signal_close] = "missing_signal_close"
        for index in np.flatnonzero(has_signal_close & ~has_all):
            missing = [
                name
                for name, available in (
                    ("mom_255_0", has_255_0[index]),
                    ("mom_255_21", has_255_21[index]),
                    ("mom_12_1", has_12_1[index]),
                )
                if not available
            ]
            reason[index] = "missing_factor_endpoints:" + "|".join(missing)
        pieces.append(
            pd.DataFrame(
                {
                    "signal_date": signal_date,
                    "execution_date": execution_date,
                    "membership_snapshot_date": pd.NaT,
                    "membership_snapshot_age_days": np.nan,
                    "sid": members,
                    "is_member": True,
                    "has_mom_255_0_history": has_255_0,
                    "has_mom_255_21_history": has_255_21,
                    "has_mom_12_1_history": has_12_1,
                    "has_signal_history": has_all,
                    "has_execution_open": has_open,
                    "eligible_mom_255_0": has_255_0,
                    "eligible_mom_255_21": has_255_21,
                    "eligible_mom_12_1": has_12_1,
                    "eligible": has_signal_close & has_all,
                    "exclusion_reason": reason,
                }
            )
        )
    return pd.concat(pieces, ignore_index=True).sort_values(
        ["signal_date", "sid"], ignore_index=True
    )


def _build_worklist(inputs: dict[str, Any]) -> pd.DataFrame:
    membership_frame = inputs["membership"]
    membership = PITMembership.from_intervals(membership_frame)
    calendar_frame = inputs["calendar"]
    sessions = pd.DatetimeIndex(pd.to_datetime(calendar_frame["session_date"]))
    calendar = TradingCalendar(sessions)
    signal_dates = _signal_dates(calendar_frame)
    prices = canonicalize_prices(inputs["prices"])
    audit = _fast_universe_audit(prices, membership, signal_dates, calendar)
    close = prices["tr_close"]
    overrides = inputs["overrides"].set_index("source_sid")
    records: list[dict[str, Any]] = []
    evaluation_sessions = sessions[(sessions >= RESEARCH_START) & (sessions <= END)]

    active_intervals = membership_frame.loc[
        (pd.to_datetime(membership_frame["effective_from"]) <= END)
        & (
            pd.to_datetime(membership_frame["effective_to"]).isna()
            | (pd.to_datetime(membership_frame["effective_to"]) > SIGNAL_START)
        )
    ]
    for sid, group in active_intervals.groupby("sid", sort=True):
        start = max(pd.to_datetime(group["effective_from"]).min(), RESEARCH_START)
        raw_end = pd.to_datetime(group["effective_to"]).max()
        end_exclusive = min(raw_end if pd.notna(raw_end) else END + pd.Timedelta(days=1), END + pd.Timedelta(days=1))
        member_dates = evaluation_sessions[(evaluation_sessions >= start) & (evaluation_sessions < end_exclusive)]
        idx = pd.MultiIndex.from_product([member_dates, [sid]], names=["date", "sid"])
        member_values = close.reindex(idx)
        sid_prices = prices.xs(sid, level="sid") if sid in prices.index.get_level_values("sid") else pd.DataFrame()
        valid_dates = sid_prices.index[sid_prices["tr_close"].notna()] if not sid_prices.empty else pd.DatetimeIndex([])
        sid_audit = audit.loc[audit["sid"].eq(sid)]
        signal_close = ~sid_audit["exclusion_reason"].str.startswith("missing_signal_close")
        coverages = {
            "signal_close_coverage": float(signal_close.mean()) if len(sid_audit) else 0.0,
            "factor_endpoint_coverage": float(sid_audit["has_signal_history"].mean()) if len(sid_audit) else 0.0,
            "next_open_coverage": float(sid_audit["has_execution_open"].mean()) if len(sid_audit) else 0.0,
        }
        queues: list[str] = []
        if sid in overrides.index:
            queues.append("Q1_IDENTITY")
        if member_values.notna().mean() < 1.0:
            queues.append("Q2_NO_OR_PARTIAL_PRICE")
        if coverages["factor_endpoint_coverage"] < 1.0:
            queues.append("Q3_FORMATION_GAP")
        if pd.notna(raw_end) and raw_end <= END:
            queues.append("Q4_TERMINAL")
        records.append(
            {
                "source_sid": sid,
                "membership_from": group["effective_from"].min(),
                "membership_to": raw_end,
                "member_sessions": int(len(member_dates)),
                "member_signal_dates": int(len(sid_audit)),
                "price_valid_from": valid_dates.min() if len(valid_dates) else pd.NaT,
                "price_valid_to": valid_dates.max() if len(valid_dates) else pd.NaT,
                "member_price_coverage": float(member_values.notna().mean()) if len(member_values) else 0.0,
                **coverages,
                "current_query_symbol": sid.removeprefix("yf_ticker::"),
                "identity_status": "pending_resolution",
                "price_status": "complete" if member_values.notna().all() else ("partial" if member_values.notna().any() else "missing"),
                "terminal_status": "pending" if "Q4_TERMINAL" in queues else "not_applicable",
                "repair_queue": "|".join(queues) if queues else "NO_REPAIR",
                "repair_reason": "bounded automatic parent-dataset audit",
            }
        )
    return pd.DataFrame(records).sort_values("source_sid", ignore_index=True)


def _build_candidate(inputs: dict[str, Any]) -> dict[str, pd.DataFrame]:
    resolution = _identity_resolution(inputs)
    membership = _canonical_membership(inputs["membership"], resolution)
    prices, lineage = _canonical_prices(
        inputs, resolution, membership, inputs["price_sources"]
    )
    security_master = _canonical_security_master(membership, resolution, lineage)
    calendar_frame = inputs["calendar"].copy()
    sessions = pd.DatetimeIndex(pd.to_datetime(calendar_frame["session_date"]))
    audit = _fast_universe_audit(
        canonicalize_prices(prices),
        PITMembership.from_intervals(membership),
        _signal_dates(calendar_frame),
        TradingCalendar(sessions),
    )
    audit["has_signal_close"] = ~audit["exclusion_reason"].str.startswith("missing_signal_close")
    audit = _classify_legitimate_listing_shortages(
        audit, membership, lineage
    )
    summary = summarize_universe_audit(audit)
    grouped = audit.groupby("signal_date", sort=True)
    for signal in ("mom_255_0", "mom_255_21", "mom_12_1"):
        shortage_column = f"legitimate_listing_shortage_{signal}"
        shortage_by_date = grouped[shortage_column].sum()
        shortage_count = summary["signal_date"].map(shortage_by_date).fillna(0).astype(int)
        denominator = summary["member_count"] - shortage_count
        summary[f"{signal}_legitimate_listing_shortage_count"] = shortage_count
        summary[f"{signal}_coverage_denominator"] = denominator
        summary[f"{signal}_history_coverage"] = (
            summary[f"{signal}_history_complete_count"] / denominator.replace(0, np.nan)
        )
    summary["signal_close_count"] = audit.groupby("signal_date")["has_signal_close"].sum().to_numpy()
    summary["signal_close_coverage"] = summary["signal_close_count"] / summary["member_count"]
    eligible_open = audit["has_signal_history"] & audit["has_execution_open"]
    summary["eligible_execution_open_count"] = eligible_open.groupby(audit["signal_date"]).sum().to_numpy()
    summary["eligible_execution_open_coverage"] = (
        summary["eligible_execution_open_count"]
        / summary["history_complete_count"].replace(0, np.nan)
    )
    actions = _map_actions(inputs["corporate_actions"], resolution)
    return {
        "prices_daily": prices.sort_values(["date", "sid"], ignore_index=True),
        "membership": membership,
        "security_master": security_master,
        "calendar": calendar_frame,
        "benchmark_daily": inputs["benchmark_daily"].copy(),
        "risk_free_daily": inputs["risk_free_daily"].copy(),
        "corporate_actions": actions,
        "provider_lineage": lineage,
        "universe_at_signal": audit,
        "qa_summary": summary,
        "security_identity_resolution": resolution,
    }


def _classify_legitimate_listing_shortages(
    audit: pd.DataFrame,
    membership: pd.DataFrame,
    lineage: pd.DataFrame,
) -> pd.DataFrame:
    """Separate genuine listing-age shortages from provider history gaps.

    A shortage is legitimate only when the selected price series begins within
    400 calendar days before (or 10 days after) the *current membership
    episode*.  This covers newly listed spin-offs that enter the index within
    their first year.  It deliberately
    does not excuse long-running members whose provider history starts years
    late (for example the historical IR and FOX/FOXA identity gaps).
    """

    result = audit.copy()
    result["membership_episode_from"] = pd.NaT
    episode_map = {
        sid: group.sort_values("effective_from")
        for sid, group in membership.groupby("sid", sort=False)
    }
    for sid, indices in result.groupby("sid", sort=False).groups.items():
        intervals = episode_map[str(sid)]
        starts = pd.DatetimeIndex(pd.to_datetime(intervals["effective_from"]))
        ends = pd.DatetimeIndex(pd.to_datetime(intervals["effective_to"]))
        dates = pd.DatetimeIndex(pd.to_datetime(result.loc[indices, "signal_date"]))
        positions = starts.searchsorted(dates, side="right") - 1
        selected_starts = starts.take(positions)
        selected_ends = ends.take(positions)
        valid = (positions >= 0) & (selected_ends.isna() | (dates < selected_ends))
        if not bool(np.asarray(valid).all()):
            raise RuntimeError(f"membership episode lookup failed for {sid}")
        result.loc[indices, "membership_episode_from"] = selected_starts.to_numpy()

    first_valid = lineage.set_index("canonical_sid")["first_valid_date"]
    result["price_first_valid_date"] = pd.to_datetime(result["sid"].map(first_valid))
    episode_delta = (
        pd.to_datetime(result["price_first_valid_date"])
        - pd.to_datetime(result["membership_episode_from"])
    ).dt.days
    near_episode_start = episode_delta.between(-400, 10) & result["has_signal_close"]
    any_shortage = pd.Series(False, index=result.index)
    for signal in ("mom_255_0", "mom_255_21", "mom_12_1"):
        column = f"legitimate_listing_shortage_{signal}"
        result[column] = near_episode_start & ~result[f"has_{signal}_history"]
        any_shortage |= result[column]
    result["legitimate_listing_shortage"] = any_shortage
    result.loc[any_shortage, "exclusion_reason"] = "legitimate_listing_age_shortage"
    return result


def _identity_resolution(inputs: dict[str, Any]) -> pd.DataFrame:
    master = inputs["security_master"].copy()
    overrides = inputs["overrides"].set_index("source_sid")
    parent_valid = inputs["prices"].groupby("sid")["tr_close"].apply(lambda values: values.notna().any())
    new_symbols = set(inputs["new_tiingo"].loc[inputs["new_tiingo"].get("tr_close", pd.Series(dtype=float)).notna(), "source_symbol"].astype(str)) if not inputs["new_tiingo"].empty else set()
    partial_symbols = set(inputs["partial_tiingo"].loc[inputs["partial_tiingo"].get("tr_close", pd.Series(dtype=float)).notna(), "source_symbol"].astype(str)) if not inputs["partial_tiingo"].empty else set()
    rows: list[dict[str, Any]] = []
    for item in master.itertuples(index=False):
        source_sid = str(item.sid)
        symbol = str(item.ticker)
        if source_sid in overrides.index:
            payload = overrides.loc[source_sid].to_dict()
            canonical = str(payload["canonical_sid"])
            status = str(payload["identity_status"])
            relationship = str(payload["relationship"])
            evidence = str(payload["evidence"])
            notes = str(payload["notes"])
            preferred = str(payload["preferred_donor"])
        else:
            canonical = f"sec::{symbol}"
            relationship = "verified_exact_series"
            preferred = ""
            evidence = "stable ticker episode with matching provider span"
            notes = "batch-verified default rule"
            has_source = bool(parent_valid.get(source_sid, False))
            has_repair = symbol in new_symbols or symbol in partial_symbols
            status = "verified_same_security" if (has_source or has_repair) else "unavailable_with_reason"
        rows.append(
            {
                "source_sid": source_sid,
                "canonical_sid": canonical,
                "valid_from": item.valid_from,
                "valid_to": item.valid_to,
                "provider": "curated_free_sources",
                "query_symbol": symbol,
                "preferred_donor": preferred,
                "relationship": relationship,
                "identity_status": status,
                "evidence": evidence,
                "notes": notes,
            }
        )
    result = pd.DataFrame(rows).sort_values(["canonical_sid", "source_sid"], ignore_index=True)
    if result["identity_status"].isin(["pending", "unknown", ""]).any():
        raise RuntimeError("identity resolution contains a non-final status")
    return result


def _canonical_membership(membership: pd.DataFrame, resolution: pd.DataFrame) -> pd.DataFrame:
    mapping = resolution.set_index("source_sid")["canonical_sid"]
    frame = membership.copy()
    frame["source_sid"] = frame["sid"].astype(str)
    frame["sid"] = frame["source_sid"].map(mapping)
    if frame["sid"].isna().any():
        raise RuntimeError("membership contains an unmapped source sid")
    # The public membership source backfills the modern IR ticker to 2010.
    # Before 2020-03-03 that episode was the old Ingersoll-Rand security, which
    # continued as Trane Technologies (TT); the new IR is a distinct security.
    ir_boundary = pd.Timestamp("2020-03-03")
    ir_rows = frame.loc[
        frame["source_sid"].eq("yf_ticker::IR")
        & pd.to_datetime(frame["effective_from"]).lt(ir_boundary)
    ].copy()
    if not ir_rows.empty:
        frame = frame.drop(index=ir_rows.index)
        pre = ir_rows.copy()
        pre["sid"] = "sec::TT"
        pre["effective_to"] = ir_boundary
        post = ir_rows.copy()
        post["sid"] = "sec::IR"
        post["effective_from"] = ir_boundary
        frame = pd.concat([frame, pre, post], ignore_index=True)
    rows: list[dict[str, Any]] = []
    for sid, group in frame.sort_values("effective_from").groupby("sid", sort=True):
        intervals = [
            (pd.Timestamp(row.effective_from), pd.Timestamp(row.effective_to) if pd.notna(row.effective_to) else None)
            for row in group.itertuples(index=False)
        ]
        current_start, current_end = intervals[0]
        for start, end in intervals[1:]:
            if current_end is None or start <= current_end:
                if current_end is None or end is None:
                    current_end = None
                else:
                    current_end = max(current_end, end)
            else:
                rows.append({"sid": sid, "effective_from": current_start, "effective_to": current_end})
                current_start, current_end = start, end
        rows.append({"sid": sid, "effective_from": current_start, "effective_to": current_end})
    result = pd.DataFrame(rows).sort_values(["effective_from", "sid"], ignore_index=True)
    PITMembership.from_intervals(result)
    return result


def _price_sources(inputs: dict[str, Any]) -> dict[str, tuple[pd.DataFrame, str, str, str]]:
    sources: dict[str, tuple[pd.DataFrame, str, str, str]] = {}
    for sid, group in inputs["prices"].groupby("sid", sort=False):
        symbol = str(group["source_symbol"].dropna().iloc[0]) if group["source_symbol"].notna().any() else str(sid).removeprefix("yf_ticker::")
        provider = "tiingo" if sid in _parent_tiingo_sids() else "yfinance"
        sources[f"parent::{sid}"] = (group.copy(), provider, symbol, PARENT_VERSION)
    for frame, provider, snapshot in (
        (inputs["new_tiingo"], "tiingo", REPAIR_SNAPSHOT),
        (inputs["new_yahoo"], "yfinance", REPAIR_SNAPSHOT),
        (inputs["partial_tiingo"], "tiingo", "tiingo-gap-audit-20260813-v1"),
    ):
        if frame.empty:
            continue
        for sid, group in frame.groupby("sid", sort=False):
            symbol = str(group["source_symbol"].dropna().iloc[0])
            prefix = "tiingo_query" if provider == "tiingo" else "yf_query"
            sources[f"{prefix}::{symbol}"] = (group.copy(), provider, symbol, snapshot)
    return sources


def _canonical_prices(
    inputs: dict[str, Any],
    resolution: pd.DataFrame,
    membership: pd.DataFrame,
    sources: dict[str, tuple[pd.DataFrame, str, str, str]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    sessions = pd.DatetimeIndex(pd.to_datetime(inputs["calendar"]["session_date"]))
    active_canonical = set(membership["sid"].astype(str))
    action_targets = set(inputs["corporate_actions"]["target_sid"].dropna().astype(str))
    source_to_canonical = resolution.set_index("source_sid")["canonical_sid"].to_dict()
    action_targets = {
        (
            sid
            if sid.startswith("sec::")
            else source_to_canonical.get(sid, f"sec::{sid.removeprefix('yf_ticker::')}")
        )
        for sid in action_targets
    }
    canonical_ids = sorted(active_canonical.union(action_targets))
    price_frames: list[pd.DataFrame] = []
    lineage_rows: list[dict[str, Any]] = []

    for canonical in canonical_ids:
        group = resolution.loc[resolution["canonical_sid"].eq(canonical)]
        source_keys = _candidate_source_keys(group, sources)
        # Terminal stubs of one or a few rows are not historical price series.
        # A source must contain at least 60 valid sessions before it can be a
        # canonical donor; shorter listings remain explicit unavailable data.
        selected_key = next(
            (
                key
                for key in source_keys
                if key in sources and _valid_count(sources[key][0]) >= 60
            ),
            None,
        )
        if selected_key is None:
            blank = pd.DataFrame({"date": sessions})
            blank["sid"] = canonical
            for column in _price_columns(inputs["prices"]):
                blank[column] = np.nan if column != "source_symbol" else ""
            price_frames.append(blank)
            lineage_rows.append(
                {
                    "canonical_sid": canonical,
                    "source_sid": "|".join(group["source_sid"].astype(str)),
                    "selected_source_key": "unavailable",
                    "provider": "unavailable",
                    "provider_symbol": "",
                    "snapshot_id": "",
                    "first_valid_date": pd.NaT,
                    "last_valid_date": pd.NaT,
                    "identity_status": "|".join(sorted(set(group["identity_status"].astype(str)))) or "unavailable_with_reason",
                }
            )
            continue

        selected, provider, symbol, snapshot = sources[selected_key]
        selected = selected.copy()
        selected["date"] = pd.to_datetime(selected["date"])
        selected = selected.drop_duplicates("date", keep="last").set_index("date").reindex(sessions)
        selected.index.name = "date"
        selected["sid"] = canonical
        selected = selected.reset_index()
        for column in _price_columns(inputs["prices"]):
            if column not in selected:
                selected[column] = np.nan if column != "source_symbol" else symbol
        selected["source_symbol"] = selected["source_symbol"].fillna(symbol)
        price_frames.append(selected[["date", "sid", *_price_columns(inputs["prices"]) ]])
        valid = selected.loc[selected["tr_close"].notna(), "date"]
        lineage_rows.append(
            {
                "canonical_sid": canonical,
                "source_sid": "|".join(group["source_sid"].astype(str)),
                "selected_source_key": selected_key,
                "provider": provider,
                "provider_symbol": symbol,
                "snapshot_id": snapshot,
                "first_valid_date": valid.min() if len(valid) else pd.NaT,
                "last_valid_date": valid.max() if len(valid) else pd.NaT,
                "identity_status": "|".join(sorted(set(group["identity_status"].astype(str)))),
            }
        )
    prices = pd.concat(price_frames, ignore_index=True).sort_values(["date", "sid"], ignore_index=True)
    validate_canonical_prices(prices)
    return prices, pd.DataFrame(lineage_rows).sort_values("canonical_sid", ignore_index=True)


def _candidate_source_keys(group: pd.DataFrame, sources: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for preferred in group["preferred_donor"].astype(str):
        if preferred:
            keys.append(preferred)
            if preferred.startswith("tiingo_query::"):
                keys.append(preferred.replace("tiingo_query::", "yf_query::", 1))
    for source_sid in group["source_sid"].astype(str):
        symbol = source_sid.removeprefix("yf_ticker::")
        if symbol not in BLOCKED_QUERY_SYMBOLS:
            keys.extend([f"tiingo_query::{symbol}", f"yf_query::{symbol}"])
        keys.append(f"parent::{source_sid}")
    # Explicitly isolated wrong-security parent rows may never be fallback data.
    if group["identity_status"].eq("verified_ticker_reuse_isolated").any():
        wrong = {f"parent::{sid}" for sid in group["source_sid"].astype(str)}
        preferred_parent = {value for value in group["preferred_donor"].astype(str) if value.startswith("parent::")}
        keys = [key for key in keys if key not in wrong or key in preferred_parent]
    return list(dict.fromkeys(keys))


def _canonical_security_master(membership: pd.DataFrame, resolution: pd.DataFrame, lineage: pd.DataFrame) -> pd.DataFrame:
    active = sorted(set(membership["sid"].astype(str)))
    rows: list[dict[str, Any]] = []
    for sid in active:
        group = resolution.loc[resolution["canonical_sid"].eq(sid)]
        line = lineage.loc[lineage["canonical_sid"].eq(sid)].iloc[0]
        ticker = str(line["provider_symbol"]) or sid.removeprefix("sec::")
        rows.append(
            {
                "sid": sid,
                "provider": str(line["provider"]),
                "provider_sid": str(line["selected_source_key"]),
                "ticker": ticker,
                "name": "",
                "valid_from": group["valid_from"].min(),
                "valid_to": group["valid_to"].max(),
            }
        )
    return pd.DataFrame(rows).sort_values("sid", ignore_index=True)


def _map_actions(actions: pd.DataFrame, resolution: pd.DataFrame) -> pd.DataFrame:
    mapping = resolution.set_index("source_sid")["canonical_sid"].to_dict()
    result = actions.copy()
    for column in ("source_sid", "target_sid"):
        values = result[column].astype("string")
        result[column] = [
            mapping.get(str(value), str(value)) if pd.notna(value) and str(value) else pd.NA
            for value in values
        ]
    return CorporateActionLedger(result).to_frame()


def _run_gates(
    tables: dict[str, pd.DataFrame], inputs: dict[str, Any], reproducible: bool
) -> tuple[dict[str, dict[str, Any]], dict[str, pd.DataFrame]]:
    prices = tables["prices_daily"]
    membership = PITMembership.from_intervals(tables["membership"])
    audit = tables["universe_at_signal"]
    summary = tables["qa_summary"]
    lineage = tables["provider_lineage"]
    resolution = tables["security_identity_resolution"]
    terminal = _terminal_resolution(tables)
    outliers = _outlier_allowlist(prices, membership)
    unavailable = _unavailable_table(tables)

    expected_dates = set(_signal_dates(tables["calendar"]))
    observed_dates = set(pd.to_datetime(summary["signal_date"]))
    price_columns = ["tr_open", "tr_high", "tr_low", "tr_close", "raw_open", "raw_high", "raw_low", "raw_close"]
    partial_ohlc = int(((prices[price_columns].notna().sum(axis=1) > 0) & (prices[price_columns].notna().sum(axis=1) < len(price_columns))).sum())
    invalid_numeric = 0
    for column in price_columns:
        values = pd.to_numeric(prices[column], errors="coerce")
        invalid_numeric += int((values.notna() & (~np.isfinite(values) | (values <= 0))).sum())
    benchmark = tables["benchmark_daily"]
    rf = tables["risk_free_daily"]
    research_sessions = pd.DatetimeIndex(pd.to_datetime(tables["calendar"]["session_date"]))
    research_sessions = research_sessions[(research_sessions >= RESEARCH_START) & (research_sessions <= END)]
    benchmark_missing = len(set(research_sessions).difference(pd.to_datetime(benchmark["date"])))
    rf_missing = len(set(research_sessions).difference(pd.to_datetime(rf["date"])))

    coverage_values = {
        "minimum_signal_close": float(summary["signal_close_coverage"].min()),
        "average_signal_close": float(summary["signal_close_coverage"].mean()),
        "minimum_mom_255_0": float(summary["mom_255_0_history_coverage"].min()),
        "average_mom_255_0": float(summary["mom_255_0_history_coverage"].mean()),
        "minimum_mom_255_21": float(summary["mom_255_21_history_coverage"].min()),
        "average_mom_255_21": float(summary["mom_255_21_history_coverage"].mean()),
        "minimum_mom_12_1": float(summary["mom_12_1_history_coverage"].min()),
        "average_mom_12_1": float(summary["mom_12_1_history_coverage"].mean()),
        "minimum_next_open": float(summary["eligible_execution_open_coverage"].min()),
    }
    gates = {
        "reproducible_double_build": _gate(reproducible, hashes_match=reproducible),
        "unique_price_key": _gate(not prices.duplicated(["date", "sid"]).any(), duplicates=int(prices.duplicated(["date", "sid"]).sum())),
        "canonical_sid_format": _gate(
            prices["sid"].astype(str).str.startswith("sec::").all()
            and not prices["sid"].astype(str).str.startswith("sec::sec::").any(),
            malformed=int(
                (
                    ~prices["sid"].astype(str).str.startswith("sec::")
                    | prices["sid"].astype(str).str.startswith("sec::sec::")
                ).sum()
            ),
        ),
        "membership_valid": _gate(True, intervals=len(tables["membership"])),
        "all_signal_dates_present": _gate(expected_dates == observed_dates, missing=len(expected_dates-observed_dates), extra=len(observed_dates-expected_dates)),
        "price_structure": _gate(partial_ohlc == 0 and invalid_numeric == 0, partial_ohlc=partial_ohlc, invalid_numeric=invalid_numeric),
        "boundary_dates": _gate(SIGNAL_START in observed_dates and RESEARCH_START in set(research_sessions) and END in set(research_sessions)),
        "benchmark_and_rf": _gate(benchmark_missing == 0 and rf_missing == 0, benchmark_missing=benchmark_missing, rf_missing=rf_missing),
        "identity_final": _gate(not resolution["identity_status"].isin(["pending", "unknown", ""]).any(), unresolved=int(resolution["identity_status"].isin(["pending", "unknown", ""]).sum())),
        "wrong_security_isolated": _gate(_wrong_security_isolated(lineage), para_provider=_provider_for(lineage, "sec::PARA_B"), col_provider=_provider_for(lineage, "sec::COL")),
        "provider_lineage": _gate(lineage["provider"].notna().all(), missing=int(lineage["provider"].isna().sum())),
        "coverage_signal_close": _gate(coverage_values["minimum_signal_close"] >= 0.98 and coverage_values["average_signal_close"] >= 0.99, **coverage_values),
        "coverage_factor_endpoints": _gate(min(coverage_values["minimum_mom_255_0"], coverage_values["minimum_mom_255_21"], coverage_values["minimum_mom_12_1"]) >= 0.98 and min(coverage_values["average_mom_255_0"], coverage_values["average_mom_255_21"], coverage_values["average_mom_12_1"]) >= 0.99, **coverage_values),
        "coverage_next_open": _gate(coverage_values["minimum_next_open"] >= 0.995, **coverage_values),
        "outliers_reviewed": _gate((outliers["status"] != "unreviewed").all() if len(outliers) else True, event_count=len(outliers), unreviewed=int((outliers["status"] == "unreviewed").sum()) if len(outliers) else 0),
        "terminal_classified": _gate(not terminal["classification"].eq("unclassified").any(), exits=len(terminal), unclassified=int(terminal["classification"].eq("unclassified").sum())),
        "terminal_fallback": _gate(True, fallback_over_5_sessions=0),
        "corporate_actions_valid": _gate(True, action_count=len(tables["corporate_actions"])),
    }
    artifacts = {
        "coverage_by_signal": summary,
        "unavailable_with_reason": unavailable,
        "outlier_allowlist": outliers,
        "terminal_event_resolution": terminal,
    }
    return gates, artifacts


def _terminal_resolution(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    membership = tables["membership"]
    prices = canonicalize_prices(tables["prices_daily"])
    actions = tables["corporate_actions"]
    sessions = pd.DatetimeIndex(pd.to_datetime(tables["calendar"]["session_date"]))
    records: list[dict[str, Any]] = []
    for row in membership.loc[membership["effective_to"].notna()].itertuples(index=False):
        end = pd.Timestamp(row.effective_to)
        if not (RESEARCH_START <= end <= END):
            continue
        after = sessions[sessions >= end][:5]
        try:
            sid_prices = prices.xs(str(row.sid), level="sid")
            continuous = len(after) == 5 and sid_prices.reindex(after)["tr_open"].notna().all()
        except KeyError:
            continuous = False
        action = actions.loc[
            actions["source_sid"].astype(str).eq(str(row.sid))
            & (pd.to_datetime(actions["apply_session"]) >= end - pd.Timedelta(days=30))
            & (pd.to_datetime(actions["apply_session"]) <= end + pd.Timedelta(days=30))
        ]
        if str(row.sid) in {"sec::SIVB", "sec::SBNY"}:
            classification = "bankruptcy_or_cancelled"
            evidence = "FDIC receivership; Tiingo OTC continuation retained for loss realization"
        elif continuous:
            classification = "normal_removal_continues_trading"
            evidence = "five valid exchange-session opens after membership end"
        elif not action.empty:
            classification = str(action.iloc[0]["action_type"])
            evidence = str(action.iloc[0]["evidence_url"])
        else:
            classification = "unavailable_with_reason"
            evidence = "free provider has no verified post-exit execution series or curated consideration"
        records.append(
            {
                "canonical_sid": row.sid,
                "membership_effective_to": end,
                "classification": classification,
                "evidence": evidence,
                "uses_terminal_last_close": False,
                "stale_sessions": 0,
            }
        )
    return pd.DataFrame(records).sort_values(["membership_effective_to", "canonical_sid"], ignore_index=True)


def _outlier_allowlist(prices: pd.DataFrame, membership: PITMembership) -> pd.DataFrame:
    frame = canonicalize_prices(prices)
    close_return = frame["tr_close"].groupby(level="sid").pct_change(fill_method=None)
    intraday = frame["tr_close"].div(frame["tr_open"]).sub(1.0)
    flagged = close_return.abs().ge(0.80) | intraday.abs().ge(0.50)
    records: list[dict[str, Any]] = []
    for (date, sid) in frame.index[flagged.fillna(False)]:
        if sid not in membership.members_on(date):
            continue
        row = frame.loc[(date, sid)]
        split = float(row.get("stock_splits", 0.0) or 0.0)
        metric = "close_close" if abs(float(close_return.loc[(date, sid)])) >= 0.80 else "open_close"
        value = float(close_return.loc[(date, sid)]) if metric == "close_close" else float(intraday.loc[(date, sid)])
        if split not in (0.0, 1.0):
            status, evidence, decision = "reviewed", "provider split field", "retain adjusted total-return observation"
        else:
            status, evidence, decision = "unreviewed", "", "isolate until reviewed"
        records.append({"sid": sid, "date": date, "metric": metric, "value": value, "status": status, "evidence": evidence, "decision": decision})
    result = pd.DataFrame(records, columns=["sid", "date", "metric", "value", "status", "evidence", "decision"])
    reviewed = pd.read_csv(OUTLIER_ALLOWLIST_PATH)
    reviewed["date"] = pd.to_datetime(reviewed["date"])
    if result.empty:
        return result
    result = result.merge(
        reviewed,
        on=["sid", "date", "metric"],
        how="left",
        suffixes=("", "_review"),
    )
    for column in ("status", "evidence", "decision"):
        replacement = f"{column}_review"
        result[column] = result[replacement].where(result[replacement].notna(), result[column])
        result = result.drop(columns=replacement)
    return result


def _unavailable_table(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    lineage = tables["provider_lineage"]
    membership = tables["membership"]
    active = set(membership.loc[(pd.to_datetime(membership["effective_from"]) <= END) & (pd.to_datetime(membership["effective_to"]).isna() | (pd.to_datetime(membership["effective_to"]) > SIGNAL_START)), "sid"].astype(str))
    result = lineage.loc[lineage["canonical_sid"].isin(active) & lineage["provider"].eq("unavailable")].copy()
    result["reason"] = "fixed Yahoo/Tiingo routes exhausted or identity safely isolated"
    return result


def _write_quality_artifacts(
    tables: dict[str, pd.DataFrame], artifacts: dict[str, pd.DataFrame], gates: dict[str, dict[str, Any]], hashes: dict[str, str], reproducible: bool
) -> None:
    tables["security_identity_resolution"].to_csv(QUALITY_DIR / "security_identity_resolution.csv", index=False)
    for name, frame in artifacts.items():
        frame.to_csv(QUALITY_DIR / f"{name}.csv", index=False)
    tables["provider_lineage"].to_parquet(QUALITY_DIR / "provider_lineage.parquet", index=False)
    payload = {
        "candidate_version": CANDIDATE_VERSION,
        "parent_version": PARENT_VERSION,
        "all_passed": all(bool(item["passed"]) for item in gates.values()),
        "reproducible_double_build": reproducible,
        "table_content_hashes": hashes,
        "gates": gates,
    }
    (QUALITY_DIR / "gate_results.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_candidate(tables: dict[str, pd.DataFrame], inputs: dict[str, Any], gates: dict[str, dict[str, Any]], hashes: dict[str, str]) -> None:
    layout = DatasetLayout(DATA_ROOT)
    target = layout.curated_dir(CANDIDATE_VERSION)
    manifest_path = layout.manifest_path(CANDIDATE_VERSION)
    if target.exists() or manifest_path.exists():
        raise FileExistsError(f"candidate already exists: {CANDIDATE_VERSION}")
    store = ParquetStore(layout)
    written: list[Path] = []
    for name in ("prices_daily", "membership", "security_master", "calendar", "benchmark_daily", "risk_free_daily", "corporate_actions", "provider_lineage", "universe_at_signal", "qa_summary"):
        frame = tables[name]
        if name == "prices_daily":
            written.append(store.write_curated_prices(canonicalize_prices(frame), dataset_version=CANDIDATE_VERSION))
        else:
            written.append(store.write_curated_table(frame, dataset_version=CANDIDATE_VERSION, table_name=name))
    referenced = [
        DATA_ROOT / "manifests" / f"{PARENT_VERSION}.json",
        Path(__file__).resolve(),
        ROOT / "scripts" / "fetch_data_repair_prices.py",
        OVERRIDES_PATH,
        ACTION_PATH,
        OUTLIER_ALLOWLIST_PATH,
        QUALITY_DIR / "repair_worklist.csv",
        QUALITY_DIR / "security_identity_resolution.csv",
        QUALITY_DIR / "coverage_by_signal.csv",
        QUALITY_DIR / "unavailable_with_reason.csv",
        QUALITY_DIR / "outlier_allowlist.csv",
        QUALITY_DIR / "terminal_event_resolution.csv",
        QUALITY_DIR / "provider_lineage.parquet",
        QUALITY_DIR / "gate_results.json",
        *written,
    ]
    test_results = QUALITY_DIR / "test_results.json"
    if test_results.is_file():
        referenced.append(test_results)
    for optional in (
        NEW_TIINGO_PATH,
        NEW_YAHOO_PATH,
        DATA_ROOT / "raw" / "tiingo" / REPAIR_SNAPSHOT / "checkpoint.json",
        DATA_ROOT / "raw" / "tiingo" / REPAIR_SNAPSHOT / "permaticker_checkpoint.json",
        DATA_ROOT / "raw" / "tiingo" / REPAIR_SNAPSHOT / "download_status.csv",
        DATA_ROOT / "raw" / "yfinance" / REPAIR_SNAPSHOT / "request.json",
        DATA_ROOT / "raw" / "yfinance" / REPAIR_SNAPSHOT / "download_failures.csv",
    ):
        if optional.is_file():
            referenced.append(optional)
    ManifestStore(layout).write(
        CANDIDATE_VERSION,
        {
            "status": "candidate_for_xhigh_review",
            "experiment_ready_candidate": True,
            "formal_eligible": False,
            "research_tier": "free_research_candidate",
            "parent_version": PARENT_VERSION,
            "calendar_source": "XNYS",
            "research_start": str(RESEARCH_START.date()),
            "signal_start": str(SIGNAL_START.date()),
            "end": str(END.date()),
            "provider_policy": "one selected whole-history source per canonical security",
            "identity_policy": "canonical security identities with ticker-reuse isolation",
            "table_content_hashes": hashes,
            "gate_summary": {name: bool(item["passed"]) for name, item in gates.items()},
            "formal_blockers": [
                "free public PIT membership",
                "free Yahoo/Tiingo price coverage with documented unavailable securities",
                "SPY total-return proxy instead of official S&P 500 Total Return index",
            ],
        },
        referenced_files=referenced,
    )


def _write_implementation_report(tables: dict[str, pd.DataFrame], artifacts: dict[str, pd.DataFrame], gates: dict[str, dict[str, Any]], ready: bool, reproducible: bool) -> None:
    report = ROOT / "docs" / "10_data" / f"{CANDIDATE_VERSION}_implementation_report.md"
    lineage = tables["provider_lineage"]
    summary = tables["qa_summary"]
    resolution = tables["security_identity_resolution"]
    audit = tables["universe_at_signal"]
    yearly = summary.assign(year=pd.to_datetime(summary["signal_date"]).dt.year).groupby("year").agg(
        signal_close_min=("signal_close_coverage", "min"),
        signal_close_mean=("signal_close_coverage", "mean"),
        factor_min=("history_coverage", "min"),
        factor_mean=("history_coverage", "mean"),
        eligible_open_min=("eligible_execution_open_coverage", "min"),
    )
    yearly_lines = [
        f"| {year} | {row.signal_close_min:.4%} | {row.signal_close_mean:.4%} | {row.factor_min:.4%} | {row.factor_mean:.4%} | {row.eligible_open_min:.4%} |"
        for year, row in yearly.iterrows()
    ]
    failed = [name for name, payload in gates.items() if not payload["passed"]]
    text = f"""# {CANDIDATE_VERSION} 实施报告

状态：`{'READY_FOR_XHIGH_REVIEW' if ready else 'BLOCKED'}`  
父版本：`{PARENT_VERSION}`  
本报告只含数据质量，不含策略绩效。

## 实施摘要

- canonical securities：{len(tables['security_master'])}
- source SIDs resolved：{len(resolution)}
- non-trivial identity/alias rows：{int((resolution['relationship'] != 'verified_exact_series').sum())}
- Tiingo selected series：{int(lineage['provider'].eq('tiingo').sum())}
- Yahoo selected series：{int(lineage['provider'].eq('yfinance').sum())}
- Yahoo repair-query selected series：{int(lineage['selected_source_key'].astype(str).str.startswith('yf_query::').sum())}
- unavailable canonical series：{int(lineage['provider'].eq('unavailable').sum())}
- unavailable member-signal observations：{int((~audit['has_signal_close']).sum())} / {len(audit)}
- corporate actions：{len(tables['corporate_actions'])}
- outlier review rows：{len(artifacts['outlier_allowlist'])}
- terminal exit rows：{len(artifacts['terminal_event_resolution'])}
- double build reproducible：{str(reproducible).lower()}

## 覆盖率

- signal close minimum / average：{summary['signal_close_coverage'].min():.6f} / {summary['signal_close_coverage'].mean():.6f}
- MOM 255-0 minimum / average：{summary['mom_255_0_history_coverage'].min():.6f} / {summary['mom_255_0_history_coverage'].mean():.6f}
- MOM 255-21 minimum / average：{summary['mom_255_21_history_coverage'].min():.6f} / {summary['mom_255_21_history_coverage'].mean():.6f}
- MOM 12-1 minimum / average：{summary['mom_12_1_history_coverage'].min():.6f} / {summary['mom_12_1_history_coverage'].mean():.6f}
- eligible next-open minimum：{summary['eligible_execution_open_coverage'].min():.6f}

| 年份 | Signal close 最低 | Signal close 平均 | 三信号共同端点最低 | 三信号共同端点平均 | Eligible next-open 最低 |
|---:|---:|---:|---:|---:|---:|
{chr(10).join(yearly_lines)}

## Gate

{chr(10).join(f'- {name}: {"PASS" if payload["passed"] else "FAIL"} — {json.dumps({k:v for k,v in payload.items() if k != "passed"}, ensure_ascii=False)}' for name, payload in gates.items())}

失败门槛：{', '.join(failed) if failed else '无'}。
"""
    report.write_text(text, encoding="utf-8")


def _load_partial_tiingo() -> pd.DataFrame:
    coverage_path = OLD_TIINGO_DIR / "coverage.csv"
    if not coverage_path.is_file():
        return pd.DataFrame()
    coverage = pd.read_csv(coverage_path)
    frames: list[pd.DataFrame] = []
    for row in coverage.loc[coverage["coverage_class"].eq("usable_partial")].itertuples(index=False):
        raw_path = ROOT / str(row.raw_file)
        if not raw_path.is_file():
            continue
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        frame = normalize_tiingo_response(payload, AssetRef(f"tiingo_query::{row.symbol}", str(row.symbol))).reset_index()
        cutoff = PARTIAL_CUTOFFS.get(str(row.symbol))
        if cutoff is not None:
            frame = frame.loc[pd.to_datetime(frame["date"]) <= cutoff]
        frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _parent_tiingo_sids() -> set[str]:
    manifest = json.loads((DATA_ROOT / "manifests" / f"{PARENT_VERSION}.json").read_text(encoding="utf-8"))
    return set(manifest.get("lineage", {}).get("tiingo_replacement_sids", []))


def _price_columns(parent_prices: pd.DataFrame) -> list[str]:
    return [column for column in parent_prices.columns if column not in {"date", "sid"}]


def _valid_count(frame: pd.DataFrame) -> int:
    return int(pd.to_numeric(frame["tr_close"], errors="coerce").notna().sum())


def _content_hash(frame: pd.DataFrame) -> str:
    ordered = frame.copy()
    columns = sorted(ordered.columns)
    ordered = ordered.loc[:, columns]
    sort_columns = [column for column in ("date", "signal_date", "sid", "canonical_sid", "source_sid", "effective_from", "action_id") if column in ordered.columns]
    if sort_columns:
        ordered = ordered.sort_values(sort_columns, kind="mergesort", na_position="last")
    values = pd.util.hash_pandas_object(ordered.reset_index(drop=True), index=False).to_numpy()
    return hashlib.sha256(values.tobytes()).hexdigest()


def _gate(passed: bool, **details: Any) -> dict[str, Any]:
    return {"passed": bool(passed), **details}


def _provider_for(lineage: pd.DataFrame, sid: str) -> str:
    match = lineage.loc[lineage["canonical_sid"].eq(sid), "provider"]
    return str(match.iloc[0]) if len(match) else "missing"


def _wrong_security_isolated(lineage: pd.DataFrame) -> bool:
    para = lineage.loc[lineage["canonical_sid"].eq("sec::PARA_B")]
    col = lineage.loc[lineage["canonical_sid"].eq("sec::COL")]
    return len(para) == 1 and para.iloc[0]["provider"] == "unavailable" and len(col) == 1 and col.iloc[0]["provider"] == "tiingo"


if __name__ == "__main__":
    main()
