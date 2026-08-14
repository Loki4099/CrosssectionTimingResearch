"""Exchange-session calendar derived from an authoritative session list."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .schema import normalize_session_date


@dataclass(frozen=True)
class TradingCalendar:
    """A strictly increasing, timezone-naive set of trading sessions."""

    sessions: pd.DatetimeIndex

    def __init__(self, sessions: object) -> None:
        index = pd.DatetimeIndex(sessions)
        if index.tz is not None:
            index = index.tz_localize(None)
        index = index.normalize().sort_values().drop_duplicates()
        if index.empty:
            raise ValueError("trading calendar cannot be empty")
        object.__setattr__(self, "sessions", index)

    @classmethod
    def from_prices(cls, prices: pd.DataFrame) -> "TradingCalendar":
        if isinstance(prices.index, pd.MultiIndex) and "date" in prices.index.names:
            return cls(prices.index.get_level_values("date").unique())
        if "date" in prices.columns:
            return cls(prices["date"].unique())
        raise ValueError("prices must expose dates in index or date column")

    @classmethod
    def from_exchange_calendars(
        cls, start: object, end: object, *, calendar_name: str = "XNYS"
    ) -> "TradingCalendar":
        """Build from optional ``exchange_calendars`` when installed."""

        try:
            import exchange_calendars as xcals
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ImportError(
                "exchange_calendars is required for exchange calendar generation"
            ) from exc
        calendar = xcals.get_calendar(calendar_name)
        sessions = calendar.sessions_in_range(
            normalize_session_date(start), normalize_session_date(end)
        )
        return cls(sessions)

    def contains(self, value: object) -> bool:
        return normalize_session_date(value) in self.sessions

    def next_session(self, value: object) -> pd.Timestamp:
        date = normalize_session_date(value)
        position = self.sessions.searchsorted(date, side="right")
        if position >= len(self.sessions):
            raise KeyError(f"no trading session after {date.date()}")
        return self.sessions[position]

    def previous_sessions(
        self, value: object, count: int, *, include: bool = True
    ) -> pd.DatetimeIndex:
        if count < 1:
            raise ValueError("count must be positive")
        date = normalize_session_date(value)
        side = "right" if include else "left"
        stop = self.sessions.searchsorted(date, side=side)
        start = stop - count
        if start < 0:
            raise KeyError(f"fewer than {count} sessions available through {date.date()}")
        return self.sessions[start:stop]

    def last_sessions_of_week(self) -> pd.DatetimeIndex:
        series = pd.Series(self.sessions, index=self.sessions)
        return pd.DatetimeIndex(
            series.groupby(self.sessions.to_period("W-FRI")).last()
        )

    def last_sessions_of_month(self) -> pd.DatetimeIndex:
        series = pd.Series(self.sessions, index=self.sessions)
        return pd.DatetimeIndex(series.groupby(self.sessions.to_period("M")).last())
