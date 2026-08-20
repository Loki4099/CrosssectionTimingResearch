"""Point-in-time security-to-SEC issuer bridge helpers.

The frozen market security master is intentionally left untouched.  This
module builds a separate, versioned bridge from the project's stable ``sid``
to an issuer CIK.  Network access belongs in the pipeline layer; every
function here is deterministic and works on already captured evidence.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher
import math
import re

import pandas as pd


_TICKER_PREFIX = re.compile(r"^[^:]+::")
_NAME_NOISE = re.compile(
    r"\b(?:incorporated|inc|corporation|corp|company|co|limited|ltd|plc|llc|lp|"
    r"holdings?|group|the|de|nv|sa|ag|na)\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_JURISDICTION_SUFFIX = re.compile(r"/[A-Za-z]{2,4}/?")


def normalize_ticker(value: object) -> str:
    """Return the audit ticker form used for matching.

    Dots, dashes and underscores are preserved because they can distinguish
    share classes.  Call :func:`ticker_variants` when provider conventions
    need to be compared.
    """

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = str(value).strip().upper()
    text = _TICKER_PREFIX.sub("", text)
    return text.replace("/", "-").replace(" ", "")


def ticker_variants(value: object) -> tuple[str, ...]:
    """Return deterministic provider variants for a ticker."""

    ticker = normalize_ticker(value)
    if not ticker:
        return ()
    variants = {ticker}
    if any(char in ticker for char in (".", "-", "_")):
        for char in (".", "-", "_"):
            variants.add(re.sub(r"[._-]", char, ticker))
    return tuple(sorted(variants))


def normalize_company_name(value: object) -> str:
    """Normalize a legal name for candidate scoring, not identity creation."""

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = _JURISDICTION_SUFFIX.sub(" ", str(value)).lower().replace("&", " and ")
    text = _NAME_NOISE.sub(" ", text)
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def company_name_score(left: object, right: object) -> float:
    """Score two names using token overlap and character similarity."""

    a, b = normalize_company_name(left), normalize_company_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    at, bt = set(a.split()), set(b.split())
    token_score = len(at & bt) / len(at | bt) if at | bt else 0.0
    sequence_score = SequenceMatcher(None, a, b).ratio()
    return float(0.6 * sequence_score + 0.4 * token_score)


def parse_sec_cik_lookup(payload: bytes | str) -> pd.DataFrame:
    """Parse the official SEC ``cik-lookup-data.txt`` name index."""

    text = payload.decode("latin-1") if isinstance(payload, bytes) else str(payload)
    rows: list[dict[str, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.rsplit(":", 2)
        if len(parts) != 3 or parts[2] != "" or not parts[1].isdigit():
            raise ValueError(f"invalid SEC CIK lookup row {line_number}")
        name = parts[0].strip()
        cik = parts[1].zfill(10)
        # The official index currently contains a handful of blank-name CIK
        # rows.  They carry no usable identity evidence and are retained in
        # the raw snapshot but intentionally excluded from the parsed index.
        if not name:
            continue
        normalized = normalize_company_name(name)
        if not normalized:
            continue
        if len(cik) != 10:
            raise ValueError(f"invalid SEC CIK lookup row {line_number}")
        rows.append(
            {"sec_name": name, "normalized_name": normalized, "cik10": cik}
        )
    frame = pd.DataFrame(
        rows, columns=["sec_name", "normalized_name", "cik10"]
    ).drop_duplicates(ignore_index=True)
    return frame.sort_values(
        ["normalized_name", "cik10", "sec_name"], ignore_index=True
    )


def build_sec_name_candidates(
    issuer_names: pd.DataFrame,
    cik_lookup: pd.DataFrame,
    *,
    minimum_score: float = 0.82,
) -> pd.DataFrame:
    """Create bounded, auditable CIK candidates from provider issuer names."""

    required_names = {"sid", "issuer_name", "source"}
    required_lookup = {"sec_name", "normalized_name", "cik10"}
    if not required_names.issubset(issuer_names.columns):
        raise ValueError("issuer_names is missing required columns")
    if not required_lookup.issubset(cik_lookup.columns):
        raise ValueError("cik_lookup is missing required columns")
    lookup = cik_lookup.copy()
    lookup["normalized_name"] = lookup["normalized_name"].astype(str)
    lookup["first_token"] = lookup["normalized_name"].str.split().str[0]
    rows: list[dict[str, object]] = []
    for item in issuer_names.sort_values(["sid", "issuer_name"]).itertuples():
        normalized = normalize_company_name(item.issuer_name)
        if not normalized:
            continue
        exact = lookup.loc[lookup["normalized_name"].eq(normalized)]
        pool = exact
        exact_match = not exact.empty
        if pool.empty:
            first = normalized.split()[0]
            pool = lookup.loc[lookup["first_token"].eq(first)]
        for candidate in pool.itertuples():
            score = (
                1.0
                if exact_match
                else company_name_score(normalized, candidate.normalized_name)
            )
            if score < minimum_score:
                continue
            rows.append(
                {
                    "sid": str(item.sid),
                    "cik10": str(candidate.cik10).zfill(10),
                    "score": float(score),
                    "matched_name": str(candidate.sec_name),
                    "source": str(item.source),
                    "issuer_name": str(item.issuer_name),
                }
            )
    result = pd.DataFrame(
        rows,
        columns=[
            "sid",
            "cik10",
            "score",
            "matched_name",
            "source",
            "issuer_name",
        ],
    )
    if result.empty:
        return result
    return result.drop_duplicates().sort_values(
        ["sid", "score", "cik10"],
        ascending=[True, False, True],
        ignore_index=True,
    )


def build_security_alias_table(
    security_master: pd.DataFrame,
    provider_lineage: pd.DataFrame,
    membership: pd.DataFrame,
) -> pd.DataFrame:
    """Build one deterministic alias record per frozen market SID."""

    required_master = {"sid", "ticker"}
    required_lineage = {"canonical_sid", "source_sid", "identity_status"}
    required_membership = {"sid", "effective_from", "effective_to"}
    if not required_master.issubset(security_master.columns):
        raise ValueError("security_master is missing required columns")
    if not required_lineage.issubset(provider_lineage.columns):
        raise ValueError("provider_lineage is missing required columns")
    if not required_membership.issubset(membership.columns):
        raise ValueError("membership is missing required columns")

    master = security_master.loc[:, ["sid", "ticker"]].copy()
    if master["sid"].duplicated().any():
        raise ValueError("security_master sid must be unique")

    lineage = provider_lineage.loc[
        :, ["canonical_sid", "source_sid", "identity_status"]
    ].rename(columns={"canonical_sid": "sid"})
    if lineage["sid"].duplicated().any():
        raise ValueError("provider_lineage canonical_sid must be unique")

    spans = membership.copy()
    spans["effective_from"] = pd.to_datetime(spans["effective_from"])
    spans["effective_to"] = pd.to_datetime(spans["effective_to"])
    grouped = spans.groupby("sid", sort=True).agg(
        membership_from=("effective_from", "min"),
        membership_to=(
            "effective_to",
            lambda values: pd.NaT if values.isna().any() else values.max(),
        ),
        membership_intervals=("sid", "size"),
    )

    table = master.merge(lineage, on="sid", how="left", validate="one_to_one")
    table = table.merge(grouped, on="sid", how="left", validate="one_to_one")

    def aliases(row: pd.Series) -> str:
        values: set[str] = set(ticker_variants(row["ticker"]))
        source = row.get("source_sid")
        if pd.notna(source):
            for item in str(source).split("|"):
                values.update(ticker_variants(item))
        return "|".join(sorted(value for value in values if value))

    table["canonical_ticker"] = table["ticker"].map(normalize_ticker)
    table["ticker_aliases"] = table.apply(aliases, axis=1)
    result = table.loc[
        :,
        [
            "sid",
            "canonical_ticker",
            "ticker_aliases",
            "membership_from",
            "membership_to",
            "membership_intervals",
            "identity_status",
        ],
    ].sort_values("sid", ignore_index=True)
    if result["ticker_aliases"].eq("").any():
        raise ValueError("every sid must have at least one ticker alias")
    return result


def resolve_entity_bridge(
    aliases: pd.DataFrame,
    ticker_candidates: pd.DataFrame,
    *,
    name_candidates: pd.DataFrame | None = None,
    overrides: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Resolve CIK evidence without silently choosing conflicts.

    ``ticker_candidates`` may contain multiple sources and multiple CIKs for a
    ticker.  A SID is accepted mechanically only when all of its ticker
    evidence points to one CIK.  A name fallback needs a score of at least
    0.92 and a 0.03 lead over the runner-up.  Everything else remains an
    explicit unresolved row.
    """

    required_alias = {
        "sid",
        "canonical_ticker",
        "ticker_aliases",
        "membership_from",
        "membership_to",
    }
    required_candidate = {"ticker", "cik10", "source"}
    if not required_alias.issubset(aliases.columns):
        raise ValueError("aliases is missing required columns")
    if not required_candidate.issubset(ticker_candidates.columns):
        raise ValueError("ticker_candidates is missing required columns")

    candidates = ticker_candidates.copy()
    candidates["ticker"] = candidates["ticker"].map(normalize_ticker)
    candidates["cik10"] = candidates["cik10"].astype(str).str.zfill(10)
    candidates = candidates.loc[candidates["ticker"].ne("")]

    name_frame = pd.DataFrame(
        columns=["sid", "cik10", "score", "matched_name", "source"]
    )
    if name_candidates is not None and not name_candidates.empty:
        required_name = {"sid", "cik10", "score", "source"}
        if not required_name.issubset(name_candidates.columns):
            raise ValueError("name_candidates is missing required columns")
        name_frame = name_candidates.copy()
        name_frame["cik10"] = name_frame["cik10"].astype(str).str.zfill(10)

    override_frame = pd.DataFrame(columns=["sid", "cik10"])
    if overrides is not None and not overrides.empty:
        required_override = {"sid", "cik10"}
        if not required_override.issubset(overrides.columns):
            raise ValueError("overrides is missing required columns")
        override_frame = overrides.copy()
        override_frame["cik10"] = override_frame["cik10"].astype(str).str.zfill(10)

    rows: list[dict[str, object]] = []
    for record in aliases.sort_values("sid").to_dict("records"):
        sid = str(record["sid"])
        override = override_frame.loc[override_frame["sid"].eq(sid)]
        if not override.empty:
            validated = _validated_override_intervals(override, sid=sid)
            # The one-row bridge remains a convenient issuer summary. For a
            # legal successor chain it carries the latest reviewed issuer;
            # the full PIT history is emitted by build_entity_cik_intervals.
            chosen = validated.sort_values(
                ["effective_from", "cik10"],
                ascending=[True, True],
                kind="stable",
            ).iloc[-1]
            rows.append(
                _bridge_row(
                    record,
                    cik10=chosen["cik10"],
                    status=(
                        "verified_interval_override"
                        if len(validated) > 1
                        else str(
                            chosen.get("review_status", "verified_override")
                        )
                    ),
                    basis=str(
                        chosen.get("mapping_basis", "manual_override")
                    ),
                    evidence=str(chosen.get("evidence_url", "")),
                    candidate_count=len(validated),
                )
            )
            continue

        ticker_set = set(str(record["ticker_aliases"]).split("|"))
        matched = candidates.loc[candidates["ticker"].isin(ticker_set)]
        ciks = sorted(set(matched["cik10"]))
        if len(ciks) == 1:
            sources = "|".join(sorted(set(matched["source"].astype(str))))
            evidence = "|".join(
                sorted(set(matched.get("evidence_url", pd.Series(dtype=str)).dropna().astype(str)))
            )
            rows.append(
                _bridge_row(
                    record,
                    cik10=ciks[0],
                    status="verified_ticker_consensus",
                    basis=sources,
                    evidence=evidence,
                    candidate_count=1,
                )
            )
            continue
        if len(ciks) > 1:
            rows.append(
                _bridge_row(
                    record,
                    cik10=pd.NA,
                    status="review_ticker_conflict",
                    basis="ticker_conflict",
                    evidence="|".join(ciks),
                    candidate_count=len(ciks),
                )
            )
            continue

        names = name_frame.loc[name_frame["sid"].eq(sid)].sort_values(
            ["score", "cik10"], ascending=[False, True]
        )
        if not names.empty:
            top = names.iloc[0]
            runner_up = float(names.iloc[1]["score"]) if len(names) > 1 else 0.0
            if float(top["score"]) >= 0.92 and float(top["score"]) - runner_up >= 0.03:
                rows.append(
                    _bridge_row(
                        record,
                        cik10=top["cik10"],
                        status="verified_name_match",
                        basis=str(top["source"]),
                        evidence=str(top.get("matched_name", "")),
                        candidate_count=len(names),
                    )
                )
                continue

        rows.append(
            _bridge_row(
                record,
                cik10=pd.NA,
                status="unresolved",
                basis="no_unique_candidate",
                evidence="",
                candidate_count=len(names),
            )
        )

    result = pd.DataFrame(rows).sort_values("sid", ignore_index=True)
    if result["sid"].duplicated().any() or len(result) != len(aliases):
        raise AssertionError("bridge must have exactly one row per sid")
    return result


def build_entity_cik_intervals(
    bridge: pd.DataFrame,
    overrides: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Expand the one-row SID summary into a point-in-time SID-to-CIK map.

    Reviewed overrides replace the mechanical mapping for their SID. Multiple
    non-overlapping overrides are expected for legal reorganisations in which
    the stable market-data SID spans more than one SEC registrant.
    """

    required = {"sid", "cik10", "effective_from", "effective_to"}
    missing = required.difference(bridge.columns)
    if missing:
        raise ValueError(f"bridge missing interval columns: {sorted(missing)}")
    if bridge["sid"].duplicated().any():
        raise ValueError("summary bridge must contain one row per sid")
    override_frame = (
        pd.DataFrame(columns=["sid", "cik10"])
        if overrides is None
        else overrides.copy()
    )
    rows: list[dict[str, object]] = []
    for base in bridge.sort_values("sid").to_dict("records"):
        sid = str(base["sid"])
        selected = override_frame.loc[override_frame["sid"].eq(sid)]
        if selected.empty:
            if pd.isna(base["cik10"]):
                continue
            rows.append(
                {
                    "sid": sid,
                    "cik10": str(base["cik10"]).zfill(10),
                    "effective_from": pd.Timestamp(base["effective_from"]).normalize(),
                    "effective_to": (
                        pd.NaT
                        if pd.isna(base["effective_to"])
                        else pd.Timestamp(base["effective_to"]).normalize()
                    ),
                    "issuer_name": "",
                    "mapping_basis": str(base.get("mapping_basis", "")),
                    "evidence_url": str(base.get("evidence", "")),
                    "review_status": str(base.get("review_status", "")),
                    "notes": "mechanical summary mapping",
                }
            )
            continue
        validated = _validated_override_intervals(selected, sid=sid)
        membership_from = pd.Timestamp(base["effective_from"]).normalize()
        membership_to = (
            pd.NaT
            if pd.isna(base["effective_to"])
            else pd.Timestamp(base["effective_to"]).normalize()
        )
        for item in validated.to_dict("records"):
            left = max(membership_from, pd.Timestamp(item["effective_from"]))
            right = item["effective_to"]
            if pd.notna(membership_to):
                right = membership_to if pd.isna(right) else min(right, membership_to)
            if pd.notna(right) and left >= right:
                continue
            rows.append(
                {
                    "sid": sid,
                    "cik10": str(item["cik10"]).zfill(10),
                    "effective_from": left,
                    "effective_to": right,
                    "issuer_name": str(item.get("issuer_name", "")),
                    "mapping_basis": str(
                        item.get("mapping_basis", "manual_interval_override")
                    ),
                    "evidence_url": str(item.get("evidence_url", "")),
                    "review_status": str(
                        item.get("review_status", "verified_override")
                    ),
                    "notes": str(item.get("notes", "")),
                }
            )
    result = pd.DataFrame(
        rows,
        columns=[
            "sid",
            "cik10",
            "effective_from",
            "effective_to",
            "issuer_name",
            "mapping_basis",
            "evidence_url",
            "review_status",
            "notes",
        ],
    ).sort_values(["sid", "effective_from", "cik10"], ignore_index=True)
    for sid, group in result.groupby("sid", sort=False):
        prior_end: pd.Timestamp | None = None
        prior_was_open = False
        records = list(group.itertuples(index=False))
        for position, item in enumerate(records):
            if prior_was_open:
                raise ValueError(f"open entity CIK interval is not last for {sid}")
            if prior_end is not None and pd.Timestamp(item.effective_from) < prior_end:
                raise ValueError(f"entity CIK intervals overlap for {sid}")
            prior_end = (
                None
                if pd.isna(item.effective_to)
                else pd.Timestamp(item.effective_to)
            )
            prior_was_open = prior_end is None
            if prior_was_open and position != len(records) - 1:
                raise ValueError(f"open entity CIK interval is not last for {sid}")
    return result


def _validated_override_intervals(
    overrides: pd.DataFrame, *, sid: str
) -> pd.DataFrame:
    required = {"sid", "cik10"}
    if not required.issubset(overrides.columns):
        raise ValueError("overrides is missing required columns")
    frame = overrides.copy()
    frame["cik10"] = frame["cik10"].astype(str).str.strip().str.zfill(10)
    if frame["cik10"].str.fullmatch(r"\d{10}").ne(True).any():
        raise ValueError(f"override CIK is invalid for {sid}")
    if "effective_from" not in frame:
        frame["effective_from"] = pd.Timestamp("1900-01-01")
    else:
        blank = frame["effective_from"].isna() | frame["effective_from"].astype(str).str.strip().eq("")
        frame.loc[blank, "effective_from"] = "1900-01-01"
        frame["effective_from"] = pd.to_datetime(
            frame["effective_from"], errors="raise"
        ).dt.normalize()
    if "effective_to" not in frame:
        frame["effective_to"] = pd.NaT
    else:
        frame["effective_to"] = pd.to_datetime(
            frame["effective_to"].replace("", pd.NA), errors="coerce"
        ).dt.normalize()
    frame = frame.sort_values(
        ["effective_from", "effective_to", "cik10"],
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)
    previous_end: pd.Timestamp | None = None
    for position, item in enumerate(frame.itertuples(index=False)):
        start = pd.Timestamp(item.effective_from)
        end = None if pd.isna(item.effective_to) else pd.Timestamp(item.effective_to)
        if end is not None and start >= end:
            raise ValueError(f"override interval is empty or reversed for {sid}")
        if previous_end is not None and start < previous_end:
            raise ValueError(f"override intervals overlap for {sid}")
        if previous_end is None and position > 0:
            raise ValueError(f"open override interval is not last for {sid}")
        previous_end = end
    return frame


def member_session_mapping_coverage(
    bridge: pd.DataFrame,
    membership: pd.DataFrame,
    sessions: Iterable[pd.Timestamp],
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, float | int]:
    """Measure mapping coverage using PIT member sessions, not SID count."""

    calendar = pd.DatetimeIndex(pd.to_datetime(list(sessions))).normalize()
    start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    total = 0
    covered = 0
    for row in membership.itertuples(index=False):
        left = max(start, pd.Timestamp(row.effective_from).normalize())
        active = (calendar >= left) & (calendar <= end)
        if pd.notna(row.effective_to):
            active &= calendar < pd.Timestamp(row.effective_to).normalize()
        count = int(active.sum())
        total += count
        mapping = bridge.loc[
            bridge["sid"].astype(str).eq(str(row.sid)) & bridge["cik10"].notna()
        ]
        mapped_active = pd.Series(False, index=range(len(calendar)), dtype=bool)
        for interval in mapping.itertuples(index=False):
            interval_active = calendar >= pd.Timestamp(
                interval.effective_from
            ).normalize()
            if pd.notna(interval.effective_to):
                interval_active &= calendar < pd.Timestamp(
                    interval.effective_to
                ).normalize()
            mapped_active |= interval_active
        covered += int((active & mapped_active.to_numpy()).sum())
    ratio = covered / total if total else float("nan")
    return {
        "member_sessions": total,
        "mapped_member_sessions": covered,
        "unmapped_member_sessions": total - covered,
        "coverage": ratio,
    }


def _bridge_row(
    record: Mapping[str, object],
    *,
    cik10: object,
    status: str,
    basis: str,
    evidence: str,
    candidate_count: int,
) -> dict[str, object]:
    return {
        "sid": record["sid"],
        "issuer_id": f"sec-cik::{cik10}" if pd.notna(cik10) else pd.NA,
        "cik10": cik10,
        "effective_from": record["membership_from"],
        "effective_to": record["membership_to"],
        "canonical_ticker": record["canonical_ticker"],
        "ticker_aliases": record["ticker_aliases"],
        "mapping_basis": basis,
        "evidence": evidence,
        "review_status": status,
        "candidate_count": int(candidate_count),
    }
