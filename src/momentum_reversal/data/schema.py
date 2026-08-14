"""Canonical schemas used at the boundary between data and research code."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


CANONICAL_PRICE_COLUMNS = (
    "tr_open",
    "tr_high",
    "tr_low",
    "tr_close",
    "raw_open",
    "raw_high",
    "raw_low",
    "raw_close",
    "volume",
    "dividends",
    "stock_splits",
)

REQUIRED_BACKTEST_PRICE_COLUMNS = ("tr_open", "tr_close")


class DataSchemaError(ValueError):
    """Raised when a dataset violates a canonical data contract."""


def normalize_session_date(value: object) -> pd.Timestamp:
    """Return a timezone-naive, normalized pandas timestamp."""

    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        # Exchange sessions are date labels, not instants. Converting midnight
        # through UTC can move the label to another civil day.
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def canonicalize_prices(
    prices: pd.DataFrame,
    *,
    required_columns: Iterable[str] = REQUIRED_BACKTEST_PRICE_COLUMNS,
) -> pd.DataFrame:
    """Normalize a long price table to a sorted ``(date, sid)`` MultiIndex.

    The function accepts either a MultiIndex or ordinary ``date``/``sid``
    columns. It does not fill missing observations or alter price values.
    """

    frame = prices.copy()
    if not (
        isinstance(frame.index, pd.MultiIndex)
        and list(frame.index.names) == ["date", "sid"]
    ):
        if {"date", "sid"}.issubset(frame.columns):
            frame = frame.set_index(["date", "sid"])
        else:
            raise DataSchemaError(
                "prices must have a (date, sid) MultiIndex or date/sid columns"
            )

    dates = pd.DatetimeIndex(frame.index.get_level_values("date"))
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    dates = dates.normalize()
    sids = frame.index.get_level_values("sid").astype(str)
    frame.index = pd.MultiIndex.from_arrays([dates, sids], names=["date", "sid"])

    if frame.index.has_duplicates:
        sample = frame.index[frame.index.duplicated()].unique().tolist()[:5]
        raise DataSchemaError(f"duplicate (date, sid) price rows: {sample}")

    missing = sorted(set(required_columns).difference(frame.columns))
    if missing:
        raise DataSchemaError(f"missing required price columns: {missing}")

    return frame.sort_index()


def validate_canonical_prices(
    prices: pd.DataFrame,
    *,
    required_columns: Iterable[str] = REQUIRED_BACKTEST_PRICE_COLUMNS,
) -> None:
    """Raise ``DataSchemaError`` for structural or impossible OHLC values.

    Missing rows and NaNs are deliberately allowed here and are surfaced by
    the coverage audit. Large returns are never silently clipped or repaired.
    """

    frame = canonicalize_prices(prices, required_columns=required_columns)

    price_columns = [
        column
        for column in (
            "tr_open",
            "tr_high",
            "tr_low",
            "tr_close",
            "raw_open",
            "raw_high",
            "raw_low",
            "raw_close",
        )
        if column in frame
    ]
    for column in price_columns:
        numeric = pd.to_numeric(frame[column], errors="coerce")
        invalid = numeric.notna() & (~np.isfinite(numeric) | (numeric <= 0))
        if invalid.any():
            sample = frame.index[invalid].tolist()[:5]
            raise DataSchemaError(f"{column} contains non-positive/invalid values: {sample}")

    for prefix in ("tr", "raw"):
        high, low = f"{prefix}_high", f"{prefix}_low"
        open_, close = f"{prefix}_open", f"{prefix}_close"
        if {high, low, open_, close}.issubset(frame.columns):
            known = frame[[high, low, open_, close]].notna().all(axis=1)
            # Total-return OHLC values are produced by multiplying every raw
            # field by the same adjustment factor.  When raw high/low equals
            # close, assigning Adj Close directly can differ from the
            # multiplied boundary by a few floating-point ulps.  Treat only a
            # gap larger than a deliberately tiny scale-aware tolerance as an
            # impossible bar; material provider errors still fail hard.
            extrema = frame[[high, low, open_, close]].abs().max(axis=1)
            tolerance = 1e-12 * extrema.clip(lower=1.0)
            max_body = frame[[open_, close]].max(axis=1)
            min_body = frame[[open_, close]].min(axis=1)
            impossible = known & (
                (frame[high] + tolerance < max_body)
                | (frame[low] - tolerance > min_body)
                | (frame[high] + tolerance < frame[low])
            )
            if impossible.any():
                sample = frame.index[impossible].tolist()[:5]
                raise DataSchemaError(f"invalid {prefix} OHLC relationship: {sample}")
