"""Convert the community fja05680 S&P 500 history into prototype PIT inputs.

This module intentionally treats a source ticker as an identity because the
public file contains tickers, not permanent security identifiers.  The output
is useful for free prototyping only and is never labelled formal-run eligible.
Ticker changes are not linked to one another; Yahoo's dot-to-hyphen query
format is the sole provider-symbol transformation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import pandas as pd


FJA05680_UPDATED_SOURCE_URL = (
    "https://github.com/fja05680/sp500/raw/refs/heads/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes%20%28Updated%29.csv"
)


class PublicPITFormatError(ValueError):
    """The public source cannot be interpreted without guessing."""


@dataclass(frozen=True, slots=True)
class PublicPITTables:
    """In-memory prototype tables plus their audit metadata."""

    membership_snapshots: pd.DataFrame
    membership_intervals: pd.DataFrame
    security_master: pd.DataFrame
    snapshot_audit: pd.DataFrame
    anomalies: pd.DataFrame
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PublicPITWriteResult:
    """Paths created by one immutable prototype conversion."""

    output_dir: Path
    manifest_path: Path
    membership_snapshots_path: Path
    membership_intervals_path: Path
    security_master_path: Path
    snapshot_audit_path: Path
    anomalies_path: Path


def source_ticker_to_sid(raw_ticker: str) -> str:
    """Return the frozen, explainable prototype identity for one source ticker."""

    value = str(raw_ticker).strip()
    if not value:
        raise ValueError("raw_ticker cannot be blank")
    return f"yf_ticker::{value}"


def source_ticker_to_yahoo(raw_ticker: str) -> str:
    """Apply only Yahoo's share-class punctuation convention."""

    value = str(raw_ticker).strip()
    if not value:
        raise ValueError("raw_ticker cannot be blank")
    return value.replace(".", "-")


def convert_fja05680_updated_csv(
    source_csv: str | Path,
    *,
    research_start: object | None = None,
    research_end: object | None = None,
    source_url: str = FJA05680_UPDATED_SOURCE_URL,
) -> PublicPITTables:
    """Convert the Updated ``date,tickers`` CSV to prototype PIT tables.

    Source rows are interpreted as complete membership snapshots effective on
    their stated dates and carried forward until the next row.  Intervals retain
    their original boundaries when a research range is supplied.  The snapshot
    output retains the last source row on or before ``research_start`` as an
    anchor, followed by all source rows through ``research_end``.

    Security-master mappings are deliberately unbounded.  This lets the data
    downloader request formation history before a ticker first enters the
    research universe.  It does *not* assert that a ticker represents the same
    company throughout history; that unresolved identity/reuse risk is recorded
    in the anomaly table and manifest.
    """

    path = Path(source_csv).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    source_sha256 = _sha256_file(path)
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    date_column, tickers_column = _resolve_source_columns(frame.columns)
    if frame.empty:
        raise PublicPITFormatError("public PIT source is empty")

    parsed_dates = pd.to_datetime(
        frame[date_column].astype(str).str.strip(), errors="raise"
    )
    dates = pd.DatetimeIndex(parsed_dates).normalize()
    if dates.tz is not None:
        raise PublicPITFormatError("public PIT dates must be timezone-naive")
    if dates.hasnans:
        raise PublicPITFormatError("public PIT dates cannot contain NaT")
    if dates.has_duplicates:
        duplicates = dates[dates.duplicated()].unique().tolist()
        raise PublicPITFormatError(f"duplicate source dates: {duplicates}")
    if not dates.is_monotonic_increasing:
        raise PublicPITFormatError("source dates must be strictly increasing")

    start, end = _research_bounds(
        research_start,
        research_end,
        source_start=pd.Timestamp(dates[0]),
        source_end=pd.Timestamp(dates[-1]),
    )

    anomaly_rows: list[dict[str, object]] = []
    snapshot_sets: list[frozenset[str]] = []
    audit_rows: list[dict[str, object]] = []
    previous: frozenset[str] = frozenset()
    for row_number, (date, raw_cell) in enumerate(
        zip(dates, frame[tickers_column], strict=True), start=2
    ):
        tokens = [token.strip() for token in str(raw_cell).split(",")]
        blanks = sum(token == "" for token in tokens)
        nonblank = [token for token in tokens if token]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for ticker in nonblank:
            if ticker in seen:
                duplicates.add(ticker)
            seen.add(ticker)
        duplicate_values = sorted(duplicates)
        members = frozenset(nonblank)
        if not members:
            raise PublicPITFormatError(
                f"source row {row_number} ({date.date()}) has no tickers"
            )
        if blanks:
            anomaly_rows.append(
                _anomaly(
                    "warning",
                    "blank_ticker_token",
                    date=date,
                    affected_count=blanks,
                    detail=f"source row {row_number}; blank tokens were omitted",
                )
            )
        if duplicate_values:
            anomaly_rows.append(
                _anomaly(
                    "warning",
                    "duplicate_ticker_in_snapshot",
                    date=date,
                    raw_ticker="|".join(duplicate_values),
                    affected_count=len(duplicate_values),
                    detail=f"source row {row_number}; duplicates were collapsed",
                )
            )

        added = members.difference(previous)
        removed = previous.difference(members)
        included = _snapshot_in_scope(date, dates, start=start, end=end)
        audit_rows.append(
            {
                "date": pd.Timestamp(date),
                "constituent_count": len(members),
                "added_count": len(added),
                "removed_count": len(removed),
                "duplicate_ticker_count": len(duplicate_values),
                "blank_ticker_count": blanks,
                "included_in_scoped_snapshots": included,
            }
        )
        snapshot_sets.append(members)
        previous = members

    full_intervals = _snapshots_to_intervals(dates, snapshot_sets)
    scoped_intervals = _scope_intervals(full_intervals, start=start, end=end)
    scoped_tickers = frozenset(scoped_intervals["raw_ticker"].astype(str))
    snapshots = _long_snapshots(
        dates,
        snapshot_sets,
        include_dates=pd.DatetimeIndex(
            [
                audit["date"]
                for audit in audit_rows
                if bool(audit["included_in_scoped_snapshots"])
            ]
        ),
        allowed_tickers=scoped_tickers,
    )

    episode_counts = scoped_intervals.groupby("raw_ticker").size()
    reentries = episode_counts[episode_counts > 1]
    for raw_ticker, episode_count in reentries.items():
        rows = scoped_intervals.loc[
            scoped_intervals["raw_ticker"].eq(raw_ticker)
        ].sort_values("effective_from")
        anomaly_rows.append(
            _anomaly(
                "warning",
                "ticker_reentry_or_reuse_unresolved",
                date=rows.iloc[1]["effective_from"],
                raw_ticker=str(raw_ticker),
                affected_count=int(episode_count),
                detail=(
                    "ticker has multiple membership episodes; the prototype SID "
                    "cannot distinguish a re-entry from ticker reuse"
                ),
            )
        )

    dotted = sorted(ticker for ticker in scoped_tickers if "." in ticker)
    for raw_ticker in dotted:
        anomaly_rows.append(
            _anomaly(
                "info",
                "yahoo_dot_to_hyphen",
                raw_ticker=raw_ticker,
                affected_count=1,
                detail=f"Yahoo query symbol is {source_ticker_to_yahoo(raw_ticker)}",
            )
        )

    yahoo_groups: dict[str, list[str]] = {}
    for raw_ticker in sorted(scoped_tickers):
        yahoo_groups.setdefault(source_ticker_to_yahoo(raw_ticker), []).append(raw_ticker)
    collisions = {
        symbol: tickers for symbol, tickers in yahoo_groups.items() if len(tickers) > 1
    }
    for symbol, tickers in collisions.items():
        anomaly_rows.append(
            _anomaly(
                "warning",
                "yahoo_symbol_collision",
                raw_ticker="|".join(tickers),
                affected_count=len(tickers),
                detail=f"multiple source tickers map to Yahoo symbol {symbol}",
            )
        )

    anomaly_rows.insert(
        0,
        _anomaly(
            "warning",
            "ticker_derived_identity_prototype_only",
            affected_count=len(scoped_tickers),
            detail=(
                "SIDs are derived from source ticker text, not permanent security "
                "identifiers; ticker changes and ticker reuse are not resolved"
            ),
        ),
    )
    security_master = _security_master(scoped_tickers)
    anomalies = pd.DataFrame.from_records(
        anomaly_rows,
        columns=[
            "severity",
            "code",
            "date",
            "raw_ticker",
            "affected_count",
            "detail",
        ],
    )
    snapshot_audit = pd.DataFrame.from_records(audit_rows)

    metadata: dict[str, Any] = {
        "source_name": "fja05680/sp500 Updated historical components",
        "source_url": source_url,
        "source_path": str(path),
        "source_sha256": source_sha256,
        "source_format": "date,tickers (complete comma-separated membership snapshot)",
        "source_row_count": len(frame),
        "source_date_start": str(pd.Timestamp(dates[0]).date()),
        "source_date_end": str(pd.Timestamp(dates[-1]).date()),
        "research_start": str(start.date()),
        "research_end": str(end.date()),
        "output_snapshot_date_start": str(snapshots["date"].min().date()),
        "output_snapshot_date_end": str(snapshots["date"].max().date()),
        "output_snapshot_date_count": int(snapshots["date"].nunique()),
        "output_interval_count": len(scoped_intervals),
        "output_security_count": len(security_master),
        "constituent_count_min": int(snapshot_audit["constituent_count"].min()),
        "constituent_count_max": int(snapshot_audit["constituent_count"].max()),
        "prototype_only": True,
        "formal_run_eligible": False,
        "status": "prototype_only",
        "membership_date_semantics": (
            "full snapshot effective on source date; carried forward until next source row"
        ),
        "membership_interval_policy": (
            "half-open [effective_from,effective_to); original boundaries retained"
        ),
        "sid_policy": "yf_ticker::<raw_ticker>",
        "yahoo_symbol_policy": "replace '.' with '-' only",
        "security_mapping_validity": (
            "unbounded solely to acquire pre-membership formation history"
        ),
        "identity_warning": (
            "ticker-derived SIDs do not resolve corporate identity, ticker changes, or reuse"
        ),
        "anomaly_count": len(anomalies),
        "anomaly_counts_by_code": {
            str(code): int(count)
            for code, count in anomalies.groupby("code").size().items()
        },
    }
    return PublicPITTables(
        membership_snapshots=snapshots,
        membership_intervals=scoped_intervals,
        security_master=security_master,
        snapshot_audit=snapshot_audit,
        anomalies=anomalies,
        metadata=metadata,
    )


def write_fja05680_prototype(
    source_csv: str | Path,
    output_dir: str | Path,
    *,
    research_start: object | None = None,
    research_end: object | None = None,
    source_url: str = FJA05680_UPDATED_SOURCE_URL,
) -> PublicPITWriteResult:
    """Write one immutable, auditable public-PIT prototype bundle."""

    destination = Path(output_dir).resolve()
    if destination.exists():
        raise FileExistsError(f"prototype output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tables = convert_fja05680_updated_csv(
        source_csv,
        research_start=research_start,
        research_end=research_end,
        source_url=source_url,
    )
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    filenames = {
        "membership_snapshots": "membership_snapshots.csv",
        "membership_intervals": "membership_intervals.csv",
        "security_master": "security_master_yfinance.csv",
        "snapshot_audit": "snapshot_audit.csv",
        "anomalies": "anomalies.csv",
    }
    try:
        for field_name, filename in filenames.items():
            frame = getattr(tables, field_name)
            frame.to_csv(staging / filename, index=False, lineterminator="\n")
        output_files = [staging / filename for filename in filenames.values()]
        manifest = {
            **tables.metadata,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "files": [
                {
                    "path": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
                for path in output_files
            ],
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return PublicPITWriteResult(
        output_dir=destination,
        manifest_path=destination / "manifest.json",
        membership_snapshots_path=destination / filenames["membership_snapshots"],
        membership_intervals_path=destination / filenames["membership_intervals"],
        security_master_path=destination / filenames["security_master"],
        snapshot_audit_path=destination / filenames["snapshot_audit"],
        anomalies_path=destination / filenames["anomalies"],
    )


def _resolve_source_columns(columns: pd.Index) -> tuple[object, object]:
    matches: dict[str, object] = {}
    for column in columns:
        key = str(column).strip().casefold()
        if key in matches:
            raise PublicPITFormatError(f"ambiguous case-insensitive column: {key}")
        matches[key] = column
    missing = {"date", "tickers"}.difference(matches)
    if missing:
        raise PublicPITFormatError(f"public PIT source missing columns: {sorted(missing)}")
    return matches["date"], matches["tickers"]


def _research_bounds(
    research_start: object | None,
    research_end: object | None,
    *,
    source_start: pd.Timestamp,
    source_end: pd.Timestamp,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = source_start if research_start is None else pd.Timestamp(research_start).normalize()
    end = source_end if research_end is None else pd.Timestamp(research_end).normalize()
    if start.tz is not None or end.tz is not None:
        raise PublicPITFormatError("research bounds must be timezone-naive")
    if start > end:
        raise PublicPITFormatError("research_start must be on or before research_end")
    if start < source_start:
        raise PublicPITFormatError(
            f"research_start precedes source coverage ({source_start.date()})"
        )
    if end > source_end:
        raise PublicPITFormatError(
            f"research_end exceeds source coverage ({source_end.date()})"
        )
    return start, end


def _snapshot_in_scope(
    date: pd.Timestamp,
    all_dates: pd.DatetimeIndex,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> bool:
    anchor_position = int(all_dates.searchsorted(start, side="right") - 1)
    anchor = pd.Timestamp(all_dates[anchor_position])
    return anchor <= date <= end


def _snapshots_to_intervals(
    dates: pd.DatetimeIndex, snapshot_sets: list[frozenset[str]]
) -> pd.DataFrame:
    active_since: dict[str, pd.Timestamp] = {}
    rows: list[dict[str, object]] = []
    previous: frozenset[str] = frozenset()
    for date, current in zip(dates, snapshot_sets, strict=True):
        current_date = pd.Timestamp(date)
        for ticker in sorted(previous.difference(current)):
            rows.append(
                {
                    "sid": source_ticker_to_sid(ticker),
                    "effective_from": active_since.pop(ticker),
                    "effective_to": current_date,
                    "raw_ticker": ticker,
                    "prototype_only": True,
                }
            )
        for ticker in sorted(current.difference(previous)):
            active_since[ticker] = current_date
        previous = current
    for ticker, effective_from in sorted(active_since.items()):
        rows.append(
            {
                "sid": source_ticker_to_sid(ticker),
                "effective_from": effective_from,
                "effective_to": pd.NaT,
                "raw_ticker": ticker,
                "prototype_only": True,
            }
        )
    return pd.DataFrame.from_records(rows).sort_values(
        ["effective_from", "sid"], ignore_index=True
    )


def _scope_intervals(
    intervals: pd.DataFrame, *, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    intersects = (intervals["effective_from"] <= end) & (
        intervals["effective_to"].isna() | (intervals["effective_to"] > start)
    )
    return intervals.loc[intersects].reset_index(drop=True)


def _long_snapshots(
    dates: pd.DatetimeIndex,
    snapshot_sets: list[frozenset[str]],
    *,
    include_dates: pd.DatetimeIndex,
    allowed_tickers: frozenset[str],
) -> pd.DataFrame:
    include = frozenset(pd.Timestamp(date) for date in include_dates)
    rows = [
        {
            "date": pd.Timestamp(date),
            "sid": source_ticker_to_sid(ticker),
            "raw_ticker": ticker,
            "prototype_only": True,
        }
        for date, members in zip(dates, snapshot_sets, strict=True)
        if pd.Timestamp(date) in include
        for ticker in sorted(members.intersection(allowed_tickers))
    ]
    if not rows:
        raise PublicPITFormatError("research scope produced no membership snapshots")
    return pd.DataFrame.from_records(rows)


def _security_master(tickers: frozenset[str]) -> pd.DataFrame:
    rows = [
        {
            "sid": source_ticker_to_sid(raw_ticker),
            "provider": "yfinance",
            "provider_sid": "",
            "ticker": source_ticker_to_yahoo(raw_ticker),
            "name": "",
            "valid_from": pd.NaT,
            "valid_to": pd.NaT,
            "raw_ticker": raw_ticker,
            "identity_basis": "source_ticker_only",
            "prototype_only": True,
        }
        for raw_ticker in sorted(tickers)
    ]
    return pd.DataFrame.from_records(rows)


def _anomaly(
    severity: str,
    code: str,
    *,
    date: object = pd.NaT,
    raw_ticker: str = "",
    affected_count: int,
    detail: str,
) -> dict[str, object]:
    return {
        "severity": severity,
        "code": code,
        "date": date,
        "raw_ticker": raw_ticker,
        "affected_count": affected_count,
        "detail": detail,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert fja05680 Updated S&P 500 history to prototype PIT inputs"
    )
    parser.add_argument("source_csv", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--research-start")
    parser.add_argument("--research-end")
    parser.add_argument("--source-url", default=FJA05680_UPDATED_SOURCE_URL)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    result = write_fja05680_prototype(
        args.source_csv,
        args.output_dir,
        research_start=args.research_start,
        research_end=args.research_end,
        source_url=args.source_url,
    )
    print(result.manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
