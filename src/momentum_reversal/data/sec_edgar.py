"""SEC EDGAR acquisition, immutable raw storage, and point-in-time parsers.

The network client deliberately uses only the Python standard library and
requires an identifying ``User-Agent``.  A transport, clock, sleeper, and rate
limiter can all be injected so tests never require network access.  Parsing is
kept in pure functions; no parser reads from or writes to the filesystem.

The public EDGAR JSON endpoints aggregate multiple filing vintages.  This
module therefore keeps each accession as a separate event and joins XBRL facts
to the filing's acceptance timestamp before any as-of selection occurs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import math
import numbers
from pathlib import Path
import re
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zlib

import pandas as pd


DEFAULT_SEC_RATE_LIMIT_PER_SECOND = 5.0
DEFAULT_SEC_COOLDOWN_SECONDS = 10 * 60
DEFAULT_SEC_TIMEOUT_SECONDS = 60.0
_ALLOWED_SEC_HOSTS = frozenset({"data.sec.gov", "www.sec.gov", "sec.gov"})
_ACCESSION_PATTERN = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,31}$")

FILING_LEDGER_COLUMNS = (
    "cik",
    "accession",
    "form",
    "filed",
    "reportDate",
    "acceptanceDateTime",
    "primaryDocument",
    "report_date",
    "accepted_at",
    "primary_document",
    "is_amendment",
    "source_shard",
)

NUMERIC_FACT_COLUMNS = (
    "cik",
    "taxonomy",
    "concept",
    "label",
    "description",
    "unit",
    "start",
    "end",
    "value",
    "accession",
    "fy",
    "fp",
    "form",
    "fact_form",
    "filing_form",
    "form_mismatch",
    "filed",
    "companyfact_filed",
    "filing_filed",
    "filed_date_mismatch",
    "frame",
    "accepted_at",
    "is_amendment",
)

_ORPHAN_ALIAS_ATTR = "companyfacts_orphan_duplicate_resolution"
_ORPHAN_ALIAS_CORE_COLUMNS = (
    "cik",
    "taxonomy",
    "concept",
    "unit",
    "start",
    "end",
    "value",
    "form",
    "filed",
)


class SECError(RuntimeError):
    """Base class for SEC acquisition failures."""


class SECDownloadError(SECError):
    """Raised when an SEC response cannot be downloaded or used."""


class SECCooldownError(SECDownloadError):
    """Raised for SEC 403/429 responses that require a global cooldown."""

    def __init__(
        self,
        status_code: int,
        *,
        url: str,
        cooldown_seconds: int = DEFAULT_SEC_COOLDOWN_SECONDS,
        record: FetchRecord | None = None,
    ) -> None:
        self.status_code = int(status_code)
        self.url = url
        self.cooldown_seconds = int(cooldown_seconds)
        self.record = record
        super().__init__(
            f"SEC returned HTTP {self.status_code}; pause all SEC requests for "
            f"at least {self.cooldown_seconds} seconds"
        )


class SECParseError(ValueError):
    """Raised when an official SEC payload violates the expected contract."""


class ImmutableRawError(RuntimeError):
    """Raised when an immutable raw object or fetch ledger is inconsistent."""


@dataclass(frozen=True, slots=True)
class SECResponse:
    """Raw HTTP response returned by an injectable SEC transport."""

    status: int
    body: bytes
    headers: Mapping[str, str]
    url: str


@dataclass(frozen=True, slots=True)
class FetchRecord:
    """One content-addressed, immutable SEC fetch ledger entry."""

    record_id: str
    requested_url: str
    response_url: str
    status: int
    retrieved_at_utc: str
    sha256: str
    size_bytes: int
    raw_path: Path
    response_headers: Mapping[str, str]

    def to_json_dict(self, *, root: Path) -> dict[str, object]:
        try:
            recorded_path = str(self.raw_path.relative_to(root))
        except ValueError:
            recorded_path = str(self.raw_path)
        return {
            "record_id": self.record_id,
            "requested_url": self.requested_url,
            "response_url": self.response_url,
            "status": self.status,
            "retrieved_at_utc": self.retrieved_at_utc,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "raw_path": recorded_path,
            "response_headers": dict(sorted(self.response_headers.items())),
        }


@dataclass(frozen=True, slots=True)
class FetchedResponse:
    """A raw response together with its immutable fetch record."""

    response: SECResponse
    record: FetchRecord

    @property
    def body(self) -> bytes:
        return self.response.body

    def decoded_body(self) -> bytes:
        headers = {str(key).lower(): str(value) for key, value in self.response.headers.items()}
        encoding = headers.get("content-encoding", "").strip().lower()
        if encoding == "gzip":
            try:
                return gzip.decompress(self.response.body)
            except OSError:
                raise SECDownloadError("SEC returned invalid gzip content") from None
        if encoding == "deflate":
            try:
                return zlib.decompress(self.response.body)
            except zlib.error:
                try:
                    return zlib.decompress(self.response.body, -zlib.MAX_WBITS)
                except zlib.error:
                    raise SECDownloadError("SEC returned invalid deflate content") from None
        return self.response.body


SECTransport = Callable[..., SECResponse]


class GlobalRateLimiter:
    """Process-global request spacing shared by every default SEC client.

    SEC's published ceiling applies across workers and machines.  This object
    enforces the process portion of that contract; callers must still avoid
    running independent machines above the configured aggregate rate.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    def wait(
        self,
        rate_limit_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not math.isfinite(rate_limit_per_second) or rate_limit_per_second <= 0:
            raise ValueError("rate_limit_per_second must be positive and finite")
        interval = 1.0 / float(rate_limit_per_second)
        with self._lock:
            now = float(clock())
            delay = max(0.0, self._next_request_at - now)
            if delay:
                sleeper(delay)
                observed = float(clock())
                now = max(observed, now + delay)
            self._next_request_at = max(now, self._next_request_at) + interval


_GLOBAL_SEC_RATE_LIMITER = GlobalRateLimiter()


class ImmutableFetchStore:
    """Content-addressed raw SEC objects plus an idempotent JSONL ledger."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.objects_root = self.root / "raw" / "sha256"
        self.ledger_path = self.root / "fetch_ledger.jsonl"
        self._lock = threading.Lock()
        self._ledger_cache: dict[str, FetchRecord] | None = None
        self._ledger_cache_signature: tuple[int, int] | None = None

    def record(
        self,
        *,
        requested_url: str,
        response: SECResponse,
        retrieved_at: datetime,
    ) -> FetchRecord:
        """Persist a response without overwriting an existing raw object.

        Repeating the same URL/status/content tuple returns the original ledger
        entry and does not append a duplicate line.  Changed content produces a
        new content-addressed object and a new ledger entry.
        """

        body = bytes(response.body)
        digest = hashlib.sha256(body).hexdigest()
        object_path = self.objects_root / digest[:2] / f"{digest}.bin"
        header_subset = _normalized_response_headers(response.headers)
        record_material = (
            f"{requested_url}\n{int(response.status)}\n{digest}".encode("utf-8")
        )
        record_id = hashlib.sha256(record_material).hexdigest()
        retrieved = _normalize_utc_datetime(retrieved_at).isoformat()

        with self._lock:
            object_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with object_path.open("xb") as handle:
                    handle.write(body)
            except FileExistsError:
                existing_size = object_path.stat().st_size
                if existing_size != len(body) or _sha256_path(object_path) != digest:
                    raise ImmutableRawError(
                        f"raw SEC object conflicts with content hash: {object_path}"
                    ) from None

            ledger_by_id = self._ledger_by_id()
            existing = ledger_by_id.get(record_id)
            if existing is not None:
                if existing.sha256 != digest or existing.status != int(response.status):
                    raise ImmutableRawError(
                        f"fetch ledger record id collision: {record_id}"
                    )
                return existing

            record = FetchRecord(
                record_id=record_id,
                requested_url=requested_url,
                response_url=response.url,
                status=int(response.status),
                retrieved_at_utc=retrieved,
                sha256=digest,
                size_bytes=len(body),
                raw_path=object_path,
                response_headers=header_subset,
            )
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                record.to_json_dict(root=self.root),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            with self.ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.write("\n")
            cached = dict(ledger_by_id)
            cached[record_id] = record
            self._ledger_cache = cached
            stat = self.ledger_path.stat()
            self._ledger_cache_signature = (stat.st_size, stat.st_mtime_ns)
            return record

    def ledger_records(self) -> tuple[FetchRecord, ...]:
        with self._lock:
            return tuple(self._ledger_by_id().values())

    def _ledger_by_id(self) -> dict[str, FetchRecord]:
        records: dict[str, FetchRecord] = {}
        if not self.ledger_path.exists():
            self._ledger_cache = records
            self._ledger_cache_signature = None
            return records
        stat = self.ledger_path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        if (
            self._ledger_cache is not None
            and self._ledger_cache_signature == signature
        ):
            return self._ledger_cache
        try:
            lines = self.ledger_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            raise ImmutableRawError(f"cannot read fetch ledger: {self.ledger_path}") from None
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                raw_path_value = Path(str(payload["raw_path"]))
                raw_path = (
                    raw_path_value
                    if raw_path_value.is_absolute()
                    else (self.root / raw_path_value).resolve()
                )
                record = FetchRecord(
                    record_id=str(payload["record_id"]),
                    requested_url=str(payload["requested_url"]),
                    response_url=str(payload["response_url"]),
                    status=int(payload["status"]),
                    retrieved_at_utc=str(payload["retrieved_at_utc"]),
                    sha256=str(payload["sha256"]),
                    size_bytes=int(payload["size_bytes"]),
                    raw_path=raw_path,
                    response_headers={
                        str(key): str(value)
                        for key, value in dict(payload["response_headers"]).items()
                    },
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                raise ImmutableRawError(
                    f"invalid fetch ledger row at {self.ledger_path}:{line_number}"
                ) from None
            previous = records.get(record.record_id)
            if previous is not None and previous != record:
                raise ImmutableRawError(
                    f"conflicting duplicate fetch ledger id: {record.record_id}"
                )
            records[record.record_id] = record
        self._ledger_cache = records
        self._ledger_cache_signature = signature
        return records


class SECClient:
    """Fair-access SEC HTTP client with injectable offline transport."""

    def __init__(
        self,
        *,
        user_agent: str,
        raw_store: ImmutableFetchStore,
        rate_limit_per_second: float = DEFAULT_SEC_RATE_LIMIT_PER_SECOND,
        timeout: float = DEFAULT_SEC_TIMEOUT_SECONDS,
        transport: SECTransport | None = None,
        limiter: GlobalRateLimiter | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.user_agent = _validate_user_agent(user_agent)
        if not math.isfinite(rate_limit_per_second) or rate_limit_per_second <= 0:
            raise ValueError("rate_limit_per_second must be positive and finite")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self.raw_store = raw_store
        self.rate_limit_per_second = float(rate_limit_per_second)
        self.timeout = float(timeout)
        self._transport = transport or _urllib_transport
        self._limiter = limiter or _GLOBAL_SEC_RATE_LIMITER
        self._clock = clock
        self._sleeper = sleeper
        self._now = now or (lambda: datetime.now(timezone.utc))

    def get(self, url: str) -> FetchedResponse:
        """Fetch one official SEC URL and record the raw response first."""

        _validate_sec_url(url)
        self._limiter.wait(
            self.rate_limit_per_second,
            clock=self._clock,
            sleeper=self._sleeper,
        )
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, application/atom+xml, application/xml, text/xml, */*",
            "Accept-Encoding": "gzip, deflate",
        }
        try:
            response = self._transport(
                url,
                headers=headers,
                timeout=self.timeout,
            )
        except SECError:
            raise
        except Exception:
            raise SECDownloadError(f"SEC request failed: {url}") from None
        if not isinstance(response, SECResponse):
            raise SECDownloadError("SEC transport must return SECResponse")

        record = self.raw_store.record(
            requested_url=url,
            response=response,
            retrieved_at=self._now(),
        )
        if response.status in {403, 429}:
            raise SECCooldownError(
                response.status,
                url=url,
                record=record,
            )
        if not 200 <= response.status < 300:
            raise SECDownloadError(f"SEC returned HTTP {response.status}: {url}")
        return FetchedResponse(response=response, record=record)

    def get_json(self, url: str) -> tuple[object, FetchRecord]:
        fetched = self.get(url)
        try:
            payload = json.loads(fetched.decoded_body().decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SECDownloadError(f"SEC returned invalid JSON: {url}") from None
        return payload, fetched.record

    def get_text(self, url: str) -> tuple[str, FetchRecord]:
        fetched = self.get(url)
        try:
            value = fetched.decoded_body().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise SECDownloadError(f"SEC returned invalid UTF-8 text: {url}") from None
        return value, fetched.record


def normalize_sec_ticker(value: object) -> str:
    """Normalize a listed SEC ticker for deterministic security matching."""

    ticker = str(value).strip().upper().replace(".", "-").replace("/", "-")
    if not ticker or not _TICKER_PATTERN.fullmatch(ticker):
        raise SECParseError(f"invalid SEC ticker: {value!r}")
    return ticker


def normalize_cik(value: object) -> str:
    """Return a ten-digit, zero-padded CIK string."""

    if isinstance(value, bool):
        raise SECParseError(f"invalid CIK: {value!r}")
    text = str(value).strip()
    if not text.isdigit() or not 1 <= len(text) <= 10 or int(text) <= 0:
        raise SECParseError(f"invalid CIK: {value!r}")
    return text.zfill(10)


def parse_company_tickers(payload: object) -> pd.DataFrame:
    """Parse SEC ``company_tickers.json`` into normalized ticker/CIK rows."""

    source = _load_json(payload, "company_tickers")
    if not isinstance(source, Mapping):
        raise SECParseError("company_tickers must be a JSON object")
    rows: list[dict[str, str]] = []
    for key in sorted(source, key=lambda item: str(item)):
        item = source[key]
        if not isinstance(item, Mapping):
            raise SECParseError(f"company_tickers row {key!r} is not an object")
        try:
            ticker = normalize_sec_ticker(item["ticker"])
            cik = normalize_cik(item["cik_str"])
            name = str(item["title"]).strip()
        except KeyError as exc:
            raise SECParseError(
                f"company_tickers row {key!r} missing field: {exc.args[0]}"
            ) from None
        if not name:
            raise SECParseError(f"company_tickers row {key!r} has an empty title")
        rows.append({"ticker": ticker, "cik": cik, "name": name})
    frame = pd.DataFrame(rows, columns=["ticker", "cik", "name"])
    return frame.drop_duplicates(ignore_index=True).sort_values(
        ["ticker", "cik"], ignore_index=True
    )


def parse_browse_edgar_atom_single_cik(payload: bytes | str) -> str:
    """Extract exactly one unique CIK from a Browse EDGAR Atom response."""

    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise SECParseError("Browse EDGAR Atom payload is invalid XML") from None
    ciks: set[str] = set()
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1].lower()
        if local_name == "cik" and element.text and element.text.strip():
            ciks.add(normalize_cik(element.text))
    if len(ciks) != 1:
        raise SECParseError(
            f"Browse EDGAR Atom payload must contain one unique CIK; found {len(ciks)}"
        )
    return next(iter(ciks))


def submission_history_file_names(root_payload: object) -> tuple[str, ...]:
    """Return safe historical shard names declared by a submissions root."""

    root = _load_json(root_payload, "submissions root")
    try:
        files = root["filings"].get("files", [])
    except (KeyError, AttributeError, TypeError):
        raise SECParseError("submissions root missing filings object") from None
    if not isinstance(files, Sequence) or isinstance(files, (str, bytes, bytearray)):
        raise SECParseError("submissions filings.files must be an array")
    names: list[str] = []
    for item in files:
        if not isinstance(item, Mapping) or "name" not in item:
            raise SECParseError("submissions history descriptor missing name")
        name = str(item["name"]).strip()
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or not name.endswith(".json")
        ):
            raise SECParseError(f"unsafe submissions history filename: {name!r}")
        names.append(name)
    return tuple(dict.fromkeys(names))


def parse_submissions(
    root_payload: object,
    historical_shards: Mapping[str, object] | Sequence[object] = (),
) -> pd.DataFrame:
    """Combine a submissions root and every supplied historical shard.

    The result uses canonical snake-case columns while reading the official
    ``accessionNumber``, ``filingDate``, ``reportDate``,
    ``acceptanceDateTime``, and ``primaryDocument`` arrays.
    """

    root = _load_json(root_payload, "submissions root")
    if not isinstance(root, Mapping):
        raise SECParseError("submissions root must be a JSON object")
    try:
        cik = normalize_cik(root["cik"])
        filings = root["filings"]
        recent = filings["recent"]
    except (KeyError, TypeError):
        raise SECParseError("submissions root missing cik or filings.recent") from None
    if not isinstance(recent, Mapping):
        raise SECParseError("submissions filings.recent must be an object")

    sources: list[tuple[str, Mapping[str, object]]] = [("recent", recent)]
    if isinstance(historical_shards, Mapping):
        if "accessionNumber" in historical_shards:
            sources.append(("history-000", historical_shards))
        else:
            for name in sorted(historical_shards):
                shard = _load_json(historical_shards[name], f"submissions shard {name}")
                if not isinstance(shard, Mapping):
                    raise SECParseError(f"submissions shard {name!r} is not an object")
                sources.append((str(name), shard))
    elif isinstance(historical_shards, Sequence) and not isinstance(
        historical_shards, (str, bytes, bytearray)
    ):
        for index, value in enumerate(historical_shards):
            shard = _load_json(value, f"submissions shard {index}")
            if not isinstance(shard, Mapping):
                raise SECParseError(f"submissions shard {index} is not an object")
            sources.append((f"history-{index:03d}", shard))
    else:
        raise SECParseError("historical_shards must be a mapping or sequence")

    rows: list[dict[str, object]] = []
    for source_name, columns in sources:
        rows.extend(_submission_rows(columns, cik=cik, source_name=source_name))
    frame = pd.DataFrame(rows, columns=FILING_LEDGER_COLUMNS)
    if frame.empty:
        return frame

    comparable = [column for column in FILING_LEDGER_COLUMNS if column != "source_shard"]
    # Most accessions appear exactly once. Vectorized de-duplication avoids a
    # pandas groupby and tiny DataFrame allocation for every filing in a
    # decades-long submissions history.
    distinct = frame.drop_duplicates(subset=comparable, keep="first")
    conflicting = distinct["accession"].duplicated(keep=False)
    if conflicting.any():
        accession = sorted(distinct.loc[conflicting, "accession"])[0]
        raise SECParseError(f"conflicting submissions metadata for {accession}")
    frame = distinct
    return frame.sort_values(["accepted_at", "accession"], ignore_index=True)


def parse_companyfacts(
    payload: object,
    filing_ledger: pd.DataFrame,
) -> pd.DataFrame:
    """Parse numeric Company Facts and join ``accepted_at`` by accession."""

    source = _load_json(payload, "companyfacts")
    if not isinstance(source, Mapping):
        raise SECParseError("companyfacts must be a JSON object")
    try:
        cik = normalize_cik(source["cik"])
        facts = source["facts"]
    except (KeyError, TypeError):
        raise SECParseError("companyfacts missing cik or facts") from None
    if not isinstance(facts, Mapping):
        raise SECParseError("companyfacts facts must be an object")
    _require_columns(
        filing_ledger,
        {"cik", "accession", "accepted_at", "is_amendment", "filed", "form"},
        "filing ledger",
    )

    rows: list[dict[str, object]] = []
    for taxonomy in sorted(facts):
        concepts = facts[taxonomy]
        if not isinstance(concepts, Mapping):
            raise SECParseError(f"companyfacts taxonomy {taxonomy!r} is not an object")
        for concept in sorted(concepts):
            definition = concepts[concept]
            if not isinstance(definition, Mapping):
                raise SECParseError(
                    f"companyfacts concept {taxonomy}:{concept} is not an object"
                )
            label = str(definition.get("label", "")).strip()
            description = str(definition.get("description", "")).strip()
            units = definition.get("units")
            if not isinstance(units, Mapping):
                raise SECParseError(
                    f"companyfacts concept {taxonomy}:{concept} missing units"
                )
            for unit in sorted(units):
                observations = units[unit]
                if not isinstance(observations, Sequence) or isinstance(
                    observations, (str, bytes, bytearray)
                ):
                    raise SECParseError(
                        f"companyfacts unit {taxonomy}:{concept}:{unit} is not an array"
                    )
                for index, observation in enumerate(observations):
                    if not isinstance(observation, Mapping):
                        raise SECParseError(
                            f"companyfacts observation {taxonomy}:{concept}:{unit}:{index} "
                            "is not an object"
                        )
                    rows.append(
                        _companyfact_row(
                            observation,
                            cik=cik,
                            taxonomy=str(taxonomy),
                            concept=str(concept),
                            label=label,
                            description=description,
                            unit=str(unit),
                        )
                    )

    raw = pd.DataFrame(rows)
    if raw.empty:
        result = pd.DataFrame(columns=NUMERIC_FACT_COLUMNS)
        result.attrs[_ORPHAN_ALIAS_ATTR] = {
            "orphan_duplicate_resolved_count": 0,
            "cik": cik,
            "accessions": [],
        }
        return result

    ledger = filing_ledger.loc[
        :, ["cik", "accession", "accepted_at", "is_amendment", "filed", "form"]
    ].copy()
    ledger["cik"] = ledger["cik"].map(normalize_cik)
    ledger["accepted_at"] = pd.to_datetime(ledger["accepted_at"], utc=True, errors="coerce")
    ledger["filed"] = pd.to_datetime(ledger["filed"], errors="coerce").dt.normalize()
    ledger["form"] = ledger["form"].astype(str).str.upper().str.strip()
    ledger["accession"] = ledger["accession"].astype(str).str.strip()
    if ledger[["accepted_at", "filed"]].isna().any().any():
        raise SECParseError("filing ledger contains invalid accepted_at or filed values")
    if ledger["form"].eq("").any():
        raise SECParseError("filing ledger contains empty form values")
    ledger = ledger.drop_duplicates()
    if ledger["accession"].duplicated().any():
        raise SECParseError("filing ledger contains conflicting duplicate accessions")
    ledger = ledger.rename(
        columns={
            "cik": "filing_cik",
            "filed": "filing_filed",
            "form": "filing_form",
        }
    )

    raw, orphan_resolution = _resolve_orphan_accession_aliases(
        raw,
        ledger,
        cik=cik,
    )

    joined = raw.merge(ledger, on="accession", how="left", validate="many_to_one")
    unmatched = joined["accepted_at"].isna()
    if unmatched.any():
        sample = sorted(joined.loc[unmatched, "accession"].astype(str).unique())[:5]
        raise SECParseError(
            f"companyfacts accessions missing from filing ledger: {sample}"
        )
    if joined["filing_cik"].ne(cik).any():
        raise SECParseError("companyfacts accession joined to a different filing CIK")
    joined["companyfact_filed"] = joined["filed"]
    filed_mismatch = joined["companyfact_filed"].notna() & joined[
        "companyfact_filed"
    ].ne(joined["filing_filed"])
    # SEC's two official aggregates occasionally disagree on the display
    # filing date for the same accession. The submissions ledger owns filing
    # and acceptance timing; the Company Facts value and mismatch flag remain
    # in the evidence table for audit rather than dropping the whole issuer.
    joined["filed_date_mismatch"] = filed_mismatch.astype(bool)
    joined["filed"] = joined["filing_filed"]
    joined["fact_form"] = joined["form"].astype(str).str.upper().str.strip()
    joined["filing_form"] = (
        joined["filing_form"].astype(str).str.upper().str.strip()
    )
    joined["form_mismatch"] = joined["fact_form"].ne(
        joined["filing_form"]
    )
    # Submissions owns the accession-level filing classification just as it
    # owns acceptance and filing timing. Company Facts' display form remains
    # available for audit but can never promote a quarterly filing to annual.
    joined["form"] = joined["filing_form"]

    fact_key = ["cik", "taxonomy", "concept", "unit", "start", "end", "accession"]
    conflicts = joined.groupby(fact_key, dropna=False)["value"].nunique(dropna=False)
    if (conflicts > 1).any():
        raise SECParseError("companyfacts contains conflicting values for one accession context")
    joined = joined.sort_values(
        [*fact_key, "frame"], na_position="last"
    ).drop_duplicates(subset=fact_key, keep="first")
    joined = joined.loc[:, NUMERIC_FACT_COLUMNS]
    result = joined.sort_values(
        ["accepted_at", "accession", "taxonomy", "concept", "unit", "end"],
        ignore_index=True,
    )
    result.attrs[_ORPHAN_ALIAS_ATTR] = orphan_resolution
    return result


def _resolve_orphan_accession_aliases(
    raw: pd.DataFrame,
    ledger: pd.DataFrame,
    *,
    cik: str,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Drop only exact orphan aliases of one unambiguous ledger observation.

    Company Facts occasionally repeats an observation under an accession that
    does not appear in Submissions while also retaining the same observation
    under the real ledger accession.  The orphan can carry different FY/frame
    display metadata, but it is never allowed to contribute a value or timing
    event.  Every core observation must match exactly and every orphan
    accession must resolve to exactly one ledger accession; otherwise parsing
    remains fail-closed.
    """

    ledger_accessions = set(
        ledger.loc[ledger["filing_cik"].eq(cik), "accession"].astype(str)
    )
    known = raw["accession"].astype(str).isin(ledger_accessions)
    if known.all():
        return raw, {
            "orphan_duplicate_resolved_count": 0,
            "cik": cik,
            "accessions": [],
        }

    candidates: dict[tuple[object, ...], set[str]] = {}
    for row in raw.loc[known].itertuples(index=False):
        key = _orphan_alias_core_key(row)
        candidates.setdefault(key, set()).add(str(row.accession))

    orphan_to_candidates: dict[str, set[str]] = {}
    orphan_counts: dict[str, int] = {}
    orphan_indexes: list[object] = []
    for index, row in raw.loc[~known].iterrows():
        orphan_accession = str(row["accession"])
        matches = candidates.get(_orphan_alias_core_key(row), set())
        if not matches:
            raise SECParseError(
                "companyfacts accessions missing from filing ledger without "
                f"an exact duplicate observation: {[orphan_accession]}"
            )
        if len(matches) != 1:
            raise SECParseError(
                "companyfacts orphan observation has multiple exact filing "
                f"ledger candidates: {orphan_accession} -> {sorted(matches)}"
            )
        orphan_to_candidates.setdefault(orphan_accession, set()).update(matches)
        orphan_counts[orphan_accession] = orphan_counts.get(orphan_accession, 0) + 1
        orphan_indexes.append(index)

    ambiguous_aliases = {
        orphan: sorted(matches)
        for orphan, matches in orphan_to_candidates.items()
        if len(matches) != 1
    }
    if ambiguous_aliases:
        raise SECParseError(
            "companyfacts orphan accession maps to multiple filing ledger "
            f"accessions: {ambiguous_aliases}"
        )

    ledger_acceptance = ledger.set_index("accession")["accepted_at"]
    accessions: list[dict[str, object]] = []
    for orphan_accession in sorted(orphan_to_candidates):
        candidate_accession = next(iter(orphan_to_candidates[orphan_accession]))
        accepted_at = pd.Timestamp(ledger_acceptance.loc[candidate_accession])
        accessions.append(
            {
                "orphan_accession": orphan_accession,
                "ledger_accession": candidate_accession,
                "resolved_observation_count": orphan_counts[orphan_accession],
                "ledger_accepted_at": accepted_at.isoformat(),
            }
        )
    resolved = raw.drop(index=orphan_indexes).reset_index(drop=True)
    return resolved, {
        "orphan_duplicate_resolved_count": len(orphan_indexes),
        "cik": cik,
        "accessions": accessions,
    }


def _orphan_alias_core_key(row: object) -> tuple[object, ...]:
    def value(column: str) -> object:
        item = row[column] if isinstance(row, pd.Series) else getattr(row, column)
        if pd.isna(item):
            return None
        if isinstance(item, pd.Timestamp):
            return item.value
        return item

    return tuple(value(column) for column in _ORPHAN_ALIAS_CORE_COLUMNS)


def facts_as_of(
    numeric_facts: pd.DataFrame,
    as_of: object,
    *,
    economic_key: Sequence[str] = (
        "cik",
        "taxonomy",
        "concept",
        "unit",
        "start",
        "end",
    ),
) -> pd.DataFrame:
    """Materialize the latest known fact version at an inclusive UTC cutoff.

    Sparse amendments update only the economic keys they contain.  Earlier
    values for other concepts remain available, while facts accepted after the
    cutoff can never replace an earlier vintage.
    """

    required = {"accepted_at", "accession", *economic_key}
    _require_columns(numeric_facts, required, "numeric facts")
    try:
        cutoff = pd.Timestamp(as_of)
    except (TypeError, ValueError, OverflowError):
        raise SECParseError(f"invalid as_of timestamp: {as_of!r}") from None
    if pd.isna(cutoff):
        raise SECParseError(f"invalid as_of timestamp: {as_of!r}")
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    else:
        cutoff = cutoff.tz_convert("UTC")

    frame = numeric_facts.copy()
    frame["accepted_at"] = pd.to_datetime(frame["accepted_at"], utc=True, errors="coerce")
    if frame["accepted_at"].isna().any():
        raise SECParseError("numeric facts contain invalid accepted_at values")
    eligible = frame.loc[frame["accepted_at"] <= cutoff].copy()
    if eligible.empty:
        return eligible.reset_index(drop=True)
    eligible = eligible.sort_values(["accepted_at", "accession"])
    eligible = eligible.drop_duplicates(
        subset=list(economic_key),
        keep="last",
    )
    return eligible.sort_values(list(economic_key), ignore_index=True)


def _submission_rows(
    columns: Mapping[str, object],
    *,
    cik: str,
    source_name: str,
) -> list[dict[str, object]]:
    official_fields = (
        "accessionNumber",
        "form",
        "filingDate",
        "reportDate",
        "acceptanceDateTime",
        "primaryDocument",
    )
    missing = [field for field in official_fields if field not in columns]
    if missing:
        raise SECParseError(
            f"submissions shard {source_name!r} missing fields: {missing}"
        )
    arrays: dict[str, Sequence[object]] = {}
    for field in official_fields:
        values = columns[field]
        if not isinstance(values, Sequence) or isinstance(
            values, (str, bytes, bytearray)
        ):
            raise SECParseError(
                f"submissions field {field!r} in {source_name!r} is not an array"
            )
        arrays[field] = values
    lengths = {len(values) for values in arrays.values()}
    if len(lengths) != 1:
        raise SECParseError(f"submissions shard {source_name!r} has unequal columns")

    rows: list[dict[str, object]] = []
    for index in range(next(iter(lengths), 0)):
        accession = str(arrays["accessionNumber"][index]).strip()
        if not _ACCESSION_PATTERN.fullmatch(accession):
            raise SECParseError(f"invalid accession number: {accession!r}")
        form = str(arrays["form"][index]).strip().upper()
        if not form:
            raise SECParseError(f"empty form for accession {accession}")
        filed = _parse_date(arrays["filingDate"][index], label="filingDate")
        report_date = _parse_optional_date(
            arrays["reportDate"][index], label="reportDate"
        )
        accepted_at = _parse_utc_timestamp(
            arrays["acceptanceDateTime"][index], label="acceptanceDateTime"
        )
        primary_document = str(arrays["primaryDocument"][index]).strip()
        rows.append(
            {
                "cik": cik,
                "accession": accession,
                "form": form,
                "filed": filed,
                # Preserve the official API field spellings in the ledger so
                # a reviewer can map the parsed row back to the source schema.
                # Snake-case aliases support the rest of the Python package.
                "reportDate": report_date,
                "acceptanceDateTime": accepted_at,
                "primaryDocument": primary_document,
                "report_date": report_date,
                "accepted_at": accepted_at,
                "primary_document": primary_document,
                "is_amendment": form.endswith("/A"),
                "source_shard": source_name,
            }
        )
    return rows


def _companyfact_row(
    observation: Mapping[str, object],
    *,
    cik: str,
    taxonomy: str,
    concept: str,
    label: str,
    description: str,
    unit: str,
) -> dict[str, object]:
    required = {"val", "end", "accn", "form"}
    missing = sorted(required.difference(observation))
    if missing:
        raise SECParseError(
            f"companyfacts observation {taxonomy}:{concept}:{unit} missing: {missing}"
        )
    value = observation["val"]
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise SECParseError(
            f"companyfacts value {taxonomy}:{concept}:{unit} is not numeric"
        )
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise SECParseError(
            f"companyfacts value {taxonomy}:{concept}:{unit} is not finite"
        )
    accession = str(observation["accn"]).strip()
    if not _ACCESSION_PATTERN.fullmatch(accession):
        raise SECParseError(f"invalid companyfacts accession: {accession!r}")
    form = str(observation["form"]).strip().upper()
    if not form:
        raise SECParseError(f"empty companyfacts form for accession {accession}")
    return {
        "cik": cik,
        "taxonomy": taxonomy,
        "concept": concept,
        "label": label,
        "description": description,
        "unit": unit,
        "start": _parse_optional_date(observation.get("start"), label="fact start"),
        "end": _parse_date(observation["end"], label="fact end"),
        "value": numeric_value,
        "accession": accession,
        "fy": _optional_text(observation.get("fy")),
        "fp": _optional_text(observation.get("fp")),
        "form": form,
        "filed": _parse_optional_date(observation.get("filed"), label="fact filed"),
        "frame": _optional_text(observation.get("frame")),
    }


def _load_json(payload: object, label: str) -> object:
    if isinstance(payload, (bytes, bytearray)):
        try:
            return json.loads(bytes(payload).decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SECParseError(f"{label} is invalid JSON") from None
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            raise SECParseError(f"{label} is invalid JSON") from None
    return payload


def _validate_user_agent(value: str) -> str:
    user_agent = str(value).strip()
    if not user_agent or "@" not in user_agent or not any(
        character.isspace() for character in user_agent
    ):
        raise ValueError(
            "SEC user_agent must declare an organization/project and contact email"
        )
    if "\r" in user_agent or "\n" in user_agent:
        raise ValueError("SEC user_agent cannot contain line breaks")
    return user_agent


def _validate_sec_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or parsed.hostname not in _ALLOWED_SEC_HOSTS:
        raise ValueError(f"SEC client only accepts official HTTPS SEC URLs: {url!r}")
    if parsed.username or parsed.password:
        raise ValueError("SEC URL cannot contain credentials")


def _normalized_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    selected = {
        "content-type",
        "content-encoding",
        "content-length",
        "etag",
        "last-modified",
        "cache-control",
        "retry-after",
    }
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    return {key: normalized[key] for key in sorted(selected.intersection(normalized))}


def _normalize_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("retrieved_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _sha256_path(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_date(value: object, *, label: str) -> pd.Timestamp:
    parsed = _parse_optional_date(value, label=label)
    if pd.isna(parsed):
        raise SECParseError(f"{label} cannot be empty")
    return parsed


def _parse_optional_date(value: object, *, label: str) -> pd.Timestamp:
    if value is None or (isinstance(value, str) and not value.strip()):
        return pd.NaT
    if isinstance(value, str):
        text = value.strip()
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            try:
                return pd.Timestamp(date.fromisoformat(text))
            except ValueError:
                raise SECParseError(f"invalid {label}: {value!r}") from None
    try:
        parsed = pd.to_datetime(value, errors="raise")
    except (TypeError, ValueError, OverflowError):
        raise SECParseError(f"invalid {label}: {value!r}") from None
    timestamp = pd.Timestamp(parsed)
    if pd.isna(timestamp):
        return pd.NaT
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    return timestamp.normalize()


def _parse_utc_timestamp(value: object, *, label: str) -> pd.Timestamp:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise SECParseError(f"{label} cannot be empty")
    try:
        if isinstance(value, str):
            text = value.strip().replace("Z", "+00:00")
            parsed_datetime = datetime.fromisoformat(text)
            if parsed_datetime.tzinfo is None:
                parsed_datetime = parsed_datetime.replace(tzinfo=timezone.utc)
            parsed = pd.Timestamp(parsed_datetime).tz_convert("UTC")
        else:
            parsed = pd.to_datetime(value, utc=True, errors="raise")
    except (TypeError, ValueError, OverflowError):
        raise SECParseError(f"invalid {label}: {value!r}") from None
    timestamp = pd.Timestamp(parsed)
    if pd.isna(timestamp):
        raise SECParseError(f"invalid {label}: {value!r}")
    return timestamp


def _optional_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise SECParseError(f"{label} missing columns: {missing}")


def _urllib_transport(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
) -> SECResponse:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(getattr(response, "status", 200))
            body = response.read()
            response_headers = dict(response.headers.items())
            response_url = str(response.geturl())
    except HTTPError as exc:
        status = int(exc.code)
        try:
            body = exc.read()
        except OSError:
            body = b""
        response_headers = dict(exc.headers.items()) if exc.headers is not None else {}
        response_url = str(exc.geturl() or url)
    except (URLError, TimeoutError, OSError):
        raise SECDownloadError(f"SEC HTTP request failed: {url}") from None
    return SECResponse(
        status=status,
        body=body,
        headers=response_headers,
        url=response_url,
    )
