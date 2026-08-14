"""Auditable price masks for known non-tradable or invalid sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .schema import DataSchemaError, canonicalize_prices, normalize_session_date


TRADABILITY_OHLC_COLUMNS = (
    "tr_open",
    "tr_high",
    "tr_low",
    "tr_close",
    "raw_open",
    "raw_high",
    "raw_low",
    "raw_close",
)

TRADABILITY_OVERRIDE_COLUMNS = (
    "override_id",
    "sid",
    "start_date",
    "end_date",
    "interval_type",
    "reason",
    "evidence",
    "notes",
)

INTERVAL_TYPES = frozenset({"closed", "half_open"})

TRADABILITY_AUDIT_COLUMNS = (
    "override_id",
    "date",
    "sid",
    "start_date",
    "end_date",
    "interval_type",
    "reason",
    "evidence",
    "masked_non_null_ohlc_count",
    "already_missing_ohlc_count",
)


@dataclass(frozen=True, slots=True)
class TradabilityOverrideLedger:
    """Validated intervals whose price bars must not be treated as tradable.

    ``closed`` means ``[start_date, end_date]`` and ``half_open`` means
    ``[start_date, end_date)``.  Both interval forms include their start date.
    """

    _frame: pd.DataFrame

    def __init__(self, frame: pd.DataFrame | None = None) -> None:
        source = (
            pd.DataFrame(columns=TRADABILITY_OVERRIDE_COLUMNS)
            if frame is None
            else frame
        )
        object.__setattr__(self, "_frame", _normalize_overrides(source))

    @classmethod
    def from_csv(cls, path: str | Path) -> "TradabilityOverrideLedger":
        """Load a UTF-8 override ledger without coercing identifiers."""

        return cls(pd.read_csv(path, dtype=str, keep_default_na=False))

    @classmethod
    def empty(cls) -> "TradabilityOverrideLedger":
        return cls()

    @property
    def is_empty(self) -> bool:
        return self._frame.empty

    def to_frame(self) -> pd.DataFrame:
        return self._frame.copy(deep=True)

    def apply(
        self,
        prices: pd.DataFrame,
        *,
        require_each_override_match: bool = True,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Mask raw/total-return OHLC and return canonical prices plus audit."""

        return apply_tradability_overrides(
            prices,
            self,
            require_each_override_match=require_each_override_match,
        )


def apply_tradability_overrides(
    prices: pd.DataFrame,
    overrides: TradabilityOverrideLedger | pd.DataFrame,
    *,
    require_each_override_match: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Set all raw/total-return OHLC fields to NaN for override matches.

    The returned price table uses the canonical ``(date, sid)`` index.  Every
    non-OHLC column, including volume and provider/source lineage, is retained
    unchanged.  The audit contains one row for every matched price row.
    """

    ledger = (
        overrides
        if isinstance(overrides, TradabilityOverrideLedger)
        else TradabilityOverrideLedger(overrides)
    )
    result = canonicalize_prices(
        prices,
        required_columns=TRADABILITY_OHLC_COLUMNS,
    ).copy(deep=True)

    for column in TRADABILITY_OHLC_COLUMNS:
        try:
            result[column] = pd.to_numeric(result[column], errors="raise").astype(float)
        except (TypeError, ValueError) as error:
            raise DataSchemaError(
                f"tradability mask requires numeric {column} values"
            ) from error

    if ledger.is_empty:
        return result, _empty_audit()

    dates = pd.DatetimeIndex(result.index.get_level_values("date"))
    sids = result.index.get_level_values("sid").astype(str)
    audit_rows: list[dict[str, object]] = []

    for override in ledger._frame.itertuples(index=False):
        before_end = dates <= override.end_date
        if override.interval_type == "half_open":
            before_end = dates < override.end_date
        mask = (
            (sids == override.sid)
            & (dates >= override.start_date)
            & before_end
        )
        if not mask.any():
            if require_each_override_match:
                raise DataSchemaError(
                    "tradability override matched no price rows: "
                    f"{override.override_id}"
                )
            continue

        matched = result.loc[mask, list(TRADABILITY_OHLC_COLUMNS)]
        non_null_counts = matched.notna().sum(axis=1)
        for (date, sid), non_null_count in non_null_counts.items():
            audit_rows.append(
                {
                    "override_id": override.override_id,
                    "date": date,
                    "sid": sid,
                    "start_date": override.start_date,
                    "end_date": override.end_date,
                    "interval_type": override.interval_type,
                    "reason": override.reason,
                    "evidence": override.evidence,
                    "masked_non_null_ohlc_count": int(non_null_count),
                    "already_missing_ohlc_count": int(
                        len(TRADABILITY_OHLC_COLUMNS) - non_null_count
                    ),
                }
            )
        result.loc[mask, list(TRADABILITY_OHLC_COLUMNS)] = np.nan

    audit = pd.DataFrame(audit_rows, columns=TRADABILITY_AUDIT_COLUMNS)
    if audit.empty:
        audit = _empty_audit()
    else:
        audit = audit.sort_values(
            ["date", "sid", "override_id"], ignore_index=True
        )
    return result, audit


def _normalize_overrides(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(TRADABILITY_OVERRIDE_COLUMNS).difference(frame.columns))
    if missing:
        raise DataSchemaError(f"tradability override ledger missing columns: {missing}")

    result = frame.loc[:, TRADABILITY_OVERRIDE_COLUMNS].copy()
    if result.empty:
        result["start_date"] = pd.to_datetime(result["start_date"])
        result["end_date"] = pd.to_datetime(result["end_date"])
        return result.reset_index(drop=True)

    required_strings = (
        "override_id",
        "sid",
        "interval_type",
        "reason",
        "evidence",
    )
    for column in required_strings:
        result[column] = result[column].astype("string").str.strip()
        invalid = result[column].isna() | result[column].eq("")
        if invalid.any():
            raise DataSchemaError(f"tradability override {column} cannot be blank")

    result["notes"] = result["notes"].astype("string").str.strip()
    result["notes"] = result["notes"].mask(result["notes"].eq(""), pd.NA)

    for column in ("start_date", "end_date"):
        try:
            result[column] = result[column].map(normalize_session_date)
        except (TypeError, ValueError, pd.errors.OutOfBoundsDatetime) as error:
            raise DataSchemaError(
                f"tradability override {column} contains an invalid date"
            ) from error
        if result[column].isna().any():
            raise DataSchemaError(f"tradability override {column} cannot contain NaT")

    if result["override_id"].duplicated().any():
        duplicated = result.loc[
            result["override_id"].duplicated(False), "override_id"
        ]
        raise DataSchemaError(
            f"duplicate tradability override IDs: {sorted(duplicated.unique().tolist())}"
        )

    unknown_types = sorted(set(result["interval_type"]).difference(INTERVAL_TYPES))
    if unknown_types:
        raise DataSchemaError(f"unsupported tradability interval types: {unknown_types}")

    closed = result["interval_type"].eq("closed")
    invalid_closed = closed & result["start_date"].gt(result["end_date"])
    invalid_half_open = ~closed & result["start_date"].ge(result["end_date"])
    if (invalid_closed | invalid_half_open).any():
        bad_ids = result.loc[
            invalid_closed | invalid_half_open, "override_id"
        ].tolist()
        raise DataSchemaError(f"invalid tradability override intervals: {bad_ids}")

    result = result.sort_values(
        ["sid", "start_date", "end_date", "override_id"], ignore_index=True
    )
    for sid, group in result.groupby("sid", sort=False):
        previous = None
        for current in group.itertuples(index=False):
            if previous is not None:
                shares_boundary = current.start_date == previous.end_date
                overlaps = (
                    current.start_date < previous.end_date
                    or (shares_boundary and previous.interval_type == "closed")
                )
                if overlaps:
                    raise DataSchemaError(
                        "overlapping tradability overrides for "
                        f"{sid}: {previous.override_id}, {current.override_id}"
                    )
            previous = current

    return result.reset_index(drop=True)


def _empty_audit() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADABILITY_AUDIT_COLUMNS)
