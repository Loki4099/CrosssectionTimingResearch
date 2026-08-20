"""Point-in-time annual fundamental factors for cross-sectional research.

The input is a long table of *canonical annual fact events*.  Each row is one
metric from one SEC filing accession.  The caller is responsible for mapping
raw XBRL tags to the metric IDs below; this module deliberately does not mix
facts from different accessions to manufacture a complete filing.

Two timing fields have distinct meanings:

``accepted_at``
    The SEC acceptance timestamp, used to order original filings and
    amendments.
``available_session``
    The first exchange session on which the filing may be used.  This is the
    causal cutoff used for signals.

An amendment therefore changes values only from its own ``available_session``
onward.  ``raw_value`` preserves the economically natural ratio, while
``score`` is oriented so that larger values are preferred by the long-only
cross section.  Missing facts remain missing -- in particular, cash-flow
items are never filled with zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

import numpy as np
import pandas as pd


class FundamentalMetric(StrEnum):
    """Canonical metric IDs accepted by the factor layer."""

    REVENUE = "revenue"
    COST_OF_GOODS_SOLD = "cost_of_goods_sold"
    TOTAL_ASSETS = "total_assets"
    CURRENT_ASSETS = "current_assets"
    CASH = "cash_and_cash_equivalents"
    CURRENT_LIABILITIES = "current_liabilities"
    SHORT_TERM_DEBT = "short_term_debt"
    TAXES_PAYABLE = "taxes_payable"
    DEPRECIATION = "depreciation_and_amortization"
    NET_INCOME = "net_income"
    CASH_FLOW_FROM_OPERATIONS = "cash_flow_from_operations"
    COMMON_BOOK_EQUITY = "common_book_equity"
    COMMON_DIVIDENDS = "common_dividends"
    COMMON_SHARE_REPURCHASES = "common_share_repurchases"
    COMMON_SHARE_ISSUANCE = "common_share_issuance"


class FundamentalFactorDefinition(StrEnum):
    """Registry IDs emitted by :func:`compute_fundamental_factor_panel`."""

    GROSS_PROFITABILITY = "XS032_GROSS_PROFIT_AT"
    ASSET_GROWTH = "XS041_ASSET_GROWTH"
    SLOAN_ACCRUALS = "XS039_ACCRUALS_V2"
    # This is intentionally a new ID: CFO accruals are not a replacement for
    # the balance-sheet Sloan definition in XS039.
    CFO_ACCRUALS_PROJECT_TRANSLATION = "XS056_CFO_ACCRUALS_PT"
    BOOK_TO_MARKET = "XS026_VALUE_BM"
    NET_PAYOUT_YIELD = "XS030_NET_PAYOUT_YIELD"


FACTOR_ORDER: tuple[FundamentalFactorDefinition, ...] = (
    FundamentalFactorDefinition.GROSS_PROFITABILITY,
    FundamentalFactorDefinition.ASSET_GROWTH,
    FundamentalFactorDefinition.SLOAN_ACCRUALS,
    FundamentalFactorDefinition.CFO_ACCRUALS_PROJECT_TRANSLATION,
    FundamentalFactorDefinition.BOOK_TO_MARKET,
    FundamentalFactorDefinition.NET_PAYOUT_YIELD,
)


_FLOW_METRICS = {
    FundamentalMetric.REVENUE.value,
    FundamentalMetric.COST_OF_GOODS_SOLD.value,
    FundamentalMetric.DEPRECIATION.value,
    FundamentalMetric.NET_INCOME.value,
    FundamentalMetric.CASH_FLOW_FROM_OPERATIONS.value,
    FundamentalMetric.COMMON_DIVIDENDS.value,
    FundamentalMetric.COMMON_SHARE_REPURCHASES.value,
    FundamentalMetric.COMMON_SHARE_ISSUANCE.value,
}

_NONFINANCIAL_FACTORS = {
    FundamentalFactorDefinition.GROSS_PROFITABILITY,
    FundamentalFactorDefinition.ASSET_GROWTH,
    FundamentalFactorDefinition.SLOAN_ACCRUALS,
    FundamentalFactorDefinition.CFO_ACCRUALS_PROJECT_TRANSLATION,
    FundamentalFactorDefinition.NET_PAYOUT_YIELD,
}

_DEFINITION_STATUS = {
    FundamentalFactorDefinition.GROSS_PROFITABILITY: "paper_canonical",
    FundamentalFactorDefinition.ASSET_GROWTH: "paper_canonical",
    FundamentalFactorDefinition.SLOAN_ACCRUALS: "paper_canonical",
    FundamentalFactorDefinition.CFO_ACCRUALS_PROJECT_TRANSLATION: (
        "project_translation"
    ),
    FundamentalFactorDefinition.BOOK_TO_MARKET: "paper_canonical_translation",
    FundamentalFactorDefinition.NET_PAYOUT_YIELD: "paper_canonical",
}


@dataclass(frozen=True)
class _Filing:
    cik: str
    accession: str
    fiscal_year: int
    period_end: pd.Timestamp
    accepted_at: pd.Timestamp
    available_session: pd.Timestamp
    sic: int | None
    sic_is_pit: bool
    sic_provenance: str
    rows: pd.DataFrame
    has_duration_metadata: bool


@dataclass(frozen=True)
class _MetricValue:
    value: float
    unit: str
    period_start: pd.Timestamp | None


@dataclass(frozen=True)
class _Result:
    raw_value: float | None
    score: float | None
    missing_reason: str | None = None
    data_gate: str = "pass"


def _normalise_cik(value: object) -> str:
    if pd.isna(value):
        raise ValueError("cik cannot be missing")
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if not text.isdigit():
        raise ValueError(f"cik must contain only digits: {value!r}")
    return text.lstrip("0").zfill(10)


def _naive_dates(values: pd.Series, name: str, *, allow_missing: bool = False) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        parsed = parsed.dt.tz_convert("UTC").dt.tz_localize(None)
    if not allow_missing and parsed.isna().any():
        raise ValueError(f"{name} cannot contain missing or invalid dates")
    return parsed.dt.normalize()


def _prepare_fact_events(fact_events: pd.DataFrame) -> list[_Filing]:
    required = {
        "cik",
        "accession",
        "accepted_at",
        "available_session",
        "fiscal_year",
        "period_end",
        "metric_id",
        "value",
        "unit",
        "sic",
    }
    missing = required.difference(fact_events.columns)
    if missing:
        raise ValueError(f"fact_events missing columns: {sorted(missing)}")
    if fact_events.empty:
        return []

    events = fact_events.copy()
    events["cik"] = events["cik"].map(_normalise_cik)
    events["accession"] = events["accession"].astype("string").str.strip()
    if events["accession"].isna().any() or (events["accession"] == "").any():
        raise ValueError("accession cannot be missing")
    events["accepted_at"] = pd.to_datetime(
        events["accepted_at"], errors="coerce", utc=True
    )
    if events["accepted_at"].isna().any():
        raise ValueError("accepted_at cannot contain missing or invalid timestamps")
    events["available_session"] = _naive_dates(
        events["available_session"], "available_session"
    )
    events["period_end"] = _naive_dates(events["period_end"], "period_end")

    duration_column = None
    if "period_start" in events.columns:
        duration_column = "period_start"
    elif "duration_start" in events.columns:
        duration_column = "duration_start"
    has_duration_metadata = duration_column is not None
    if duration_column is None:
        events["period_start"] = pd.NaT
    else:
        events["period_start"] = _naive_dates(
            events[duration_column], duration_column, allow_missing=True
        )

    events["fiscal_year"] = pd.to_numeric(events["fiscal_year"], errors="coerce")
    if events["fiscal_year"].isna().any():
        raise ValueError("fiscal_year must be numeric and non-missing")
    events["fiscal_year"] = events["fiscal_year"].astype(int)
    events["metric_id"] = events["metric_id"].astype("string").str.strip()
    events["value"] = pd.to_numeric(events["value"], errors="coerce")
    events["unit"] = events["unit"].astype("string").str.strip().str.upper()
    events["sic"] = pd.to_numeric(events["sic"], errors="coerce")
    if "sic_is_pit" in events.columns:
        events["sic_is_pit"] = events["sic_is_pit"].map(_coerce_audit_flag)
    else:
        events["sic_is_pit"] = False
    if "sic_provenance" in events.columns:
        events["sic_provenance"] = (
            events["sic_provenance"].astype("string").fillna("").str.strip()
        )
    else:
        events["sic_provenance"] = "unverified_no_pit_sic"
    if (events["sic_is_pit"] & events["sic"].isna()).any():
        raise ValueError("sic_is_pit cannot be true when sic is missing")

    filings: list[_Filing] = []
    keys = ["cik", "accession", "fiscal_year", "period_end"]
    for (cik, accession, fiscal_year, period_end), rows in events.groupby(
        keys, sort=False, dropna=False
    ):
        for column in ("accepted_at", "available_session"):
            if rows[column].nunique(dropna=False) != 1:
                raise ValueError(
                    f"{column} is inconsistent within accession {accession} "
                    f"and period {period_end.date()}"
                )
        sic_values = rows["sic"].dropna().unique()
        if len(sic_values) > 1:
            raise ValueError(
                f"sic is inconsistent within accession {accession} and period "
                f"{period_end.date()}"
            )
        sic = None if len(sic_values) == 0 else int(sic_values[0])
        pit_flags = rows["sic_is_pit"].drop_duplicates().tolist()
        if len(pit_flags) != 1:
            raise ValueError(
                f"sic_is_pit is inconsistent within accession {accession} "
                f"and period {period_end.date()}"
            )
        sic_is_pit = bool(pit_flags[0])
        provenance_values = rows["sic_provenance"].drop_duplicates().tolist()
        if len(provenance_values) != 1:
            raise ValueError(
                f"sic_provenance is inconsistent within accession {accession} "
                f"and period {period_end.date()}"
            )
        sic_provenance = str(provenance_values[0]).strip()
        if not sic_is_pit:
            sic_provenance = "unverified_no_pit_sic"
        elif not sic_provenance:
            raise ValueError("PIT SIC requires non-empty sic_provenance")
        filings.append(
            _Filing(
                cik=str(cik),
                accession=str(accession),
                fiscal_year=int(fiscal_year),
                period_end=pd.Timestamp(period_end),
                accepted_at=rows["accepted_at"].iloc[0],
                available_session=pd.Timestamp(rows["available_session"].iloc[0]),
                sic=sic,
                sic_is_pit=sic_is_pit,
                sic_provenance=sic_provenance,
                rows=rows.reset_index(drop=True),
                has_duration_metadata=has_duration_metadata,
            )
        )
    return filings


def _prepare_sid_cik_map(sid_cik_map: pd.DataFrame) -> pd.DataFrame:
    required = {"sid", "cik"}
    missing = required.difference(sid_cik_map.columns)
    if missing:
        raise ValueError(f"sid_cik_map missing columns: {sorted(missing)}")
    mapping = sid_cik_map.copy()
    mapping["sid"] = mapping["sid"].astype("string").str.strip()
    if mapping["sid"].isna().any() or (mapping["sid"] == "").any():
        raise ValueError("sid cannot be missing")
    mapping["cik"] = mapping["cik"].map(_normalise_cik)
    if "effective_from" in mapping:
        mapping["effective_from"] = _naive_dates(
            mapping["effective_from"], "effective_from"
        )
    else:
        mapping["effective_from"] = pd.Timestamp("1900-01-01")
    if "effective_to" in mapping:
        mapping["effective_to"] = _naive_dates(
            mapping["effective_to"], "effective_to", allow_missing=True
        )
    else:
        mapping["effective_to"] = pd.NaT
    invalid = mapping["effective_to"].notna() & (
        mapping["effective_to"] <= mapping["effective_from"]
    )
    if invalid.any():
        raise ValueError("effective_to must be later than effective_from")
    return mapping


def _coerce_audit_flag(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str):
        normalised = value.strip().lower()
        if normalised in {"true", "1", "yes", "y"}:
            return True
        if normalised in {"false", "0", "no", "n"}:
            return False
    if isinstance(value, (int, np.integer)) and value in (0, 1):
        return bool(value)
    return False


def _prepare_market_equity(market_equity: pd.DataFrame | None) -> pd.DataFrame | None:
    if market_equity is None:
        return None
    required = {
        "cik",
        "measurement_date",
        "available_session",
        "issuer_market_equity",
        "unit",
        "all_share_classes_audited",
    }
    missing = required.difference(market_equity.columns)
    if missing:
        raise ValueError(f"issuer_market_equity missing columns: {sorted(missing)}")
    values = market_equity.copy()
    values["cik"] = values["cik"].map(_normalise_cik)
    values["measurement_date"] = _naive_dates(
        values["measurement_date"], "measurement_date"
    )
    values["available_session"] = _naive_dates(
        values["available_session"], "available_session"
    )
    values["issuer_market_equity"] = pd.to_numeric(
        values["issuer_market_equity"], errors="coerce"
    )
    values["unit"] = values["unit"].astype("string").str.strip().str.upper()
    values["all_share_classes_audited"] = values[
        "all_share_classes_audited"
    ].map(_coerce_audit_flag)
    return values.sort_values(
        ["cik", "measurement_date", "available_session"], kind="stable"
    ).reset_index(drop=True)


def _latest_filings(
    filings: list[_Filing], cik: str, signal_date: pd.Timestamp
) -> tuple[_Filing | None, _Filing | None]:
    eligible = [
        filing
        for filing in filings
        if filing.cik == cik
        and filing.available_session <= signal_date
        and filing.period_end <= signal_date
    ]
    if not eligible:
        return None, None

    # First select the newest available revision for every fiscal period.
    revised: dict[tuple[int, pd.Timestamp], _Filing] = {}
    for filing in eligible:
        key = (filing.fiscal_year, filing.period_end)
        incumbent = revised.get(key)
        filing_order = (
            filing.available_session,
            filing.accepted_at,
            filing.accession,
        )
        if incumbent is None or filing_order > (
            incumbent.available_session,
            incumbent.accepted_at,
            incumbent.accession,
        ):
            revised[key] = filing

    current = max(
        revised.values(),
        key=lambda filing: (filing.period_end, filing.fiscal_year),
    )
    prior_candidates = [
        filing
        for filing in revised.values()
        if filing.fiscal_year == current.fiscal_year - 1
        and filing.period_end < current.period_end
    ]
    prior = (
        max(prior_candidates, key=lambda filing: filing.period_end)
        if prior_candidates
        else None
    )
    return current, prior


def _metric(filing: _Filing, metric: FundamentalMetric) -> tuple[_MetricValue | None, str | None]:
    rows = filing.rows.loc[filing.rows["metric_id"] == metric.value]
    if rows.empty:
        return None, f"missing_metric:{metric.value}"
    if len(rows) != 1:
        return None, f"ambiguous_metric:{metric.value}"
    row = rows.iloc[0]
    value = float(row["value"])
    unit = row["unit"]
    if not np.isfinite(value):
        return None, f"invalid_metric:{metric.value}"
    if pd.isna(unit) or not str(unit).strip():
        return None, f"missing_unit:{metric.value}"
    start = row["period_start"]
    period_start = None if pd.isna(start) else pd.Timestamp(start)
    return _MetricValue(value, str(unit), period_start), None


def _required(
    filing: _Filing, metrics: Iterable[FundamentalMetric]
) -> tuple[dict[FundamentalMetric, _MetricValue] | None, _Result | None]:
    values: dict[FundamentalMetric, _MetricValue] = {}
    reasons: list[str] = []
    for metric in metrics:
        value, reason = _metric(filing, metric)
        if reason is not None:
            reasons.append(reason)
        else:
            assert value is not None
            values[metric] = value
    if reasons:
        return None, _Result(
            None, None, ";".join(reasons), "blocked_missing_facts"
        )

    flow_values = [
        value
        for metric, value in values.items()
        if metric.value in _FLOW_METRICS
    ]
    if filing.has_duration_metadata and flow_values:
        starts = [value.period_start for value in flow_values]
        if any(start is None for start in starts):
            return None, _Result(
                None,
                None,
                "missing_flow_duration",
                "blocked_duration_integrity",
            )
        if len(set(starts)) != 1:
            return None, _Result(
                None,
                None,
                "flow_duration_mismatch",
                "blocked_duration_integrity",
            )
    return values, None


def _units_match(values: Iterable[_MetricValue]) -> bool:
    return len({value.unit for value in values}) == 1


def _invalid(reason: str, gate: str = "blocked_invalid_facts") -> _Result:
    return _Result(None, None, reason, gate)


def _gross_profitability(current: _Filing) -> _Result:
    values, error = _required(
        current,
        (
            FundamentalMetric.REVENUE,
            FundamentalMetric.COST_OF_GOODS_SOLD,
            FundamentalMetric.TOTAL_ASSETS,
        ),
    )
    if error:
        return error
    assert values is not None
    if not _units_match(values.values()):
        return _invalid("unit_mismatch")
    assets = values[FundamentalMetric.TOTAL_ASSETS].value
    if assets <= 0:
        return _invalid("nonpositive_total_assets")
    if values[FundamentalMetric.REVENUE].value < 0:
        return _invalid("negative_revenue")
    if values[FundamentalMetric.COST_OF_GOODS_SOLD].value < 0:
        return _invalid("negative_cost_of_goods_sold")
    raw_value = (
        values[FundamentalMetric.REVENUE].value
        - values[FundamentalMetric.COST_OF_GOODS_SOLD].value
    ) / assets
    return _Result(raw_value, raw_value)


def _asset_growth(current: _Filing, prior: _Filing | None) -> _Result:
    if prior is None:
        return _invalid("missing_prior_fiscal_year", "blocked_missing_history")
    current_values, error = _required(current, (FundamentalMetric.TOTAL_ASSETS,))
    if error:
        return error
    prior_values, error = _required(prior, (FundamentalMetric.TOTAL_ASSETS,))
    if error:
        return error
    assert current_values is not None and prior_values is not None
    current_assets = current_values[FundamentalMetric.TOTAL_ASSETS]
    prior_assets = prior_values[FundamentalMetric.TOTAL_ASSETS]
    if not _units_match((current_assets, prior_assets)):
        return _invalid("unit_mismatch")
    if current_assets.value <= 0 or prior_assets.value <= 0:
        return _invalid("nonpositive_total_assets")
    raw_value = current_assets.value / prior_assets.value - 1.0
    return _Result(raw_value, -raw_value)


def _sloan_accruals(current: _Filing, prior: _Filing | None) -> _Result:
    """Strict Sloan balance-sheet accruals, including depreciation."""

    if prior is None:
        return _invalid("missing_prior_fiscal_year", "blocked_missing_history")
    balance_metrics = (
        FundamentalMetric.CURRENT_ASSETS,
        FundamentalMetric.CASH,
        FundamentalMetric.CURRENT_LIABILITIES,
        FundamentalMetric.SHORT_TERM_DEBT,
        FundamentalMetric.TAXES_PAYABLE,
        FundamentalMetric.TOTAL_ASSETS,
    )
    current_values, error = _required(
        current, (*balance_metrics, FundamentalMetric.DEPRECIATION)
    )
    if error:
        return error
    prior_values, error = _required(prior, balance_metrics)
    if error:
        return error
    assert current_values is not None and prior_values is not None
    all_values = [*current_values.values(), *prior_values.values()]
    if not _units_match(all_values):
        return _invalid("unit_mismatch")
    for metric in (*balance_metrics, FundamentalMetric.DEPRECIATION):
        if current_values[metric].value < 0:
            return _invalid(f"negative_{metric.value}")
    for metric in balance_metrics:
        if prior_values[metric].value < 0:
            return _invalid(f"negative_prior_{metric.value}")

    delta_current_assets = (
        current_values[FundamentalMetric.CURRENT_ASSETS].value
        - prior_values[FundamentalMetric.CURRENT_ASSETS].value
    )
    delta_cash = (
        current_values[FundamentalMetric.CASH].value
        - prior_values[FundamentalMetric.CASH].value
    )
    delta_current_liabilities = (
        current_values[FundamentalMetric.CURRENT_LIABILITIES].value
        - prior_values[FundamentalMetric.CURRENT_LIABILITIES].value
    )
    delta_short_debt = (
        current_values[FundamentalMetric.SHORT_TERM_DEBT].value
        - prior_values[FundamentalMetric.SHORT_TERM_DEBT].value
    )
    delta_taxes_payable = (
        current_values[FundamentalMetric.TAXES_PAYABLE].value
        - prior_values[FundamentalMetric.TAXES_PAYABLE].value
    )
    accruals = (
        (delta_current_assets - delta_cash)
        - (delta_current_liabilities - delta_short_debt - delta_taxes_payable)
        - current_values[FundamentalMetric.DEPRECIATION].value
    )
    average_assets = 0.5 * (
        current_values[FundamentalMetric.TOTAL_ASSETS].value
        + prior_values[FundamentalMetric.TOTAL_ASSETS].value
    )
    if average_assets <= 0:
        return _invalid("nonpositive_average_total_assets")
    raw_value = accruals / average_assets
    return _Result(raw_value, -raw_value)


def _cfo_accruals(current: _Filing, prior: _Filing | None) -> _Result:
    if prior is None:
        return _invalid("missing_prior_fiscal_year", "blocked_missing_history")
    current_values, error = _required(
        current,
        (
            FundamentalMetric.NET_INCOME,
            FundamentalMetric.CASH_FLOW_FROM_OPERATIONS,
            FundamentalMetric.TOTAL_ASSETS,
        ),
    )
    if error:
        return error
    prior_values, error = _required(prior, (FundamentalMetric.TOTAL_ASSETS,))
    if error:
        return error
    assert current_values is not None and prior_values is not None
    all_values = [*current_values.values(), *prior_values.values()]
    if not _units_match(all_values):
        return _invalid("unit_mismatch")
    average_assets = 0.5 * (
        current_values[FundamentalMetric.TOTAL_ASSETS].value
        + prior_values[FundamentalMetric.TOTAL_ASSETS].value
    )
    if average_assets <= 0:
        return _invalid("nonpositive_average_total_assets")
    accruals = (
        current_values[FundamentalMetric.NET_INCOME].value
        - current_values[FundamentalMetric.CASH_FLOW_FROM_OPERATIONS].value
    )
    raw_value = accruals / average_assets
    return _Result(raw_value, -raw_value)


def _market_equity_asof(
    market_equity: pd.DataFrame | None,
    cik: str,
    measurement_cutoff: pd.Timestamp,
    signal_date: pd.Timestamp,
    *,
    max_staleness: pd.Timedelta = pd.Timedelta(days=7),
) -> tuple[_MetricValue | None, _Result | None]:
    if market_equity is None:
        return None, _invalid(
            "issuer_market_equity_not_provided",
            "blocked_issuer_market_equity_unavailable",
        )
    eligible = market_equity.loc[
        (market_equity["cik"] == cik)
        & (market_equity["measurement_date"] <= measurement_cutoff)
        & (market_equity["available_session"] <= signal_date)
    ]
    if eligible.empty:
        return None, _invalid(
            "issuer_market_equity_unavailable",
            "blocked_issuer_market_equity_unavailable",
        )
    row = eligible.iloc[-1]
    if measurement_cutoff - row["measurement_date"] > max_staleness:
        return None, _invalid(
            "issuer_market_equity_stale",
            "blocked_issuer_market_equity_unavailable",
        )
    if not bool(row["all_share_classes_audited"]):
        return None, _invalid(
            "issuer_market_equity_all_share_classes_not_audited",
            "blocked_issuer_market_equity_audit",
        )
    value = float(row["issuer_market_equity"])
    unit = row["unit"]
    if not np.isfinite(value) or value <= 0:
        return None, _invalid(
            "invalid_issuer_market_equity",
            "blocked_issuer_market_equity_unavailable",
        )
    if pd.isna(unit) or not str(unit).strip():
        return None, _invalid(
            "missing_issuer_market_equity_unit",
            "blocked_issuer_market_equity_unavailable",
        )
    return _MetricValue(value, str(unit), None), None


def _book_to_market(
    current: _Filing,
    signal_date: pd.Timestamp,
    market_equity: pd.DataFrame | None,
) -> _Result:
    values, error = _required(current, (FundamentalMetric.COMMON_BOOK_EQUITY,))
    if error:
        return error
    assert values is not None
    book_equity = values[FundamentalMetric.COMMON_BOOK_EQUITY]
    if book_equity.value <= 0:
        return _invalid("nonpositive_common_book_equity")
    issuer_me, error = _market_equity_asof(
        market_equity, current.cik, signal_date, signal_date
    )
    if error:
        return error
    assert issuer_me is not None
    if not _units_match((book_equity, issuer_me)):
        return _invalid("unit_mismatch")
    raw_value = book_equity.value / issuer_me.value
    return _Result(raw_value, raw_value)


def _net_payout_yield(
    current: _Filing,
    signal_date: pd.Timestamp,
    market_equity: pd.DataFrame | None,
) -> _Result:
    values, error = _required(
        current,
        (
            FundamentalMetric.COMMON_DIVIDENDS,
            FundamentalMetric.COMMON_SHARE_REPURCHASES,
            FundamentalMetric.COMMON_SHARE_ISSUANCE,
        ),
    )
    if error:
        return error
    assert values is not None
    issuer_me, error = _market_equity_asof(
        market_equity, current.cik, current.period_end, signal_date
    )
    if error:
        return error
    assert issuer_me is not None
    if not _units_match((*values.values(), issuer_me)):
        return _invalid("unit_mismatch")
    payout = (
        values[FundamentalMetric.COMMON_DIVIDENDS].value
        + values[FundamentalMetric.COMMON_SHARE_REPURCHASES].value
        - values[FundamentalMetric.COMMON_SHARE_ISSUANCE].value
    )
    raw_value = payout / issuer_me.value
    return _Result(raw_value, raw_value)


def _is_financial(sic: int | None) -> bool | None:
    if sic is None:
        return None
    return 6000 <= sic <= 6999


def _compute_factor(
    factor: FundamentalFactorDefinition,
    current: _Filing,
    prior: _Filing | None,
    signal_date: pd.Timestamp,
    market_equity: pd.DataFrame | None,
    apply_pit_financial_sector_filter: bool,
) -> _Result:
    if factor in _NONFINANCIAL_FACTORS and apply_pit_financial_sector_filter:
        if not current.sic_is_pit:
            return _invalid("missing_sic", "blocked_applicability_unknown")
        financial = _is_financial(current.sic)
        if financial is None:
            return _invalid("missing_sic", "blocked_applicability_unknown")
        if financial:
            return _Result(
                None,
                None,
                "financial_sector_not_applicable",
                "not_applicable_financial",
            )
    if factor is FundamentalFactorDefinition.GROSS_PROFITABILITY:
        return _gross_profitability(current)
    if factor is FundamentalFactorDefinition.ASSET_GROWTH:
        return _asset_growth(current, prior)
    if factor is FundamentalFactorDefinition.SLOAN_ACCRUALS:
        return _sloan_accruals(current, prior)
    if factor is FundamentalFactorDefinition.CFO_ACCRUALS_PROJECT_TRANSLATION:
        return _cfo_accruals(current, prior)
    if factor is FundamentalFactorDefinition.BOOK_TO_MARKET:
        return _book_to_market(current, signal_date, market_equity)
    if factor is FundamentalFactorDefinition.NET_PAYOUT_YIELD:
        return _net_payout_yield(current, signal_date, market_equity)
    raise AssertionError(f"unhandled factor: {factor}")


def _applicability_metadata(
    current: _Filing | None,
    *,
    apply_pit_financial_sector_filter: bool,
) -> tuple[str, str]:
    if current is None or not current.sic_is_pit:
        return "unverified_no_pit_sic", "unverified_no_pit_sic"
    scope = (
        "pit_sic_financial_filter"
        if apply_pit_financial_sector_filter
        else "all_sectors_pit_sic_available_filter_disabled"
    )
    return scope, current.sic_provenance


def compute_fundamental_factor_panel(
    fact_events: pd.DataFrame,
    sid_cik_map: pd.DataFrame,
    signal_dates: Iterable[pd.Timestamp],
    *,
    issuer_market_equity: pd.DataFrame | None = None,
    apply_pit_financial_sector_filter: bool = False,
) -> pd.DataFrame:
    """Return an auditable long fundamental-factor panel.

    Parameters
    ----------
    fact_events:
        Long annual canonical facts.  Required columns are ``cik``,
        ``accession``, ``accepted_at``, ``available_session``, ``fiscal_year``,
        ``period_end``, ``metric_id``, ``value``, ``unit`` and ``sic``.
        ``period_start`` (or ``duration_start``) is optional; when supplied it
        is a hard same-duration gate for flow metrics.
    sid_cik_map:
        At minimum ``sid`` and ``cik``.  Optional ``effective_from`` and
        exclusive ``effective_to`` columns make identifier changes PIT-safe.
    signal_dates:
        Monthly exchange-session dates.  They must be timezone-naive.
    issuer_market_equity:
        Optional issuer-level market-equity observations for BM and NPY.  This
        is separate from SEC facts because it must aggregate *all* common share
        classes.  The required audit flag is a hard gate; no single-share-class
        approximation is made.
    apply_pit_financial_sector_filter:
        Apply the paper factors' financial-sector exclusion only when fact
        events explicitly carry ``sic_is_pit=True`` and non-empty
        ``sic_provenance``. The default is false because SEC submissions
        exposes current issuer SIC, not a filing-vintage classification.

    Returns
    -------
    pandas.DataFrame
        Multi-indexed by ``(signal_date, sid, factor_id)``. ``raw_value`` is
        the natural economic ratio and ``score`` is the long-only directional
        value. Missing values are retained with a machine-readable
        ``missing_reason`` and ``data_gate``.
    """

    if not isinstance(apply_pit_financial_sector_filter, (bool, np.bool_)):
        raise TypeError("apply_pit_financial_sector_filter must be boolean")

    filings = _prepare_fact_events(fact_events)
    filings_by_cik: dict[str, list[_Filing]] = {}
    for filing in filings:
        filings_by_cik.setdefault(filing.cik, []).append(filing)
    mapping = _prepare_sid_cik_map(sid_cik_map)
    market_equity = _prepare_market_equity(issuer_market_equity)
    dates = pd.DatetimeIndex(pd.to_datetime(list(signal_dates)))
    if dates.tz is not None:
        raise ValueError("signal_dates must be timezone-naive")
    dates = dates.normalize().sort_values().unique()
    if dates.hasnans:
        raise ValueError("signal_dates cannot contain NaT")

    rows: list[dict[str, object]] = []
    for signal_date in dates:
        active = mapping.loc[
            (mapping["effective_from"] <= signal_date)
            & (
                mapping["effective_to"].isna()
                | (signal_date < mapping["effective_to"])
            )
        ]
        duplicate_sids = active.loc[active["sid"].duplicated(keep=False), "sid"]
        if not duplicate_sids.empty:
            raise ValueError(
                "sid_cik_map has overlapping active mappings for: "
                f"{sorted(duplicate_sids.astype(str).unique().tolist())}"
            )
        for map_row in active.sort_values("sid", kind="stable").itertuples():
            sid = str(map_row.sid)
            cik = str(map_row.cik)
            current, prior = _latest_filings(
                filings_by_cik.get(cik, []), cik, signal_date
            )
            for factor in FACTOR_ORDER:
                if current is None:
                    result = _Result(
                        None,
                        None,
                        "no_available_filing",
                        "blocked_missing_filing",
                    )
                else:
                    result = _compute_factor(
                        factor,
                        current,
                        prior,
                        signal_date,
                        market_equity,
                        bool(apply_pit_financial_sector_filter),
                    )
                applicability_scope, applicability_provenance = (
                    _applicability_metadata(
                        current,
                        apply_pit_financial_sector_filter=bool(
                            apply_pit_financial_sector_filter
                        ),
                    )
                )
                rows.append(
                    {
                        "signal_date": signal_date,
                        "sid": sid,
                        "factor_id": factor.value,
                        "cik": cik,
                        "raw_value": (
                            np.nan
                            if result.raw_value is None
                            else float(result.raw_value)
                        ),
                        "score": (
                            np.nan if result.score is None else float(result.score)
                        ),
                        "definition_status": _DEFINITION_STATUS[factor],
                        "source_fiscal_year": (
                            pd.NA if current is None else current.fiscal_year
                        ),
                        "source_period_end": (
                            pd.NaT if current is None else current.period_end
                        ),
                        "source_accession": (
                            pd.NA if current is None else current.accession
                        ),
                        "source_available_session": (
                            pd.NaT if current is None else current.available_session
                        ),
                        "missing_reason": (
                            pd.NA
                            if result.missing_reason is None
                            else result.missing_reason
                        ),
                        "data_gate": result.data_gate,
                        "applicability_scope": applicability_scope,
                        "applicability_provenance": applicability_provenance,
                    }
                )

    columns = [
        "signal_date",
        "sid",
        "factor_id",
        "cik",
        "raw_value",
        "score",
        "definition_status",
        "source_fiscal_year",
        "source_period_end",
        "source_accession",
        "source_available_session",
        "missing_reason",
        "data_gate",
        "applicability_scope",
        "applicability_provenance",
    ]
    panel = pd.DataFrame(rows, columns=columns)
    if panel.empty:
        return panel.set_index(["signal_date", "sid", "factor_id"])
    panel["source_fiscal_year"] = panel["source_fiscal_year"].astype("Int64")
    return panel.set_index(["signal_date", "sid", "factor_id"]).sort_index()


# Short alias for callers that use the generic factor-module naming pattern.
compute_fundamental_scores = compute_fundamental_factor_panel
compute_cross_sectional_fundamental_panel = compute_fundamental_factor_panel
compute_cross_sectional_fundamental_scores = compute_fundamental_factor_panel


__all__ = [
    "FundamentalFactorDefinition",
    "FundamentalMetric",
    "compute_cross_sectional_fundamental_panel",
    "compute_cross_sectional_fundamental_scores",
    "compute_fundamental_factor_panel",
    "compute_fundamental_scores",
]
