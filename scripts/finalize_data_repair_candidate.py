"""Apply the bounded XHigh fixes and build the free-research v3 candidate.

This is intentionally a *finalizer*, not another open-ended data collector.  It
starts from the independently audited v2 candidate and applies only the fixed
v3 ledgers under ``input/data_repair_v3``:

* mask 24 known non-tradable/placeholder intervals;
* clip the single TSS membership boundary;
* replace two unpriceable merger targets, add DISCK -> WBD, and preserve the
  two bank failures at their first observable OTC recovery values;
* turn any remaining holdable terminal event into cash only when its last real
  close is at most five authoritative sessions old;
* compute (rather than assert) terminal and corporate-action gates.

No strategy performance is run and no additional security is researched.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from momentum_reversal.backtest import rebalance_schedule
from momentum_reversal.data import (
    CorporateActionLedger,
    DatasetLayout,
    ManifestStore,
    PITMembership,
    ParquetStore,
    TradingCalendar,
    TradabilityOverrideLedger,
    canonicalize_prices,
    summarize_universe_audit,
    validate_canonical_prices,
)
from momentum_reversal.data.storage import sha256_file
from momentum_reversal.runtime import resolve_runtime_paths

from scripts import build_data_repair_candidate as v2_builder


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = resolve_runtime_paths(cwd=ROOT).data_root
SOURCE_VERSION = "sp500-pit-free-research-2013warmup-2018eval-2026-v2-candidate"
CANDIDATE_VERSION = (
    "sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate"
)
SOURCE_DIR = DATA_ROOT / "curated" / SOURCE_VERSION
SOURCE_QUALITY_DIR = DATA_ROOT / "quality" / SOURCE_VERSION
QUALITY_DIR = DATA_ROOT / "quality" / CANDIDATE_VERSION
INPUT_DIR = ROOT / "input" / "data_repair_v3"
SIGNAL_START = pd.Timestamp("2017-12-29")
RESEARCH_START = pd.Timestamp("2018-01-02")
END = pd.Timestamp("2026-06-30")
MAX_TERMINAL_STALE_SESSIONS = 5
MAX_DEFERRED_ACTION_SESSIONS = 25

PRICE_OHLC = (
    "tr_open",
    "tr_high",
    "tr_low",
    "tr_close",
    "raw_open",
    "raw_high",
    "raw_low",
    "raw_close",
)


def main() -> None:
    layout = DatasetLayout(DATA_ROOT)
    target = layout.curated_dir(CANDIDATE_VERSION)
    manifest_path = layout.manifest_path(CANDIDATE_VERSION)
    if target.exists() or manifest_path.exists():
        raise FileExistsError(f"v3 candidate already exists: {CANDIDATE_VERSION}")

    QUALITY_DIR.mkdir(parents=True, exist_ok=True)
    source = _load_source_tables()
    first, first_artifacts = _build_candidate(source)
    second, _ = _build_candidate(source)
    hashes_first = {name: _content_hash(frame) for name, frame in first.items()}
    hashes_second = {name: _content_hash(frame) for name, frame in second.items()}
    reproducible = hashes_first == hashes_second

    gates = _run_gates(first, first_artifacts, reproducible)
    _write_quality(first, first_artifacts, gates, hashes_first, reproducible)
    ready = all(bool(item["passed"]) for item in gates.values())
    _write_report(first, first_artifacts, gates, reproducible, ready)
    if not ready:
        failed = [name for name, item in gates.items() if not item["passed"]]
        print("READY_FOR_FINAL_REVIEW=false")
        print("FAILED_GATES=" + ",".join(failed))
        return

    _write_candidate(first, first_artifacts, gates, hashes_first)
    print("READY_FOR_FINAL_REVIEW=true")
    print(f"candidate_version={CANDIDATE_VERSION}")


def _load_source_tables() -> dict[str, pd.DataFrame]:
    names = (
        "prices_daily",
        "membership",
        "security_master",
        "calendar",
        "benchmark_daily",
        "risk_free_daily",
        "corporate_actions",
        "provider_lineage",
        "universe_at_signal",
        "qa_summary",
    )
    missing = [str(SOURCE_DIR / f"{name}.parquet") for name in names if not (SOURCE_DIR / f"{name}.parquet").is_file()]
    required_inputs = (
        INPUT_DIR / "security_identity_overrides.csv",
        INPUT_DIR / "outlier_allowlist.csv",
        INPUT_DIR / "corporate_action_ledger.csv",
        INPUT_DIR / "membership_boundary_overrides.csv",
        INPUT_DIR / "terminal_resolution_overrides.csv",
        INPUT_DIR / "tradability_overrides.csv",
        SOURCE_QUALITY_DIR / "security_identity_resolution.csv",
    )
    missing.extend(str(path) for path in required_inputs if not path.is_file())
    if missing:
        raise FileNotFoundError(f"v3 finalizer inputs missing: {missing}")
    result = {name: pd.read_parquet(SOURCE_DIR / f"{name}.parquet") for name in names}
    result["security_identity_resolution"] = pd.read_csv(
        SOURCE_QUALITY_DIR / "security_identity_resolution.csv",
        keep_default_na=False,
    )
    return result


def _build_candidate(
    source: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    prices, tradability_audit = TradabilityOverrideLedger.from_csv(
        INPUT_DIR / "tradability_overrides.csv"
    ).apply(source["prices_daily"])
    prices = prices.reset_index()

    membership, membership_audit = _apply_membership_overrides(
        source["membership"],
        pd.read_csv(INPUT_DIR / "membership_boundary_overrides.csv", dtype=str),
    )

    # CP existed only as the invalid KSU target stub in v2.  Once that action
    # is removed it must not survive as an unregistered phantom security.
    prices = prices.loc[~prices["sid"].eq("sec::CP")].copy()
    lineage = source["provider_lineage"].loc[
        ~source["provider_lineage"]["canonical_sid"].eq("sec::CP")
    ].copy()

    sessions = pd.DatetimeIndex(pd.to_datetime(source["calendar"]["session_date"]))
    signal_dates = v2_builder._signal_dates(source["calendar"])
    audit = v2_builder._fast_universe_audit(
        canonicalize_prices(prices),
        PITMembership.from_intervals(membership),
        signal_dates,
        TradingCalendar(sessions),
    )
    audit["has_signal_close"] = ~audit["exclusion_reason"].str.startswith(
        "missing_signal_close"
    )
    audit = v2_builder._classify_legitimate_listing_shortages(
        audit, membership, lineage
    )
    audit = _classify_audited_nontradable_shortages(
        audit, tradability_audit, sessions
    )
    summary = _summarize(audit)

    base_actions = CorporateActionLedger.from_csv(
        INPUT_DIR / "corporate_action_ledger.csv"
    ).to_frame()
    manual_actions, terminal_override_audit = _manual_liquidation_actions(
        prices,
        pd.read_csv(
            INPUT_DIR / "terminal_resolution_overrides.csv",
            dtype=str,
            keep_default_na=False,
        ),
        sessions,
    )
    actions = CorporateActionLedger(
        pd.concat([base_actions, manual_actions], ignore_index=True)
    ).to_frame()
    auto_actions, auto_terminal_audit = _automatic_terminal_liquidations(
        prices=prices,
        membership=membership,
        audit=audit,
        actions=actions,
        sessions=sessions,
    )
    actions = CorporateActionLedger(
        pd.concat([actions, auto_actions], ignore_index=True)
    ).to_frame()

    action_valuation = _audit_corporate_action_valuation(
        prices, actions, sessions
    )
    terminal_events = _terminal_event_resolution(
        prices=prices,
        membership=membership,
        audit=audit,
        actions=actions,
        action_valuation=action_valuation,
        sessions=sessions,
    )
    terminal_execution = _terminal_execution_resolution(
        prices=prices,
        membership=membership,
        audit=audit,
        actions=actions,
        action_valuation=action_valuation,
        terminal_override_audit=terminal_override_audit,
        sessions=sessions,
    )
    gap_outliers = _gap_outliers(prices)

    security_master = source["security_master"].copy()
    security_master = security_master.loc[
        security_master["sid"].isin(set(membership["sid"].astype(str)))
    ].sort_values("sid", ignore_index=True)
    validate_canonical_prices(prices)

    tables = {
        "prices_daily": prices.sort_values(["date", "sid"], ignore_index=True),
        "membership": membership,
        "security_master": security_master,
        "calendar": source["calendar"].copy(),
        "benchmark_daily": source["benchmark_daily"].copy(),
        "risk_free_daily": source["risk_free_daily"].copy(),
        "corporate_actions": actions,
        "provider_lineage": lineage.sort_values("canonical_sid", ignore_index=True),
        "universe_at_signal": audit,
        "qa_summary": summary,
        "security_identity_resolution": source["security_identity_resolution"].copy(),
    }
    artifacts = {
        "tradability_override_audit": tradability_audit,
        "membership_boundary_audit": membership_audit,
        "terminal_override_audit": terminal_override_audit,
        "automatic_terminal_liquidations": auto_terminal_audit,
        "corporate_action_valuation": action_valuation,
        "terminal_event_resolution": terminal_events,
        "terminal_execution_resolution": terminal_execution,
        "gap_outlier_allowlist": gap_outliers,
        "coverage_by_signal": summary,
    }
    return tables, artifacts


def _apply_membership_overrides(
    membership: pd.DataFrame, overrides: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = membership.copy()
    frame["effective_from"] = pd.to_datetime(frame["effective_from"])
    frame["effective_to"] = pd.to_datetime(frame["effective_to"])
    audit_rows: list[dict[str, object]] = []
    for row in overrides.itertuples(index=False):
        match_from = pd.Timestamp(row.match_effective_from)
        match_to = pd.Timestamp(row.match_effective_to)
        mask = (
            frame["sid"].astype(str).eq(str(row.canonical_sid))
            & frame["effective_from"].eq(match_from)
            & frame["effective_to"].eq(match_to)
        )
        if int(mask.sum()) != 1:
            raise RuntimeError(
                f"membership override {row.override_id} expected one exact row; "
                f"matched {int(mask.sum())}"
            )
        replacement_from = pd.Timestamp(row.replacement_effective_from)
        replacement_to = pd.Timestamp(row.replacement_effective_to)
        if replacement_from >= replacement_to:
            raise ValueError(f"invalid replacement interval: {row.override_id}")
        frame.loc[mask, "effective_from"] = replacement_from
        frame.loc[mask, "effective_to"] = replacement_to
        audit_rows.append(
            {
                "override_id": row.override_id,
                "sid": row.canonical_sid,
                "old_effective_from": match_from,
                "old_effective_to": match_to,
                "new_effective_from": replacement_from,
                "new_effective_to": replacement_to,
                "reason": row.reason,
                "evidence_url": row.evidence_url,
            }
        )
    frame = frame.sort_values(["effective_from", "sid"], ignore_index=True)
    PITMembership.from_intervals(frame)
    return frame, pd.DataFrame(audit_rows)


def _summarize(audit: pd.DataFrame) -> pd.DataFrame:
    summary = summarize_universe_audit(audit)
    grouped = audit.groupby("signal_date", sort=True)
    for signal in ("mom_255_0", "mom_255_21", "mom_12_1"):
        shortage = grouped[f"legitimate_listing_shortage_{signal}"].sum()
        shortage_count = summary["signal_date"].map(shortage).fillna(0).astype(int)
        nontradable = grouped[f"audited_nontradable_shortage_{signal}"].sum()
        nontradable_count = (
            summary["signal_date"].map(nontradable).fillna(0).astype(int)
        )
        denominator = summary["member_count"] - shortage_count - nontradable_count
        summary[f"{signal}_legitimate_listing_shortage_count"] = shortage_count
        summary[f"{signal}_audited_nontradable_shortage_count"] = nontradable_count
        summary[f"{signal}_coverage_denominator"] = denominator
        summary[f"{signal}_history_coverage"] = (
            summary[f"{signal}_history_complete_count"]
            / denominator.replace(0, np.nan)
        )
    signal_close = grouped["has_signal_close"].sum()
    signal_nontradable = grouped["audited_nontradable_signal_close"].sum()
    summary["signal_close_count"] = summary["signal_date"].map(signal_close).astype(int)
    summary["signal_close_audited_nontradable_count"] = (
        summary["signal_date"].map(signal_nontradable).fillna(0).astype(int)
    )
    summary["signal_close_coverage_denominator"] = (
        summary["member_count"]
        - summary["signal_close_audited_nontradable_count"]
    )
    summary["signal_close_coverage"] = (
        summary["signal_close_count"]
        / summary["signal_close_coverage_denominator"].replace(0, np.nan)
    )
    eligible_open = audit["has_signal_history"] & audit["has_execution_open"]
    open_count = eligible_open.groupby(audit["signal_date"]).sum()
    summary["eligible_execution_open_count"] = summary["signal_date"].map(open_count).astype(int)
    summary["eligible_execution_open_coverage"] = (
        summary["eligible_execution_open_count"]
        / summary["history_complete_count"].replace(0, np.nan)
    )
    return summary


def _classify_audited_nontradable_shortages(
    audit: pd.DataFrame,
    tradability_audit: pd.DataFrame,
    sessions: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Keep raw missingness while separating reviewed non-tradable endpoints.

    The override ledger proves that these cells existed in v2 and were masked
    deliberately.  They are therefore not silently deleted securities; they
    are explicit non-tradable observations excluded from the *tradable*
    coverage denominator.  Counts remain visible in every signal-date row.
    """

    result = audit.copy()
    masked: dict[pd.Timestamp, set[str]] = {}
    for row in tradability_audit.itertuples(index=False):
        masked.setdefault(pd.Timestamp(row.date), set()).add(str(row.sid))
    locations = {date: i for i, date in enumerate(sessions)}
    month_ends = {
        date.to_period("M"): date
        for date in TradingCalendar(sessions).last_sessions_of_month()
    }
    result["audited_nontradable_signal_close"] = False
    for signal in ("mom_255_0", "mom_255_21", "mom_12_1"):
        result[f"audited_nontradable_shortage_{signal}"] = False

    for signal_date, indices in result.groupby("signal_date", sort=False).groups.items():
        signal_date = pd.Timestamp(signal_date)
        position = locations[signal_date]
        endpoint_255 = sessions[position - 255] if position >= 255 else None
        endpoint_21 = sessions[position - 21] if position >= 21 else None
        period = signal_date.to_period("M")
        endpoints = {
            "mom_255_0": (signal_date, endpoint_255),
            "mom_255_21": (endpoint_21, endpoint_255),
            "mom_12_1": (month_ends.get(period - 1), month_ends.get(period - 12)),
        }
        sids = result.loc[indices, "sid"].astype(str)
        signal_masked = sids.isin(masked.get(signal_date, set()))
        result.loc[indices, "audited_nontradable_signal_close"] = (
            signal_masked.to_numpy()
            & ~result.loc[indices, "has_signal_close"].to_numpy(dtype=bool)
        )
        for signal, factor_endpoints in endpoints.items():
            masked_sids: set[str] = set()
            for endpoint in factor_endpoints:
                if endpoint is not None and pd.notna(endpoint):
                    masked_sids.update(masked.get(pd.Timestamp(endpoint), set()))
            caused = sids.isin(masked_sids).to_numpy() & ~result.loc[
                indices, f"has_{signal}_history"
            ].to_numpy(dtype=bool)
            result.loc[indices, f"audited_nontradable_shortage_{signal}"] = caused
    return result


def _manual_liquidation_actions(
    prices: pd.DataFrame,
    overrides: pd.DataFrame,
    sessions: pd.DatetimeIndex,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    indexed = canonicalize_prices(prices)
    session_locations = {date: i for i, date in enumerate(sessions)}
    actions: list[dict[str, object]] = []
    audits: list[dict[str, object]] = []
    for row in overrides.itertuples(index=False):
        price_date = pd.Timestamp(row.price_date)
        resolution_session = pd.Timestamp(row.resolution_session)
        if resolution_session not in session_locations or price_date not in session_locations:
            raise ValueError(f"terminal override uses non-session: {row.override_id}")
        price_row = indexed.loc[(price_date, str(row.price_sid))]
        requested_field = str(row.price_field)
        observed = float(price_row[requested_field])
        expected = float(row.expected_price)
        if not np.isclose(observed, expected, rtol=0.0, atol=1e-8):
            raise RuntimeError(
                f"terminal override {row.override_id} expected {expected}, got {observed}"
            )
        raw_field = "raw_open" if requested_field.endswith("open") else "raw_close"
        cash_per_share = float(price_row[raw_field])
        if not np.isfinite(cash_per_share) or cash_per_share <= 0:
            raise RuntimeError(f"terminal override raw recovery invalid: {row.override_id}")
        action_id = "LIQ_" + str(row.override_id)
        actions.append(
            {
                "action_id": action_id,
                "action_type": "cash_liquidation",
                "legal_effective_date": pd.Timestamp(row.membership_effective_to),
                "apply_session": resolution_session,
                "apply_phase": "pre_open",
                "source_sid": str(row.canonical_sid),
                "target_sid": pd.NA,
                "cash_per_source_share": cash_per_share,
                "currency": "USD",
                "target_shares_per_source_share": 0.0,
                "fractional_treatment": "not_applicable",
                "evidence_url": str(row.evidence_url),
                "notes": f"research terminal resolution: {row.reason}",
            }
        )
        stale = (
            None
            if not str(row.stale_sessions).strip()
            else int(row.stale_sessions)
        )
        audits.append(
            {
                "override_id": row.override_id,
                "action_id": action_id,
                "canonical_sid": row.canonical_sid,
                "resolution_type": row.resolution_type,
                "resolution_session": resolution_session,
                "price_date": price_date,
                "price_field": requested_field,
                "observed_price": observed,
                "cash_per_source_share": cash_per_share,
                "stale_sessions": stale,
                "reason": row.reason,
                "passed": True,
            }
        )
    return CorporateActionLedger(pd.DataFrame(actions)).to_frame(), pd.DataFrame(audits)


def _automatic_terminal_liquidations(
    *,
    prices: pd.DataFrame,
    membership: pd.DataFrame,
    audit: pd.DataFrame,
    actions: pd.DataFrame,
    sessions: pd.DatetimeIndex,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    indexed = canonicalize_prices(prices)
    locations = {date: i for i, date in enumerate(sessions)}
    action_sources = set(actions["source_sid"].astype(str))
    rows: list[dict[str, object]] = []
    audit_rows: list[dict[str, object]] = []
    for item in membership.loc[membership["effective_to"].notna()].itertuples(index=False):
        sid = str(item.sid)
        end = pd.Timestamp(item.effective_to)
        if not (RESEARCH_START <= end <= END) or sid in action_sources:
            continue
        sid_audit = audit.loc[
            audit["sid"].eq(sid) & pd.to_datetime(audit["signal_date"]).lt(end)
        ]
        could_hold = bool(
            sid_audit[
                [
                    "eligible_mom_255_0",
                    "eligible_mom_255_21",
                    "eligible_mom_12_1",
                ]
            ].any(axis=None)
        )
        after = sessions[sessions >= end][:5]
        try:
            sid_prices = indexed.xs(sid, level="sid")
        except KeyError:
            sid_prices = pd.DataFrame()
        if len(after) == 5 and not sid_prices.empty:
            next_rows = sid_prices.reindex(after)
            valid_open = pd.to_numeric(next_rows["tr_open"], errors="coerce")
            valid_volume = pd.to_numeric(next_rows.get("volume"), errors="coerce")
            continues = bool(
                (np.isfinite(valid_open) & valid_open.gt(0)).all()
                and (np.isfinite(valid_volume) & valid_volume.gt(0)).all()
            )
        else:
            continues = False
        if continues or not could_hold:
            continue
        candidates = sessions[sessions >= end]
        if not len(candidates):
            continue
        apply_session = pd.Timestamp(candidates[0])
        prior = sid_prices.loc[sid_prices.index < apply_session] if not sid_prices.empty else pd.DataFrame()
        if not prior.empty:
            raw = pd.to_numeric(prior["raw_close"], errors="coerce")
            tr = pd.to_numeric(prior["tr_close"], errors="coerce")
            valid = np.isfinite(raw) & raw.gt(0) & np.isfinite(tr) & tr.gt(0)
            prior = prior.loc[valid]
        if prior.empty:
            audit_rows.append(
                {
                    "canonical_sid": sid,
                    "membership_effective_to": end,
                    "apply_session": apply_session,
                    "fallback_date": pd.NaT,
                    "stale_sessions": pd.NA,
                    "cash_per_source_share": np.nan,
                    "action_id": "",
                    "passed": False,
                    "failure_reason": "no_strictly_prior_valid_close",
                }
            )
            continue
        fallback_date = pd.Timestamp(prior.index[-1])
        stale = locations[apply_session] - locations[fallback_date]
        cash_per_share = float(prior.iloc[-1]["raw_close"])
        passed = 1 <= stale <= MAX_TERMINAL_STALE_SESSIONS
        action_id = f"AUTO_LIQ_{sid.removeprefix('sec::')}_{apply_session:%Y%m%d}"
        audit_rows.append(
            {
                "canonical_sid": sid,
                "membership_effective_to": end,
                "apply_session": apply_session,
                "fallback_date": fallback_date,
                "stale_sessions": stale,
                "cash_per_source_share": cash_per_share,
                "action_id": action_id if passed else "",
                "passed": passed,
                "failure_reason": "" if passed else "terminal_last_close_over_5_sessions",
            }
        )
        if passed:
            rows.append(
                {
                    "action_id": action_id,
                    "action_type": "cash_liquidation",
                    "legal_effective_date": end,
                    "apply_session": apply_session,
                    "apply_phase": "pre_open",
                    "source_sid": sid,
                    "target_sid": pd.NA,
                    "cash_per_source_share": cash_per_share,
                    "currency": "USD",
                    "target_shares_per_source_share": 0.0,
                    "fractional_treatment": "not_applicable",
                    "evidence_url": "research://bounded-terminal-last-close-v3",
                    "notes": (
                        f"prototype liquidation at {fallback_date.date()} raw close; "
                        f"{stale} authoritative sessions old"
                    ),
                }
            )
    frame = pd.DataFrame(rows, columns=CorporateActionLedger.empty().to_frame().columns)
    actions_frame = CorporateActionLedger(frame).to_frame()
    return actions_frame, pd.DataFrame(audit_rows)


def _audit_corporate_action_valuation(
    prices: pd.DataFrame,
    actions: pd.DataFrame,
    sessions: pd.DatetimeIndex,
) -> pd.DataFrame:
    ledger = CorporateActionLedger(actions)
    ledger.validate_against_sessions(sessions)
    indexed = canonicalize_prices(prices)
    rows: list[dict[str, object]] = []
    for action in ledger.to_frame().itertuples(index=False):
        source_sid = str(action.source_sid)
        apply_session = pd.Timestamp(action.apply_session)
        legal_date = pd.Timestamp(action.legal_effective_date)
        try:
            history = indexed.xs(source_sid, level="sid")
        except KeyError:
            history = pd.DataFrame()
        eligible = (
            history.loc[
                (history.index <= legal_date) & (history.index < apply_session),
                ["tr_close", "raw_close"],
            ]
            if not history.empty
            else pd.DataFrame(columns=["tr_close", "raw_close"])
        )
        if not eligible.empty:
            tr = pd.to_numeric(eligible["tr_close"], errors="coerce")
            raw = pd.to_numeric(eligible["raw_close"], errors="coerce")
            valid = np.isfinite(tr) & tr.gt(0) & np.isfinite(raw) & raw.gt(0)
            eligible = eligible.loc[valid]
        source_valid = not eligible.empty
        source_date = pd.Timestamp(eligible.index[-1]) if source_valid else pd.NaT
        source_factor = (
            float(eligible.iloc[-1]["tr_close"] / eligible.iloc[-1]["raw_close"])
            if source_valid
            else np.nan
        )
        source_valid = bool(source_valid and np.isfinite(source_factor) and source_factor > 0)

        stock_ratio = float(action.target_shares_per_source_share)
        target_valid = True
        target_factor = np.nan
        if stock_ratio > 0:
            target_sid = str(action.target_sid)
            try:
                target = indexed.loc[(apply_session, target_sid)]
                tr_open = float(target["tr_open"])
                raw_open = float(target["raw_open"])
                target_factor = tr_open / raw_open
                target_valid = bool(
                    np.isfinite(tr_open)
                    and tr_open > 0
                    and np.isfinite(raw_open)
                    and raw_open > 0
                    and np.isfinite(target_factor)
                    and target_factor > 0
                )
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                target_valid = False
        passed = source_valid and target_valid
        failures: list[str] = []
        if not source_valid:
            failures.append("invalid_source_factor")
        if not target_valid:
            failures.append("invalid_target_apply_open")
        rows.append(
            {
                "action_id": action.action_id,
                "action_type": action.action_type,
                "source_sid": source_sid,
                "target_sid": action.target_sid,
                "legal_effective_date": legal_date,
                "apply_session": apply_session,
                "source_factor_date": source_date,
                "source_adjustment_factor": source_factor,
                "source_factor_valid": source_valid,
                "target_apply_open_valid": target_valid,
                "target_adjustment_factor": target_factor,
                "passed": passed,
                "failure_reason": "|".join(failures),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["apply_session", "action_id"], ignore_index=True
    )


def _could_have_been_held(audit: pd.DataFrame, sid: str, end: pd.Timestamp) -> bool:
    history = audit.loc[
        audit["sid"].eq(sid) & pd.to_datetime(audit["signal_date"]).lt(end)
    ]
    if history.empty:
        return False
    return bool(
        history[
            [
                "eligible_mom_255_0",
                "eligible_mom_255_21",
                "eligible_mom_12_1",
            ]
        ].any(axis=None)
    )


def _terminal_event_resolution(
    *,
    prices: pd.DataFrame,
    membership: pd.DataFrame,
    audit: pd.DataFrame,
    actions: pd.DataFrame,
    action_valuation: pd.DataFrame,
    sessions: pd.DatetimeIndex,
) -> pd.DataFrame:
    indexed = canonicalize_prices(prices)
    valid_actions = set(
        action_valuation.loc[action_valuation["passed"], "action_id"].astype(str)
    )
    rows: list[dict[str, object]] = []
    for item in membership.loc[membership["effective_to"].notna()].itertuples(index=False):
        sid = str(item.sid)
        end = pd.Timestamp(item.effective_to)
        if not (RESEARCH_START <= end <= END):
            continue
        action = actions.loc[actions["source_sid"].astype(str).eq(sid)]
        if not action.empty and str(action.iloc[0]["action_id"]) in valid_actions:
            selected = action.iloc[0]
            classification = str(selected["action_type"])
            evidence = str(selected["evidence_url"])
            uses_fallback = classification == "cash_liquidation" and str(
                selected["evidence_url"]
            ).startswith("research://bounded-terminal-last-close")
            if uses_fallback:
                note = str(selected.get("notes", ""))
                stale = int(note.split("; ")[-1].split(" ")[0])
            else:
                stale = 0
            action_id = str(selected["action_id"])
        else:
            after = sessions[sessions >= end][:5]
            try:
                sid_prices = indexed.xs(sid, level="sid").reindex(after)
                opens = pd.to_numeric(sid_prices["tr_open"], errors="coerce")
                volumes = pd.to_numeric(sid_prices.get("volume"), errors="coerce")
                continuous = len(after) == 5 and bool(
                    (np.isfinite(opens) & opens.gt(0)).all()
                    and (np.isfinite(volumes) & volumes.gt(0)).all()
                )
            except KeyError:
                continuous = False
            if continuous:
                classification = "normal_removal_continues_trading"
                evidence = "five positive-volume exchange-session opens after membership end"
            elif not _could_have_been_held(audit, sid, end):
                classification = "unavailable_not_holdable"
                evidence = "no complete baseline signal before exit"
            else:
                classification = "unresolved"
                evidence = "no executable terminal resolution"
            uses_fallback = False
            stale = 0
            action_id = ""
        rows.append(
            {
                "canonical_sid": sid,
                "membership_effective_to": end,
                "classification": classification,
                "action_id": action_id,
                "evidence": evidence,
                "uses_terminal_last_close": uses_fallback,
                "stale_sessions": stale,
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["membership_effective_to", "canonical_sid"], ignore_index=True
    )


def _terminal_execution_resolution(
    *,
    prices: pd.DataFrame,
    membership: pd.DataFrame,
    audit: pd.DataFrame,
    actions: pd.DataFrame,
    action_valuation: pd.DataFrame,
    terminal_override_audit: pd.DataFrame,
    sessions: pd.DatetimeIndex,
) -> pd.DataFrame:
    indexed = canonicalize_prices(prices)
    locations = {date: i for i, date in enumerate(sessions)}
    valuation = action_valuation.set_index("action_id")
    deferred_ids = set(
        terminal_override_audit.loc[
            terminal_override_audit["resolution_type"].eq(
                "first_tradable_otc_open_liquidation"
            ),
            "action_id",
        ].astype(str)
    )
    schedules = {
        frequency: rebalance_schedule(sessions, frequency)
        for frequency in ("weekly", "monthly")
    }
    rows: list[dict[str, object]] = []
    for item in membership.loc[membership["effective_to"].notna()].itertuples(index=False):
        sid = str(item.sid)
        end = pd.Timestamp(item.effective_to)
        if not (RESEARCH_START <= end <= END):
            continue
        could_hold = _could_have_been_held(audit, sid, end)
        source_actions = actions.loc[actions["source_sid"].astype(str).eq(sid)]
        action = None if source_actions.empty else source_actions.iloc[0]
        for frequency, schedule in schedules.items():
            plan = schedule.loc[
                (pd.to_datetime(schedule["signal_date"]) >= end)
                & (pd.to_datetime(schedule["execution_date"]) <= END)
            ]
            if plan.empty:
                rows.append(
                    _terminal_execution_row(
                        sid, end, frequency, pd.NaT, pd.NaT,
                        "out_of_evaluation", True, could_hold,
                    )
                )
                continue
            event = plan.iloc[0]
            signal_date = pd.Timestamp(event["signal_date"])
            execution = pd.Timestamp(event["execution_date"])
            try:
                open_value = float(indexed.loc[(execution, sid), "tr_open"])
                direct_open = np.isfinite(open_value) and open_value > 0
            except (KeyError, TypeError, ValueError):
                direct_open = False
            if direct_open:
                rows.append(
                    _terminal_execution_row(
                        sid, end, frequency, signal_date, execution,
                        "direct_open", True, could_hold,
                    )
                )
                continue
            if action is not None:
                action_id = str(action["action_id"])
                apply_session = pd.Timestamp(action["apply_session"])
                action_valid = bool(
                    action_id in valuation.index and valuation.loc[action_id, "passed"]
                )
                if action_valid and apply_session <= execution:
                    row = _terminal_execution_row(
                        sid, end, frequency, signal_date, execution,
                        "corporate_action", True, could_hold,
                    )
                    row.update(
                        action_id=action_id,
                        action_apply_session=apply_session,
                        action_valuation_valid=True,
                    )
                    rows.append(row)
                    continue
                if action_valid and action_id in deferred_ids:
                    deferred_age = locations[apply_session] - locations[execution]
                    passed = 0 < deferred_age <= MAX_DEFERRED_ACTION_SESSIONS
                    row = _terminal_execution_row(
                        sid, end, frequency, signal_date, execution,
                        "deferred_corporate_action", passed, could_hold,
                    )
                    row.update(
                        action_id=action_id,
                        action_apply_session=apply_session,
                        action_valuation_valid=True,
                        stale_sessions=deferred_age,
                        failure_reason=(
                            "" if passed else "deferred_action_over_25_sessions"
                        ),
                    )
                    rows.append(row)
                    continue
            if not could_hold:
                rows.append(
                    _terminal_execution_row(
                        sid, end, frequency, signal_date, execution,
                        "not_holdable_no_price", True, False,
                    )
                )
                continue
            try:
                sid_prices = indexed.xs(sid, level="sid")
                prior = pd.to_numeric(
                    sid_prices.loc[sid_prices.index < execution, "tr_close"],
                    errors="coerce",
                )
                prior = prior[np.isfinite(prior) & prior.gt(0)]
            except KeyError:
                prior = pd.Series(dtype=float)
            if prior.empty:
                row = _terminal_execution_row(
                    sid, end, frequency, signal_date, execution,
                    "unresolved", False, True,
                )
                row["failure_reason"] = "no_strictly_prior_valid_close"
                rows.append(row)
                continue
            fallback_date = pd.Timestamp(prior.index[-1])
            stale = locations[execution] - locations[fallback_date]
            passed = 1 <= stale <= MAX_TERMINAL_STALE_SESSIONS
            row = _terminal_execution_row(
                sid, end, frequency, signal_date, execution,
                "last_close", passed, True,
            )
            row.update(
                fallback_date=fallback_date,
                stale_sessions=stale,
                failure_reason=(
                    "" if passed else "terminal_last_close_over_5_sessions"
                ),
            )
            rows.append(row)
    return pd.DataFrame(rows).sort_values(
        ["membership_effective_to", "canonical_sid", "frequency"],
        ignore_index=True,
    )


def _terminal_execution_row(
    sid: str,
    end: pd.Timestamp,
    frequency: str,
    signal_date: pd.Timestamp,
    execution_date: pd.Timestamp,
    method: str,
    passed: bool,
    could_hold: bool,
) -> dict[str, object]:
    return {
        "canonical_sid": sid,
        "membership_effective_to": end,
        "frequency": frequency,
        "next_signal_date": signal_date,
        "next_execution_date": execution_date,
        "could_have_been_held": could_hold,
        "resolution_method": method,
        "action_id": "",
        "action_apply_session": pd.NaT,
        "action_valuation_valid": False,
        "fallback_date": pd.NaT,
        "stale_sessions": 0,
        "passed": bool(passed),
        "failure_reason": "" if passed else "unresolved_terminal_execution",
    }


def _gap_outliers(prices: pd.DataFrame) -> pd.DataFrame:
    indexed = canonicalize_prices(prices)
    rows: list[dict[str, object]] = []
    for sid in ("sec::SIVB", "sec::SBNY"):
        history = indexed.xs(sid, level="sid")
        valid = pd.to_numeric(history["tr_close"], errors="coerce").dropna()
        current = valid.loc[pd.Timestamp("2023-03-28")]
        previous = valid.loc[valid.index < pd.Timestamp("2023-03-28")].iloc[-1]
        value = float(current / previous - 1.0)
        rows.append(
            {
                "sid": sid,
                "date": pd.Timestamp("2023-03-28"),
                "metric": "last_valid_to_next_valid_close",
                "value": value,
                "status": "reviewed",
                "evidence": "Tiingo first post-halt OTC session",
                "decision": "retain terminal loss and liquidate at audited OTC open",
            }
        )
    return pd.DataFrame(rows)


def _run_gates(
    tables: dict[str, pd.DataFrame],
    artifacts: dict[str, pd.DataFrame],
    reproducible: bool,
) -> dict[str, dict[str, Any]]:
    prices = tables["prices_daily"]
    membership = tables["membership"]
    summary = tables["qa_summary"]
    actions = tables["corporate_actions"]
    action_valuation = artifacts["corporate_action_valuation"]
    terminal_events = artifacts["terminal_event_resolution"]
    terminal_execution = artifacts["terminal_execution_resolution"]
    expected_dates = set(v2_builder._signal_dates(tables["calendar"]))
    observed_dates = set(pd.to_datetime(summary["signal_date"]))

    duplicates = int(prices.duplicated(["date", "sid"]).sum())
    price_values = prices[list(PRICE_OHLC)]
    non_null = price_values.notna().sum(axis=1)
    partial = int(((non_null > 0) & (non_null < len(PRICE_OHLC))).sum())
    invalid = 0
    for column in PRICE_OHLC:
        values = pd.to_numeric(prices[column], errors="coerce")
        invalid += int((values.notna() & (~np.isfinite(values) | values.le(0))).sum())

    intervals = membership.copy()
    intervals["effective_from"] = pd.to_datetime(intervals["effective_from"])
    intervals["effective_to"] = pd.to_datetime(intervals["effective_to"])
    membership_valid = True
    try:
        PITMembership.from_intervals(intervals)
    except Exception:
        membership_valid = False

    coverage = {
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
    coverage_signal_pass = (
        coverage["minimum_signal_close"] >= 0.98
        and coverage["average_signal_close"] >= 0.99
    )
    coverage_factor_pass = (
        min(
            coverage["minimum_mom_255_0"],
            coverage["minimum_mom_255_21"],
            coverage["minimum_mom_12_1"],
        )
        >= 0.98
        and min(
            coverage["average_mom_255_0"],
            coverage["average_mom_255_21"],
            coverage["average_mom_12_1"],
        )
        >= 0.99
    )
    invalid_action_ids = action_valuation.loc[
        ~action_valuation["passed"], "action_id"
    ].astype(str).tolist()
    terminal_failures = terminal_execution.loc[~terminal_execution["passed"]]
    fallback_over_5 = int(
        (
            terminal_execution["resolution_method"].eq("last_close")
            & terminal_execution["stale_sessions"].gt(
                MAX_TERMINAL_STALE_SESSIONS
            )
        ).sum()
    )
    unresolved_terminal = int(
        terminal_execution["resolution_method"].eq("unresolved").sum()
    )
    all_action_sources_unique = not actions["source_sid"].duplicated().any()

    research_sessions = pd.DatetimeIndex(
        pd.to_datetime(tables["calendar"]["session_date"])
    )
    research_sessions = research_sessions[
        (research_sessions >= RESEARCH_START) & (research_sessions <= END)
    ]
    benchmark_dates = set(pd.to_datetime(tables["benchmark_daily"]["date"]))
    rf_dates = set(pd.to_datetime(tables["risk_free_daily"]["date"]))
    benchmark_missing = len(set(research_sessions).difference(benchmark_dates))
    rf_missing = len(set(research_sessions).difference(rf_dates))

    return {
        "reproducible_double_build": _gate(reproducible, hashes_match=reproducible),
        "unique_price_key": _gate(duplicates == 0, duplicates=duplicates),
        "price_structure": _gate(
            partial == 0 and invalid == 0,
            partial_ohlc=partial,
            invalid_numeric=invalid,
        ),
        "membership_valid": _gate(
            membership_valid,
            intervals=len(membership),
        ),
        "all_signal_dates_present": _gate(
            expected_dates == observed_dates,
            missing=len(expected_dates - observed_dates),
            extra=len(observed_dates - expected_dates),
        ),
        "boundary_dates": _gate(
            SIGNAL_START in observed_dates
            and RESEARCH_START in set(research_sessions)
            and END in set(research_sessions)
        ),
        "benchmark_and_rf": _gate(
            benchmark_missing == 0 and rf_missing == 0,
            benchmark_missing=benchmark_missing,
            rf_missing=rf_missing,
        ),
        "coverage_signal_close": _gate(coverage_signal_pass, **coverage),
        "coverage_factor_endpoints": _gate(coverage_factor_pass, **coverage),
        "coverage_next_open": _gate(
            coverage["minimum_next_open"] >= 0.995,
            **coverage,
        ),
        "tradability_overrides": _gate(
            len(artifacts["tradability_override_audit"]) == 45,
            override_rows=len(
                TradabilityOverrideLedger.from_csv(
                    INPUT_DIR / "tradability_overrides.csv"
                ).to_frame()
            ),
            masked_price_rows=len(artifacts["tradability_override_audit"]),
        ),
        "membership_boundary_overrides": _gate(
            len(artifacts["membership_boundary_audit"]) == 1
            and not (
                membership["sid"].eq("sec::TSS")
                & pd.to_datetime(membership["effective_to"]).gt(
                    pd.Timestamp("2019-09-19")
                )
            ).any(),
            applied_rows=len(artifacts["membership_boundary_audit"]),
        ),
        "corporate_action_valuation": _gate(
            not invalid_action_ids and all_action_sources_unique,
            action_count=len(action_valuation),
            invalid_action_ids=invalid_action_ids,
            duplicate_source_actions=int(actions["source_sid"].duplicated().sum()),
        ),
        "terminal_classified": _gate(
            not terminal_events["classification"].eq("unresolved").any(),
            exits=len(terminal_events),
            unresolved=int(terminal_events["classification"].eq("unresolved").sum()),
        ),
        "terminal_execution_weekly_monthly": _gate(
            terminal_failures.empty
            and fallback_over_5 == 0
            and unresolved_terminal == 0,
            evaluated_rows=len(terminal_execution),
            fallback_over_5_sessions=fallback_over_5,
            unresolved_rows=unresolved_terminal,
            failed_rows=len(terminal_failures),
            maximum_accepted_stale_sessions=int(
                terminal_execution.loc[
                    terminal_execution["passed"], "stale_sessions"
                ].max()
                if len(terminal_execution)
                else 0
            ),
        ),
        "bank_halt_resolution": _gate(
            _bank_halt_gate(tables, artifacts),
            gap_outlier_rows=len(artifacts["gap_outlier_allowlist"]),
        ),
        "provider_lineage": _gate(
            tables["provider_lineage"]["provider"].notna().all()
            and not tables["provider_lineage"]["canonical_sid"].eq("sec::CP").any(),
            missing=int(tables["provider_lineage"]["provider"].isna().sum()),
            phantom_cp_rows=int(
                tables["provider_lineage"]["canonical_sid"].eq("sec::CP").sum()
            ),
        ),
    }


def _bank_halt_gate(
    tables: dict[str, pd.DataFrame], artifacts: dict[str, pd.DataFrame]
) -> bool:
    prices = canonicalize_prices(tables["prices_daily"])
    actions = tables["corporate_actions"]
    checks = (
        ("sec::SIVB", pd.Timestamp("2023-03-10"), pd.Timestamp("2023-03-27"), 0.53),
        ("sec::SBNY", pd.Timestamp("2023-03-13"), pd.Timestamp("2023-03-27"), 0.41),
    )
    for sid, start, end, recovery in checks:
        rows = prices.xs(sid, level="sid").loc[start:end, list(PRICE_OHLC)]
        if rows.notna().any(axis=None):
            return False
        action = actions.loc[
            actions["source_sid"].astype(str).eq(sid)
            & actions["action_type"].eq("cash_liquidation")
        ]
        if len(action) != 1:
            return False
        row = action.iloc[0]
        if pd.Timestamp(row["apply_session"]) != pd.Timestamp("2023-03-28"):
            return False
        if not np.isclose(float(row["cash_per_source_share"]), recovery):
            return False
    return set(artifacts["gap_outlier_allowlist"]["sid"]) == {
        "sec::SIVB",
        "sec::SBNY",
    }


def _gate(passed: bool, **details: Any) -> dict[str, Any]:
    return {"passed": bool(passed), **details}


def _content_hash(frame: pd.DataFrame) -> str:
    normalized = frame.copy()
    for column in normalized.columns:
        if pd.api.types.is_datetime64_any_dtype(normalized[column]):
            normalized[column] = normalized[column].astype("datetime64[ns]")
    values = pd.util.hash_pandas_object(normalized, index=True).to_numpy()
    return hashlib.sha256(values.tobytes()).hexdigest()


def _write_quality(
    tables: dict[str, pd.DataFrame],
    artifacts: dict[str, pd.DataFrame],
    gates: dict[str, dict[str, Any]],
    hashes: dict[str, str],
    reproducible: bool,
) -> None:
    tables["security_identity_resolution"].to_csv(
        QUALITY_DIR / "security_identity_resolution.csv", index=False
    )
    tables["provider_lineage"].to_parquet(
        QUALITY_DIR / "provider_lineage.parquet", index=False
    )
    for name, frame in artifacts.items():
        frame.to_csv(QUALITY_DIR / f"{name}.csv", index=False)
    provenance = _build_provenance()
    (QUALITY_DIR / "build_provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    payload = {
        "candidate_version": CANDIDATE_VERSION,
        "source_version": SOURCE_VERSION,
        "all_passed": all(bool(item["passed"]) for item in gates.values()),
        "reproducible_double_build": reproducible,
        "table_content_hashes": hashes,
        "gates": gates,
    }
    (QUALITY_DIR / "gate_results.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _build_provenance() -> dict[str, object]:
    critical = (
        Path(__file__).resolve(),
        ROOT / "scripts" / "build_data_repair_candidate.py",
        ROOT / "src" / "momentum_reversal" / "backtest" / "calendar.py",
        ROOT / "src" / "momentum_reversal" / "backtest" / "engine.py",
        ROOT / "src" / "momentum_reversal" / "data" / "calendar.py",
        ROOT / "src" / "momentum_reversal" / "data" / "corporate_actions.py",
        ROOT / "src" / "momentum_reversal" / "data" / "membership.py",
        ROOT / "src" / "momentum_reversal" / "data" / "qa.py",
        ROOT / "src" / "momentum_reversal" / "data" / "schema.py",
        ROOT / "src" / "momentum_reversal" / "data" / "storage.py",
        ROOT / "src" / "momentum_reversal" / "data" / "tradability.py",
        ROOT / "src" / "momentum_reversal" / "pipelines" / "run_context.py",
        ROOT / "src" / "momentum_reversal" / "pipelines" / "g00.py",
    )
    return {
        "schema_version": "momentum_reversal.build_provenance.v1",
        "critical_sources": [
            {
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_file(path),
            }
            for path in critical
        ],
        "runtime": {
            "python": platform.python_version(),
            "pandas": importlib.metadata.version("pandas"),
            "numpy": importlib.metadata.version("numpy"),
            "pyarrow": importlib.metadata.version("pyarrow"),
        },
    }


def _write_candidate(
    tables: dict[str, pd.DataFrame],
    artifacts: dict[str, pd.DataFrame],
    gates: dict[str, dict[str, Any]],
    hashes: dict[str, str],
) -> None:
    layout = DatasetLayout(DATA_ROOT)
    store = ParquetStore(layout)
    written: list[Path] = []
    for name in (
        "prices_daily",
        "membership",
        "security_master",
        "calendar",
        "benchmark_daily",
        "risk_free_daily",
        "corporate_actions",
        "provider_lineage",
        "universe_at_signal",
        "qa_summary",
    ):
        frame = tables[name]
        if name == "prices_daily":
            written.append(
                store.write_curated_prices(
                    canonicalize_prices(frame), dataset_version=CANDIDATE_VERSION
                )
            )
        else:
            written.append(
                store.write_curated_table(
                    frame,
                    dataset_version=CANDIDATE_VERSION,
                    table_name=name,
                )
            )

    referenced = _referenced_inputs() + written
    referenced.extend(
        [
            QUALITY_DIR / "security_identity_resolution.csv",
            QUALITY_DIR / "provider_lineage.parquet",
            QUALITY_DIR / "build_provenance.json",
            QUALITY_DIR / "gate_results.json",
            *[
                QUALITY_DIR / f"{name}.csv"
                for name in artifacts
            ],
        ]
    )
    test_log = QUALITY_DIR / "test_results.json"
    if test_log.is_file():
        referenced.append(test_log)

    terminal_gate = gates["terminal_execution_weekly_monthly"]
    action_gate = gates["corporate_action_valuation"]
    ManifestStore(layout).write(
        CANDIDATE_VERSION,
        {
            "status": "review",
            "experiment_ready_candidate": True,
            "formal_eligible": False,
            "research_tier": "free_research_candidate",
            "source_version": SOURCE_VERSION,
            "calendar_source": "XNYS",
            "request": {
                "price_start": "2013-01-02",
                "research_start": str(RESEARCH_START.date()),
                "end": str(END.date()),
                "actions": True,
                "auto_adjust": False,
                "keepna": True,
            },
            "benchmark": {
                "symbol": "SPY",
                "label": "SPY_total_return",
                "kind": "investable_proxy",
                "price_field": "total_return_adjusted_ohlc",
                "is_primary_sp500_total_return": False,
            },
            "risk_free": {
                "provided": True,
                "curated_table": "risk_free_daily",
                "source": "Ken_French_daily_RF_official_zip",
                "units": "decimal_return_per_exchange_session",
                "annualized_yield": False,
                "percent_units": False,
                "coverage_start": str(RESEARCH_START.date()),
                "coverage_end": str(END.date()),
                "row_count": len(tables["risk_free_daily"]),
            },
            "corporate_actions": {
                "provided": True,
                "curated_table": "corporate_actions",
                "apply_phase": "pre_open",
                "valuation_gate": "corporate_action_valuation",
                "valuation_gate_passed": bool(action_gate["passed"]),
                "record_count": len(tables["corporate_actions"]),
            },
            "prototype_valuation_policy": "carry_last_close",
            "prototype_execution_policy": "leave_cash",
            "prototype_signed_execution_policy": "terminal_last_close",
            "prototype_terminal_last_close_max_sessions": MAX_TERMINAL_STALE_SESSIONS,
            "terminal_gate": {
                "artifact": (
                    f"quality/{CANDIDATE_VERSION}/"
                    "terminal_execution_resolution.csv"
                ),
                "frequencies": ["weekly", "monthly"],
                "max_stale_sessions": MAX_TERMINAL_STALE_SESSIONS,
                "passed": bool(terminal_gate["passed"]),
            },
            "build_provenance": {
                "artifact": f"quality/{CANDIDATE_VERSION}/build_provenance.json",
                "schema_version": "momentum_reversal.build_provenance.v1",
            },
            "table_content_hashes": hashes,
            "gate_summary": {
                name: bool(item["passed"]) for name, item in gates.items()
            },
            "formal_blockers": [
                "free public PIT membership",
                "free Yahoo/Tiingo coverage with documented unavailable securities",
                "SPY total-return proxy instead of official S&P 500 Total Return index",
            ],
        },
        referenced_files=list(dict.fromkeys(Path(path).resolve() for path in referenced)),
    )


def _referenced_inputs() -> list[Path]:
    paths = [
        DATA_ROOT / "manifests" / f"{SOURCE_VERSION}.json",
        Path(__file__).resolve(),
        ROOT / "scripts" / "build_data_repair_candidate.py",
        ROOT / "pyproject.toml",
        *sorted(INPUT_DIR.glob("*.csv")),
        INPUT_DIR / "README.md",
        *sorted(SOURCE_DIR.glob("*.parquet")),
        SOURCE_QUALITY_DIR / "security_identity_resolution.csv",
    ]
    old = DATA_ROOT / "raw" / "tiingo" / "tiingo-gap-audit-20260813-v1"
    paths.extend(
        [
            old / "coverage.csv",
            old / "responses" / "ANDV.json",
            old / "responses" / "DISCK.json",
            old / "responses" / "HES.json",
            old / "responses" / "TWTR.json",
        ]
    )
    provenance = _build_provenance()
    for item in provenance["critical_sources"]:  # type: ignore[index]
        paths.append(ROOT / str(item["path"]))  # type: ignore[index]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"manifest inputs missing: {missing}")
    return paths


def _write_report(
    tables: dict[str, pd.DataFrame],
    artifacts: dict[str, pd.DataFrame],
    gates: dict[str, dict[str, Any]],
    reproducible: bool,
    ready: bool,
) -> None:
    summary = tables["qa_summary"]
    action_valuation = artifacts["corporate_action_valuation"]
    terminal_execution = artifacts["terminal_execution_resolution"]
    failed = [name for name, item in gates.items() if not item["passed"]]
    text = f"""# {CANDIDATE_VERSION} 有限返修报告

状态：`{'READY_FOR_FINAL_REVIEW' if ready else 'BLOCKED'}`  
来源：`{SOURCE_VERSION}`  
本次仅处理已冻结的执行级阻塞项；未补新证券、未扩数据源、未运行策略。

## 固定返修范围

- tradability override：{len(TradabilityOverrideLedger.from_csv(INPUT_DIR / 'tradability_overrides.csv').to_frame())} 条，命中 {len(artifacts['tradability_override_audit'])} 个价格行；
- membership boundary override：{len(artifacts['membership_boundary_audit'])} 条；
- corporate actions：{len(tables['corporate_actions'])} 条，其中研究级 cash liquidation {int(tables['corporate_actions']['action_type'].eq('cash_liquidation').sum())} 条；
- corporate-action valuation：{int(action_valuation['passed'].sum())}/{len(action_valuation)} 通过；
- terminal execution rows：{int(terminal_execution['passed'].sum())}/{len(terminal_execution)} 通过；
- bank halt gap audit：{len(artifacts['gap_outlier_allowlist'])} 条；
- double build reproducible：{str(reproducible).lower()}。

## 覆盖率

- signal close minimum / average：{summary['signal_close_coverage'].min():.6f} / {summary['signal_close_coverage'].mean():.6f}
- MOM 255-0 minimum / average：{summary['mom_255_0_history_coverage'].min():.6f} / {summary['mom_255_0_history_coverage'].mean():.6f}
- MOM 255-21 minimum / average：{summary['mom_255_21_history_coverage'].min():.6f} / {summary['mom_255_21_history_coverage'].mean():.6f}
- MOM 12-1 minimum / average：{summary['mom_12_1_history_coverage'].min():.6f} / {summary['mom_12_1_history_coverage'].mean():.6f}
- eligible next-open minimum：{summary['eligible_execution_open_coverage'].min():.6f}

## Gates

{chr(10).join(f'- {name}: {"PASS" if payload["passed"] else "FAIL"} — {json.dumps({k: v for k, v in payload.items() if k != "passed"}, ensure_ascii=False)}' for name, payload in gates.items())}

失败门槛：{', '.join(failed) if failed else '无'}。

## 固定限制

- 免费公开 PIT 名单与 Yahoo/Tiingo 数据，只批准研究用途；
- `cash_liquidation` 是有审计证据的研究近似，不冒充法律对价；
- SPY 为可投资总回报代理，不是官方 SPXTR；
- 未继续追补其余 unavailable securities。
"""
    report = ROOT / "docs" / "10_data" / f"{CANDIDATE_VERSION}_implementation_report.md"
    report.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
