"""Validated terminal corporate-action ledger used by the backtest engine.

The first research batch only needs terminal cash, stock, and cash-and-stock
mergers.  These events are deliberately separate from ticker aliases: an
alias says two provider symbols represent the same security, while a merger
changes the assets and/or cash owned by the portfolio.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .schema import DataSchemaError, normalize_session_date


CORPORATE_ACTION_COLUMNS = (
    "action_id",
    "action_type",
    "legal_effective_date",
    "apply_session",
    "apply_phase",
    "source_sid",
    "target_sid",
    "cash_per_source_share",
    "currency",
    "target_shares_per_source_share",
    "fractional_treatment",
    "evidence_url",
    "notes",
)

TERMINAL_ACTION_TYPES = frozenset(
    {
        "cash_merger",
        "stock_merger",
        "cash_and_stock_merger",
        # Bounded research approximation for an otherwise unpriceable
        # terminal holding.  It remains distinct from legal merger terms.
        "cash_liquidation",
    }
)
FRACTIONAL_TREATMENTS = frozenset(
    {"cash_in_lieu", "fractional_shares", "not_applicable"}
)


@dataclass(frozen=True, slots=True)
class CorporateActionLedger:
    """Immutable, normalized view of terminal corporate actions.

    Date ranges are not inferred from prices.  ``legal_effective_date`` is the
    legal event date and ``apply_session`` is the exchange session on which the
    engine changes the position before the open.  Version 1 supports only the
    ``pre_open`` phase so event/rebalance ordering is unambiguous.
    """

    _frame: pd.DataFrame

    def __init__(self, frame: pd.DataFrame | None = None) -> None:
        source = (
            pd.DataFrame(columns=CORPORATE_ACTION_COLUMNS)
            if frame is None
            else frame
        )
        object.__setattr__(self, "_frame", _normalize_ledger(source))

    @classmethod
    def from_csv(cls, path: str | Path) -> "CorporateActionLedger":
        """Load a UTF-8 CSV without converting SIDs to numeric values."""

        return cls(pd.read_csv(path, dtype=str, keep_default_na=False))

    @classmethod
    def empty(cls) -> "CorporateActionLedger":
        return cls()

    @property
    def is_empty(self) -> bool:
        return self._frame.empty

    def to_frame(self) -> pd.DataFrame:
        return self._frame.copy(deep=True)

    def actions_on(self, session: object) -> pd.DataFrame:
        """Return a copy of actions applied before one session's open."""

        date = normalize_session_date(session)
        return self._frame.loc[self._frame["apply_session"].eq(date)].copy(deep=True)

    def validate_against_sessions(self, sessions: object) -> None:
        """Reject an in-range apply date that is not an exchange session.

        A project-wide ledger may contain events before or after a particular
        dataset.  Those records are harmless and remain available for another
        run; only records inside the supplied calendar bounds are validated.
        """

        index = pd.DatetimeIndex(pd.to_datetime(list(sessions)))
        if index.tz is not None:
            raise DataSchemaError("corporate-action sessions must be timezone-naive")
        index = index.normalize()
        if index.empty:
            raise DataSchemaError("corporate-action session calendar cannot be empty")
        if index.hasnans or index.has_duplicates or not index.is_monotonic_increasing:
            raise DataSchemaError(
                "corporate-action session calendar must be valid, unique, and sorted"
            )
        in_range = self._frame["apply_session"].between(index[0], index[-1])
        missing = self._frame.loc[
            in_range & ~self._frame["apply_session"].isin(index),
            ["action_id", "apply_session"],
        ]
        if not missing.empty:
            examples = [
                f"{row.action_id}@{row.apply_session.date()}"
                for row in missing.itertuples(index=False)
            ]
            raise DataSchemaError(
                "corporate actions use non-session apply dates: " + ", ".join(examples)
            )


def _normalize_ledger(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(CORPORATE_ACTION_COLUMNS).difference(frame.columns))
    if missing:
        raise DataSchemaError(f"corporate-action ledger missing columns: {missing}")

    result = frame.loc[:, CORPORATE_ACTION_COLUMNS].copy()
    if result.empty:
        result["legal_effective_date"] = pd.to_datetime(
            result["legal_effective_date"]
        )
        result["apply_session"] = pd.to_datetime(result["apply_session"])
        result["cash_per_source_share"] = pd.to_numeric(
            result["cash_per_source_share"]
        )
        result["target_shares_per_source_share"] = pd.to_numeric(
            result["target_shares_per_source_share"]
        )
        return result.reset_index(drop=True)

    required_strings = (
        "action_id",
        "action_type",
        "apply_phase",
        "source_sid",
        "fractional_treatment",
        "evidence_url",
    )
    for column in required_strings:
        result[column] = result[column].astype("string").str.strip()
        invalid = result[column].isna() | result[column].eq("")
        if invalid.any():
            raise DataSchemaError(f"corporate-action {column} cannot be blank")

    for column in ("target_sid", "currency", "notes"):
        result[column] = result[column].astype("string").str.strip()
        result[column] = result[column].mask(result[column].eq(""), pd.NA)

    for column in ("legal_effective_date", "apply_session"):
        try:
            parsed = pd.to_datetime(result[column], errors="raise")
        except (TypeError, ValueError) as error:
            raise DataSchemaError(
                f"corporate-action {column} contains an invalid date"
            ) from error
        if getattr(parsed.dt, "tz", None) is not None:
            parsed = parsed.dt.tz_localize(None)
        result[column] = parsed.dt.normalize()
        if result[column].isna().any():
            raise DataSchemaError(f"corporate-action {column} cannot contain NaT")

    numeric_columns = (
        "cash_per_source_share",
        "target_shares_per_source_share",
    )
    for column in numeric_columns:
        # Blank consideration means zero, but malformed non-blank text is fatal.
        values = result[column].replace("", 0.0)
        try:
            values = pd.to_numeric(values, errors="raise").astype(float)
        except (TypeError, ValueError) as error:
            raise DataSchemaError(
                f"corporate-action {column} must be numeric"
            ) from error
        invalid = ~np.isfinite(values) | (values < 0)
        if invalid.any():
            raise DataSchemaError(
                f"corporate-action {column} must be finite and non-negative"
            )
        result[column] = values

    if result["action_id"].duplicated().any():
        duplicated = result.loc[result["action_id"].duplicated(False), "action_id"]
        raise DataSchemaError(
            f"duplicate corporate-action IDs: {sorted(duplicated.unique().tolist())}"
        )
    if result["source_sid"].duplicated().any():
        duplicated = result.loc[result["source_sid"].duplicated(False), "source_sid"]
        raise DataSchemaError(
            "multiple terminal actions for source SIDs: "
            f"{sorted(duplicated.unique().tolist())}"
        )
    unknown_types = sorted(set(result["action_type"]).difference(TERMINAL_ACTION_TYPES))
    if unknown_types:
        raise DataSchemaError(f"unsupported corporate-action types: {unknown_types}")
    if not result["apply_phase"].eq("pre_open").all():
        raise DataSchemaError("only pre_open corporate actions are supported")
    if (result["legal_effective_date"] > result["apply_session"]).any():
        raise DataSchemaError(
            "corporate action cannot be applied before its legal effective date"
        )
    unknown_fractional = sorted(
        set(result["fractional_treatment"]).difference(FRACTIONAL_TREATMENTS)
    )
    if unknown_fractional:
        raise DataSchemaError(
            f"unsupported fractional treatments: {unknown_fractional}"
        )

    cash = result["cash_per_source_share"]
    ratio = result["target_shares_per_source_share"]
    cash_only = result["action_type"].isin(
        ["cash_merger", "cash_liquidation"]
    )
    stock_only = result["action_type"].eq("stock_merger")
    mixed = result["action_type"].eq("cash_and_stock_merger")
    if (cash_only & ((cash <= 0) | (ratio != 0))).any():
        raise DataSchemaError(
            "cash_merger/cash_liquidation requires positive cash and zero stock ratio"
        )
    if (stock_only & ((cash != 0) | (ratio <= 0))).any():
        raise DataSchemaError("stock_merger requires zero cash and positive stock ratio")
    if (mixed & ((cash <= 0) | (ratio <= 0))).any():
        raise DataSchemaError(
            "cash_and_stock_merger requires positive cash and stock ratio"
        )

    has_stock = ratio.gt(0)
    missing_target = result["target_sid"].isna()
    if (has_stock & missing_target).any():
        raise DataSchemaError("stock consideration requires target_sid")
    if (has_stock & result["target_sid"].eq(result["source_sid"])).any():
        raise DataSchemaError("corporate-action source_sid and target_sid must differ")
    has_cash = cash.gt(0)
    if (has_cash & result["currency"].isna()).any():
        raise DataSchemaError("cash consideration requires currency")
    if (~has_cash & result["currency"].notna()).any():
        raise DataSchemaError("currency must be blank when cash consideration is zero")

    # Processing a target and another terminal source on the same pre-open is
    # ordering-dependent.  Keep v1 explicit instead of silently choosing an order.
    for _, group in result.groupby("apply_session", sort=False):
        sources = set(group["source_sid"])
        targets = set(group["target_sid"].dropna())
        chained = sorted(sources.intersection(targets))
        if chained:
            raise DataSchemaError(
                f"same-session chained corporate actions are unsupported: {chained}"
            )

    return result.sort_values(
        ["apply_session", "action_id"], ignore_index=True
    ).reset_index(drop=True)
