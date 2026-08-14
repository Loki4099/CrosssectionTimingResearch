"""Strict local risk-free return ingestion.

The project accepts already-converted daily decimal returns. It deliberately
does not guess whether percentages or annualized yields need conversion.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .calendar import TradingCalendar
from .qa import DataQualityError
from .schema import normalize_session_date


def load_daily_risk_free_csv(
    path: str | Path,
    calendar: TradingCalendar,
    *,
    research_start: object,
    end: object,
    maximum_absolute_daily_return: float = 0.01,
) -> pd.DataFrame:
    """Load ``date,rf_return`` and require complete research-session coverage.

    ``rf_return`` must already be a one-day decimal return (for example
    ``0.0002`` means 2 basis points). Values outside +/-1% are rejected by
    default because they almost certainly represent annual yields or percent
    units rather than a daily T-bill return.
    """

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    frame = pd.read_csv(source)
    missing = {"date", "rf_return"}.difference(frame.columns)
    if missing:
        raise DataQualityError(
            f"risk-free CSV missing required columns: {sorted(missing)}"
        )
    data = frame.loc[:, ["date", "rf_return"]].copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    if data["date"].isna().any():
        raise DataQualityError("risk-free CSV contains invalid dates")
    if data["date"].dt.tz is not None:
        data["date"] = data["date"].dt.tz_localize(None)
    data["date"] = data["date"].dt.normalize()
    if data["date"].duplicated().any():
        duplicates = data.loc[data["date"].duplicated(False), "date"].unique()
        raise DataQualityError(
            f"risk-free CSV contains duplicate dates: {duplicates[:10].tolist()}"
        )
    data["rf_return"] = pd.to_numeric(data["rf_return"], errors="coerce")
    invalid = (
        data["rf_return"].isna()
        | ~np.isfinite(data["rf_return"])
        | (data["rf_return"] <= -1.0)
    )
    if invalid.any():
        bad = data.loc[invalid, "date"].tolist()
        raise DataQualityError(
            f"risk-free CSV contains invalid returns on: {bad[:10]}"
        )
    wrong_units = data["rf_return"].abs() > maximum_absolute_daily_return
    if wrong_units.any():
        bad = data.loc[wrong_units, ["date", "rf_return"]].head(10).to_dict("records")
        raise DataQualityError(
            "risk-free returns exceed the daily-decimal guardrail; convert "
            f"annualized/percent inputs before ingestion: {bad}"
        )

    start_date = normalize_session_date(research_start)
    end_date = normalize_session_date(end)
    required = calendar.sessions[
        (calendar.sessions >= start_date) & (calendar.sessions <= end_date)
    ]
    if required.empty:
        raise DataQualityError("risk-free research interval contains no sessions")
    indexed = data.set_index("date").sort_index()
    missing_dates = required.difference(indexed.index)
    if len(missing_dates):
        raise DataQualityError(
            "risk-free CSV does not cover every research session: "
            f"{missing_dates[:10].tolist()}"
        )
    curated = indexed.reindex(required).reset_index()
    curated.columns = ["date", "rf_return"]
    return curated


def align_daily_risk_free(
    frame: pd.DataFrame, strategy_dates: pd.Index
) -> pd.Series:
    """Return strictly aligned risk-free returns for one strategy path."""

    missing = {"date", "rf_return"}.difference(frame.columns)
    if missing:
        raise DataQualityError(
            f"risk_free_daily missing required columns: {sorted(missing)}"
        )
    dates = pd.DatetimeIndex(pd.to_datetime(frame["date"], errors="coerce"))
    if dates.isna().any():
        raise DataQualityError("risk_free_daily contains invalid dates")
    dates = dates.normalize()
    if dates.has_duplicates:
        raise DataQualityError("risk_free_daily contains duplicate dates")
    values = pd.Series(
        pd.to_numeric(frame["rf_return"], errors="coerce").to_numpy(),
        index=dates,
        name="rf_return",
    )
    requested = pd.DatetimeIndex(strategy_dates).normalize()
    missing_dates = requested.difference(values.index)
    if len(missing_dates):
        raise DataQualityError(
            "risk_free_daily does not cover every strategy session: "
            f"{missing_dates[:10].tolist()}"
        )
    aligned = values.reindex(requested)
    if aligned.isna().any() or not np.isfinite(aligned).all():
        bad = aligned.index[aligned.isna() | ~np.isfinite(aligned)]
        raise DataQualityError(
            f"risk_free_daily has invalid values on: {bad[:10].tolist()}"
        )
    aligned.index = strategy_dates
    return aligned.astype(float)

