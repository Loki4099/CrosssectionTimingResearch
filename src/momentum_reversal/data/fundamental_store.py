"""Canonical annual SEC facts and their point-in-time provenance."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


_GROSS_PROFIT_IDENTITY_METRICS = frozenset(
    {"revenue", "cost_of_goods_sold", "gross_profit"}
)
_GROSS_PROFIT_IDENTITY_RTOL = 1e-6
_GROSS_PROFIT_IDENTITY_ATOL_USD = 1.0


@dataclass(frozen=True)
class CanonicalizationResult:
    facts: pd.DataFrame
    audit: pd.DataFrame


def load_sec_metric_registry(path: str | Path) -> pd.DataFrame:
    """Load and validate the versioned SEC tag-to-metric registry."""

    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {
        "metric_id",
        "taxonomy",
        "tag_priority",
        "period_type",
        "unit_family",
    }
    if not required.issubset(frame.columns):
        raise ValueError("SEC metric registry is missing required columns")
    if frame["metric_id"].eq("").any() or frame["metric_id"].duplicated().any():
        raise ValueError("SEC metric_id must be unique and non-empty")
    allowed_periods = {"instant", "duration_fy"}
    if not set(frame["period_type"]).issubset(allowed_periods):
        raise ValueError("unsupported SEC metric period_type")
    if frame["tag_priority"].str.strip().eq("").any():
        raise ValueError("every SEC metric needs at least one tag")
    return frame


def canonicalize_annual_facts(
    raw_facts: pd.DataFrame,
    filings: pd.DataFrame,
    metric_registry: pd.DataFrame,
    *,
    minimum_duration_days: int = 300,
    maximum_duration_days: int = 430,
) -> CanonicalizationResult:
    """Map Company Facts rows to strict annual canonical fact events.

    Each accession remains an independent vintage.  A later filing that
    restates an earlier period therefore creates another event with a later
    ``accepted_at``; it never replaces an older event in this table.
    """

    required_fact = {
        "cik",
        "accession",
        "taxonomy",
        "tag",
        "unit",
        "value",
        "period_end",
        "form",
    }
    required_filing = {"cik", "accession", "accepted_at", "available_session"}
    if not required_fact.issubset(raw_facts.columns):
        raise ValueError(f"raw_facts missing {sorted(required_fact - set(raw_facts.columns))}")
    if not required_filing.issubset(filings.columns):
        raise ValueError(f"filings missing {sorted(required_filing - set(filings.columns))}")

    tag_map_rows: list[dict[str, Any]] = []
    for row in metric_registry.itertuples(index=False):
        for priority, tag in enumerate(str(row.tag_priority).split("|")):
            tag_map_rows.append(
                {
                    "metric_id": str(row.metric_id),
                    "taxonomy": str(row.taxonomy),
                    "tag": tag.strip(),
                    "tag_priority_rank": priority,
                    "period_type": str(row.period_type),
                    "unit_family": str(row.unit_family).upper(),
                }
            )
    tag_map = pd.DataFrame(tag_map_rows)
    if tag_map.duplicated(["taxonomy", "tag", "metric_id"]).any():
        raise ValueError("duplicate SEC tag mapping")

    facts = raw_facts.copy()
    facts["cik"] = facts["cik"].map(_cik10)
    facts["accession"] = facts["accession"].astype(str).str.strip()
    facts["taxonomy"] = facts["taxonomy"].astype(str).str.strip()
    facts["tag"] = facts["tag"].astype(str).str.strip()
    facts["unit"] = facts["unit"].astype(str).str.strip()
    facts["value"] = pd.to_numeric(facts["value"], errors="coerce")
    facts["period_end"] = pd.to_datetime(facts["period_end"], errors="coerce").dt.normalize()
    if "period_start" not in facts:
        facts["period_start"] = pd.NaT
    facts["period_start"] = pd.to_datetime(facts["period_start"], errors="coerce").dt.normalize()
    facts["form"] = facts["form"].astype(str).str.upper().str.strip()
    facts = facts.loc[facts["form"].isin({"10-K", "10-K/A"})]
    facts = facts.merge(
        tag_map,
        on=["taxonomy", "tag"],
        how="inner",
        validate="many_to_many",
    )
    facts["duration_days"] = (
        facts["period_end"] - facts["period_start"]
    ).dt.days
    instant = facts["period_type"].eq("instant") & facts["period_start"].isna()
    annual = facts["period_type"].eq("duration_fy") & facts["duration_days"].between(
        minimum_duration_days, maximum_duration_days, inclusive="both"
    )
    facts = facts.loc[instant | annual]
    facts = facts.loc[facts["value"].map(np.isfinite) & facts["period_end"].notna()]
    facts = facts.loc[
        facts.apply(lambda row: _unit_matches(row["unit"], row["unit_family"]), axis=1)
    ]

    filing_columns = ["cik", "accession", "accepted_at", "available_session"]
    for optional in ("sic", "filed_date", "primary_document"):
        if optional in filings.columns:
            filing_columns.append(optional)
    ledger = filings.loc[:, filing_columns].copy()
    ledger["cik"] = ledger["cik"].map(_cik10)
    ledger["accession"] = ledger["accession"].astype(str).str.strip()
    if ledger.duplicated(["cik", "accession"]).any():
        raise ValueError("filing ledger has duplicate CIK/accession")
    merged = facts.merge(
        ledger,
        on=["cik", "accession"],
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    unmatched = merged["_merge"].ne("both")

    audit_rows: list[dict[str, Any]] = [
        {"check_id": "raw_rows", "count": int(len(raw_facts)), "status": "info"},
        {"check_id": "mapped_annual_rows", "count": int(len(merged)), "status": "info"},
        {
            "check_id": "unmatched_accession_rows",
            "count": int(unmatched.sum()),
            "status": "pass" if not unmatched.any() else "fail",
        },
    ]
    merged = merged.loc[~unmatched].drop(columns="_merge")
    merged["accepted_at"] = pd.to_datetime(merged["accepted_at"], utc=True)
    merged["available_session"] = pd.to_datetime(
        merged["available_session"], errors="coerce"
    ).dt.normalize()
    merged["fiscal_year"] = merged["period_end"].dt.year.astype(int)

    keys = ["cik", "accession", "period_end", "metric_id"]
    selected_rows: list[pd.Series] = []
    ambiguous = 0
    generic = merged.loc[
        ~merged["metric_id"].isin(_GROSS_PROFIT_IDENTITY_METRICS)
    ]
    for _, group in generic.groupby(keys, sort=True, dropna=False):
        best_rank = int(group["tag_priority_rank"].min())
        best = group.loc[group["tag_priority_rank"].eq(best_rank)].copy()
        distinct = best[["unit", "value", "period_start"]].drop_duplicates()
        if len(distinct) > 1:
            ambiguous += 1
            continue
        selected_rows.append(best.sort_values(["tag", "unit"], kind="stable").iloc[0])
    identity_rows, identity_audit, identity_ambiguous = (
        _select_gross_profit_identity_rows(
            merged.loc[
                merged["metric_id"].isin(_GROSS_PROFIT_IDENTITY_METRICS)
            ]
        )
    )
    selected_rows.extend(identity_rows)
    ambiguous += identity_ambiguous
    audit_rows.append(
        {
            "check_id": "ambiguous_metric_contexts",
            "count": ambiguous,
            "status": "pass" if ambiguous == 0 else "review",
        }
    )
    audit_rows.extend(identity_audit)

    columns = [
        "cik",
        "accession",
        "accepted_at",
        "available_session",
        "fiscal_year",
        "period_start",
        "period_end",
        "metric_id",
        "value",
        "unit",
        "taxonomy",
        "tag",
        "form",
    ]
    for optional in ("sic", "filed_date", "primary_document"):
        if optional in merged.columns:
            columns.append(optional)
    canonical = pd.DataFrame(selected_rows)
    if canonical.empty:
        canonical = pd.DataFrame(columns=columns)
    else:
        canonical = canonical.loc[:, columns]
        canonical = canonical.sort_values(
            ["cik", "accepted_at", "period_end", "metric_id", "accession"],
            ignore_index=True,
        )
    if canonical.duplicated(keys).any():
        sample = canonical.loc[
            canonical.duplicated(keys, keep=False),
            [*keys, "tag", "value"],
        ].head(10)
        raise AssertionError(
            "canonical annual fact key is not unique: "
            f"{sample.to_dict(orient='records')}"
        )
    return CanonicalizationResult(canonical, pd.DataFrame(audit_rows))


def _select_gross_profit_identity_rows(
    facts: pd.DataFrame,
) -> tuple[list[pd.Series], list[dict[str, Any]], int]:
    """Select revenue/COGS/gross-profit facts with an accounting identity.

    Fixed tag priority alone is unsafe when a filing exposes both a broad
    legacy revenue concept and a newly preferred but narrower revenue concept.
    Within one accession and fiscal period, prefer the lowest-priority-rank
    combination satisfying ``revenue - COGS == gross profit``.  If no direct
    COGS combination reconciles, a compatible revenue/gross-profit pair may
    synthesize COGS so downstream gross profitability equals the issuer's
    reported gross profit.  Every such decision is surfaced in the audit.
    """

    base_keys = ["cik", "accession", "period_end"]
    selected: list[pd.Series] = []
    tested = 0
    mismatched = 0
    alternate_tags = 0
    synthetic_cogs = 0
    unresolved = 0
    ambiguous = 0

    for _context, group in facts.groupby(base_keys, sort=True, dropna=False):
        candidates: dict[str, list[pd.Series]] = {}
        for metric_id in sorted(_GROSS_PROFIT_IDENTITY_METRICS):
            metric_rows = group.loc[group["metric_id"].eq(metric_id)]
            ranked, rank_ambiguities = _unambiguous_ranked_candidates(metric_rows)
            candidates[metric_id] = ranked
            ambiguous += rank_ambiguities

        revenues = candidates["revenue"]
        costs = candidates["cost_of_goods_sold"]
        gross_profits = candidates["gross_profit"]
        matching: list[tuple[tuple[Any, ...], pd.Series, pd.Series, pd.Series]] = []
        if revenues and costs and gross_profits:
            tested += 1
            for revenue, cost, gross_profit in product(
                revenues, costs, gross_profits
            ):
                if not _same_flow_context(revenue, cost, gross_profit):
                    continue
                if not _gross_profit_identity_holds(revenue, cost, gross_profit):
                    continue
                ordering = _identity_candidate_order(revenue, cost, gross_profit)
                matching.append((ordering, revenue, cost, gross_profit))

        if matching:
            _, revenue, cost, gross_profit = min(matching, key=lambda item: item[0])
            selected.extend((revenue, cost, gross_profit))
            first_tags = {
                metric_id: str(rows[0]["tag"])
                for metric_id, rows in candidates.items()
                if rows
            }
            chosen_tags = {
                "revenue": str(revenue["tag"]),
                "cost_of_goods_sold": str(cost["tag"]),
                "gross_profit": str(gross_profit["tag"]),
            }
            if any(
                chosen_tags[metric_id] != first_tags[metric_id]
                for metric_id in chosen_tags
            ):
                alternate_tags += 1
            continue

        if revenues and costs and gross_profits:
            mismatched += 1

        # Reported gross profit is the direct economic quantity needed by the
        # paper factor.  When direct COGS cannot reconcile (or is absent), use
        # the best same-unit/same-duration revenue/gross-profit pair and make
        # the derived COGS provenance explicit.  Do not manufacture a negative
        # cost from an incompatible narrow revenue tag.
        compatible_pairs: list[tuple[tuple[Any, ...], pd.Series, pd.Series]] = []
        for revenue, gross_profit in product(revenues, gross_profits):
            if not _same_flow_context(revenue, gross_profit):
                continue
            derived_cost = float(revenue["value"]) - float(gross_profit["value"])
            tolerance = _identity_tolerance(
                float(revenue["value"]), float(gross_profit["value"])
            )
            if derived_cost < -tolerance:
                continue
            ordering = (
                int(revenue["tag_priority_rank"])
                + int(gross_profit["tag_priority_rank"]),
                int(revenue["tag_priority_rank"]),
                int(gross_profit["tag_priority_rank"]),
                str(revenue["tag"]),
                str(gross_profit["tag"]),
            )
            compatible_pairs.append((ordering, revenue, gross_profit))

        if compatible_pairs:
            _, revenue, gross_profit = min(
                compatible_pairs, key=lambda item: item[0]
            )
            derived = revenue.copy()
            derived["metric_id"] = "cost_of_goods_sold"
            derived["value"] = max(
                0.0, float(revenue["value"]) - float(gross_profit["value"])
            )
            derived["taxonomy"] = gross_profit["taxonomy"]
            derived["tag"] = (
                f"synthetic_revenue_minus_{gross_profit['tag']}"
            )
            derived["tag_priority_rank"] = gross_profit["tag_priority_rank"]
            selected.extend((revenue, derived, gross_profit))
            synthetic_cogs += 1
            continue

        # Preserve individually usable evidence without allowing the factor
        # layer to combine an irreconcilable revenue/COGS pair into a score.
        if gross_profits:
            selected.append(gross_profits[0])
        if revenues and not gross_profits:
            selected.append(revenues[0])
        if costs and not (revenues and gross_profits):
            selected.append(costs[0])
        if revenues and gross_profits:
            unresolved += 1

    audit = [
        {
            "check_id": "gross_profit_identity_tested_contexts",
            "count": tested,
            "status": "info",
        },
        {
            "check_id": "gross_profit_identity_mismatch_contexts",
            "count": mismatched,
            "status": "pass" if mismatched == 0 else "review",
        },
        {
            "check_id": "gross_profit_identity_alternate_tag_contexts",
            "count": alternate_tags,
            "status": "info",
        },
        {
            "check_id": "gross_profit_identity_synthetic_cogs_contexts",
            "count": synthetic_cogs,
            "status": "info",
        },
        {
            "check_id": "gross_profit_identity_unresolved_contexts",
            "count": unresolved,
            "status": "pass" if unresolved == 0 else "review",
        },
    ]
    return selected, audit, ambiguous


def _unambiguous_ranked_candidates(
    rows: pd.DataFrame,
) -> tuple[list[pd.Series], int]:
    if rows.empty:
        return [], 0
    candidates: list[pd.Series] = []
    ambiguous = 0
    for _rank, ranked in rows.groupby("tag_priority_rank", sort=True):
        distinct = ranked[["unit", "value", "period_start"]].drop_duplicates()
        if len(distinct) != 1:
            ambiguous += 1
            continue
        candidates.append(
            ranked.sort_values(["tag", "unit"], kind="stable").iloc[0]
        )
    candidates.sort(
        key=lambda row: (int(row["tag_priority_rank"]), str(row["tag"]))
    )
    return candidates, ambiguous


def _same_flow_context(*rows: pd.Series) -> bool:
    if len({str(row["unit"]) for row in rows}) != 1:
        return False
    starts = [row["period_start"] for row in rows]
    if all(pd.isna(value) for value in starts):
        return True
    if any(pd.isna(value) for value in starts):
        return False
    return len({pd.Timestamp(value) for value in starts}) == 1


def _identity_tolerance(*values: float) -> float:
    scale = max(1.0, *(abs(float(value)) for value in values))
    return max(_GROSS_PROFIT_IDENTITY_ATOL_USD, _GROSS_PROFIT_IDENTITY_RTOL * scale)


def _gross_profit_identity_holds(
    revenue: pd.Series, cost: pd.Series, gross_profit: pd.Series
) -> bool:
    revenue_value = float(revenue["value"])
    cost_value = float(cost["value"])
    gross_value = float(gross_profit["value"])
    residual = revenue_value - cost_value - gross_value
    return abs(residual) <= _identity_tolerance(
        revenue_value, cost_value, gross_value
    )


def _identity_candidate_order(
    revenue: pd.Series, cost: pd.Series, gross_profit: pd.Series
) -> tuple[Any, ...]:
    ranks = (
        int(revenue["tag_priority_rank"]),
        int(cost["tag_priority_rank"]),
        int(gross_profit["tag_priority_rank"]),
    )
    return (
        sum(ranks),
        *ranks,
        str(revenue["tag"]),
        str(cost["tag"]),
        str(gross_profit["tag"]),
    )


def _unit_matches(unit: object, family: object) -> bool:
    text = str(unit).strip().upper()
    expected = str(family).strip().upper()
    if expected == "USD":
        return text == "USD"
    if expected == "SHARES":
        return text in {"SHARES", "SHARE"}
    return text == expected


def _cik10(value: object) -> str:
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if not text.isdigit():
        raise ValueError(f"invalid CIK={value!r}")
    return text.zfill(10)
