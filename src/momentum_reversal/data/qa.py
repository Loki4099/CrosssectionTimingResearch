"""Explicit data-quality and point-in-time coverage audits."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from .calendar import TradingCalendar
from .membership import PITUniverse
from .schema import DataSchemaError, canonicalize_prices, normalize_session_date


class DataQualityError(RuntimeError):
    """Raised when an experiment would otherwise continue with invalid data."""


def build_universe_audit(
    prices: pd.DataFrame,
    membership: PITUniverse,
    signal_dates: Iterable[object],
    calendar: TradingCalendar,
) -> pd.DataFrame:
    """Audit the exact formation endpoints for every frozen momentum signal.

    ``eligible`` is the conservative all-signal compatibility field.  The three
    signal-specific eligibility fields are authoritative for their respective
    paths.  The next execution open is diagnostic and never changes any of
    those signal-date eligibility decisions.
    """

    frame = canonicalize_prices(prices)
    close = frame["tr_close"].unstack("sid")
    open_ = frame["tr_open"].unstack("sid")
    records: list[dict[str, object]] = []
    session_positions = pd.Series(
        np.arange(len(calendar.sessions)), index=calendar.sessions
    )
    month_ends = pd.Series(
        calendar.last_sessions_of_month(),
        index=calendar.last_sessions_of_month().to_period("M"),
    )

    for value in signal_dates:
        signal_date = normalize_session_date(value)
        if not calendar.contains(signal_date):
            raise DataQualityError(f"signal date is not a trading session: {signal_date.date()}")
        members = tuple(sorted(map(str, membership.members_on(signal_date))))
        snapshot_date = pd.NaT
        snapshot_age_days = np.nan
        snapshot_dates = getattr(membership, "snapshot_dates", ())
        if snapshot_dates:
            past_snapshots = [date for date in snapshot_dates if date <= signal_date]
            if past_snapshots:
                snapshot_date = max(past_snapshots)
                snapshot_age_days = int((signal_date - snapshot_date).days)
        try:
            execution_date = calendar.next_session(signal_date)
        except KeyError:
            execution_date = pd.NaT

        position = int(session_positions.loc[signal_date])
        endpoint_255 = calendar.sessions[position - 255] if position >= 255 else None
        endpoint_21 = calendar.sessions[position - 21] if position >= 21 else None
        signal_period = signal_date.to_period("M")
        endpoint_month_1 = month_ends.get(signal_period - 1)
        endpoint_month_12 = month_ends.get(signal_period - 12)

        signal_close = _valid_prices_at(close, (signal_date,), members)
        history_255_0 = _valid_prices_at(
            close, _defined_endpoints(signal_date, endpoint_255), members
        )
        history_255_21 = _valid_prices_at(
            close, _defined_endpoints(endpoint_21, endpoint_255), members
        )
        history_12_1 = _valid_prices_at(
            close, _defined_endpoints(endpoint_month_1, endpoint_month_12), members
        )
        history_complete = history_255_0 & history_255_21 & history_12_1
        if pd.notna(execution_date):
            execution_open = _valid_prices_at(open_, (execution_date,), members)
        else:
            execution_open = pd.Series(False, index=members, dtype=bool)

        for sid in members:
            has_history = bool(history_complete.get(sid, False))
            has_255_0 = bool(history_255_0.get(sid, False))
            has_255_21 = bool(history_255_21.get(sid, False))
            has_12_1 = bool(history_12_1.get(sid, False))
            has_signal_close = bool(signal_close.get(sid, False))
            has_execution_open = bool(execution_open.get(sid, False))
            if not has_signal_close:
                reason = "missing_signal_close"
            elif not has_history:
                missing_factors = [
                    name
                    for name, available in (
                        ("mom_255_0", has_255_0),
                        ("mom_255_21", has_255_21),
                        ("mom_12_1", has_12_1),
                    )
                    if not available
                ]
                reason = "missing_factor_endpoints:" + "|".join(missing_factors)
            else:
                reason = ""
            records.append(
                {
                    "signal_date": signal_date,
                    "execution_date": execution_date,
                    "membership_snapshot_date": snapshot_date,
                    "membership_snapshot_age_days": snapshot_age_days,
                    "sid": sid,
                    "is_member": True,
                    "has_mom_255_0_history": has_255_0,
                    "has_mom_255_21_history": has_255_21,
                    "has_mom_12_1_history": has_12_1,
                    "has_signal_history": has_history,
                    "has_execution_open": has_execution_open,
                    "eligible_mom_255_0": has_255_0,
                    "eligible_mom_255_21": has_255_21,
                    "eligible_mom_12_1": has_12_1,
                    "eligible": has_signal_close and has_history,
                    "exclusion_reason": reason,
                }
            )
    columns = [
        "signal_date",
        "execution_date",
        "membership_snapshot_date",
        "membership_snapshot_age_days",
        "sid",
        "is_member",
        "has_mom_255_0_history",
        "has_mom_255_21_history",
        "has_mom_12_1_history",
        "has_signal_history",
        "has_execution_open",
        "eligible_mom_255_0",
        "eligible_mom_255_21",
        "eligible_mom_12_1",
        "eligible",
        "exclusion_reason",
    ]
    return pd.DataFrame.from_records(records, columns=columns).sort_values(
        ["signal_date", "sid"], ignore_index=True
    )


def summarize_universe_audit(audit: pd.DataFrame) -> pd.DataFrame:
    required = {
        "signal_date",
        "sid",
        "has_mom_255_0_history",
        "has_mom_255_21_history",
        "has_mom_12_1_history",
        "has_signal_history",
        "has_execution_open",
        "eligible",
    }
    missing = required.difference(audit.columns)
    if missing:
        raise DataSchemaError(f"universe audit missing columns: {sorted(missing)}")
    grouped = audit.groupby("signal_date", sort=True)
    summary = grouped.agg(
        member_count=("sid", "size"),
        mom_255_0_history_complete_count=("has_mom_255_0_history", "sum"),
        mom_255_21_history_complete_count=("has_mom_255_21_history", "sum"),
        mom_12_1_history_complete_count=("has_mom_12_1_history", "sum"),
        history_complete_count=("has_signal_history", "sum"),
        eligible_count=("eligible", "sum"),
        execution_open_count=("has_execution_open", "sum"),
        membership_snapshot_age_days=("membership_snapshot_age_days", "max"),
    )
    summary["history_coverage"] = (
        summary["history_complete_count"] / summary["member_count"]
    )
    for signal in ("mom_255_0", "mom_255_21", "mom_12_1"):
        summary[f"{signal}_history_coverage"] = (
            summary[f"{signal}_history_complete_count"] / summary["member_count"]
        )
    summary["execution_open_coverage"] = (
        summary["execution_open_count"] / summary["member_count"]
    )
    return summary.reset_index()


def _defined_endpoints(*values: object) -> tuple[pd.Timestamp, ...] | None:
    if any(value is None or pd.isna(value) for value in values):
        return None
    return tuple(normalize_session_date(value) for value in values)


def _valid_prices_at(
    panel: pd.DataFrame,
    endpoints: tuple[pd.Timestamp, ...] | None,
    members: tuple[str, ...],
) -> pd.Series:
    if endpoints is None:
        return pd.Series(False, index=members, dtype=bool)
    values = panel.reindex(index=pd.DatetimeIndex(endpoints), columns=members)
    numeric = values.apply(pd.to_numeric, errors="coerce")
    valid = numeric.notna() & np.isfinite(numeric) & numeric.gt(0.0)
    return valid.all(axis=0)


def require_execution_prices(
    prices: pd.DataFrame, execution_date: object, selected_sids: Iterable[str]
) -> None:
    """Fail explicitly if a selected security lacks its execution open."""

    frame = canonicalize_prices(prices)
    date = normalize_session_date(execution_date)
    requested = tuple(sorted(set(map(str, selected_sids))))
    available: set[str] = set()
    if date in frame.index.get_level_values("date"):
        day = frame.xs(date, level="date")
        available = set(day.index[day["tr_open"].notna()].astype(str))
    missing = sorted(set(requested).difference(available))
    if missing:
        raise DataQualityError(
            f"missing execution tr_open on {date.date()} for selected sids: {missing}"
        )


def flag_extreme_close_returns(
    prices: pd.DataFrame, *, absolute_log_return: float = np.log(2.0)
) -> pd.DataFrame:
    """Return (do not alter) observations whose close return needs review."""

    if absolute_log_return <= 0:
        raise ValueError("absolute_log_return must be positive")
    frame = canonicalize_prices(prices)
    log_price = np.log(frame["tr_close"])
    returns = log_price.groupby(level="sid").diff()
    flagged = returns[returns.abs() > absolute_log_return].rename("log_return")
    return flagged.reset_index()
