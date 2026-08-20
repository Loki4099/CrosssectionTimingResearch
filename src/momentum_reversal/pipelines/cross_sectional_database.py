"""Build the versioned cross-sectional market and SEC research database.

This module is deliberately staged.  The frozen market dataset is only read;
the SEC issuer bridge and every subsequent table are written under new,
versioned runtime directories.  Network responses are delegated to the
content-addressed :mod:`momentum_reversal.data.sec_edgar` store so an
interrupted build can resume without overwriting evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import tomllib
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote, urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import pandas as pd

from momentum_reversal.data.entity_bridge import (
    build_entity_cik_intervals,
    build_sec_name_candidates,
    build_security_alias_table,
    member_session_mapping_coverage,
    parse_sec_cik_lookup,
    resolve_entity_bridge,
)
from momentum_reversal.data.entity_temporal_audit import (
    build_entity_temporal_support_qa,
)
from momentum_reversal.data.sec_edgar import (
    FetchedResponse,
    ImmutableFetchStore,
    SECClient,
    SECCooldownError,
    SECParseError,
    SECResponse,
    normalize_cik,
    parse_browse_edgar_atom_single_cik,
    parse_company_tickers,
    parse_submissions,
    submission_history_file_names,
)
from momentum_reversal.data.fundamental_store import load_sec_metric_registry
from momentum_reversal.data.factor_database import (
    build_factor_coverage_qa,
    build_factor_database,
    write_factor_database_bundle,
)
from momentum_reversal.data.research_catalog import rebuild_research_catalog
from momentum_reversal.data.sec_fundamental_pipeline import (
    build_sec_fundamental_tables,
    filter_companyfacts_to_metric_registry,
)
from momentum_reversal.data.tiingo_provider import resolve_tiingo_api_token
from momentum_reversal.factors.cross_sectional_market import (
    FACTOR_IDS as MARKET_FACTOR_IDS,
    materialize_cross_sectional_market_factors,
)
from momentum_reversal.factors.cross_sectional_fundamental import (
    compute_fundamental_factor_panel,
)


PROGRAM_RELATIVE_PATH = Path(
    "config/research/cross_sectional_alpha/data_program.toml"
)
FUNDAMENTAL_BUILD_LOGIC_VERSION = (
    "research_interval_form_timing_identity_canonicalization_companyfacts_na_v3"
)
_REVIEWED_COMPANYFACTS_NOT_APPLICABLE = {
    "0001132979": "FRC",
    "0001288784": "SBNY",
}
_PERIODIC_FORM_BASES = ("10-K", "10-Q", "20-F", "40-F")
_PERIODIC_FORMS = frozenset(
    form
    for base in _PERIODIC_FORM_BASES
    for form in (base, f"{base}/A")
)
_VERSION_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class CrossSectionalDatabaseError(RuntimeError):
    """Raised when a database stage cannot satisfy its frozen contract."""


class IdentifierCoverageError(CrossSectionalDatabaseError):
    """Raised after evidence is saved when the SID-to-CIK gate is not met."""


@dataclass(frozen=True)
class DatabaseLayout:
    project_root: Path
    runtime_root: Path
    program_path: Path
    market_root: Path
    raw_root: Path
    curated_root: Path
    derived_root: Path
    catalog_path: Path
    program: Mapping[str, Any]

    @classmethod
    def load(
        cls,
        *,
        project_root: str | Path,
        runtime_root: str | Path,
        program_path: str | Path | None = None,
    ) -> "DatabaseLayout":
        project = Path(project_root).expanduser().resolve()
        runtime = Path(runtime_root).expanduser().resolve()
        selected = (
            Path(program_path).expanduser().resolve()
            if program_path is not None
            else project / PROGRAM_RELATIVE_PATH
        )
        if not selected.is_file():
            raise FileNotFoundError(selected)
        with selected.open("rb") as handle:
            program = tomllib.load(handle)
        versions = program["versions"]
        storage = program["storage"]
        normalized_versions = {
            str(key): _validated_version_token(value, label=f"versions.{key}")
            for key, value in versions.items()
        }
        market_root = _runtime_descendant(
            runtime,
            Path("data") / "curated" / normalized_versions["market_dataset"],
            label="market dataset root",
        )
        raw_root = _runtime_descendant(
            runtime,
            Path(str(storage["raw_relative_path"]))
            / normalized_versions["fundamentals"],
            label="raw data root",
        )
        curated_root = _runtime_descendant(
            runtime,
            Path(str(storage["curated_relative_path"]))
            / normalized_versions["fundamentals"],
            label="curated data root",
        )
        derived_root = _runtime_descendant(
            runtime,
            Path(str(storage["derived_relative_path"]))
            / normalized_versions["factor_build"],
            label="derived data root",
        )
        catalog_path = _runtime_descendant(
            runtime,
            Path(str(storage["catalog_relative_path"])),
            label="research catalog",
        )
        if not market_root.is_dir():
            raise FileNotFoundError(market_root)
        return cls(
            project_root=project,
            runtime_root=runtime,
            program_path=selected,
            market_root=market_root,
            raw_root=raw_root,
            curated_root=curated_root,
            derived_root=derived_root,
            catalog_path=catalog_path,
            program=program,
        )

    @property
    def identifier_root(self) -> Path:
        return self.raw_root / "identifiers"

    @property
    def sec_store_root(self) -> Path:
        return self.raw_root / "sec"

    @property
    def tiingo_identifier_store_root(self) -> Path:
        return self.raw_root / "tiingo_identifiers"

    @property
    def factor_bundle_root(self) -> Path:
        return self.derived_root / "factor_database"


def _validated_version_token(value: object, *, label: str) -> str:
    token = str(value).strip()
    if not _VERSION_TOKEN_PATTERN.fullmatch(token):
        raise CrossSectionalDatabaseError(
            f"{label} must be one safe path token, got {value!r}"
        )
    return token


def _runtime_descendant(runtime_root: Path, relative: Path, *, label: str) -> Path:
    """Resolve one configured runtime path without permitting root escape."""

    if relative.is_absolute() or relative.drive:
        raise CrossSectionalDatabaseError(f"{label} must be runtime-relative")
    resolved = (runtime_root / relative).resolve()
    try:
        resolved.relative_to(runtime_root.resolve())
    except ValueError as exc:
        raise CrossSectionalDatabaseError(
            f"{label} escapes the configured runtime root"
        ) from exc
    if resolved == runtime_root.resolve():
        raise CrossSectionalDatabaseError(
            f"{label} cannot be the runtime root itself"
        )
    return resolved


def build_identifier_stage(
    layout: DatabaseLayout,
    *,
    sec_user_agent: str,
    tiingo_api_token: str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Resolve the frozen market SIDs to SEC CIKs and persist all evidence.

    The first pass uses current SEC ticker data and the official Browse EDGAR
    ticker endpoint for historical aliases not present in the current table.
    Conflicting evidence remains unresolved.  Optional reviewed overrides are
    the only way to force a mapping.
    """

    layout.identifier_root.mkdir(parents=True, exist_ok=True)
    store = ImmutableFetchStore(layout.sec_store_root)
    sec_config = layout.program["sec"]
    client = SECClient(
        user_agent=sec_user_agent,
        raw_store=store,
        rate_limit_per_second=float(sec_config["max_requests_per_second"]),
        timeout=float(sec_config["timeout_seconds"]),
    )

    security_master = pd.read_parquet(layout.market_root / "security_master.parquet")
    provider_lineage = pd.read_parquet(
        layout.market_root / "provider_lineage.parquet"
    )
    membership = pd.read_parquet(layout.market_root / "membership.parquet")
    calendar = pd.read_parquet(layout.market_root / "calendar.parquet")
    aliases = build_security_alias_table(
        security_master, provider_lineage, membership
    )

    tickers_payload, ticker_record = _cached_sec_json(
        client,
        store,
        str(sec_config["company_tickers_url"]),
        refresh=refresh,
    )
    current = parse_company_tickers(tickers_payload).rename(
        columns={"cik": "cik10"}
    )
    current["source"] = "sec_current_company_tickers"
    current["evidence_url"] = str(sec_config["company_tickers_url"])

    alias_values = sorted(
        {
            ticker
            for values in aliases["ticker_aliases"].astype(str)
            for ticker in values.split("|")
            if ticker
        }
    )
    current_tickers = set(current["ticker"].astype(str))
    browse_rows: list[dict[str, str]] = []
    failure_rows: list[dict[str, str]] = []
    browse_base = str(sec_config["browse_company_url"])
    for ticker in alias_values:
        if ticker in current_tickers:
            continue
        url = _browse_ticker_url(browse_base, ticker)
        try:
            payload, record = _cached_sec_text(
                client, store, url, refresh=refresh
            )
            cik10 = parse_browse_edgar_atom_single_cik(payload)
        except (CrossSectionalDatabaseError, SECParseError) as exc:
            failure_rows.append(
                {
                    "ticker": ticker,
                    "requested_url": url,
                    "failure_type": type(exc).__name__,
                    "failure_message": str(exc),
                }
            )
            continue
        browse_rows.append(
            {
                "ticker": ticker,
                "cik10": cik10,
                "name": "",
                "source": "sec_browse_ticker",
                "evidence_url": url,
                "raw_sha256": record.sha256,
            }
        )

    browse = pd.DataFrame(
        browse_rows,
        columns=[
            "ticker",
            "cik10",
            "name",
            "source",
            "evidence_url",
            "raw_sha256",
        ],
    )
    current["raw_sha256"] = ticker_record.sha256
    candidates = pd.concat(
        [
            current.loc[
                :,
                [
                    "ticker",
                    "cik10",
                    "name",
                    "source",
                    "evidence_url",
                    "raw_sha256",
                ],
            ],
            browse,
        ],
        ignore_index=True,
    ).drop_duplicates()
    overrides = _load_overrides(
        layout.project_root
        / str(layout.program["identifier_resolution"]["manual_overrides_path"])
    )
    bridge = resolve_entity_bridge(
        aliases,
        candidates,
        overrides=overrides,
    )
    bridge_intervals = build_entity_cik_intervals(bridge, overrides)

    sessions = pd.DatetimeIndex(pd.to_datetime(calendar["session_date"]))
    sample = layout.program["sample"]
    minimum = float(
        layout.program["identifier_resolution"][
            "minimum_member_session_coverage"
        ]
    )
    coverage = member_session_mapping_coverage(
        bridge_intervals,
        membership,
        sessions,
        start=pd.Timestamp(sample["history_start"]),
        end=pd.Timestamp(sample["evaluation_end"]),
    )

    metadata = pd.DataFrame(
        columns=["sid", "ticker", "issuer_name", "source", "raw_sha256"]
    )
    name_candidates = pd.DataFrame(
        columns=[
            "sid",
            "cik10",
            "score",
            "matched_name",
            "source",
            "issuer_name",
        ]
    )
    temporal_evidence = pd.DataFrame()
    if (
        float(coverage["coverage"]) < minimum
        and bool(
            layout.program["identifier_resolution"].get(
                "use_tiingo_name_fallback", False
            )
        )
        and tiingo_api_token
    ):
        metadata = _collect_tiingo_issuer_names(
            layout,
            aliases,
            bridge,
            token=tiingo_api_token,
            refresh=refresh,
        )
        if not metadata.empty:
            lookup_payload, _ = _cached_sec_text(
                client,
                store,
                str(sec_config["cik_lookup_url"]),
                refresh=refresh,
            )
            lookup = parse_sec_cik_lookup(lookup_payload)
            raw_name_candidates = build_sec_name_candidates(metadata, lookup)
            name_candidates, temporal_evidence = _select_temporal_name_candidates(
                raw_name_candidates,
                aliases,
                client=client,
                store=store,
                submissions_url_template=str(sec_config["submissions_url_template"]),
                sample_start=pd.Timestamp(sample["history_start"]),
                sample_end=pd.Timestamp(sample["evaluation_end"]),
                refresh=refresh,
            )
            bridge = resolve_entity_bridge(
                aliases,
                candidates,
                name_candidates=name_candidates,
                overrides=overrides,
            )
            bridge_intervals = build_entity_cik_intervals(bridge, overrides)
            coverage = member_session_mapping_coverage(
                bridge_intervals,
                membership,
                sessions,
                start=pd.Timestamp(sample["history_start"]),
                end=pd.Timestamp(sample["evaluation_end"]),
            )
    mapped = int(bridge["cik10"].notna().sum())
    qa = {
        "schema_version": "cross_sectional_alpha.identifier_qa.v1",
        "entity_bridge_version": str(layout.program["versions"]["entity_bridge"]),
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "market_dataset": str(layout.program["versions"]["market_dataset"]),
        "security_count": int(len(bridge)),
        "mapped_security_count": mapped,
        "unmapped_security_count": int(len(bridge) - mapped),
        "entity_cik_interval_count": int(len(bridge_intervals)),
        "multi_cik_security_count": int(
            bridge_intervals.groupby("sid")["cik10"].nunique().gt(1).sum()
        ),
        "member_session_coverage": coverage,
        "minimum_member_session_coverage": minimum,
        "coverage_gate_passed": bool(float(coverage["coverage"]) >= minimum),
        "current_ticker_rows": int(len(current)),
        "historical_browse_rows": int(len(browse)),
        "historical_browse_failures": int(len(failure_rows)),
        "tiingo_name_rows": int(len(metadata)),
        "sec_name_candidate_rows": int(len(name_candidates)),
        "sec_name_temporal_evidence_rows": int(len(temporal_evidence)),
        "raw_fetch_record_count": len(store.ledger_records()),
    }

    failures = pd.DataFrame(
        failure_rows,
        columns=[
            "ticker",
            "requested_url",
            "failure_type",
            "failure_message",
        ],
    )
    _atomic_parquet(aliases, layout.identifier_root / "security_aliases.parquet")
    _atomic_parquet(current, layout.identifier_root / "sec_current_tickers.parquet")
    _atomic_parquet(candidates, layout.identifier_root / "ticker_candidates.parquet")
    _atomic_parquet(metadata, layout.identifier_root / "issuer_name_evidence.parquet")
    _atomic_parquet(
        name_candidates, layout.identifier_root / "sec_name_candidates.parquet"
    )
    _atomic_parquet(
        temporal_evidence,
        layout.identifier_root / "sec_name_temporal_evidence.parquet",
    )
    _atomic_parquet(failures, layout.identifier_root / "browse_failures.parquet")
    _atomic_parquet(bridge, layout.identifier_root / "entity_bridge.parquet")
    _atomic_parquet(
        bridge_intervals,
        layout.identifier_root / "entity_cik_intervals.parquet",
    )
    _atomic_json(qa, layout.identifier_root / "mapping_qa.json")
    _atomic_json(
        _directory_manifest(
            layout.identifier_root,
            identifier="cross_sectional_alpha.identifier_manifest.v1",
            extra={
                "entity_bridge_version": str(
                    layout.program["versions"]["entity_bridge"]
                )
            },
        ),
        layout.identifier_root / "manifest.json",
    )
    if not qa["coverage_gate_passed"]:
        raise IdentifierCoverageError(
            "SID-to-CIK member-session coverage is "
            f"{float(coverage['coverage']):.4%}, below {minimum:.2%}; "
            f"review {layout.identifier_root / 'entity_bridge.parquet'}"
        )
    return qa


def _select_temporal_name_candidates(
    candidates: pd.DataFrame,
    aliases: pd.DataFrame,
    *,
    client: SECClient,
    store: ImmutableFetchStore,
    submissions_url_template: str,
    sample_start: pd.Timestamp,
    sample_end: pd.Timestamp,
    refresh: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Disambiguate same-name CIKs with the issuer's annual filing history."""

    output_columns = list(candidates.columns)
    evidence_columns = [
        "sid",
        "cik10",
        "issuer_name",
        "sec_submission_name",
        "name_score",
        "membership_from",
        "membership_to",
        "annual_filing_count_in_span",
        "first_annual_filing",
        "last_annual_filing",
        "raw_sha256",
        "selection_status",
    ]
    if candidates.empty:
        return candidates, pd.DataFrame(columns=evidence_columns)

    aliases_by_sid = aliases.set_index("sid")
    roots: dict[str, tuple[Mapping[str, Any], Any]] = {}
    for cik10 in sorted(candidates["cik10"].astype(str).unique()):
        url = submissions_url_template.format(cik10=cik10)
        payload, record = _cached_sec_json(
            client, store, url, refresh=refresh
        )
        if not isinstance(payload, Mapping):
            raise CrossSectionalDatabaseError(
                f"SEC submissions root is not an object for CIK {cik10}"
            )
        payload_cik = str(payload.get("cik", "")).strip().zfill(10)
        if payload_cik != cik10:
            raise CrossSectionalDatabaseError(
                f"SEC submissions CIK mismatch: expected {cik10}, got {payload_cik}"
            )
        roots[cik10] = (payload, record)

    evidence_rows: list[dict[str, Any]] = []
    collapsed = (
        candidates.sort_values(
            ["sid", "cik10", "score", "matched_name"],
            ascending=[True, True, False, True],
            kind="stable",
        )
        .drop_duplicates(["sid", "cik10"], keep="first")
        .reset_index(drop=True)
    )
    for row in collapsed.itertuples(index=False):
        alias = aliases_by_sid.loc[str(row.sid)]
        left = max(sample_start, pd.Timestamp(alias["membership_from"]))
        right = sample_end
        if pd.notna(alias["membership_to"]):
            right = min(right, pd.Timestamp(alias["membership_to"]))
        root, record = roots[str(row.cik10)]
        annual_dates = _annual_filing_dates(root)
        # A one-year shoulder admits the first report after a merger and the
        # final report shortly after a constituent leaves, without making a
        # decades-old same-name filer look contemporaneous.
        in_span = annual_dates[
            (annual_dates >= left - pd.Timedelta(days=370))
            & (annual_dates <= right + pd.Timedelta(days=370))
        ]
        submission_name = str(root.get("name", "")).strip()
        from momentum_reversal.data.entity_bridge import company_name_score

        evidence_rows.append(
            {
                "sid": str(row.sid),
                "cik10": str(row.cik10),
                "issuer_name": str(row.issuer_name),
                "sec_submission_name": submission_name,
                "name_score": company_name_score(
                    row.issuer_name, submission_name
                ),
                "membership_from": left,
                "membership_to": right,
                "annual_filing_count_in_span": int(len(in_span)),
                "first_annual_filing": (
                    pd.NaT if len(annual_dates) == 0 else annual_dates.min()
                ),
                "last_annual_filing": (
                    pd.NaT if len(annual_dates) == 0 else annual_dates.max()
                ),
                "raw_sha256": record.sha256,
                "selection_status": "candidate",
            }
        )

    evidence = pd.DataFrame(evidence_rows, columns=evidence_columns)
    selected_rows: list[pd.Series] = []
    for sid, group in evidence.groupby("sid", sort=True):
        eligible = group.loc[
            (group["annual_filing_count_in_span"] > 0)
            & (group["name_score"] >= 0.75)
        ].copy()
        if eligible.empty:
            # A single CIK from exact name evidence remains acceptable even
            # when its annual filings have already moved to a history shard.
            original = collapsed.loc[collapsed["sid"].eq(sid)]
            if len(original) == 1 and float(original.iloc[0]["score"]) >= 0.92:
                selected_rows.append(original.iloc[0])
                evidence.loc[
                    (evidence["sid"].eq(sid))
                    & (evidence["cik10"].eq(str(original.iloc[0]["cik10"]))),
                    "selection_status",
                ] = "selected_unique_name_without_recent_10k"
            continue
        eligible = eligible.sort_values(
            [
                "annual_filing_count_in_span",
                "last_annual_filing",
                "name_score",
                "cik10",
            ],
            ascending=[False, False, False, True],
            kind="stable",
        )
        top = eligible.iloc[0]
        runner_count = (
            int(eligible.iloc[1]["annual_filing_count_in_span"])
            if len(eligible) > 1
            else -1
        )
        top_count = int(top["annual_filing_count_in_span"])
        if len(eligible) > 1 and top_count == runner_count:
            top_last = pd.Timestamp(top["last_annual_filing"])
            runner_last = pd.Timestamp(eligible.iloc[1]["last_annual_filing"])
            if abs((top_last - runner_last).days) < 370:
                evidence.loc[evidence["sid"].eq(sid), "selection_status"] = (
                    "review_temporal_tie"
                )
                continue
        original = collapsed.loc[
            collapsed["sid"].eq(sid) & collapsed["cik10"].eq(top["cik10"])
        ].iloc[0]
        selected_rows.append(original)
        evidence.loc[
            evidence["sid"].eq(sid) & evidence["cik10"].eq(top["cik10"]),
            "selection_status",
        ] = "selected_temporal_10k_coverage"
        evidence.loc[
            evidence["sid"].eq(sid)
            & ~evidence["cik10"].eq(top["cik10"])
            & evidence["selection_status"].eq("candidate"),
            "selection_status",
        ] = "rejected_temporal_10k_coverage"

    selected = pd.DataFrame(selected_rows, columns=output_columns)
    if not selected.empty:
        selected = selected.sort_values(["sid", "cik10"], ignore_index=True)
    return selected, evidence.sort_values(["sid", "cik10"], ignore_index=True)


def _annual_filing_dates(root: Mapping[str, Any]) -> pd.DatetimeIndex:
    try:
        recent = root["filings"]["recent"]
        forms = list(recent["form"])
        filing_dates = list(recent["filingDate"])
    except (KeyError, TypeError):
        raise CrossSectionalDatabaseError(
            "SEC submissions root is missing filings.recent form/filingDate"
        ) from None
    if len(forms) != len(filing_dates):
        raise CrossSectionalDatabaseError(
            "SEC submissions recent form/filingDate lengths differ"
        )
    values = [
        date
        for form, date in zip(forms, filing_dates, strict=True)
        if str(form).upper() in {"10-K", "10-K/A"}
    ]
    return pd.DatetimeIndex(pd.to_datetime(values, errors="coerce")).dropna()


def _collect_tiingo_issuer_names(
    layout: DatabaseLayout,
    aliases: pd.DataFrame,
    bridge: pd.DataFrame,
    *,
    token: str,
    refresh: bool,
) -> pd.DataFrame:
    """Obtain issuer names only for unresolved, non-reused market identities."""

    unresolved = bridge.loc[bridge["cik10"].isna(), ["sid"]].merge(
        aliases.loc[:, ["sid", "canonical_ticker", "ticker_aliases", "identity_status"]],
        on="sid",
        how="left",
        validate="one_to_one",
    )
    unresolved = unresolved.loc[
        ~unresolved["identity_status"].astype(str).str.contains(
            "ticker_reuse", case=False, na=False
        )
    ]
    store = ImmutableFetchStore(layout.tiingo_identifier_store_root)
    rows: list[dict[str, str]] = []
    rate_limited = False
    for item in unresolved.sort_values("sid").itertuples():
        values = [str(item.canonical_ticker)]
        values.extend(str(item.ticker_aliases).split("|"))
        tickers = list(dict.fromkeys(value for value in values if value))
        for ticker in tickers:
            try:
                payload, record = _cached_tiingo_metadata(
                    store,
                    ticker,
                    token=token,
                    refresh=refresh,
                )
            except CrossSectionalDatabaseError as exc:
                if "rate limit" not in str(exc).lower():
                    raise
                rate_limited = True
                break
            if not isinstance(payload, Mapping):
                continue
            name = str(payload.get("name", "")).strip()
            if not name:
                continue
            rows.append(
                {
                    "sid": str(item.sid),
                    "ticker": ticker,
                    "issuer_name": name,
                    "source": "tiingo_identifier_metadata",
                    "raw_sha256": record.sha256,
                }
            )
            break
        if rate_limited:
            break
    return pd.DataFrame(
        rows,
        columns=["sid", "ticker", "issuer_name", "source", "raw_sha256"],
    )


def _cached_tiingo_metadata(
    store: ImmutableFetchStore,
    ticker: str,
    *,
    token: str,
    refresh: bool,
) -> tuple[object | None, Any | None]:
    encoded = quote(str(ticker), safe="")
    url = f"https://api.tiingo.com/tiingo/daily/{encoded}"
    record = None if refresh else _latest_record_for_url(store, url)
    if record is not None:
        if int(record.status) == 429:
            raise CrossSectionalDatabaseError(
                "Tiingo rate limit reached during identifier fallback"
            )
        if not 200 <= int(record.status) < 300:
            return None, record
        try:
            return json.loads(_decoded_cached_body(record).decode("utf-8-sig")), record
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CrossSectionalDatabaseError(
                f"cached Tiingo metadata is invalid: {record.raw_path}"
            ) from None

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Token {token}",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=30.0) as response:  # noqa: S310
            raw_response = SECResponse(
                status=int(getattr(response, "status", 200)),
                url=str(response.geturl()),
                headers={str(k): str(v) for k, v in response.headers.items()},
                body=response.read(),
            )
    except HTTPError as exc:
        raw_response = SECResponse(
            status=int(exc.code),
            url=url,
            headers={str(k): str(v) for k, v in (exc.headers or {}).items()},
            body=exc.read(),
        )
    except (URLError, TimeoutError, OSError):
        raise CrossSectionalDatabaseError(
            f"Tiingo identity request failed for {ticker}"
        ) from None
    record = store.record(
        requested_url=url,
        response=raw_response,
        retrieved_at=datetime.now(timezone.utc),
    )
    if raw_response.status == 429:
        raise CrossSectionalDatabaseError(
            "Tiingo rate limit reached during identifier fallback"
        )
    if not 200 <= raw_response.status < 300:
        return None, record
    try:
        payload = json.loads(
            FetchedResponse(raw_response, record).decoded_body().decode("utf-8-sig")
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CrossSectionalDatabaseError(
            f"Tiingo returned invalid metadata for {ticker}"
        ) from None
    time.sleep(0.12)
    return payload, record


def load_identifier_bridge(layout: DatabaseLayout) -> pd.DataFrame:
    path = layout.identifier_root / "entity_bridge.parquet"
    if not path.is_file():
        raise FileNotFoundError(path)
    return pd.read_parquet(path)


def load_identifier_intervals(layout: DatabaseLayout) -> pd.DataFrame:
    path = layout.identifier_root / "entity_cik_intervals.parquet"
    if not path.is_file():
        raise FileNotFoundError(path)
    return pd.read_parquet(path)


def build_fundamental_stage(
    layout: DatabaseLayout,
    *,
    sec_user_agent: str,
    refresh: bool = False,
    limit_ciks: int | None = None,
    allow_incomplete_identifiers: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Fetch, canonicalize, and publish annual SEC facts for mapped issuers."""

    reporter = progress or (lambda _: None)
    if not bool(
        layout.program["identifier_resolution"].get(
            "issuer_temporal_support_required", False
        )
    ):
        raise CrossSectionalDatabaseError(
            "issuer_temporal_support_required must remain true"
        )
    mapping_qa_path = layout.identifier_root / "mapping_qa.json"
    if not mapping_qa_path.is_file():
        raise FileNotFoundError(mapping_qa_path)
    mapping_qa = json.loads(mapping_qa_path.read_text(encoding="utf-8"))
    if (
        not bool(mapping_qa.get("coverage_gate_passed"))
        and not allow_incomplete_identifiers
    ):
        raise IdentifierCoverageError(
            "identifier stage has not passed its member-session coverage gate"
        )
    bridge = load_identifier_bridge(layout)
    bridge_intervals = load_identifier_intervals(layout)
    interval_start = pd.to_datetime(
        bridge_intervals["effective_from"], errors="coerce"
    ).dt.normalize()
    interval_end = pd.to_datetime(
        bridge_intervals["effective_to"], errors="coerce"
    ).dt.normalize()
    research_start = pd.Timestamp(
        layout.program["sample"]["history_start"]
    ).normalize()
    research_end = pd.Timestamp(
        layout.program["sample"]["evaluation_end"]
    ).normalize()
    overlaps_research = interval_start.le(research_end) & (
        interval_end.isna() | interval_end.gt(research_start)
    )
    ciks = sorted(
        bridge_intervals.loc[
            overlaps_research
            &
            bridge_intervals["cik10"].notna(), "cik10"
        ].astype(str).unique()
    )
    if limit_ciks is not None:
        if limit_ciks <= 0:
            raise ValueError("limit_ciks must be positive")
        ciks = ciks[: int(limit_ciks)]

    store = ImmutableFetchStore(layout.sec_store_root)
    sec_config = layout.program["sec"]
    client = SECClient(
        user_agent=sec_user_agent,
        raw_store=store,
        rate_limit_per_second=float(sec_config["max_requests_per_second"]),
        timeout=float(sec_config["timeout_seconds"]),
    )
    metric_registry = load_sec_metric_registry(
        layout.project_root / str(layout.program["fundamentals"]["metric_registry"])
    )
    companyfacts_exceptions = load_sec_companyfacts_exceptions(
        layout.project_root
        / str(
            layout.program["fundamentals"]["companyfacts_exceptions"]
        )
    )
    build_signature = _fundamental_build_signature(layout)
    if limit_ciks is None:
        frozen = _load_fundamental_freeze_fail_closed(
            layout, build_signature
        )
        if frozen is not None:
            if refresh:
                raise CrossSectionalDatabaseError(
                    "fundamental version is already frozen; a network refresh "
                    "requires a new fundamentals version"
                )
            return dict(frozen["build_summary"])
    schedule = _research_xnys_schedule()
    by_cik_root = layout.curated_root / "by_cik"
    by_cik_root.mkdir(parents=True, exist_ok=True)
    failures: list[dict[str, str]] = []

    for index, cik10 in enumerate(ciks, start=1):
        destination = by_cik_root / cik10
        if not refresh and _cik_bundle_valid(
            destination, build_signature=build_signature
        ):
            if index == 1 or index % 25 == 0 or index == len(ciks):
                reporter(f"fundamentals {index}/{len(ciks)} cached CIK {cik10}")
            continue
        try:
            root_url = str(sec_config["submissions_url_template"]).format(
                cik10=cik10
            )
            root_payload, root_record = _cached_sec_json(
                client, store, root_url, refresh=refresh
            )
            history_payloads: dict[str, object] = {}
            history_records: list[Any] = []
            for name in submission_history_file_names(root_payload):
                history_url = f"https://data.sec.gov/submissions/{name}"
                payload, record = _cached_sec_json(
                    client, store, history_url, refresh=refresh
                )
                history_payloads[name] = payload
                history_records.append(record)
            facts_url = str(sec_config["companyfacts_url_template"]).format(
                cik10=cik10
            )
            cached_facts_record = _reusable_sec_record(store, facts_url)
            if (
                cached_facts_record is not None
                and int(cached_facts_record.status) == 404
            ):
                source_applicability = (
                    _companyfacts_not_applicable_resolution(
                        cik10=cik10,
                        facts_url=facts_url,
                        facts_record=cached_facts_record,
                        submissions_root=root_payload,
                        submissions_history=history_payloads,
                        exceptions=companyfacts_exceptions,
                    )
                )
                # This object is only an in-memory adapter into the ordinary
                # canonicalization path.  The immutable 404 XML remains the
                # sole Company Facts raw evidence and no fact is synthesized.
                registered_payload: object = {
                    "cik": cik10,
                    "entityName": str(
                        root_payload.get("name", "")
                        if isinstance(root_payload, Mapping)
                        else ""
                    ),
                    "facts": {},
                }
                facts_record = cached_facts_record
            else:
                companyfacts_payload, facts_record = _cached_sec_json(
                    client, store, facts_url, refresh=refresh
                )
                registered_payload = filter_companyfacts_to_metric_registry(
                    companyfacts_payload, metric_registry
                )
                source_applicability = _available_companyfacts_applicability(
                    cik10, facts_record
                )
            result = build_sec_fundamental_tables(
                root_payload,
                history_payloads,
                registered_payload,
                metric_registry,
                schedule,
                availability_buffer_minutes=float(
                    sec_config["availability_buffer_minutes"]
                ),
                minimum_duration_days=int(
                    layout.program["fundamentals"]["minimum_duration_days"]
                ),
                maximum_duration_days=int(
                    layout.program["fundamentals"]["maximum_duration_days"]
                ),
            )
            registered = _registered_companyfacts(result.facts, metric_registry)
            coverage_qa = _source_applicability_qa(
                result.coverage_qa, source_applicability
            )
            _write_cik_bundle(
                destination,
                cik10=cik10,
                filings=result.filings,
                registered_facts=registered,
                canonical=result.canonical,
                coverage_qa=coverage_qa,
                raw_records=[root_record, *history_records, facts_record],
                build_signature=build_signature,
                source_applicability=source_applicability,
            )
        except SECCooldownError:
            # A 403/429 is a global SEC instruction, not an issuer-level data
            # defect.  Continuing would turn one rate-limit response into
            # hundreds of misleading per-CIK failures.
            raise
        except Exception as exc:  # each CIK remains an explicit audit row
            failures.append(
                {
                    "cik10": cik10,
                    "failure_type": type(exc).__name__,
                    "failure_message": str(exc),
                }
            )
        if index == 1 or index % 10 == 0 or index == len(ciks):
            reporter(
                f"fundamentals {index}/{len(ciks)}; failures={len(failures)}"
            )

    failed_ciks = {str(item["cik10"]) for item in failures}
    successful_dirs = [
        by_cik_root / cik10
        for cik10 in ciks
        if cik10 not in failed_ciks
        and _cik_bundle_valid(
            by_cik_root / cik10, build_signature=build_signature
        )
    ]
    filings = _concat_cik_table(successful_dirs, "filings.parquet")
    facts = _concat_cik_table(successful_dirs, "registered_facts.parquet")
    canonical = _concat_cik_table(successful_dirs, "canonical_annual_facts.parquet")
    coverage_qa = _concat_cik_table(successful_dirs, "coverage_qa.parquet")
    source_applicability_rows: list[dict[str, object]] = []
    for directory in successful_dirs:
        source_path = directory / "source_applicability.json"
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        if (
            normalize_cik(payload.get("cik10")) != directory.name
            or payload.get("status")
            not in {"available", "resolved_not_applicable"}
            or bool(payload.get("imputation_applied"))
            or int(payload.get("imputed_fact_rows", -1)) != 0
        ):
            raise CrossSectionalDatabaseError(
                f"invalid source applicability artifact: {source_path}"
            )
        source_applicability_rows.append(dict(payload))
    source_applicability = pd.DataFrame(source_applicability_rows)
    if not source_applicability.empty:
        source_applicability = source_applicability.sort_values(
            "cik10", ignore_index=True
        )
    failure_frame = pd.DataFrame(
        failures, columns=["cik10", "failure_type", "failure_message"]
    )

    aggregate_root = (
        layout.curated_root
        if limit_ciks is None
        else layout.curated_root / "_smoke" / f"first_{int(limit_ciks):04d}"
    )
    aggregate_root.mkdir(parents=True, exist_ok=True)
    _atomic_parquet(filings, aggregate_root / "filings.parquet")
    _atomic_parquet(facts, aggregate_root / "registered_facts.parquet")
    _atomic_parquet(
        canonical, aggregate_root / "canonical_annual_facts.parquet"
    )
    _atomic_parquet(coverage_qa, aggregate_root / "coverage_qa.parquet")
    _atomic_parquet(
        source_applicability,
        aggregate_root / "source_applicability.parquet",
    )
    _atomic_parquet(failure_frame, aggregate_root / "fetch_failures.parquet")
    _atomic_parquet(bridge, aggregate_root / "entity_bridge.parquet")
    _atomic_parquet(
        bridge_intervals,
        aggregate_root / "entity_cik_intervals.parquet",
    )
    cik_manifest_index = []
    for directory in successful_dirs:
        manifest_path = directory / "manifest.json"
        cik_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        cik_manifest_index.append(
            {
                "cik10": directory.name,
                "content_sha256": str(cik_manifest["content_sha256"]),
                "fundamental_build_signature": str(
                    cik_manifest["fundamental_build_signature"]
                ),
                "source_applicability": str(
                    cik_manifest["source_applicability"]["status"]
                ),
            }
        )
    _atomic_json(
        {
            "schema_version": (
                "cross_sectional_alpha.fundamental_cik_manifest_index.v1"
            ),
            "fundamental_build_signature": build_signature,
            "cik_count": len(cik_manifest_index),
            "ciks": cik_manifest_index,
        },
        aggregate_root / "cik_manifest_index.json",
    )

    accounting_qa, accounting_summary = build_accounting_identity_qa(
        canonical,
        relative_tolerance=float(
            layout.program.get("quality", {}).get(
                "gross_profit_identity_relative_tolerance", 0.01
            )
        ),
    )
    _atomic_parquet(accounting_qa, aggregate_root / "accounting_identity_qa.parquet")
    _atomic_json(
        accounting_summary,
        aggregate_root / "accounting_identity_summary.json",
    )

    temporal_applicability = source_applicability.rename(
        columns={"status": "source_applicability_status"}
    )
    temporal_qa, temporal_summary = build_entity_temporal_support_qa(
        filings,
        bridge_intervals,
        history_start=layout.program["sample"]["history_start"],
        evaluation_end=layout.program["sample"]["evaluation_end"],
        source_applicability=temporal_applicability,
        minimum_long_interval_days=int(
            layout.program["identifier_resolution"].get(
                "minimum_temporal_support_interval_days", 365
            )
        ),
    )
    _atomic_parquet(
        temporal_qa,
        aggregate_root / "entity_temporal_support_qa.parquet",
    )
    _atomic_json(
        temporal_summary,
        aggregate_root / "entity_temporal_support_summary.json",
    )

    completed = len(successful_dirs)
    resolved_not_applicable_ciks = sorted(
        source_applicability.loc[
            source_applicability["status"].eq("resolved_not_applicable"),
            "cik10",
        ].astype(str).tolist()
        if not source_applicability.empty
        else []
    )
    source_applicability_counts = {
        str(key): int(value)
        for key, value in (
            source_applicability["status"].value_counts(sort=False).sort_index().items()
            if not source_applicability.empty
            else []
        )
    }
    summary = {
        "schema_version": "cross_sectional_alpha.fundamental_summary.v1",
        "fundamental_version": str(layout.program["versions"]["fundamentals"]),
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested_cik_count": len(ciks),
        "completed_cik_count": completed,
        "failed_cik_count": len(ciks) - completed,
        "source_applicability_cik_count": int(len(source_applicability)),
        "source_applicability_counts": source_applicability_counts,
        "resolved_not_applicable_cik_count": int(
            len(resolved_not_applicable_ciks)
        ),
        "resolved_not_applicable_ciks": resolved_not_applicable_ciks,
        "not_applicable_imputed_fact_rows": 0,
        "filing_rows": int(len(filings)),
        "registered_fact_rows": int(len(facts)),
        "canonical_fact_rows": int(len(canonical)),
        "metric_count": int(canonical["metric_id"].nunique()) if not canonical.empty else 0,
        "raw_fetch_record_count": len(store.ledger_records()),
        "fundamental_build_signature": build_signature,
        "accounting_identity": accounting_summary,
        "entity_temporal_support": temporal_summary,
        "identifier_coverage_gate_passed": bool(
            mapping_qa.get("coverage_gate_passed")
        ),
        "limited_smoke_build": limit_ciks is not None,
    }
    quality_gate_passed = bool(
        completed == len(ciks)
        and accounting_summary.get("identity_gate_passed", False)
        and temporal_summary.get("temporal_support_gate_passed", False)
    )
    summary["fundamental_quality_gate_passed"] = quality_gate_passed
    _atomic_json(summary, aggregate_root / "build_summary.json")
    manifest = _directory_manifest(
        aggregate_root,
        identifier="cross_sectional_alpha.fundamental_manifest.v1",
        extra={
            "fundamental_version": str(layout.program["versions"]["fundamentals"]),
            "market_dataset": str(layout.program["versions"]["market_dataset"]),
            "fundamental_build_signature": build_signature,
        },
    )
    _atomic_json(manifest, aggregate_root / "manifest.json")
    if limit_ciks is None:
        _atomic_json(
            {
                "schema_version": "cross_sectional_alpha.fundamental_freeze.v1",
                "fundamental_version": str(
                    layout.program["versions"]["fundamentals"]
                ),
                "status": (
                    "frozen_complete"
                    if quality_gate_passed
                    else "incomplete_or_quality_gate_failed"
                ),
                "formal_eligible": False,
                "fundamental_build_signature": build_signature,
                "content_sha256": str(manifest["content_sha256"]),
                "manifest_sha256": _sha256(aggregate_root / "manifest.json"),
                "build_summary": summary,
            },
            aggregate_root / "FROZEN.json",
        )
    return summary


def build_market_factor_stage(layout: DatabaseLayout) -> dict[str, Any]:
    """Materialize every registered market factor from the frozen PIT bundle.

    This stage performs no network access and never mutates the frozen market
    dataset. The volume gate is evaluated from member-session observations
    before either volume-dependent factor is authorized; failed volume QA
    remains visible as explicit ineligible factor rows.
    """

    if (layout.derived_root / "FROZEN.json").is_file():
        if _load_current_factor_freeze(layout) is None:
            raise CrossSectionalDatabaseError(
                "factor build is frozen with drift; market factors cannot be "
                "overwritten under the same version"
            )
        return json.loads(
            (layout.derived_root / "market_factor_build_summary.json").read_text(
                encoding="utf-8"
            )
        )

    calendar = pd.read_parquet(layout.market_root / "calendar.parquet")
    prices = pd.read_parquet(layout.market_root / "prices_daily.parquet")
    benchmark = pd.read_parquet(layout.market_root / "benchmark_daily.parquet")
    membership = pd.read_parquet(layout.market_root / "membership.parquet")
    signal_dates = _program_signal_dates(layout, calendar)
    volume_qa = build_market_volume_qa(
        prices,
        membership,
        calendar=calendar,
        history_start=layout.program["sample"]["history_start"],
        evaluation_end=layout.program["sample"]["evaluation_end"],
        minimum_coverage=float(
            layout.program.get("market_quality", {}).get(
                "minimum_price_conditional_volume_coverage", 0.999
            )
        ),
    )
    panel = materialize_cross_sectional_market_factors(
        prices,
        benchmark,
        calendar,
        membership,
        signal_dates,
        factor_ids=MARKET_FACTOR_IDS,
        volume_qa_passed=bool(volume_qa["volume_qa_passed"]),
    )

    layout.derived_root.mkdir(parents=True, exist_ok=True)
    _atomic_parquet(panel, layout.derived_root / "market_factor_panel.parquet")
    _atomic_parquet(
        pd.DataFrame({"signal_date": signal_dates}),
        layout.derived_root / "signal_dates.parquet",
    )
    _atomic_json(volume_qa, layout.derived_root / "market_volume_qa.json")
    factor_summary = (
        panel.groupby("factor_id", sort=True)
        .agg(
            total_rows=("eligible", "size"),
            eligible_rows=("eligible", "sum"),
            first_signal_date=("signal_date", "min"),
            last_signal_date=("signal_date", "max"),
        )
        .reset_index()
    )
    factor_summary["eligible_rows"] = factor_summary["eligible_rows"].astype(int)
    factor_summary["coverage_rate"] = (
        factor_summary["eligible_rows"] / factor_summary["total_rows"]
    )
    _atomic_parquet(
        factor_summary, layout.derived_root / "market_factor_summary.parquet"
    )
    summary = {
        "schema_version": "cross_sectional_alpha.market_factor_summary.v1",
        "factor_build_version": str(layout.program["versions"]["factor_build"]),
        "market_dataset": str(layout.program["versions"]["market_dataset"]),
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "signal_date_count": int(len(signal_dates)),
        "first_signal_date": signal_dates.min().date().isoformat(),
        "last_signal_date": signal_dates.max().date().isoformat(),
        "factor_count": int(panel["factor_id"].nunique()),
        "row_count": int(len(panel)),
        "eligible_row_count": int(panel["eligible"].sum()),
        "volume_qa_passed": bool(volume_qa["volume_qa_passed"]),
    }
    _atomic_json(summary, layout.derived_root / "market_factor_build_summary.json")
    return summary


def build_market_volume_qa(
    prices: pd.DataFrame,
    membership: pd.DataFrame,
    *,
    calendar: pd.DataFrame | None = None,
    history_start: object,
    evaluation_end: object,
    minimum_coverage: float = 0.98,
) -> dict[str, Any]:
    """Evaluate the frozen share-volume field on the PIT member-day universe."""

    required_prices = {
        "date",
        "sid",
        "raw_close",
        "tr_close",
        "volume",
        "stock_splits",
    }
    missing_prices = required_prices.difference(prices.columns)
    if missing_prices:
        raise ValueError(f"prices missing volume-QA columns: {sorted(missing_prices)}")
    required_membership = {"sid", "effective_from", "effective_to"}
    missing_membership = required_membership.difference(membership.columns)
    if missing_membership:
        raise ValueError(
            "membership missing volume-QA columns: "
            f"{sorted(missing_membership)}"
        )
    if not 0.0 < float(minimum_coverage) <= 1.0:
        raise ValueError("minimum_coverage must be in (0, 1]")

    start = pd.Timestamp(history_start).normalize()
    end = pd.Timestamp(evaluation_end).normalize()
    if start > end:
        raise ValueError("history_start cannot exceed evaluation_end")
    date_values = pd.to_datetime(prices["date"]).dt.normalize()
    frame = prices.loc[
        date_values.between(start, end), list(required_prices)
    ].copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    intervals = membership.loc[:, list(required_membership)].copy()
    intervals["effective_from"] = pd.to_datetime(
        intervals["effective_from"]
    ).dt.normalize()
    intervals["effective_to"] = pd.to_datetime(
        intervals["effective_to"], errors="coerce"
    ).dt.normalize()
    joined = frame.merge(intervals, on="sid", how="inner", validate="many_to_many")
    member = joined.loc[
        joined["date"].ge(joined["effective_from"])
        & (
            joined["effective_to"].isna()
            | joined["date"].lt(joined["effective_to"])
        )
    ].copy()
    if member.duplicated(["date", "sid"]).any():
        raise ValueError("overlapping membership intervals duplicate member-days")
    total = int(len(member))
    if calendar is None:
        sessions = pd.DatetimeIndex(frame["date"].drop_duplicates()).sort_values()
    else:
        if "session_date" not in calendar.columns:
            raise ValueError("calendar missing session_date for volume QA")
        calendar_dates = pd.to_datetime(
            calendar["session_date"], errors="coerce"
        ).dt.normalize()
        if calendar_dates.isna().any():
            raise ValueError("calendar contains invalid session_date")
        sessions = pd.DatetimeIndex(
            calendar_dates.loc[calendar_dates.between(start, end)]
        ).drop_duplicates().sort_values()
    expected_total = 0
    for row in intervals.itertuples(index=False):
        active = sessions >= pd.Timestamp(row.effective_from)
        if pd.notna(row.effective_to):
            active &= sessions < pd.Timestamp(row.effective_to)
        expected_total += int(active.sum())
    if total != expected_total:
        raise ValueError(
            "prices omit one or more expected PIT member-session rows: "
            f"observed={total}, expected={expected_total}"
        )
    if total == 0:
        raise ValueError("member-day universe is empty for volume QA")
    raw_close = pd.to_numeric(member["raw_close"], errors="coerce")
    tr_close = pd.to_numeric(member["tr_close"], errors="coerce")
    volume = pd.to_numeric(member["volume"], errors="coerce")
    splits = pd.to_numeric(member["stock_splits"], errors="coerce")
    price_valid = raw_close.gt(0) & tr_close.gt(0)
    volume_known = volume.notna()
    volume_nonnegative = volume.ge(0)
    dollar_volume_positive = raw_close.mul(volume).gt(0)
    split_known = splits.notna()
    negative_volume_rows = int(volume.lt(0).sum())
    price_valid_rows = int(price_valid.sum())
    if price_valid_rows == 0:
        raise ValueError("volume QA has no valid-price member-days")
    member_session_volume_coverage = float(
        (price_valid & volume_known).sum() / total
    )
    member_session_dollar_volume_coverage = float(
        dollar_volume_positive.sum() / total
    )
    # The volume gate measures the incremental quality of the volume field.
    # A member-day with no usable price is already ineligible for every market
    # factor and must not be counted a second time as a volume-specific defect.
    volume_coverage = float(
        (price_valid & volume_known).sum() / price_valid_rows
    )
    dollar_volume_coverage = float(
        dollar_volume_positive.sum() / price_valid_rows
    )
    split_event_rows = int(splits.gt(0).sum())
    split_event_missing_volume_rows = int((splits.gt(0) & ~volume_known).sum())
    passed = bool(
        volume_coverage >= minimum_coverage
        and dollar_volume_coverage >= minimum_coverage
        and negative_volume_rows == 0
        and split_event_missing_volume_rows == 0
    )
    return {
        "schema_version": "cross_sectional_alpha.market_volume_qa.v1",
        "history_start": start.date().isoformat(),
        "evaluation_end": end.date().isoformat(),
        "minimum_coverage": float(minimum_coverage),
        "member_session_rows": total,
        "expected_member_session_rows": expected_total,
        "member_session_keyspace_complete": True,
        "valid_price_rows": price_valid_rows,
        "volume_known_rows": int(volume_known.sum()),
        "volume_nonnegative_rows": int(volume_nonnegative.sum()),
        "positive_dollar_volume_rows": int(dollar_volume_positive.sum()),
        "split_known_rows": int(split_known.sum()),
        "negative_volume_rows": negative_volume_rows,
        "split_event_rows": split_event_rows,
        "split_event_missing_volume_rows": split_event_missing_volume_rows,
        "member_session_price_coverage": float(price_valid_rows / total),
        "member_session_volume_coverage": member_session_volume_coverage,
        "member_session_positive_dollar_volume_coverage": (
            member_session_dollar_volume_coverage
        ),
        "price_conditional_volume_coverage": volume_coverage,
        "price_conditional_positive_dollar_volume_coverage": (
            dollar_volume_coverage
        ),
        "gate_denominator": "member_sessions_with_valid_raw_and_total_return_close",
        "volume_qa_passed": passed,
    }


def build_accounting_identity_qa(
    canonical_facts: pd.DataFrame,
    *,
    relative_tolerance: float = 0.01,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Audit Revenue - COGS = Gross Profit in identical filing contexts.

    Synthetic COGS rows are retained and labelled, but do not count as an
    independent reconciliation.  Direct contexts outside tolerance are a
    hard review signal because gross-profitability must not combine
    semantically different revenue and expense scopes.
    """

    required = {
        "cik",
        "accession",
        "period_end",
        "metric_id",
        "value",
        "unit",
        "tag",
    }
    missing = required.difference(canonical_facts.columns)
    if missing:
        raise ValueError(
            "canonical_facts missing accounting-QA columns: "
            f"{sorted(missing)}"
        )
    if not 0.0 <= float(relative_tolerance) < 1.0:
        raise ValueError("relative_tolerance must be in [0, 1)")
    metrics = {"revenue", "cost_of_goods_sold", "gross_profit"}
    frame = canonical_facts.loc[
        canonical_facts["metric_id"].isin(metrics)
    ].copy()
    keys = ["cik", "accession", "period_end", "unit"]
    if frame.empty:
        output = pd.DataFrame(
            columns=[
                *keys,
                "revenue",
                "cost_of_goods_sold",
                "gross_profit",
                "cogs_tag",
                "cogs_is_synthetic",
                "identity_residual",
                "relative_error",
                "status",
            ]
        )
    else:
        if frame.duplicated([*keys, "metric_id"]).any():
            raise ValueError("canonical accounting metrics are not unique")
        values = frame.pivot(index=keys, columns="metric_id", values="value")
        tags = frame.pivot(index=keys, columns="metric_id", values="tag")
        output = values.reindex(columns=sorted(metrics)).reset_index()
        output = output.rename_axis(columns=None)
        cogs_tags = tags.get("cost_of_goods_sold")
        output["cogs_tag"] = (
            cogs_tags.reindex(values.index).astype("string").to_numpy()
            if cogs_tags is not None
            else pd.array([pd.NA] * len(output), dtype="string")
        )
        output["cogs_is_synthetic"] = output["cogs_tag"].str.startswith(
            "synthetic_", na=False
        )
        complete = output[list(metrics)].notna().all(axis=1)
        output["identity_residual"] = (
            output["revenue"]
            - output["cost_of_goods_sold"]
            - output["gross_profit"]
        )
        denominator = output["gross_profit"].abs().clip(lower=1.0)
        output["relative_error"] = output["identity_residual"].abs() / denominator
        output["status"] = "insufficient_direct_metrics"
        output.loc[complete & output["cogs_is_synthetic"], "status"] = (
            "synthetic_identity"
        )
        direct = complete & ~output["cogs_is_synthetic"]
        output.loc[
            direct & output["relative_error"].le(relative_tolerance), "status"
        ] = "pass"
        output.loc[
            direct & output["relative_error"].gt(relative_tolerance), "status"
        ] = "fail_scope_mismatch"
        output = output.sort_values(keys, ignore_index=True)

    direct_rows = output["status"].isin({"pass", "fail_scope_mismatch"})
    failures = output["status"].eq("fail_scope_mismatch")
    summary = {
        "schema_version": "cross_sectional_alpha.accounting_identity_summary.v1",
        "relative_tolerance": float(relative_tolerance),
        "context_count": int(len(output)),
        "complete_direct_context_count": int(direct_rows.sum()),
        "direct_pass_count": int(output["status"].eq("pass").sum()),
        "direct_failure_count": int(failures.sum()),
        "synthetic_identity_count": int(
            output["status"].eq("synthetic_identity").sum()
        ),
        "insufficient_direct_metric_count": int(
            output["status"].eq("insufficient_direct_metrics").sum()
        ),
        "identity_gate_passed": bool(not failures.any()),
    }
    return output, summary


def _program_signal_dates(
    layout: DatabaseLayout, calendar: pd.DataFrame
) -> pd.DatetimeIndex:
    required = {"session_date", "month_last_session"}
    missing = required.difference(calendar.columns)
    if missing:
        raise ValueError(f"calendar missing columns: {sorted(missing)}")
    sessions = pd.to_datetime(calendar["session_date"]).dt.normalize()
    start = pd.Timestamp(layout.program["sample"]["history_start"]).normalize()
    end = pd.Timestamp(layout.program["sample"]["evaluation_end"]).normalize()
    mask = calendar["month_last_session"].astype(bool) & sessions.between(start, end)
    result = pd.DatetimeIndex(sessions.loc[mask]).sort_values()
    if result.empty or result.has_duplicates:
        raise ValueError("program signal calendar must be nonempty and unique")
    return result


def build_factor_stage(layout: DatabaseLayout) -> dict[str, Any]:
    """Combine frozen market and SEC facts into the auditable factor database."""

    fundamental_summary_path = layout.curated_root / "build_summary.json"
    fundamental_freeze_path = layout.curated_root / "FROZEN.json"
    if not fundamental_summary_path.is_file() or not fundamental_freeze_path.is_file():
        raise CrossSectionalDatabaseError(
            "full fundamental stage must complete before factor assembly"
        )
    fundamental_summary = json.loads(
        fundamental_summary_path.read_text(encoding="utf-8")
    )
    fundamental_freeze = json.loads(
        fundamental_freeze_path.read_text(encoding="utf-8")
    )
    if fundamental_freeze.get("status") != "frozen_complete":
        raise CrossSectionalDatabaseError(
            "fundamental FROZEN status is not frozen_complete"
        )
    if fundamental_freeze.get("fundamental_build_signature") != (
        _fundamental_build_signature(layout)
    ):
        raise CrossSectionalDatabaseError(
            "fundamental FROZEN build signature does not match current code/config"
        )
    fundamental_manifest_path = layout.curated_root / "manifest.json"
    if (
        not fundamental_manifest_path.is_file()
        or fundamental_freeze.get("manifest_sha256")
        != _sha256(fundamental_manifest_path)
    ):
        raise CrossSectionalDatabaseError("fundamental manifest anchor mismatch")
    if fundamental_freeze.get("build_summary") != fundamental_summary:
        raise CrossSectionalDatabaseError("fundamental freeze summary mismatch")
    requested = int(fundamental_summary.get("requested_cik_count", 0))
    completed = int(fundamental_summary.get("completed_cik_count", -1))
    if bool(fundamental_summary.get("limited_smoke_build")) or completed != requested:
        raise CrossSectionalDatabaseError(
            "factor assembly requires a non-limited fundamental build with no "
            "unresolved CIK failures"
        )
    if not bool(
        fundamental_summary.get("accounting_identity", {}).get(
            "identity_gate_passed", False
        )
    ):
        raise CrossSectionalDatabaseError(
            "fundamental accounting identity gate has not passed"
        )
    if not bool(
        fundamental_summary.get("entity_temporal_support", {}).get(
            "temporal_support_gate_passed", False
        )
    ):
        raise CrossSectionalDatabaseError(
            "fundamental entity temporal-support gate has not passed"
        )
    existing_factor_freeze = layout.derived_root / "FROZEN.json"
    if existing_factor_freeze.is_file():
        current_summary = _load_current_factor_freeze(layout)
        if current_summary is None:
            raise CrossSectionalDatabaseError(
                "factor-build version is already frozen but its evidence or "
                "code/config anchors drifted; choose a new factor_build version"
            )
        return current_summary

    market_panel_path = layout.derived_root / "market_factor_panel.parquet"
    signal_dates_path = layout.derived_root / "signal_dates.parquet"
    if not market_panel_path.is_file() or not signal_dates_path.is_file():
        raise CrossSectionalDatabaseError(
            "market-factors stage must complete before factor assembly"
        )
    market_panel = pd.read_parquet(market_panel_path)
    signal_dates = pd.DatetimeIndex(
        pd.read_parquet(signal_dates_path)["signal_date"]
    )
    fact_events = pd.read_parquet(
        layout.curated_root / "canonical_annual_facts.parquet"
    )
    mappings = pd.read_parquet(
        layout.curated_root / "entity_cik_intervals.parquet"
    ).rename(columns={"cik10": "cik"})
    fundamental_panel = compute_fundamental_factor_panel(
        fact_events,
        mappings,
        signal_dates,
        issuer_market_equity=None,
    ).reset_index()
    _atomic_parquet(
        fundamental_panel, layout.derived_root / "fundamental_factor_panel.parquet"
    )

    membership = pd.read_parquet(layout.market_root / "membership.parquet")
    registry_path = layout.project_root / str(
        layout.program["factors"]["registry"]
    )
    active_registry = pd.read_csv(
        registry_path, dtype=str, keep_default_na=False
    )
    factor_database = build_factor_database(
        market_panel,
        fundamental_panel,
        membership,
        signal_dates,
        active_registry,
    )
    coverage = build_factor_coverage_qa(factor_database)
    factor_manifest = write_factor_database_bundle(
        factor_database,
        layout.factor_bundle_root,
        coverage_qa=coverage,
    )
    independently_rebuilt_database = build_factor_database(
        market_panel,
        fundamental_panel,
        membership,
        signal_dates,
        active_registry,
    )
    pd.testing.assert_frame_equal(
        independently_rebuilt_database,
        factor_database,
        check_dtype=True,
        check_exact=True,
    )
    independently_rebuilt_coverage = build_factor_coverage_qa(
        independently_rebuilt_database
    )
    deterministic_qa = verify_factor_bundle_determinism(
        independently_rebuilt_database,
        independently_rebuilt_coverage,
        expected_content_sha256=str(factor_manifest["content_sha256"]),
        temporary_parent=layout.derived_root,
    )
    del independently_rebuilt_database, independently_rebuilt_coverage
    _atomic_json(
        deterministic_qa,
        layout.derived_root / "deterministic_rebuild_qa.json",
    )
    causality_qa = verify_actual_future_input_invariance(
        layout,
        factor_database=factor_database,
        active_registry=active_registry,
        signal_dates=signal_dates,
        fact_events=fact_events,
        mappings=mappings,
        membership=membership,
    )
    _atomic_json(causality_qa, layout.derived_root / "causality_qa.json")
    readiness = evaluate_factor_readiness(
        factor_database,
        active_registry,
        evaluation_start=layout.program["sample"]["evaluation_start"],
        evaluation_end=layout.program["sample"]["evaluation_end"],
        member_coverage_minimum=float(
            layout.program["factor_readiness"][
                "evaluation_member_coverage_minimum"
            ]
        ),
        minimum_covered_signal_months=int(
            layout.program["factor_readiness"][
                "minimum_covered_signal_months"
            ]
        ),
        minimum_eligible_names=int(
            layout.program["factor_readiness"][
                "minimum_eligible_names_per_covered_month"
            ]
        ),
        coverage_alternative=dict(
            layout.program["factor_readiness"].get("coverage_alternative", {})
        ),
    )
    _atomic_parquet(readiness, layout.factor_bundle_root / "factor_readiness.parquet")
    _atomic_csv(
        readiness,
        layout.factor_bundle_root / "factor_readiness.csv",
    )

    bundle_manifest = build_data_bundle_manifest(
        layout,
        factor_manifest=factor_manifest,
        readiness=readiness,
    )
    bundle_manifest_path = layout.derived_root / "data_bundle_manifest.json"
    _atomic_json(bundle_manifest, bundle_manifest_path)
    rebuild_research_catalog(
        catalog_path=layout.catalog_path,
        bundle_manifest_path=bundle_manifest_path,
        factor_registry_path=registry_path,
        factor_definition_registry_path=(
            layout.project_root
            / "config/research/cross_sectional_alpha/factor_definition_registry.csv"
        ),
        metric_registry_path=(
            layout.project_root
            / str(layout.program["fundamentals"]["metric_registry"])
        ),
        data_program_path=layout.program_path,
    )

    ready = readiness.loc[
        readiness["selection_status"].isin(
            ["ready_first_round", "ready_coverage_alternative"]
        ),
        "selected_factor_id",
    ].dropna()
    blocked = readiness.loc[
        readiness["registered_first_round"]
        & ~readiness["selection_status"].isin(
            ["ready_first_round", "ready_coverage_alternative"]
        ),
        "factor_id",
    ]
    summary = {
        "schema_version": "cross_sectional_alpha.factor_database_summary.v1",
        "data_bundle_id": str(layout.program["versions"]["data_bundle"]),
        "factor_build_version": str(layout.program["versions"]["factor_build"]),
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "formal_eligible": False,
        "signal_date_count": int(len(signal_dates)),
        "factor_count": int(factor_database["factor_id"].nunique()),
        "factor_database_rows": int(len(factor_database)),
        "eligible_rows": int(factor_database["eligible"].sum()),
        "ready_first_round_factor_ids": sorted(set(ready.astype(str))),
        "blocked_first_round_factor_ids": sorted(set(blocked.astype(str))),
        "performance_used_for_readiness": False,
        "deterministic_rebuild_passed": bool(
            deterministic_qa["deterministic_rebuild_passed"]
        ),
        "actual_future_input_invariance_passed": bool(
            causality_qa["actual_future_input_invariance_passed"]
        ),
        "coverage_alternative_map": dict(
            layout.program["factor_readiness"].get(
                "coverage_alternative", {}
            )
        ),
        "catalog_path": str(layout.catalog_path),
        "bundle_manifest_sha256": _sha256(bundle_manifest_path),
    }
    _atomic_json(summary, layout.derived_root / "data_quality_summary.json")
    _atomic_json(
        {
            "schema_version": "cross_sectional_alpha.factor_database_freeze.v1",
            "status": "frozen_data_ready",
            "formal_eligible": False,
            "data_bundle_id": str(layout.program["versions"]["data_bundle"]),
            "bundle_manifest_sha256": _sha256(bundle_manifest_path),
            "factor_content_sha256": str(factor_manifest["content_sha256"]),
            "summary": summary,
        },
        layout.derived_root / "FROZEN.json",
    )
    return summary


def evaluate_factor_readiness(
    factor_database: pd.DataFrame,
    active_registry: pd.DataFrame,
    *,
    evaluation_start: object,
    evaluation_end: object,
    member_coverage_minimum: float,
    minimum_covered_signal_months: int,
    minimum_eligible_names: int,
    coverage_alternative: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Apply frozen, data-only readiness gates without reading returns."""

    required_database = {"signal_date", "sid", "factor_id", "eligible"}
    missing_database = required_database.difference(factor_database.columns)
    if missing_database:
        raise ValueError(
            f"factor_database missing readiness columns: {sorted(missing_database)}"
        )
    required_registry = {"factor_id", "first_round_eligible", "data_family"}
    missing_registry = required_registry.difference(active_registry.columns)
    if missing_registry:
        raise ValueError(
            f"active_registry missing readiness columns: {sorted(missing_registry)}"
        )
    start = pd.Timestamp(evaluation_start).normalize()
    end = pd.Timestamp(evaluation_end).normalize()
    if start > end:
        raise ValueError("evaluation_start cannot exceed evaluation_end")
    frame = factor_database.copy()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"]).dt.normalize()
    frame = frame.loc[frame["signal_date"].between(start, end)].copy()
    if frame.empty:
        raise ValueError("evaluation factor database is empty")
    frame["eligible"] = frame["eligible"].astype(bool)
    alternatives = {
        str(key): str(value)
        for key, value in (coverage_alternative or {}).items()
    }
    alternative_of = {value: key for key, value in alternatives.items()}
    rows: list[dict[str, Any]] = []
    metrics: dict[str, dict[str, Any]] = {}
    for factor_id, group in frame.groupby("factor_id", sort=True):
        by_date = group.groupby("signal_date", sort=True).agg(
            member_count=("sid", "size"),
            eligible_names=("eligible", "sum"),
        )
        eligible_rows = int(group["eligible"].sum())
        total_rows = int(len(group))
        covered = by_date["eligible_names"].ge(minimum_eligible_names)
        metrics[str(factor_id)] = {
            "evaluation_rows": total_rows,
            "eligible_rows": eligible_rows,
            "evaluation_member_coverage": (
                eligible_rows / total_rows if total_rows else float("nan")
            ),
            "covered_signal_months": int(covered.sum()),
            "total_signal_months": int(len(by_date)),
            "minimum_observed_eligible_names": int(
                by_date["eligible_names"].min()
            ),
            "median_eligible_names": float(by_date["eligible_names"].median()),
            "maximum_eligible_names": int(by_date["eligible_names"].max()),
        }
    registry = active_registry.copy()
    registry["registered_first_round"] = registry["first_round_eligible"].map(
        _csv_boolean
    )
    for item in registry.sort_values("factor_id").itertuples(index=False):
        factor_id = str(item.factor_id)
        metric = metrics.get(
            factor_id,
            {
                "evaluation_rows": 0,
                "eligible_rows": 0,
                "evaluation_member_coverage": 0.0,
                "covered_signal_months": 0,
                "total_signal_months": 0,
                "minimum_observed_eligible_names": 0,
                "median_eligible_names": 0.0,
                "maximum_eligible_names": 0,
            },
        )
        member_coverage_passed = bool(
            metric["evaluation_member_coverage"] >= member_coverage_minimum
        )
        month_count_gate_passed = bool(
            metric["covered_signal_months"] >= minimum_covered_signal_months
        )
        gate_passed = member_coverage_passed and month_count_gate_passed
        registered = bool(item.registered_first_round)
        alternative = alternatives.get(factor_id, "")
        alternative_metrics = metrics.get(alternative)
        alternative_passed = bool(
            alternative
            and alternative_metrics is not None
            and alternative_metrics["evaluation_member_coverage"]
            >= member_coverage_minimum
            and alternative_metrics["covered_signal_months"]
            >= minimum_covered_signal_months
        )
        if registered and gate_passed:
            status = "ready_first_round"
            selected = factor_id
        elif registered and alternative_passed:
            status = "ready_coverage_alternative"
            selected = alternative
        elif registered:
            status = "blocked_data_readiness"
            selected = pd.NA
        elif gate_passed:
            status = "available_expanded_not_first_round"
            selected = pd.NA
        else:
            status = "blocked_expanded_data_readiness"
            selected = pd.NA
        failure_reasons: list[str] = []
        if not member_coverage_passed:
            failure_reasons.append("member_coverage_below_threshold")
        if not month_count_gate_passed:
            failure_reasons.append("covered_month_count_below_threshold")
        rows.append(
            {
                "factor_id": factor_id,
                "data_family": str(item.data_family),
                "registered_first_round": registered,
                "evaluation_start": start,
                "evaluation_end": end,
                "required_member_coverage": float(member_coverage_minimum),
                "required_covered_signal_months": int(
                    minimum_covered_signal_months
                ),
                "required_eligible_names_per_month": int(
                    minimum_eligible_names
                ),
                **metric,
                "member_coverage_passed": member_coverage_passed,
                "month_count_gate_passed": month_count_gate_passed,
                "coverage_gate_passed": gate_passed,
                "coverage_alternative_factor_id": alternative or pd.NA,
                "alternative_of_factor_id": alternative_of.get(
                    factor_id, pd.NA
                ),
                "coverage_alternative_passed": alternative_passed,
                "selection_status": status,
                "selected_factor_id": selected,
                "failure_reasons": "|".join(failure_reasons),
                "performance_used": False,
            }
        )
    return pd.DataFrame(rows).sort_values("factor_id", ignore_index=True)


def verify_factor_bundle_determinism(
    factor_database: pd.DataFrame,
    coverage_qa: Mapping[str, pd.DataFrame],
    *,
    expected_content_sha256: str,
    temporary_parent: str | Path,
) -> dict[str, Any]:
    """Independently serialize the same bundle and compare content hashes."""

    parent = Path(temporary_parent)
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="factor-determinism-", dir=parent
    ) as directory:
        rebuilt = write_factor_database_bundle(
            factor_database,
            Path(directory) / "factor_database",
            coverage_qa=coverage_qa,
        )
    observed = str(rebuilt["content_sha256"])
    passed = observed == str(expected_content_sha256)
    result = {
        "schema_version": "cross_sectional_alpha.deterministic_rebuild_qa.v1",
        "expected_content_sha256": str(expected_content_sha256),
        "rebuilt_content_sha256": observed,
        "deterministic_rebuild_passed": bool(passed),
        "input_row_count": int(len(factor_database)),
        "factor_computation_rebuilt_independently": True,
    }
    if not passed:
        raise CrossSectionalDatabaseError(
            "independent factor bundle serialization is not deterministic"
        )
    return result


def _load_current_factor_freeze(
    layout: DatabaseLayout,
) -> dict[str, Any] | None:
    freeze_path = layout.derived_root / "FROZEN.json"
    manifest_path = layout.derived_root / "data_bundle_manifest.json"
    summary_path = layout.derived_root / "data_quality_summary.json"
    try:
        frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if frozen.get("status") != "frozen_data_ready":
            return None
        manifest_sha = _sha256(manifest_path)
        if frozen.get("bundle_manifest_sha256") != manifest_sha:
            return None
        if summary.get("bundle_manifest_sha256") != manifest_sha:
            return None
        if frozen.get("summary") != summary:
            return None
        for item in manifest.get("components", []):
            path = Path(str(item["path"]))
            if not path.is_absolute():
                path = (manifest_path.parent / path).resolve()
            if not path.is_file() or _sha256(path) != str(item["sha256"]):
                return None
            if item.get("component_kind") == "parquet":
                import pyarrow.parquet as pq

                observed = int(pq.ParquetFile(path).metadata.num_rows)
                if observed != int(item["row_count"]):
                    return None
        if not layout.catalog_path.is_file():
            return None
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None
    return dict(summary)


def verify_actual_future_input_invariance(
    layout: DatabaseLayout,
    *,
    factor_database: pd.DataFrame,
    active_registry: pd.DataFrame,
    signal_dates: pd.DatetimeIndex,
    fact_events: pd.DataFrame,
    mappings: pd.DataFrame,
    membership: pd.DataFrame,
) -> dict[str, Any]:
    """Rebuild historical cross-sections after physically truncating inputs."""

    evaluation_start = pd.Timestamp(
        layout.program["sample"]["evaluation_start"]
    ).normalize()
    candidates = signal_dates[signal_dates >= evaluation_start]
    if len(candidates) < 3:
        raise CrossSectionalDatabaseError(
            "not enough evaluation signals for actual causality audit"
        )
    checkpoints = pd.DatetimeIndex(
        sorted({candidates[0], candidates[len(candidates) // 2]})
    )
    volume_qa = json.loads(
        (layout.derived_root / "market_volume_qa.json").read_text(
            encoding="utf-8"
        )
    )
    results: list[dict[str, Any]] = []
    for cutoff in checkpoints:
        prices = pd.read_parquet(
            layout.market_root / "prices_daily.parquet",
            filters=[("date", "<=", cutoff)],
        )
        benchmark = pd.read_parquet(
            layout.market_root / "benchmark_daily.parquet",
            filters=[("date", "<=", cutoff)],
        )
        calendar = pd.read_parquet(layout.market_root / "calendar.parquet")
        calendar_dates = pd.to_datetime(calendar["session_date"]).dt.normalize()
        calendar = calendar.loc[calendar_dates.le(cutoff)].copy()
        truncated_market = materialize_cross_sectional_market_factors(
            prices,
            benchmark,
            calendar,
            membership,
            [cutoff],
            factor_ids=MARKET_FACTOR_IDS,
            volume_qa_passed=bool(volume_qa["volume_qa_passed"]),
        )
        available = pd.to_datetime(
            fact_events["available_session"], errors="coerce"
        ).dt.normalize()
        truncated_facts = fact_events.loc[available.le(cutoff)].copy()
        truncated_fundamental = compute_fundamental_factor_panel(
            truncated_facts,
            mappings,
            [cutoff],
            issuer_market_equity=None,
        ).reset_index()
        rebuilt = build_factor_database(
            truncated_market,
            truncated_fundamental,
            membership,
            [cutoff],
            active_registry,
        )
        expected = factor_database.loc[
            pd.to_datetime(factor_database["signal_date"]).dt.normalize().eq(
                cutoff
            )
        ].reset_index(drop=True)
        try:
            pd.testing.assert_frame_equal(
                rebuilt,
                expected,
                check_dtype=True,
                check_exact=True,
            )
        except AssertionError as exc:
            raise CrossSectionalDatabaseError(
                f"future-input invariance failed at {cutoff.date()}: {exc}"
            ) from exc
        digest = hashlib.sha256(
            pd.util.hash_pandas_object(
                rebuilt.astype("string"), index=False
            ).to_numpy().tobytes()
        ).hexdigest()
        results.append(
            {
                "cutoff_signal_date": cutoff.date().isoformat(),
                "compared_rows": int(len(rebuilt)),
                "cross_section_sha256": digest,
                "future_rows_physically_removed": True,
                "exact_match": True,
            }
        )
        del prices, benchmark, calendar, truncated_market, truncated_facts
        del truncated_fundamental, rebuilt, expected
    return {
        "schema_version": "cross_sectional_alpha.actual_causality_qa.v1",
        "method": "physical_future_truncation_and_exact_cross_section_rebuild",
        "checkpoint_count": len(results),
        "checks": results,
        "actual_future_input_invariance_passed": True,
    }


def build_data_bundle_manifest(
    layout: DatabaseLayout,
    *,
    factor_manifest: Mapping[str, Any],
    readiness: pd.DataFrame,
) -> dict[str, Any]:
    """Describe every authoritative Parquet/manifest component for the catalog."""

    del readiness  # content is referenced from the written readiness artifact
    component_specs = [
        ("market_prices_daily", layout.market_root / "prices_daily.parquet", "parquet", "v_market_prices_daily", layout.program["versions"]["market_dataset"]),
        ("market_membership", layout.market_root / "membership.parquet", "parquet", "v_market_membership", layout.program["versions"]["market_dataset"]),
        ("market_calendar", layout.market_root / "calendar.parquet", "parquet", "v_market_calendar", layout.program["versions"]["market_dataset"]),
        ("market_benchmark", layout.market_root / "benchmark_daily.parquet", "parquet", "v_market_benchmark", layout.program["versions"]["market_dataset"]),
        ("entity_cik_intervals", layout.curated_root / "entity_cik_intervals.parquet", "parquet", "v_entity_cik_intervals", layout.program["versions"]["entity_bridge"]),
        ("sec_filings", layout.curated_root / "filings.parquet", "parquet", "v_sec_filings", layout.program["versions"]["fundamentals"]),
        ("sec_registered_facts", layout.curated_root / "registered_facts.parquet", "parquet", "v_sec_registered_facts", layout.program["versions"]["fundamentals"]),
        ("sec_canonical_annual_facts", layout.curated_root / "canonical_annual_facts.parquet", "parquet", "v_sec_canonical_annual_facts", layout.program["versions"]["fundamentals"]),
        ("sec_source_applicability", layout.curated_root / "source_applicability.parquet", "parquet", "v_sec_source_applicability", layout.program["versions"]["fundamentals"]),
        ("market_factor_panel", layout.derived_root / "market_factor_panel.parquet", "parquet", "v_market_factor_panel", layout.program["versions"]["factor_build"]),
        ("fundamental_factor_panel", layout.derived_root / "fundamental_factor_panel.parquet", "parquet", "v_fundamental_factor_panel", layout.program["versions"]["factor_build"]),
        ("factor_values", layout.factor_bundle_root / "factor_values.parquet", "parquet", "v_factor_values", layout.program["versions"]["factor_build"]),
        ("factor_coverage", layout.factor_bundle_root / "factor_coverage.parquet", "parquet", "v_factor_coverage", layout.program["versions"]["factor_build"]),
        ("factor_date_coverage", layout.factor_bundle_root / "date_coverage.parquet", "parquet", "v_factor_date_coverage", layout.program["versions"]["factor_build"]),
        ("factor_year_coverage", layout.factor_bundle_root / "year_coverage.parquet", "parquet", "v_factor_year_coverage", layout.program["versions"]["factor_build"]),
        ("factor_missing_reason_coverage", layout.factor_bundle_root / "missing_reason_coverage.parquet", "parquet", "v_factor_missing_reason_coverage", layout.program["versions"]["factor_build"]),
        ("factor_readiness", layout.factor_bundle_root / "factor_readiness.parquet", "parquet", "v_factor_readiness", layout.program["versions"]["factor_build"]),
        ("deterministic_rebuild_qa", layout.derived_root / "deterministic_rebuild_qa.json", "json", "", layout.program["versions"]["factor_build"]),
        ("causality_qa", layout.derived_root / "causality_qa.json", "json", "", layout.program["versions"]["factor_build"]),
        ("factor_content_manifest", layout.factor_bundle_root / "factor_content_manifest.json", "manifest", "", layout.program["versions"]["factor_build"]),
        ("fundamental_manifest", layout.curated_root / "manifest.json", "manifest", "", layout.program["versions"]["fundamentals"]),
        ("fundamental_freeze", layout.curated_root / "FROZEN.json", "json", "", layout.program["versions"]["fundamentals"]),
        ("fundamental_cik_manifest_index", layout.curated_root / "cik_manifest_index.json", "json", "", layout.program["versions"]["fundamentals"]),
        ("fundamental_accounting_identity", layout.curated_root / "accounting_identity_qa.parquet", "parquet", "v_fundamental_accounting_identity", layout.program["versions"]["fundamentals"]),
        ("entity_temporal_support", layout.curated_root / "entity_temporal_support_qa.parquet", "parquet", "v_entity_temporal_support", layout.program["versions"]["fundamentals"]),
        ("entity_temporal_support_summary", layout.curated_root / "entity_temporal_support_summary.json", "json", "", layout.program["versions"]["fundamentals"]),
        ("identifier_manifest", layout.identifier_root / "manifest.json", "manifest", "", layout.program["versions"]["entity_bridge"]),
        ("identifier_mapping_qa", layout.identifier_root / "mapping_qa.json", "json", "", layout.program["versions"]["entity_bridge"]),
        ("market_freeze", layout.market_root / "FROZEN.json", "json", "", layout.program["versions"]["market_dataset"]),
        ("data_program", layout.program_path, "toml", "", layout.program["program_id"]),
        ("active_factor_registry", layout.project_root / str(layout.program["factors"]["registry"]), "csv", "", layout.program["versions"]["factor_build"]),
        ("factor_definition_registry", layout.project_root / "config/research/cross_sectional_alpha/factor_definition_registry.csv", "csv", "", layout.program["versions"]["factor_build"]),
        ("sec_metric_registry", layout.project_root / str(layout.program["fundamentals"]["metric_registry"]), "csv", "", layout.program["versions"]["fundamentals"]),
        ("sec_companyfacts_exceptions", layout.project_root / str(layout.program["fundamentals"]["companyfacts_exceptions"]), "csv", "", layout.program["versions"]["fundamentals"]),
        ("sec_cik_overrides", layout.project_root / str(layout.program["identifier_resolution"]["manual_overrides_path"]), "csv", "", layout.program["versions"]["entity_bridge"]),
        ("code_market_factor", layout.project_root / "src/momentum_reversal/factors/cross_sectional_market.py", "python", "", layout.program["versions"]["factor_build"]),
        ("code_fundamental_factor", layout.project_root / "src/momentum_reversal/factors/cross_sectional_fundamental.py", "python", "", layout.program["versions"]["factor_build"]),
        ("code_factor_database", layout.project_root / "src/momentum_reversal/data/factor_database.py", "python", "", layout.program["versions"]["factor_build"]),
        ("code_sec_fundamental", layout.project_root / "src/momentum_reversal/data/sec_fundamental_pipeline.py", "python", "", layout.program["versions"]["fundamentals"]),
        ("code_fundamental_store", layout.project_root / "src/momentum_reversal/data/fundamental_store.py", "python", "", layout.program["versions"]["fundamentals"]),
        ("code_sec_edgar", layout.project_root / "src/momentum_reversal/data/sec_edgar.py", "python", "", layout.program["versions"]["fundamentals"]),
        ("code_entity_bridge", layout.project_root / "src/momentum_reversal/data/entity_bridge.py", "python", "", layout.program["versions"]["entity_bridge"]),
        ("code_entity_temporal_audit", layout.project_root / "src/momentum_reversal/data/entity_temporal_audit.py", "python", "", layout.program["versions"]["entity_bridge"]),
        ("code_database_pipeline", Path(__file__).resolve(), "python", "", layout.program["versions"]["factor_build"]),
    ]
    components: list[dict[str, Any]] = []
    for component_id, path, kind, view_name, version in component_specs:
        if not path.is_file():
            raise FileNotFoundError(path)
        row_count = None
        if kind == "parquet":
            import pyarrow.parquet as pq

            row_count = int(pq.ParquetFile(path).metadata.num_rows)
        component = {
            "component_id": component_id,
            "component_kind": kind,
            "path": Path(
                os.path.relpath(path.resolve(), layout.derived_root.resolve())
            ).as_posix(),
            "sha256": _sha256(path),
            "row_count": row_count,
            "source_version": str(version),
        }
        if view_name:
            component["view_name"] = view_name
        components.append(component)
    stable = {
        "schema_version": "cross_sectional_alpha.data_bundle_manifest.v1",
        "data_bundle_id": str(layout.program["versions"]["data_bundle"]),
        "versions": dict(layout.program["versions"]),
        "program": {
            "path": Path(
                os.path.relpath(
                    layout.program_path.resolve(), layout.derived_root.resolve()
                )
            ).as_posix(),
            "sha256": _sha256(layout.program_path),
        },
        "parquet_is_evidence": True,
        "duckdb_is_rebuildable_catalog": True,
        "formal_eligible": False,
        "factor_content_sha256": str(factor_manifest["content_sha256"]),
        "components": sorted(components, key=lambda item: item["component_id"]),
    }
    encoded = json.dumps(
        stable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return {
        **stable,
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _csv_boolean(value: object) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise ValueError(f"invalid CSV boolean: {value!r}")


def load_sec_companyfacts_exceptions(
    path: str | Path,
) -> dict[str, dict[str, object]]:
    """Load the closed, code-reviewed Company Facts exception registry.

    This is intentionally not an extensible "ignore 404" switch.  Both the
    machine-readable registry and this code-level allow-list must agree, so a
    new issuer requires an explicit code review as well as a CSV edit.
    """

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    frame = pd.read_csv(source, dtype=str, keep_default_na=False)
    required = {
        "cik10",
        "ticker",
        "resolution",
        "required_http_status",
        "required_error_code",
        "periodic_form_bases",
        "include_amendments",
        "required_periodic_form_count",
        "review_status",
        "reviewed_date",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise CrossSectionalDatabaseError(
            "Company Facts exception registry missing columns: "
            f"{sorted(missing)}"
        )
    if frame.empty:
        raise CrossSectionalDatabaseError(
            "Company Facts exception registry cannot be empty"
        )

    records: dict[str, dict[str, object]] = {}
    for row in frame.itertuples(index=False):
        try:
            cik10 = normalize_cik(row.cik10)
            http_status = int(str(row.required_http_status).strip())
            required_periodic_count = int(
                str(row.required_periodic_form_count).strip()
            )
            include_amendments = _csv_boolean(row.include_amendments)
        except (SECParseError, TypeError, ValueError) as exc:
            raise CrossSectionalDatabaseError(
                "invalid Company Facts exception registry row: "
                f"cik={getattr(row, 'cik10', '')!r}"
            ) from exc
        if cik10 in records:
            raise CrossSectionalDatabaseError(
                f"duplicate Company Facts exception CIK: {cik10}"
            )
        ticker = str(row.ticker).strip().upper()
        reviewed_date = str(row.reviewed_date).strip()
        try:
            parsed_reviewed_date = pd.Timestamp(reviewed_date)
        except ValueError:
            parsed_reviewed_date = pd.NaT
        if (
            ticker != _REVIEWED_COMPANYFACTS_NOT_APPLICABLE.get(cik10)
            or str(row.resolution).strip() != "resolved_not_applicable"
            or http_status != 404
            or str(row.required_error_code).strip() != "NoSuchKey"
            or str(row.periodic_form_bases).strip()
            != "|".join(_PERIODIC_FORM_BASES)
            or not include_amendments
            or required_periodic_count != 0
            or str(row.review_status).strip().lower() != "approved"
            or not reviewed_date
            or pd.isna(parsed_reviewed_date)
        ):
            raise CrossSectionalDatabaseError(
                "Company Facts exception row is not an approved strict "
                f"NoSuchKey/zero-periodic-forms rule: {cik10}"
            )
        records[cik10] = {
            "cik10": cik10,
            "ticker": ticker,
            "resolution": "resolved_not_applicable",
            "required_http_status": http_status,
            "required_error_code": "NoSuchKey",
            "periodic_form_bases": "|".join(_PERIODIC_FORM_BASES),
            "include_amendments": True,
            "required_periodic_form_count": required_periodic_count,
            "review_status": "approved",
            "reviewed_date": reviewed_date,
        }
    if set(records) != set(_REVIEWED_COMPANYFACTS_NOT_APPLICABLE):
        raise CrossSectionalDatabaseError(
            "Company Facts exception registry must contain exactly the "
            "code-reviewed FRC and SBNY CIKs"
        )
    return records


def _companyfacts_not_applicable_resolution(
    *,
    cik10: str,
    facts_url: str,
    facts_record: Any,
    submissions_root: object,
    submissions_history: Mapping[str, object],
    exceptions: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """Validate every prerequisite for one cached NoSuchKey resolution."""

    expected_cik = normalize_cik(cik10)
    exception = exceptions.get(expected_cik)
    if exception is None:
        raise CrossSectionalDatabaseError(
            "cached Company Facts HTTP 404 is not in the reviewed exception "
            f"registry: {expected_cik}"
        )
    if int(facts_record.status) != int(exception["required_http_status"]):
        raise CrossSectionalDatabaseError(
            f"Company Facts exception HTTP status mismatch for {expected_cik}"
        )
    if (
        str(facts_record.requested_url) != facts_url
        or str(facts_record.response_url) != facts_url
    ):
        raise CrossSectionalDatabaseError(
            f"Company Facts exception URL mismatch for {expected_cik}"
        )
    content_type = str(
        dict(facts_record.response_headers).get("content-type", "")
    ).split(";", 1)[0].strip().lower()
    if content_type not in {"application/xml", "text/xml"}:
        raise CrossSectionalDatabaseError(
            f"Company Facts exception is not an official XML error for {expected_cik}"
        )
    raw_path = Path(facts_record.raw_path)
    try:
        raw_size = raw_path.stat().st_size
        raw_sha256 = _sha256(raw_path)
    except OSError:
        raise CrossSectionalDatabaseError(
            f"Company Facts exception raw evidence is unreadable for {expected_cik}"
        ) from None
    if (
        raw_size != int(facts_record.size_bytes)
        or raw_sha256 != str(facts_record.sha256)
    ):
        raise CrossSectionalDatabaseError(
            f"Company Facts exception raw evidence hash mismatch for {expected_cik}"
        )
    try:
        error_root = ET.fromstring(_decoded_cached_body(facts_record))
    except (ET.ParseError, UnicodeError):
        raise CrossSectionalDatabaseError(
            f"Company Facts exception XML is invalid for {expected_cik}"
        ) from None
    error_codes = [
        str(element.text).strip()
        for element in error_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "Code"
        and element.text is not None
    ]
    object_keys = [
        str(element.text).strip().lstrip("/")
        for element in error_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "Key"
        and element.text is not None
    ]
    expected_key = f"api/xbrl/companyfacts/CIK{expected_cik}.json"
    if error_codes != [str(exception["required_error_code"])] or object_keys != [
        expected_key
    ]:
        raise CrossSectionalDatabaseError(
            f"Company Facts exception is not the expected NoSuchKey for {expected_cik}"
        )

    if not isinstance(submissions_root, Mapping) or "cik" not in submissions_root:
        raise CrossSectionalDatabaseError(
            f"submissions root lacks a CIK for Company Facts exception {expected_cik}"
        )
    try:
        submissions_cik = normalize_cik(submissions_root["cik"])
    except SECParseError as exc:
        raise CrossSectionalDatabaseError(
            f"submissions root has an invalid CIK for {expected_cik}"
        ) from exc
    if submissions_cik != expected_cik:
        raise CrossSectionalDatabaseError(
            "submissions/Company Facts exception CIK mismatch: "
            f"expected={expected_cik}, observed={submissions_cik}"
        )
    filings = parse_submissions(submissions_root, submissions_history)
    normalized_forms = filings["form"].astype(str).str.upper().str.strip()
    periodic = filings.loc[normalized_forms.isin(_PERIODIC_FORMS)].copy()
    required_count = int(exception["required_periodic_form_count"])
    if len(periodic) != required_count:
        observed = sorted(periodic["form"].astype(str).unique())
        raise CrossSectionalDatabaseError(
            "Company Facts exception periodic-form condition failed for "
            f"{expected_cik}: count={len(periodic)}, forms={observed}"
        )
    return {
        "schema_version": (
            "cross_sectional_alpha.sec_source_applicability.v1"
        ),
        "cik10": expected_cik,
        "ticker": str(exception["ticker"]),
        "source": "sec_companyfacts",
        "status": "resolved_not_applicable",
        "reason_code": "cached_404_no_such_key_and_zero_periodic_forms",
        "explicit_missing": True,
        "fact_value_state": "missing_source_not_applicable",
        "imputation_policy": "none",
        "imputation_applied": False,
        "imputed_fact_rows": 0,
        "submissions_cik10": submissions_cik,
        "periodic_form_count": int(len(periodic)),
        "periodic_form_bases": str(exception["periodic_form_bases"]),
        "periodic_form_amendments_included": bool(
            exception["include_amendments"]
        ),
        "companyfacts_http_status": int(facts_record.status),
        "companyfacts_error_code": error_codes[0],
        "companyfacts_object_key": object_keys[0],
        "companyfacts_raw_record_id": str(facts_record.record_id),
        "companyfacts_raw_sha256": raw_sha256,
        "companyfacts_raw_size_bytes": int(raw_size),
        "exception_review_status": str(exception["review_status"]),
        "exception_reviewed_date": str(exception["reviewed_date"]),
    }


def _available_companyfacts_applicability(
    cik10: str, facts_record: Any
) -> dict[str, object]:
    return {
        "schema_version": (
            "cross_sectional_alpha.sec_source_applicability.v1"
        ),
        "cik10": normalize_cik(cik10),
        "ticker": None,
        "source": "sec_companyfacts",
        "status": "available",
        "reason_code": "companyfacts_http_success",
        "explicit_missing": False,
        "fact_value_state": "observed_source_available",
        "imputation_policy": "none",
        "imputation_applied": False,
        "imputed_fact_rows": 0,
        "submissions_cik10": normalize_cik(cik10),
        "periodic_form_count": None,
        "periodic_form_bases": "|".join(_PERIODIC_FORM_BASES),
        "periodic_form_amendments_included": True,
        "companyfacts_http_status": int(facts_record.status),
        "companyfacts_error_code": None,
        "companyfacts_object_key": None,
        "companyfacts_raw_record_id": str(facts_record.record_id),
        "companyfacts_raw_sha256": str(facts_record.sha256),
        "companyfacts_raw_size_bytes": int(facts_record.size_bytes),
        "exception_review_status": None,
        "exception_reviewed_date": None,
    }


def _source_applicability_qa(
    coverage_qa: pd.DataFrame,
    source_applicability: Mapping[str, object],
) -> pd.DataFrame:
    coverage = coverage_qa.copy()
    coverage["source_applicability"] = str(source_applicability["status"])
    source_row = pd.DataFrame(
        [
            {
                "check_id": "companyfacts_source_applicability",
                "group": "source",
                "numerator": 1,
                "denominator": 1,
                "coverage": 1.0,
                "status": "pass",
                "source_applicability": str(source_applicability["status"]),
            }
        ]
    )
    return pd.concat([coverage, source_row], ignore_index=True, sort=False)


def _research_xnys_schedule() -> pd.DataFrame:
    try:
        import exchange_calendars as xcals
    except ImportError as exc:  # pragma: no cover - environment guard
        raise CrossSectionalDatabaseError(
            "exchange_calendars is required for SEC availability mapping"
        ) from exc
    calendar = xcals.get_calendar("XNYS")
    schedule = calendar.schedule.loc[
        (calendar.schedule.index >= pd.Timestamp("2009-01-01"))
        & (calendar.schedule.index <= pd.Timestamp("2027-01-15"))
    ].copy()
    return schedule


def _registered_companyfacts(
    facts: pd.DataFrame, metric_registry: pd.DataFrame
) -> pd.DataFrame:
    pairs: set[tuple[str, str]] = set()
    for row in metric_registry.itertuples(index=False):
        for tag in str(row.tag_priority).split("|"):
            pairs.add((str(row.taxonomy), tag.strip()))
    if facts.empty:
        return facts.copy()
    mask = [
        (str(taxonomy), str(tag)) in pairs
        for taxonomy, tag in zip(facts["taxonomy"], facts["tag"], strict=True)
    ]
    return facts.loc[mask].reset_index(drop=True)


def _write_cik_bundle(
    destination: Path,
    *,
    cik10: str,
    filings: pd.DataFrame,
    registered_facts: pd.DataFrame,
    canonical: pd.DataFrame,
    coverage_qa: pd.DataFrame,
    raw_records: Iterable[Any],
    build_signature: str,
    source_applicability: Mapping[str, object],
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if (
        normalize_cik(source_applicability.get("cik10")) != normalize_cik(cik10)
        or source_applicability.get("status")
        not in {"available", "resolved_not_applicable"}
        or bool(source_applicability.get("imputation_applied"))
        or int(source_applicability.get("imputed_fact_rows", -1)) != 0
    ):
        raise CrossSectionalDatabaseError(
            f"invalid source applicability for CIK {cik10}"
        )
    coverage = coverage_qa.copy()
    if "cik10" not in coverage.columns:
        coverage.insert(0, "cik10", cik10)
    _atomic_parquet(filings, destination / "filings.parquet")
    _atomic_parquet(registered_facts, destination / "registered_facts.parquet")
    _atomic_parquet(canonical, destination / "canonical_annual_facts.parquet")
    _atomic_parquet(coverage, destination / "coverage_qa.parquet")
    _atomic_json(
        dict(source_applicability),
        destination / "source_applicability.json",
    )
    manifest = _directory_manifest(
        destination,
        identifier="cross_sectional_alpha.fundamental_cik_manifest.v1",
        extra={
            "cik10": cik10,
            "fundamental_build_signature": build_signature,
            "source_applicability": dict(source_applicability),
            "raw_records": [
                {
                    "record_id": record.record_id,
                    "requested_url": record.requested_url,
                    "response_url": record.response_url,
                    "status": int(record.status),
                    "sha256": record.sha256,
                    "size_bytes": int(record.size_bytes),
                }
                for record in raw_records
            ],
        },
    )
    _atomic_json(manifest, destination / "manifest.json")


def _cik_bundle_valid(
    destination: Path, *, build_signature: str | None = None
) -> bool:
    manifest_path = destination / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            build_signature is not None
            and manifest.get("fundamental_build_signature") != build_signature
        ):
            return False
        for item in manifest["files"]:
            path = destination / str(item["path"])
            if (
                not path.is_file()
                or path.stat().st_size != int(item["size_bytes"])
                or _sha256(path) != str(item["sha256"])
            ):
                return False
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return True


def _fundamental_build_signature(layout: DatabaseLayout) -> str:
    """Hash every local input that can change canonical SEC outputs."""

    inputs = [
        layout.program_path,
        layout.project_root
        / str(layout.program["fundamentals"]["metric_registry"]),
        layout.project_root
        / str(
            layout.program["fundamentals"]["companyfacts_exceptions"]
        ),
        layout.project_root
        / str(
            layout.program["identifier_resolution"]["manual_overrides_path"]
        ),
        layout.project_root / "src/momentum_reversal/data/entity_bridge.py",
        layout.project_root / "src/momentum_reversal/data/fundamental_store.py",
        layout.project_root
        / "src/momentum_reversal/data/sec_fundamental_pipeline.py",
        layout.project_root / "src/momentum_reversal/data/sec_edgar.py",
        layout.project_root
        / "src/momentum_reversal/data/entity_temporal_audit.py",
        Path(__file__).resolve(),
    ]
    identifier_manifest = layout.identifier_root / "manifest.json"
    if identifier_manifest.is_file():
        inputs.append(identifier_manifest)
    material: list[dict[str, str]] = [
        {
            "path": "<fundamental_build_logic_version>",
            "sha256": hashlib.sha256(
                FUNDAMENTAL_BUILD_LOGIC_VERSION.encode("utf-8")
            ).hexdigest(),
        }
    ]
    for path in inputs:
        try:
            logical_path = (
                "project/" + path.relative_to(layout.project_root).as_posix()
            )
        except ValueError:
            try:
                logical_path = (
                    "runtime/" + path.relative_to(layout.runtime_root).as_posix()
                )
            except ValueError as exc:
                raise CrossSectionalDatabaseError(
                    f"build-signature input is outside project/runtime: {path}"
                ) from exc
        material.append(
            {"path": logical_path, "sha256": _sha256(path)}
        )
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _current_fundamental_freeze(
    layout: DatabaseLayout, build_signature: str
) -> Mapping[str, Any] | None:
    """Return an already valid full freeze, otherwise report no match."""

    freeze_path = layout.curated_root / "FROZEN.json"
    manifest_path = layout.curated_root / "manifest.json"
    summary_path = layout.curated_root / "build_summary.json"
    if not freeze_path.is_file():
        return None
    try:
        frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
        if frozen.get("status") != "frozen_complete":
            return None
        if frozen.get("fundamental_build_signature") != build_signature:
            return None
        if not manifest_path.is_file() or not summary_path.is_file():
            return None
        if frozen.get("manifest_sha256") != _sha256(manifest_path):
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("fundamental_build_signature") != build_signature:
            return None
        if frozen.get("content_sha256") != manifest.get("content_sha256"):
            return None
        raw_files = manifest.get("files")
        if not isinstance(raw_files, list):
            return None
        member_names: set[str] = set()
        for item in raw_files:
            if not isinstance(item, Mapping):
                return None
            name = str(item.get("path", ""))
            if not name or Path(name).name != name or name in member_names:
                return None
            member = (layout.curated_root / name).resolve()
            try:
                member.relative_to(layout.curated_root.resolve())
            except ValueError:
                return None
            if (
                not member.is_file()
                or member.stat().st_size != int(item.get("size_bytes", -1))
                or _sha256(member) != str(item.get("sha256", ""))
            ):
                return None
            member_names.add(name)
        actual_names = {
            path.name
            for path in layout.curated_root.iterdir()
            if path.is_file()
            and path.name
            not in {"manifest.json", "FROZEN.json", "build_summary.json"}
            and not path.name.startswith(".")
        }
        if actual_names != member_names:
            return None
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary != frozen.get("build_summary"):
            return None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return frozen


def _load_fundamental_freeze_fail_closed(
    layout: DatabaseLayout, build_signature: str
) -> Mapping[str, Any] | None:
    """Reuse a valid freeze and refuse to overwrite a drifted formal freeze.

    An incomplete build marker may be resumed. Once a version has reached
    ``frozen_complete``, any code, configuration, manifest, summary, or member
    drift requires a new fundamental version instead of an in-place rebuild.
    """

    freeze_path = layout.curated_root / "FROZEN.json"
    if not freeze_path.is_file():
        return None
    try:
        marker = json.loads(freeze_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CrossSectionalDatabaseError(
            "fundamental FROZEN marker is unreadable; refusing overwrite"
        ) from exc
    if not isinstance(marker, Mapping):
        raise CrossSectionalDatabaseError(
            "fundamental FROZEN marker is invalid; refusing overwrite"
        )
    frozen = _current_fundamental_freeze(layout, build_signature)
    if frozen is not None:
        return frozen
    status = str(marker.get("status", ""))
    if status == "incomplete_or_quality_gate_failed":
        return None
    raise CrossSectionalDatabaseError(
        "fundamental version has an existing FROZEN marker with drift; "
        "refusing overwrite and requiring a new fundamentals version"
    )


def _concat_cik_table(directories: Iterable[Path], filename: str) -> pd.DataFrame:
    paths = [path / filename for path in directories]
    if not paths:
        return pd.DataFrame()
    frames = [pd.read_parquet(path) for path in paths]
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True, sort=False)
    sort_columns = [
        column
        for column in ("cik", "accepted_at", "accession", "metric_id", "check_id")
        if column in combined.columns
    ]
    if sort_columns:
        combined = combined.sort_values(sort_columns, ignore_index=True)
    return combined


def _cached_sec_json(
    client: SECClient,
    store: ImmutableFetchStore,
    url: str,
    *,
    refresh: bool,
) -> tuple[object, Any]:
    record = None if refresh else _reusable_sec_record(store, url)
    if record is not None:
        if not 200 <= int(record.status) < 300:
            raise CrossSectionalDatabaseError(
                f"cached SEC request failed with HTTP {record.status}: {url}"
            )
        try:
            return json.loads(_decoded_cached_body(record).decode("utf-8-sig")), record
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise CrossSectionalDatabaseError(
                f"cached SEC JSON is invalid: {record.raw_path}"
            ) from None
    return client.get_json(url)


def _cached_sec_text(
    client: SECClient,
    store: ImmutableFetchStore,
    url: str,
    *,
    refresh: bool,
) -> tuple[str, Any]:
    record = None if refresh else _reusable_sec_record(store, url)
    if record is not None:
        if not 200 <= int(record.status) < 300:
            raise CrossSectionalDatabaseError(
                f"cached SEC request failed with HTTP {record.status}: {url}"
            )
        try:
            return _decoded_cached_body(record).decode("utf-8-sig"), record
        except (OSError, UnicodeDecodeError):
            raise CrossSectionalDatabaseError(
                f"cached SEC text is invalid: {record.raw_path}"
            ) from None
    return client.get_text(url)


def _decoded_cached_body(record: Any) -> bytes:
    try:
        body = record.raw_path.read_bytes()
    except OSError:
        raise CrossSectionalDatabaseError(
            f"cached SEC response cannot be read: {record.raw_path}"
        ) from None
    fetched = FetchedResponse(
        response=SECResponse(
            status=int(record.status),
            body=body,
            headers=dict(record.response_headers),
            url=str(record.response_url),
        ),
        record=record,
    )
    return fetched.decoded_body()


def _latest_successful_record(
    store: ImmutableFetchStore, url: str
) -> Any | None:
    records = [
        item
        for item in store.ledger_records()
        if item.requested_url == url and 200 <= int(item.status) < 300
    ]
    if not records:
        return None
    return sorted(
        records,
        key=lambda item: (item.retrieved_at_utc, item.record_id),
    )[-1]


def _reusable_sec_record(store: ImmutableFetchStore, url: str) -> Any | None:
    """Reuse a successful response, or a captured hard failure if none exists."""

    successful = _latest_successful_record(store, url)
    if successful is not None:
        return successful
    return _latest_record_for_url(store, url)


def _latest_record_for_url(store: ImmutableFetchStore, url: str) -> Any | None:
    records = [
        item for item in store.ledger_records() if item.requested_url == url
    ]
    if not records:
        return None
    return sorted(
        records,
        key=lambda item: (item.retrieved_at_utc, item.record_id),
    )[-1]


def _browse_ticker_url(base: str, ticker: str) -> str:
    query = urlencode(
        {
            "action": "getcompany",
            "CIK": ticker,
            "owner": "exclude",
            "count": "10",
            "output": "atom",
        },
        quote_via=quote,
    )
    return f"{base}?{query}"


def _load_overrides(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if frame.empty:
        return frame
    return frame.loc[frame["sid"].str.strip().ne("")].copy()


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.building")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, path)


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.building")
    frame.to_csv(temporary, index=False, lineterminator="\n")
    os.replace(temporary, path)


def _atomic_json(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        default=_json_default,
    ) + "\n"
    temporary = path.with_name(f".{path.name}.building")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _directory_manifest(
    root: Path,
    *,
    identifier: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(root.glob("*"), key=lambda item: item.name):
        if (
            not path.is_file()
            or path.name in {"manifest.json", "FROZEN.json", "build_summary.json"}
            or path.name.startswith(".")
        ):
            continue
        files.append(
            {
                "path": path.name,
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    stable: dict[str, Any] = {
        "schema_version": identifier,
        "files": files,
    }
    if extra:
        stable.update(dict(extra))
    encoded = json.dumps(
        stable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return {
        **stable,
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def _json_default(value: object) -> object:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


__all__ = [
    "CrossSectionalDatabaseError",
    "DatabaseLayout",
    "IdentifierCoverageError",
    "build_fundamental_stage",
    "build_factor_stage",
    "build_identifier_stage",
    "build_market_factor_stage",
    "build_market_volume_qa",
    "build_data_bundle_manifest",
    "evaluate_factor_readiness",
    "load_identifier_bridge",
    "load_identifier_intervals",
    "load_sec_companyfacts_exceptions",
]
