"""Pure SEC Company Facts to canonical annual-fundamental pipeline.

The acquisition layer in :mod:`momentum_reversal.data.sec_edgar` deliberately
stops at immutable filing and numeric-fact events.  This module connects those
events to an authoritative XNYS schedule and to the canonical annual metric
store.  It performs no network or filesystem I/O.

Availability is conservative and explicit: a filing becomes usable at
``accepted_at + availability_buffer``.  Its ``signal_date`` is the first XNYS
session whose actual close is not earlier than that timestamp.  Consequently,
weekend, holiday, after-close, and early-close cases naturally roll forward.
Every accession remains a separate vintage; amendments never overwrite prior
events.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
from typing import Any

import pandas as pd

from .fundamental_store import canonicalize_annual_facts
from .sec_edgar import (
    SECParseError,
    normalize_cik,
    parse_companyfacts,
    parse_submissions,
)


DEFAULT_AVAILABILITY_BUFFER_MINUTES = 10.0
_ORPHAN_ALIAS_ATTR = "companyfacts_orphan_duplicate_resolution"

_SESSION_DATE_COLUMNS = ("session_date", "session", "date")
_SESSION_CLOSE_COLUMNS = (
    "signal_close",
    "session_close",
    "market_close",
    "close",
)


@dataclass(frozen=True)
class SECFundamentalPipelineResult:
    """Deterministic tables emitted for one SEC registrant."""

    filings: pd.DataFrame
    facts: pd.DataFrame
    canonical: pd.DataFrame
    coverage_qa: pd.DataFrame

    @property
    def qa(self) -> pd.DataFrame:
        """Short alias for callers that expose a generic QA table."""

        return self.coverage_qa


def normalize_xnys_signal_calendar(schedule: pd.DataFrame) -> pd.DataFrame:
    """Validate an authoritative XNYS session schedule.

    The schedule may expose session labels in a ``session_date``, ``session``,
    or ``date`` column, or in its index.  The actual close must be in one of
    ``signal_close``, ``session_close``, ``market_close``, or ``close`` and
    must be timezone-aware.  This accepts the dataframe returned by
    ``exchange_calendars.get_calendar("XNYS").schedule`` without adaptation.
    """

    if not isinstance(schedule, pd.DataFrame):
        raise TypeError("XNYS schedule must be a pandas DataFrame")
    if schedule.empty:
        raise SECParseError("XNYS schedule cannot be empty")

    date_column = next(
        (column for column in _SESSION_DATE_COLUMNS if column in schedule.columns),
        None,
    )
    if date_column is None:
        if isinstance(schedule.index, pd.RangeIndex):
            raise SECParseError("XNYS schedule must expose exchange session dates")
        date_values: Sequence[object] = list(schedule.index)
    else:
        date_values = schedule[date_column].tolist()

    close_column = next(
        (column for column in _SESSION_CLOSE_COLUMNS if column in schedule.columns),
        None,
    )
    if close_column is None:
        raise SECParseError("XNYS schedule must expose actual session close timestamps")

    session_dates = _naive_session_dates(date_values)
    close_times = _aware_utc_timestamps(
        schedule[close_column].tolist(),
        label="XNYS session close",
    )
    normalized = pd.DataFrame(
        {
            "session_date": session_dates,
            "signal_close": close_times,
        }
    )
    if normalized["session_date"].duplicated().any():
        raise SECParseError("XNYS session dates must be unique")
    if not normalized["session_date"].is_monotonic_increasing:
        raise SECParseError("XNYS session dates must be strictly increasing")
    if normalized["signal_close"].duplicated().any():
        raise SECParseError("XNYS session close timestamps must be unique")
    if not normalized["signal_close"].is_monotonic_increasing:
        raise SECParseError("XNYS session closes must be strictly increasing")
    return normalized


def map_accepted_at_to_signal_date(
    accepted_at: object,
    xnys_schedule: pd.DataFrame,
    *,
    availability_buffer_minutes: float = DEFAULT_AVAILABILITY_BUFFER_MINUTES,
) -> pd.Timestamp:
    """Map one SEC acceptance timestamp to its first legal signal session."""

    mapped = map_accepted_at_to_signal_dates(
        pd.Series([accepted_at]),
        xnys_schedule,
        availability_buffer_minutes=availability_buffer_minutes,
    )
    return pd.Timestamp(mapped.iloc[0])


def map_accepted_at_to_signal_dates(
    accepted_at: pd.Series | Sequence[object],
    xnys_schedule: pd.DataFrame,
    *,
    availability_buffer_minutes: float = DEFAULT_AVAILABILITY_BUFFER_MINUTES,
) -> pd.Series:
    """Vectorized availability mapping while preserving the input index."""

    buffer_minutes = _validate_buffer_minutes(availability_buffer_minutes)
    calendar = normalize_xnys_signal_calendar(xnys_schedule)
    if isinstance(accepted_at, pd.Series):
        source = accepted_at.copy()
    elif isinstance(accepted_at, Sequence) and not isinstance(
        accepted_at, (str, bytes, bytearray)
    ):
        source = pd.Series(list(accepted_at))
    else:
        raise TypeError("accepted_at must be a pandas Series or a sequence")
    if source.empty:
        return pd.Series([], index=source.index, dtype="datetime64[ns]", name="signal_date")

    accepted_utc = _aware_utc_timestamps(source.tolist(), label="accepted_at")
    ready_at = accepted_utc + pd.to_timedelta(buffer_minutes, unit="m")
    positions = calendar["signal_close"].searchsorted(ready_at, side="left")
    if (positions >= len(calendar)).any():
        first = int(positions.argmax())
        raise SECParseError(
            "XNYS schedule does not extend through the first legal signal date "
            f"for accepted_at={accepted_utc.iloc[first].isoformat()}"
        )
    values = calendar["session_date"].to_numpy()[positions]
    return pd.Series(values, index=source.index, name="signal_date", dtype="datetime64[ns]")


def assign_filing_signal_dates(
    filing_ledger: pd.DataFrame,
    xnys_schedule: pd.DataFrame,
    *,
    availability_buffer_minutes: float = DEFAULT_AVAILABILITY_BUFFER_MINUTES,
) -> pd.DataFrame:
    """Attach conservative availability timestamps and signal sessions."""

    _require_columns(
        filing_ledger,
        {"cik", "accession", "accepted_at"},
        "filing ledger",
    )
    frame = filing_ledger.copy()
    if frame.empty:
        frame["availability_ready_at"] = pd.Series(dtype="datetime64[ns, UTC]")
        frame["signal_date"] = pd.Series(dtype="datetime64[ns]")
        frame["available_session"] = pd.Series(dtype="datetime64[ns]")
        return frame

    frame["cik"] = frame["cik"].map(normalize_cik)
    frame["accession"] = frame["accession"].astype(str).str.strip()
    if frame.duplicated(["cik", "accession"]).any():
        raise SECParseError("filing ledger contains duplicate CIK/accession vintages")
    accepted_utc = _aware_utc_timestamps(
        frame["accepted_at"].tolist(),
        label="accepted_at",
    )
    accepted_utc.index = frame.index
    buffer_minutes = _validate_buffer_minutes(availability_buffer_minutes)
    frame["accepted_at"] = accepted_utc
    frame["availability_ready_at"] = accepted_utc + pd.to_timedelta(
        buffer_minutes, unit="m"
    )
    frame["signal_date"] = map_accepted_at_to_signal_dates(
        frame["accepted_at"],
        xnys_schedule,
        availability_buffer_minutes=buffer_minutes,
    )
    # ``fundamental_store`` predates the research-layer ``signal_date`` name.
    # Keep its established contract without creating a second timing rule.
    frame["available_session"] = frame["signal_date"]
    return frame.sort_values(["accepted_at", "accession"], ignore_index=True)


def adapt_companyfacts_for_canonicalization(
    numeric_facts: pd.DataFrame,
    filing_ledger: pd.DataFrame,
) -> pd.DataFrame:
    """Add canonical-store aliases and filing provenance to numeric facts."""

    required_facts = {
        "cik",
        "accession",
        "taxonomy",
        "concept",
        "unit",
        "start",
        "end",
        "value",
        "form",
        "accepted_at",
    }
    required_filings = {
        "cik",
        "accession",
        "accepted_at",
        "signal_date",
        "available_session",
        "sic",
    }
    _require_columns(numeric_facts, required_facts, "numeric Company Facts")
    _require_columns(filing_ledger, required_filings, "filing ledger")

    orphan_resolution = dict(
        numeric_facts.attrs.get(
            _ORPHAN_ALIAS_ATTR,
            {
                "orphan_duplicate_resolved_count": 0,
                "cik": None,
                "accessions": [],
            },
        )
    )
    frame = numeric_facts.copy()
    if frame.empty:
        for column, dtype in (
            ("tag", "object"),
            ("period_start", "datetime64[ns]"),
            ("period_end", "datetime64[ns]"),
            ("signal_date", "datetime64[ns]"),
            ("available_session", "datetime64[ns]"),
            ("sic", "object"),
            ("sec_current_sic", "object"),
            ("sic_is_pit", "bool"),
            ("sic_provenance", "object"),
            ("period_end_after_accepted_at", "bool"),
            ("period_end_after_available_session", "bool"),
            ("canonical_timing_eligible", "bool"),
        ):
            frame[column] = pd.Series(dtype=dtype)
        frame.attrs[_ORPHAN_ALIAS_ATTR] = orphan_resolution
        return frame

    frame["cik"] = frame["cik"].map(normalize_cik)
    frame["accession"] = frame["accession"].astype(str).str.strip()
    frame["accepted_at"] = _aware_utc_timestamps(
        frame["accepted_at"].tolist(), label="fact accepted_at"
    ).to_numpy()
    frame["tag"] = frame["concept"].astype(str).str.strip()
    frame["period_start"] = pd.to_datetime(frame["start"], errors="coerce").dt.normalize()
    frame["period_end"] = pd.to_datetime(frame["end"], errors="coerce").dt.normalize()

    metadata_columns = [
        "cik",
        "accession",
        "accepted_at",
        "signal_date",
        "available_session",
        "sic",
    ]
    for optional in (
        "primary_document",
        "filed_date",
        "sec_current_sic",
        "sic_is_pit",
        "sic_provenance",
    ):
        if optional in filing_ledger.columns:
            metadata_columns.append(optional)
    metadata = filing_ledger.loc[:, metadata_columns].copy()
    metadata["cik"] = metadata["cik"].map(normalize_cik)
    metadata["accession"] = metadata["accession"].astype(str).str.strip()
    metadata["accepted_at"] = _aware_utc_timestamps(
        metadata["accepted_at"].tolist(), label="filing accepted_at"
    ).to_numpy()
    if metadata.duplicated(["cik", "accession"]).any():
        raise SECParseError("filing ledger contains duplicate CIK/accession vintages")
    metadata = metadata.rename(columns={"accepted_at": "filing_accepted_at"})

    joined = frame.merge(
        metadata,
        on=["cik", "accession"],
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    unmatched = joined["_merge"].ne("both")
    if unmatched.any():
        sample = sorted(joined.loc[unmatched, "accession"].unique())[:5]
        raise SECParseError(f"numeric facts contain orphan accessions: {sample}")
    acceptance_mismatch = joined["accepted_at"].ne(joined["filing_accepted_at"])
    if acceptance_mismatch.any():
        sample = sorted(joined.loc[acceptance_mismatch, "accession"].unique())[:5]
        raise SECParseError(f"fact/filing acceptance mismatch for accessions: {sample}")
    joined = joined.drop(columns=["_merge", "filing_accepted_at"])
    accepted_date = (
        pd.to_datetime(joined["accepted_at"], utc=True)
        .dt.tz_convert(None)
        .dt.normalize()
    )
    available_session = pd.to_datetime(
        joined["available_session"], errors="coerce"
    ).dt.normalize()
    joined["period_end_after_accepted_at"] = joined["period_end"].gt(
        accepted_date
    )
    joined["period_end_after_available_session"] = joined["period_end"].gt(
        available_session
    )
    joined["canonical_timing_eligible"] = ~(
        joined["period_end_after_accepted_at"]
        | joined["period_end_after_available_session"]
    )
    result = joined.sort_values(
        ["accepted_at", "accession", "taxonomy", "tag", "unit", "period_end"],
        ignore_index=True,
    )
    result.attrs[_ORPHAN_ALIAS_ATTR] = orphan_resolution
    return result


def build_sec_fundamental_tables(
    submissions_root: object,
    submissions_history: Mapping[str, object] | Sequence[object],
    companyfacts_payload: object,
    metric_registry: pd.DataFrame,
    xnys_schedule: pd.DataFrame,
    *,
    availability_buffer_minutes: float = DEFAULT_AVAILABILITY_BUFFER_MINUTES,
    minimum_duration_days: int = 300,
    maximum_duration_days: int = 430,
) -> SECFundamentalPipelineResult:
    """Build filing, fact, canonical, and coverage-QA tables for one CIK."""

    root = _load_json_object(submissions_root, "submissions root")
    filings = parse_submissions(root, submissions_history)
    filings = assign_filing_signal_dates(
        filings,
        xnys_schedule,
        availability_buffer_minutes=availability_buffer_minutes,
    )
    current_sic = _normalize_optional_sic(root.get("sic"))
    # submissions.sic is current entity metadata, not a filing-vintage field.
    # Preserve it as evidence but never backfill it into historical ``sic``.
    filings["sic"] = pd.NA
    filings["sec_current_sic"] = current_sic
    filings["sic_is_pit"] = False
    filings["sic_provenance"] = "unverified_no_pit_sic"
    filings["filed_date"] = filings["filed"]

    parsed_facts = parse_companyfacts(companyfacts_payload, filings)
    facts = adapt_companyfacts_for_canonicalization(parsed_facts, filings)
    # The canonicalizer deliberately obtains all filing provenance from its
    # ledger.  Pass only fact-context columns here so identically named
    # provenance columns cannot acquire pandas ``_x``/``_y`` suffixes.
    canonical_input = facts.loc[
        facts["canonical_timing_eligible"].astype(bool),
        [
            "cik",
            "accession",
            "taxonomy",
            "tag",
            "unit",
            "value",
            "period_start",
            "period_end",
            "form",
        ],
    ]
    canonicalized = canonicalize_annual_facts(
        canonical_input,
        filings,
        metric_registry,
        minimum_duration_days=minimum_duration_days,
        maximum_duration_days=maximum_duration_days,
    )
    canonical = canonicalized.facts.copy()
    canonical["signal_date"] = canonical["available_session"]
    canonical["sec_current_sic"] = current_sic
    canonical["sic_is_pit"] = False
    canonical["sic_provenance"] = "unverified_no_pit_sic"
    canonical = canonical.sort_values(
        ["cik", "accepted_at", "period_end", "metric_id", "accession"],
        ignore_index=True,
    )
    coverage_qa = _coverage_qa(
        filings,
        facts,
        canonical,
        canonicalized.audit,
    )
    return SECFundamentalPipelineResult(
        filings=filings,
        facts=facts,
        canonical=canonical,
        coverage_qa=coverage_qa,
    )


def filter_companyfacts_to_metric_registry(
    companyfacts_payload: object,
    metric_registry: pd.DataFrame,
) -> Mapping[str, object]:
    """Return a lossless registered-tag view of an SEC Company Facts object.

    The immutable raw response remains the evidence source. This helper only
    removes taxonomy/concept branches that the frozen metric registry can
    never select, avoiding the cost of expanding hundreds of unrelated XBRL
    concepts into pandas rows before the same registry filter is applied.
    """

    source = _load_json_object(companyfacts_payload, "companyfacts")
    required = {"taxonomy", "tag_priority"}
    missing = required.difference(metric_registry.columns)
    if missing:
        raise ValueError(f"metric registry missing columns: {sorted(missing)}")
    facts = source.get("facts")
    if not isinstance(facts, Mapping):
        raise SECParseError("companyfacts missing facts object")
    allowed: dict[str, set[str]] = {}
    for row in metric_registry.itertuples(index=False):
        taxonomy = str(row.taxonomy).strip()
        tags = {item.strip() for item in str(row.tag_priority).split("|") if item.strip()}
        allowed.setdefault(taxonomy, set()).update(tags)
    selected: dict[str, dict[str, object]] = {}
    for taxonomy in sorted(allowed):
        concepts = facts.get(taxonomy)
        if concepts is None:
            continue
        if not isinstance(concepts, Mapping):
            raise SECParseError(
                f"companyfacts taxonomy {taxonomy!r} is not an object"
            )
        kept = {
            concept: concepts[concept]
            for concept in sorted(allowed[taxonomy])
            if concept in concepts
        }
        if kept:
            selected[taxonomy] = kept
    result = dict(source)
    result["facts"] = selected
    return result


def run_sec_fundamental_pipeline(
    submissions_root: object,
    submissions_history: Mapping[str, object] | Sequence[object],
    companyfacts_payload: object,
    metric_registry: pd.DataFrame,
    xnys_schedule: pd.DataFrame,
    **kwargs: Any,
) -> SECFundamentalPipelineResult:
    """Named pipeline alias for orchestration code; still pure and offline."""

    return build_sec_fundamental_tables(
        submissions_root,
        submissions_history,
        companyfacts_payload,
        metric_registry,
        xnys_schedule,
        **kwargs,
    )


def _coverage_qa(
    filings: pd.DataFrame,
    facts: pd.DataFrame,
    canonical: pd.DataFrame,
    canonical_audit: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    cik_values: set[str] = set()
    for frame in (filings, facts, canonical):
        if "cik" in frame.columns:
            cik_values.update(frame["cik"].dropna().astype(str).unique())
    if len(cik_values) > 1:
        raise SECParseError(
            f"coverage QA received multiple CIKs: {sorted(cik_values)}"
        )
    qa_cik = next(iter(cik_values), None)

    def add(
        check_id: str,
        group: str,
        numerator: int,
        denominator: int,
        *,
        required: bool,
    ) -> None:
        coverage = (
            float(numerator) / float(denominator) if denominator else float("nan")
        )
        if denominator == 0:
            status = "review"
        elif required:
            status = "pass" if numerator == denominator else "fail"
        else:
            status = "pass" if numerator == denominator else "review"
        rows.append(
            {
                "check_id": check_id,
                "group": group,
                "numerator": int(numerator),
                "denominator": int(denominator),
                "coverage": coverage,
                "status": status,
                "cik": qa_cik,
                "orphan_duplicate_resolved_count": pd.NA,
                "accessions": pd.NA,
            }
        )

    orphan_resolution = facts.attrs.get(
        _ORPHAN_ALIAS_ATTR,
        {
            "orphan_duplicate_resolved_count": 0,
            "cik": qa_cik,
            "accessions": [],
        },
    )
    orphan_count = int(
        orphan_resolution.get("orphan_duplicate_resolved_count", 0)
    )
    orphan_cik = orphan_resolution.get("cik") or qa_cik
    if qa_cik is not None and orphan_cik is not None and str(orphan_cik) != qa_cik:
        raise SECParseError("orphan alias QA CIK differs from filing CIK")
    accessions = orphan_resolution.get("accessions", [])
    if not isinstance(accessions, list):
        raise SECParseError("orphan alias QA accessions must be a list")
    original_observation_count = len(facts) + orphan_count
    rows.append(
        {
            "check_id": "companyfacts_orphan_duplicate_resolved_count",
            "group": "facts",
            "numerator": orphan_count,
            "denominator": original_observation_count,
            "coverage": (
                float(orphan_count) / original_observation_count
                if original_observation_count
                else float("nan")
            ),
            "status": "review" if orphan_count else "pass",
            "cik": orphan_cik,
            "orphan_duplicate_resolved_count": orphan_count,
            "accessions": json.dumps(
                accessions,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
    )

    add(
        "filing_signal_date_coverage",
        "filings",
        int(filings["signal_date"].notna().sum()),
        len(filings),
        required=True,
    )
    add(
        "filing_sic_coverage",
        "filings",
        int(filings["sic"].notna().sum()),
        len(filings),
        required=False,
    )
    if "sec_current_sic" in filings.columns:
        add(
            "filing_current_non_pit_sic_coverage",
            "filings",
            int(filings["sec_current_sic"].notna().sum()),
            len(filings),
            required=False,
        )
    fact_accessions = set(facts["accession"].astype(str))
    filing_accessions = set(filings["accession"].astype(str))
    joined_facts = int(facts["accession"].astype(str).isin(filing_accessions).sum())
    add(
        "fact_filing_vintage_coverage",
        "facts",
        joined_facts,
        len(facts),
        required=True,
    )
    add(
        "fact_signal_date_coverage",
        "facts",
        int(facts["signal_date"].notna().sum()),
        len(facts),
        required=True,
    )
    if "filed_date_mismatch" in facts.columns:
        mismatch_count = int(facts["filed_date_mismatch"].astype(bool).sum())
        rows.append(
            {
                "check_id": "companyfacts_submissions_filed_date_mismatches",
                "group": "facts",
                "numerator": mismatch_count,
                "denominator": len(facts),
                "coverage": (
                    float(mismatch_count) / len(facts)
                    if len(facts)
                    else float("nan")
                ),
                "status": "pass" if mismatch_count == 0 else "review",
                "cik": qa_cik,
                "orphan_duplicate_resolved_count": pd.NA,
                "accessions": pd.NA,
            }
        )
    if "form_mismatch" in facts.columns:
        form_mismatch_count = int(facts["form_mismatch"].astype(bool).sum())
        rows.append(
            {
                "check_id": "companyfacts_submissions_form_mismatches",
                "group": "facts",
                "numerator": form_mismatch_count,
                "denominator": len(facts),
                "coverage": (
                    float(form_mismatch_count) / len(facts)
                    if len(facts)
                    else float("nan")
                ),
                "status": "pass" if form_mismatch_count == 0 else "review",
                "cik": qa_cik,
                "orphan_duplicate_resolved_count": pd.NA,
                "accessions": pd.NA,
            }
        )
    for column, check_id in (
        (
            "period_end_after_accepted_at",
            "companyfacts_period_end_after_acceptance",
        ),
        (
            "period_end_after_available_session",
            "companyfacts_period_end_after_available_session",
        ),
    ):
        if column not in facts.columns:
            continue
        violation_count = int(facts[column].astype(bool).sum())
        rows.append(
            {
                "check_id": check_id,
                "group": "facts",
                "numerator": violation_count,
                "denominator": len(facts),
                "coverage": (
                    float(violation_count) / len(facts)
                    if len(facts)
                    else float("nan")
                ),
                "status": "pass" if violation_count == 0 else "review",
                "cik": qa_cik,
                "orphan_duplicate_resolved_count": pd.NA,
                "accessions": pd.NA,
            }
        )
    if "canonical_timing_eligible" in facts.columns:
        excluded_count = int((~facts["canonical_timing_eligible"].astype(bool)).sum())
        rows.append(
            {
                "check_id": "canonical_timing_gate_excluded_rows",
                "group": "canonicalize",
                "numerator": excluded_count,
                "denominator": len(facts),
                "coverage": (
                    float(excluded_count) / len(facts)
                    if len(facts)
                    else float("nan")
                ),
                "status": "pass" if excluded_count == 0 else "review",
                "cik": qa_cik,
                "orphan_duplicate_resolved_count": pd.NA,
                "accessions": pd.NA,
            }
        )
    annual_accessions = set(
        filings.loc[filings["form"].astype(str).str.upper().isin({"10-K", "10-K/A"}), "accession"]
        .astype(str)
        .tolist()
    )
    add(
        "annual_filing_fact_accession_coverage",
        "facts",
        len(annual_accessions.intersection(fact_accessions)),
        len(annual_accessions),
        required=False,
    )
    canonical_accessions = set(canonical["accession"].astype(str))
    add(
        "annual_fact_canonical_accession_coverage",
        "canonical",
        len(canonical_accessions.intersection(fact_accessions)),
        len(annual_accessions.intersection(fact_accessions)),
        required=False,
    )

    for audit in canonical_audit.itertuples(index=False):
        count = int(audit.count)
        rows.append(
            {
                "check_id": f"canonicalize:{audit.check_id}",
                "group": "canonicalize",
                "numerator": count,
                "denominator": 0,
                "coverage": float("nan"),
                "status": str(audit.status),
                "cik": qa_cik,
                "orphan_duplicate_resolved_count": pd.NA,
                "accessions": pd.NA,
            }
        )
    return pd.DataFrame(
        rows,
        columns=(
            "check_id",
            "group",
            "numerator",
            "denominator",
            "coverage",
            "status",
            "cik",
            "orphan_duplicate_resolved_count",
            "accessions",
        ),
    )


def _load_json_object(payload: object, label: str) -> Mapping[str, object]:
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = json.loads(bytes(payload).decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SECParseError(f"{label} is invalid JSON") from None
    elif isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            raise SECParseError(f"{label} is invalid JSON") from None
    if not isinstance(payload, Mapping):
        raise SECParseError(f"{label} must be a JSON object")
    return payload


def _normalize_optional_sic(value: object) -> object:
    if value is None:
        return pd.NA
    text = str(value).strip()
    if not text:
        return pd.NA
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if not text.isdigit() or not 1 <= len(text) <= 4:
        raise SECParseError(f"invalid SEC SIC: {value!r}")
    return text.zfill(4)


def _naive_session_dates(values: Sequence[object]) -> pd.Series:
    dates: list[pd.Timestamp] = []
    for value in values:
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError):
            raise SECParseError(f"invalid XNYS session date: {value!r}") from None
        if pd.isna(timestamp):
            raise SECParseError("XNYS session dates cannot contain NaT")
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("UTC").tz_localize(None)
        if timestamp != timestamp.normalize():
            raise SECParseError("XNYS session labels must be normalized dates")
        dates.append(timestamp)
    return pd.Series(dates, dtype="datetime64[ns]")


def _aware_utc_timestamps(values: Sequence[object], *, label: str) -> pd.Series:
    timestamps: list[pd.Timestamp] = []
    for value in values:
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError):
            raise SECParseError(f"invalid {label}: {value!r}") from None
        if pd.isna(timestamp) or timestamp.tzinfo is None:
            raise SECParseError(f"{label} values must be valid timezone-aware timestamps")
        timestamps.append(timestamp.tz_convert("UTC"))
    return pd.Series(timestamps, dtype="datetime64[ns, UTC]")


def _validate_buffer_minutes(value: float) -> float:
    try:
        minutes = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("availability_buffer_minutes must be a finite number") from None
    if not math.isfinite(minutes) or minutes < 0:
        raise ValueError("availability_buffer_minutes must be finite and non-negative")
    return minutes


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{label} must be a pandas DataFrame")
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise SECParseError(f"{label} missing columns: {missing}")
