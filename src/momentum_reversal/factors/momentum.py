"""Causal momentum signals for the first experiment batch.

All lags are measured on the shared exchange-session calendar.  A missing price at
an exact lag remains missing; the factor layer never forward-fills securities.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Iterable

import numpy as np
import pandas as pd


class MomentumDefinition(StrEnum):
    """Frozen definitions in the baseline research plan."""

    MOM_255_0 = "mom_255_0"
    MOM_255_21 = "mom_255_21"
    MOM_12_1 = "mom_12_1"


def _close_matrix(prices: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(prices.index, pd.MultiIndex) or prices.index.names != ["date", "sid"]:
        raise ValueError("prices must use a MultiIndex named ['date', 'sid']")
    if "tr_close" not in prices.columns:
        raise ValueError("prices must contain tr_close")
    if not prices.index.is_unique:
        raise ValueError("prices index must be unique")

    frame = prices[["tr_close"]].reset_index()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    if frame["date"].dt.tz is not None:
        raise ValueError("price dates must be timezone-naive")
    return frame.pivot(index="date", columns="sid", values="tr_close").sort_index()


def _authoritative_sessions(values: Iterable[pd.Timestamp]) -> pd.DatetimeIndex:
    """Validate an explicitly supplied exchange-session sequence.

    Session order is information: silently sorting or deduplicating a malformed
    calendar could change lag endpoints.  The caller must therefore supply one
    strictly increasing, timezone-naive sequence.
    """

    sessions = pd.DatetimeIndex(pd.to_datetime(list(values)))
    if sessions.tz is not None:
        raise ValueError("sessions must be timezone-naive")
    sessions = sessions.normalize()
    if sessions.empty:
        raise ValueError("sessions cannot be empty")
    if sessions.hasnans:
        raise ValueError("sessions cannot contain NaT")
    if sessions.has_duplicates or not sessions.is_monotonic_increasing:
        raise ValueError("sessions must be unique and strictly increasing")
    return sessions


def _calendar_month_signal(close: pd.DataFrame, signal_dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Return prior-month / month-minus-12 log returns at each signal date.

    For any signal in calendar month ``m``, numerator and denominator are the
    closes on the exchange's final sessions of ``m-1`` and ``m-12``.  Therefore
    scores intentionally remain unchanged across weekly signals in one month.
    """

    month_key = close.index.to_period("M")
    is_last_session = ~month_key.duplicated(keep="last")
    month_close = close.loc[is_last_session].copy()
    month_close.index = month_close.index.to_period("M")

    rows: list[pd.Series] = []
    for date in signal_dates:
        period = date.to_period("M")
        numerator_period = period - 1
        denominator_period = period - 12
        if numerator_period not in month_close.index or denominator_period not in month_close.index:
            score = pd.Series(np.nan, index=close.columns, dtype=float)
        else:
            numerator = month_close.loc[numerator_period]
            denominator = month_close.loc[denominator_period]
            score = np.log(numerator / denominator)
        score.name = date
        rows.append(score)
    return pd.DataFrame(rows, index=signal_dates, columns=close.columns)


def compute_momentum_scores(
    prices: pd.DataFrame,
    signal_dates: Iterable[pd.Timestamp],
    definition: MomentumDefinition | str,
    *,
    sessions: Iterable[pd.Timestamp] | None = None,
) -> pd.Series:
    """Compute a long ``(signal_date, sid) -> score`` Series.

    No membership filter is applied here.  The backtest intersects the score
    panel with point-in-time membership separately, which keeps the factor pure
    and makes survivorship tests straightforward.
    """

    definition = MomentumDefinition(definition)
    close = _close_matrix(prices)
    if sessions is not None:
        calendar = _authoritative_sessions(sessions)
        non_sessions = close.index.difference(calendar)
        if len(non_sessions):
            raise ValueError(
                "price dates are not present in the authoritative session calendar: "
                f"{non_sessions[:5].tolist()}"
            )
        # Reindex before shifting.  A missing market session must remain a gap;
        # it must not compress a 255-session lag into 254 exchange sessions.
        close = close.reindex(calendar)
    dates = pd.DatetimeIndex(pd.to_datetime(list(signal_dates))).normalize().sort_values().unique()
    if dates.tz is not None:
        raise ValueError("signal_dates must be timezone-naive")
    missing_dates = dates.difference(close.index)
    if len(missing_dates):
        raise ValueError(f"signal dates are not exchange sessions: {missing_dates.tolist()}")

    if definition is MomentumDefinition.MOM_255_0:
        panel = np.log(close / close.shift(255)).reindex(dates)
    elif definition is MomentumDefinition.MOM_255_21:
        panel = np.log(close.shift(21) / close.shift(255)).reindex(dates)
    else:
        panel = _calendar_month_signal(close, dates)

    panel.index.name = "signal_date"
    panel.columns.name = "sid"
    # melt preserves explicit missing cells under both pandas 2 and 3.  That is
    # important because early formation dates can be entirely missing, yet the
    # engine still needs a complete, auditable signal-date panel.
    scores = (
        panel.reset_index()
        .melt(id_vars=["signal_date"], var_name="sid", value_name="score")
        .set_index(["signal_date", "sid"])["score"]
    )
    return scores.sort_index()


def compute_reversal_scores(
    prices: pd.DataFrame,
    signal_dates: Iterable[pd.Timestamp],
    *,
    lookback: int,
    sessions: Iterable[pd.Timestamp] | None = None,
) -> pd.Series:
    """Compute ``-log(TR(t) / TR(t-lookback))`` on exact session lags."""

    if lookback <= 0:
        raise ValueError("reversal lookback must be positive")
    close = _close_matrix(prices)
    if sessions is not None:
        calendar = _authoritative_sessions(sessions)
        non_sessions = close.index.difference(calendar)
        if len(non_sessions):
            raise ValueError(
                "price dates are not present in the authoritative session calendar: "
                f"{non_sessions[:5].tolist()}"
            )
        close = close.reindex(calendar)
    dates = (
        pd.DatetimeIndex(pd.to_datetime(list(signal_dates)))
        .normalize()
        .sort_values()
        .unique()
    )
    if dates.tz is not None:
        raise ValueError("signal_dates must be timezone-naive")
    missing_dates = dates.difference(close.index)
    if len(missing_dates):
        raise ValueError(f"signal dates are not exchange sessions: {missing_dates.tolist()}")

    panel = -np.log(close / close.shift(lookback)).reindex(dates)
    panel.index.name = "signal_date"
    panel.columns.name = "sid"
    scores = (
        panel.reset_index()
        .melt(id_vars=["signal_date"], var_name="sid", value_name="score")
        .set_index(["signal_date", "sid"])["score"]
    )
    return scores.sort_index()
