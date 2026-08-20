"""Point-in-time temporal support checks for SID-to-CIK intervals."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


ISSUER_PERIODIC_FORMS = frozenset(
    {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}
)


def build_entity_temporal_support_qa(
    filings: pd.DataFrame,
    intervals: pd.DataFrame,
    *,
    history_start: object,
    evaluation_end: object,
    source_applicability: pd.DataFrame | None = None,
    minimum_long_interval_days: int = 365,
    periodic_forms: Iterable[str] = ISSUER_PERIODIC_FORMS,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Audit that long research intervals have contemporaneous issuer filings.

    A current ticker match alone is not historical identity evidence.  For an
    interval lasting at least ``minimum_long_interval_days``, the mapped CIK
    must have filed at least one issuer periodic report while that SID was an
    active constituent.  The only exception is an explicitly reviewed SEC
    source-applicability row such as a bank issuer for which Company Facts is
    officially absent.  Short IPO/spin episodes remain visible but are not
    rejected merely because their first periodic filing occurs after removal.
    """

    required_filings = {"cik", "form", "filed_date"}
    required_intervals = {"sid", "cik10", "effective_from", "effective_to"}
    missing_filings = required_filings.difference(filings.columns)
    missing_intervals = required_intervals.difference(intervals.columns)
    if missing_filings:
        raise ValueError(f"filings missing columns: {sorted(missing_filings)}")
    if missing_intervals:
        raise ValueError(f"intervals missing columns: {sorted(missing_intervals)}")
    if int(minimum_long_interval_days) <= 0:
        raise ValueError("minimum_long_interval_days must be positive")

    start = pd.Timestamp(history_start).normalize()
    end_exclusive = pd.Timestamp(evaluation_end).normalize() + pd.Timedelta(days=1)
    forms = {str(value).upper() for value in periodic_forms}

    facts = filings.loc[:, ["cik", "form", "filed_date"]].copy()
    facts["cik10"] = facts.pop("cik").astype(str).str.zfill(10)
    facts["form"] = facts["form"].astype(str).str.upper()
    facts["filed_date"] = pd.to_datetime(facts["filed_date"], errors="coerce").dt.normalize()
    facts = facts.loc[facts["form"].isin(forms) & facts["filed_date"].notna()]
    filings_by_cik = {
        str(cik10): group["filed_date"].sort_values().reset_index(drop=True)
        for cik10, group in facts.groupby("cik10", sort=False)
    }

    applicability: dict[str, str] = {}
    if source_applicability is not None and not source_applicability.empty:
        required_applicability = {"cik10", "source_applicability_status"}
        missing = required_applicability.difference(source_applicability.columns)
        if missing:
            raise ValueError(
                f"source_applicability missing columns: {sorted(missing)}"
            )
        for item in source_applicability.itertuples(index=False):
            key = str(item.cik10).zfill(10)
            status = str(item.source_applicability_status)
            prior = applicability.get(key)
            if prior is not None and prior != status:
                raise ValueError(f"conflicting source applicability for {key}")
            applicability[key] = status

    rows: list[dict[str, object]] = []
    for item in intervals.sort_values(["sid", "effective_from", "cik10"]).itertuples(index=False):
        left = max(pd.Timestamp(item.effective_from).normalize(), start)
        raw_right = (
            end_exclusive
            if pd.isna(item.effective_to)
            else pd.Timestamp(item.effective_to).normalize()
        )
        right = min(raw_right, end_exclusive)
        if left >= right:
            continue
        cik10 = str(item.cik10).zfill(10)
        dates = filings_by_cik.get(cik10, pd.Series(dtype="datetime64[ns]"))
        inside = dates.loc[(dates >= left) & (dates < right)]
        interval_days = int((right - left).days)
        long_interval = interval_days >= int(minimum_long_interval_days)
        source_status = applicability.get(cik10, "available")
        resolved_not_applicable = source_status == "resolved_not_applicable"
        temporal_support_passed = bool(
            len(inside) > 0 or not long_interval or resolved_not_applicable
        )
        if len(inside) > 0:
            status = "supported_by_periodic_filing"
        elif resolved_not_applicable:
            status = "reviewed_source_not_applicable"
        elif not long_interval:
            status = "short_interval_no_periodic_required"
        else:
            status = "long_interval_without_periodic_support"
        rows.append(
            {
                "sid": str(item.sid),
                "cik10": cik10,
                "effective_from": pd.Timestamp(item.effective_from).normalize(),
                "effective_to": (
                    pd.NaT
                    if pd.isna(item.effective_to)
                    else pd.Timestamp(item.effective_to).normalize()
                ),
                "research_interval_from": left,
                "research_interval_to": right,
                "research_interval_days": interval_days,
                "issuer_periodic_filing_count": int(len(inside)),
                "first_periodic_filed": inside.min() if len(inside) else pd.NaT,
                "last_periodic_filed": inside.max() if len(inside) else pd.NaT,
                "source_applicability_status": source_status,
                "temporal_support_status": status,
                "temporal_support_passed": temporal_support_passed,
            }
        )

    qa = pd.DataFrame(rows)
    failed = qa.loc[~qa["temporal_support_passed"]] if not qa.empty else qa
    summary: dict[str, object] = {
        "schema_version": "cross_sectional_alpha.entity_temporal_support.v1",
        "history_start": start.strftime("%Y-%m-%d"),
        "evaluation_end": (end_exclusive - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        "minimum_long_interval_days": int(minimum_long_interval_days),
        "interval_count": int(len(qa)),
        "long_interval_count": int(
            (qa["research_interval_days"] >= int(minimum_long_interval_days)).sum()
        )
        if not qa.empty
        else 0,
        "resolved_not_applicable_interval_count": int(
            qa["source_applicability_status"].eq("resolved_not_applicable").sum()
        )
        if not qa.empty
        else 0,
        "failed_interval_count": int(len(failed)),
        "failed_sids": sorted(failed["sid"].astype(str).unique().tolist())
        if not failed.empty
        else [],
        "temporal_support_gate_passed": bool(failed.empty),
    }
    return qa, summary

