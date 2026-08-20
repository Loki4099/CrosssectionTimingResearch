"""Unified, auditable factor database assembly and publication helpers.

The factor calculators remain the owners of economic definitions.  This module
only standardises their panels, completes the point-in-time key space, ranks
eligible observations, reports coverage, and publishes immutable Parquet
content with a deterministic manifest.

All assembly and QA functions are pure.  The two explicitly named write
functions are the only filesystem-mutating entry points, and both require a
caller-supplied output directory.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd

from momentum_reversal.data.membership import PITMembership


KEY_COLUMNS: tuple[str, ...] = ("signal_date", "sid", "factor_id")
CORE_COLUMNS: tuple[str, ...] = (
    "signal_date",
    "sid",
    "factor_id",
    "raw_value",
    "score",
    "eligible",
    "missing_reason",
    "rank",
    "percentile",
    "source_panel",
)

COVERAGE_FILENAMES: Mapping[str, str] = {
    "factor": "factor_coverage.parquet",
    "date": "date_coverage.parquet",
    "year": "year_coverage.parquet",
    "missing_reason": "missing_reason_coverage.parquet",
}

_NO_SOURCE_REASON = "no_source_factor_row"
_MISSING_VALUE_REASON = "missing_score_or_raw_value"
_SOURCE_INELIGIBLE_REASON = "source_marked_ineligible"


def build_factor_database(
    market_panel: pd.DataFrame,
    fundamental_panel: pd.DataFrame,
    membership: PITMembership | pd.DataFrame | Any,
    signal_dates: Iterable[object],
    active_factors: pd.DataFrame | Iterable[str],
) -> pd.DataFrame:
    """Build the complete monthly member-by-factor research panel.

    Only factor IDs in ``active_factors`` and only members active on each signal
    date are emitted.  Missing source rows are not dropped: they become explicit
    ineligible rows with ``no_source_factor_row``.  Source rows for inactive
    factors or non-members do not enter the database.

    Current market and fundamental panels expose both the economically natural
    ``raw_value`` and the long-only directional ``score``.  For compatibility
    with previously materialised score-only panels, a missing ``raw_value``
    column is interpreted as ``raw_value == score``; explicit raw values are
    always preserved without reconstruction.
    """

    signals = _normalise_signal_dates(signal_dates)
    factor_ids, source_factor_map = _normalise_active_factors(active_factors)
    universe = _normalise_membership(membership)
    members_by_date = _members_for_signals(universe, signals)

    market = _standardise_source_panel(
        market_panel, source_panel="market", signal_dates=signals
    )
    fundamental = _standardise_source_panel(
        fundamental_panel, source_panel="fundamental", signal_dates=signals
    )
    source = pd.concat([market, fundamental], ignore_index=True, sort=False)
    if source.duplicated(list(KEY_COLUMNS)).any():
        duplicate = source.loc[
            source.duplicated(list(KEY_COLUMNS), keep=False), list(KEY_COLUMNS)
        ].head(5)
        raise ValueError(
            "source panels overlap or contain duplicate factor keys: "
            f"{duplicate.to_dict(orient='records')}"
        )

    expected_rows: list[tuple[pd.Timestamp, str, str]] = []
    for signal_date in signals:
        members = members_by_date[pd.Timestamp(signal_date)]
        expected_rows.extend(
            (pd.Timestamp(signal_date), sid, factor_id)
            for sid in members
            for factor_id in factor_ids
        )
    expected = pd.DataFrame(expected_rows, columns=KEY_COLUMNS)

    source["source_factor_id"] = source["factor_id"]
    source["factor_id"] = source["factor_id"].map(source_factor_map)
    source = source.loc[source["factor_id"].notna()].copy()
    if source.duplicated(list(KEY_COLUMNS)).any():
        raise ValueError(
            "source_definition_id mapping creates duplicate active factor keys"
        )
    active = set(factor_ids)
    source = source.loc[
        source["signal_date"].isin(signals) & source["factor_id"].isin(active)
    ].copy()
    merged = expected.merge(
        source,
        on=list(KEY_COLUMNS),
        how="left",
        sort=False,
        validate="one_to_one",
    )

    absent = merged["source_panel"].isna()
    merged.loc[absent, "source_panel"] = "missing"
    merged.loc[absent, "raw_value"] = np.nan
    merged.loc[absent, "score"] = np.nan
    merged.loc[absent, "eligible"] = False
    merged.loc[absent, "missing_reason"] = _NO_SOURCE_REASON

    merged["eligible"] = merged["eligible"].fillna(False).astype(bool)
    merged["raw_value"] = pd.to_numeric(
        merged["raw_value"], errors="coerce"
    ).astype(float)
    merged["score"] = pd.to_numeric(merged["score"], errors="coerce").astype(
        float
    )
    finite = np.isfinite(merged["raw_value"].to_numpy()) & np.isfinite(
        merged["score"].to_numpy()
    )
    newly_invalid = merged["eligible"] & ~finite
    merged.loc[newly_invalid, "eligible"] = False
    merged.loc[
        newly_invalid & merged["missing_reason"].isna(), "missing_reason"
    ] = _MISSING_VALUE_REASON
    missing_reason = (
        merged["missing_reason"].astype("string").str.strip().replace("", pd.NA)
    )
    merged["missing_reason"] = missing_reason
    needs_reason = ~merged["eligible"] & merged["missing_reason"].isna()
    merged.loc[needs_reason, "missing_reason"] = _SOURCE_INELIGIBLE_REASON
    if (merged["eligible"] & merged["missing_reason"].notna()).any():
        raise ValueError("eligible source rows cannot carry a missing_reason")

    merged["rank"] = pd.Series(pd.NA, index=merged.index, dtype="Int64")
    merged["percentile"] = np.nan
    eligible_index = merged.index[merged["eligible"] & finite]
    ranked = merged.loc[
        eligible_index, ["signal_date", "factor_id", "sid", "score"]
    ].sort_values(
        ["signal_date", "factor_id", "score", "sid"],
        ascending=[True, True, False, True],
        kind="mergesort",
    )
    ranked["rank"] = (
        ranked.groupby(["signal_date", "factor_id"], sort=False).cumcount() + 1
    )
    ranked["eligible_count"] = ranked.groupby(
        ["signal_date", "factor_id"], sort=False
    )["sid"].transform("size")
    ranked["percentile"] = 1.0 - (
        (ranked["rank"] - 1) / ranked["eligible_count"]
    )
    merged.loc[ranked.index, "rank"] = pd.array(ranked["rank"], dtype="Int64")
    merged.loc[ranked.index, "percentile"] = ranked["percentile"].to_numpy()

    merged["missing_reason"] = merged["missing_reason"].astype("string")
    merged["source_panel"] = merged["source_panel"].astype("string")
    merged = merged.sort_values(list(KEY_COLUMNS), kind="mergesort").reset_index(
        drop=True
    )
    extra_columns = sorted(set(merged.columns).difference(CORE_COLUMNS))
    result = merged.loc[:, [*CORE_COLUMNS, *extra_columns]]
    validate_factor_database(result)
    return result


def build_factor_coverage_qa(
    factor_database: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Return deterministic factor/date/year/missing-reason coverage tables."""

    validate_factor_database(factor_database)
    frame = factor_database.copy()
    frame["signal_date"] = _date_column(frame["signal_date"], "signal_date")
    frame["year"] = frame["signal_date"].dt.year.astype(int)
    qa = {
        "factor": _coverage_summary(frame, ["factor_id"]),
        "date": _coverage_summary(frame, ["signal_date", "factor_id"]),
        "year": _coverage_summary(frame, ["year", "factor_id"]),
        "missing_reason": _missing_reason_summary(frame),
    }
    return qa


def validate_factor_database(factor_database: pd.DataFrame) -> None:
    """Raise on broken keys, non-finite values, or rank-contract violations."""

    required = set(CORE_COLUMNS)
    missing = required.difference(factor_database.columns)
    if missing:
        raise ValueError(f"factor database missing columns: {sorted(missing)}")
    if factor_database.duplicated(list(KEY_COLUMNS)).any():
        raise ValueError("factor database key must be unique")
    if factor_database[list(KEY_COLUMNS)].isna().any().any():
        raise ValueError("factor database keys cannot be missing")
    if factor_database.empty:
        return

    dates = pd.to_datetime(factor_database["signal_date"], errors="coerce")
    if dates.isna().any():
        raise ValueError("signal_date contains invalid dates")
    if getattr(dates.dt, "tz", None) is not None:
        raise ValueError("signal_date must be timezone-naive")
    for column in ("sid", "factor_id"):
        values = factor_database[column].astype("string").str.strip()
        if values.isna().any() or values.eq("").any():
            raise ValueError(f"{column} cannot be blank")

    raw = _numeric_column(factor_database["raw_value"], "raw_value")
    score = _numeric_column(factor_database["score"], "score")
    eligible = _coerce_boolean(factor_database["eligible"], "eligible")
    finite = np.isfinite(raw.to_numpy()) & np.isfinite(score.to_numpy())
    if (eligible.to_numpy() & ~finite).any():
        raise ValueError("eligible rows require finite raw_value and score")

    reason = factor_database["missing_reason"].astype("string").str.strip()
    if (eligible.to_numpy() & reason.notna().to_numpy()).any():
        raise ValueError("eligible rows cannot carry missing_reason")
    if ((~eligible.to_numpy()) & (reason.isna() | reason.eq("")).to_numpy()).any():
        raise ValueError("ineligible rows require missing_reason")

    rank = pd.to_numeric(factor_database["rank"], errors="coerce")
    percentile = pd.to_numeric(factor_database["percentile"], errors="coerce")
    if rank.loc[~eligible].notna().any() or percentile.loc[~eligible].notna().any():
        raise ValueError("ineligible rows cannot be ranked")
    if rank.loc[eligible].isna().any() or percentile.loc[eligible].isna().any():
        raise ValueError("eligible rows require rank and percentile")
    if eligible.any():
        eligible_rank = rank.loc[eligible].to_numpy(dtype=float)
        eligible_percentile = percentile.loc[eligible].to_numpy(dtype=float)
        if (eligible_rank < 1).any() or not np.equal(
            eligible_rank, np.floor(eligible_rank)
        ).all():
            raise ValueError("rank must contain positive integers")
        if (
            ~np.isfinite(eligible_percentile).all()
            or (eligible_percentile <= 0).any()
            or (eligible_percentile > 1).any()
        ):
            raise ValueError("eligible percentile must be in (0, 1]")

    expected = factor_database.loc[
        eligible, ["signal_date", "factor_id", "sid", "score", "rank", "percentile"]
    ].sort_values(
        ["signal_date", "factor_id", "score", "sid"],
        ascending=[True, True, False, True],
        kind="mergesort",
    )
    if not expected.empty:
        expected_rank = (
            expected.groupby(["signal_date", "factor_id"], sort=False).cumcount()
            + 1
        )
        group_size = expected.groupby(
            ["signal_date", "factor_id"], sort=False
        )["sid"].transform("size")
        expected_percentile = 1.0 - (expected_rank - 1) / group_size
        if not np.array_equal(
            expected["rank"].to_numpy(dtype=int), expected_rank.to_numpy(dtype=int)
        ):
            raise ValueError("rank does not follow descending score and SID tie-break")
        if not np.allclose(
            expected["percentile"].to_numpy(dtype=float),
            expected_percentile.to_numpy(dtype=float),
            rtol=0.0,
            atol=1e-15,
        ):
            raise ValueError("percentile is inconsistent with deterministic rank")


def assert_past_factor_invariance(
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
    *,
    through_date: object,
) -> bool:
    """Assert that adding or changing future inputs left past output unchanged."""

    cutoff = _normalise_one_date(through_date, "through_date")
    left = _comparison_panel(baseline, cutoff)
    right = _comparison_panel(candidate, cutoff)
    pd.testing.assert_frame_equal(left, right, check_like=False)
    return True


def atomic_write_parquet(
    frame: pd.DataFrame,
    output_dir: str | Path,
    *,
    filename: str,
) -> Path:
    """Write one Parquet file through a same-directory temporary file."""

    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if Path(filename).name != filename or not filename.endswith(".parquet"):
        raise ValueError("filename must be a basename ending in .parquet")
    target = directory / filename
    temporary = directory / f".{filename}.{uuid4().hex}.tmp"
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def build_content_manifest(
    files: Mapping[str, str | Path],
    *,
    base_dir: str | Path | None = None,
    schema_version: str = "cross_sectional_alpha.factor_content.v1",
) -> dict[str, Any]:
    """Describe Parquet evidence by SHA256, byte size, rows, and Arrow schema."""

    if not files:
        raise ValueError("files cannot be empty")
    base = None if base_dir is None else Path(base_dir).resolve()
    entries: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for logical_name, raw_path in sorted(files.items()):
        name = str(logical_name).strip()
        if not name:
            raise ValueError("manifest logical names cannot be blank")
        path = Path(raw_path).resolve()
        if path in seen_paths:
            raise ValueError(f"manifest repeats file path: {path}")
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.lower() != ".parquet":
            raise ValueError(f"manifest content must be Parquet: {path}")
        seen_paths.add(path)
        metadata = _parquet_metadata(path)
        display_path = path.name
        if base is not None:
            try:
                display_path = path.relative_to(base).as_posix()
            except ValueError as error:
                raise ValueError(f"manifest file is outside base_dir: {path}") from error
        entries.append(
            {
                "logical_name": name,
                "path": display_path,
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
                "rows": metadata["rows"],
                "schema": metadata["schema"],
            }
        )

    canonical_entries = json.dumps(
        entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "schema_version": str(schema_version),
        "content_sha256": hashlib.sha256(canonical_entries).hexdigest(),
        "files": entries,
    }


def write_factor_database_bundle(
    factor_database: pd.DataFrame,
    output_dir: str | Path,
    *,
    coverage_qa: Mapping[str, pd.DataFrame] | None = None,
    factor_filename: str = "factor_values.parquet",
    manifest_filename: str = "factor_content_manifest.json",
) -> dict[str, Any]:
    """Atomically publish factor/QA Parquets, then publish their manifest last."""

    validate_factor_database(factor_database)
    qa = (
        build_factor_coverage_qa(factor_database)
        if coverage_qa is None
        else _normalise_coverage_mapping(coverage_qa)
    )
    directory = Path(output_dir).resolve()
    if Path(manifest_filename).name != manifest_filename or not manifest_filename.endswith(
        ".json"
    ):
        raise ValueError("manifest_filename must be a basename ending in .json")
    if factor_filename in set(COVERAGE_FILENAMES.values()):
        raise ValueError("factor_filename cannot collide with a coverage filename")

    paths: dict[str, Path] = {
        "factor_values": atomic_write_parquet(
            factor_database, directory, filename=factor_filename
        )
    }
    for name, filename in COVERAGE_FILENAMES.items():
        paths[f"coverage_{name}"] = atomic_write_parquet(
            qa[name], directory, filename=filename
        )
    manifest = build_content_manifest(paths, base_dir=directory)
    _atomic_write_json(manifest, directory / manifest_filename)
    return manifest


def _standardise_source_panel(
    panel: pd.DataFrame,
    *,
    source_panel: str,
    signal_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    frame = panel.copy()
    missing_keys = set(KEY_COLUMNS).difference(frame.columns)
    if missing_keys and isinstance(frame.index, pd.MultiIndex) and set(
        KEY_COLUMNS
    ).issubset(frame.index.names):
        frame = frame.reset_index()
    required = {*KEY_COLUMNS, "score"}
    missing = required.difference(frame.columns)
    if missing:
        if frame.empty:
            return pd.DataFrame(
                columns=[
                    *KEY_COLUMNS,
                    "raw_value",
                    "score",
                    "eligible",
                    "missing_reason",
                    "source_panel",
                ]
            )
        raise ValueError(
            f"{source_panel} panel missing columns: {sorted(missing)}"
        )
    frame["signal_date"] = _date_column(
        frame["signal_date"], f"{source_panel} signal_date"
    )
    frame["sid"] = _string_column(frame["sid"], f"{source_panel} sid")
    frame["factor_id"] = _string_column(
        frame["factor_id"], f"{source_panel} factor_id"
    )
    # Rows outside the explicitly requested build dates are outside this
    # content version.  Discard them before value validation so even malformed
    # future observations cannot affect a past-only rebuild.
    frame = frame.loc[frame["signal_date"].isin(signal_dates)].copy()
    if frame.duplicated(list(KEY_COLUMNS)).any():
        raise ValueError(f"{source_panel} panel factor key must be unique")

    frame["score"] = _numeric_column(frame["score"], f"{source_panel} score")
    if "raw_value" in frame.columns:
        frame["raw_value"] = _numeric_column(
            frame["raw_value"], f"{source_panel} raw_value"
        )
    else:
        # Compatibility with legacy score-only source panels. New factor
        # calculators should always publish raw_value explicitly.
        frame["raw_value"] = frame["score"]

    if "missing_reason" not in frame.columns:
        frame["missing_reason"] = pd.NA
    frame["missing_reason"] = (
        frame["missing_reason"].astype("string").str.strip().replace("", pd.NA)
    )
    finite = np.isfinite(frame["raw_value"].to_numpy()) & np.isfinite(
        frame["score"].to_numpy()
    )
    if "eligible" in frame.columns:
        source_eligible = _coerce_boolean(
            frame["eligible"], f"{source_panel} eligible"
        )
    else:
        source_eligible = pd.Series(
            finite & frame["missing_reason"].isna().to_numpy(),
            index=frame.index,
            dtype=bool,
        )
        if "data_gate" in frame.columns:
            source_eligible &= frame["data_gate"].astype("string").eq("pass")
    frame["eligible"] = source_eligible & finite
    invalidated = source_eligible & ~finite
    frame.loc[
        invalidated & frame["missing_reason"].isna(), "missing_reason"
    ] = _MISSING_VALUE_REASON
    needs_reason = ~frame["eligible"] & frame["missing_reason"].isna()
    frame.loc[needs_reason, "missing_reason"] = _SOURCE_INELIGIBLE_REASON
    if (frame["eligible"] & frame["missing_reason"].notna()).any():
        raise ValueError(
            f"{source_panel} eligible rows cannot carry missing_reason"
        )
    frame["source_panel"] = source_panel
    return frame


def _normalise_signal_dates(values: Iterable[object]) -> pd.DatetimeIndex:
    dates = pd.DatetimeIndex(pd.to_datetime(list(values), errors="coerce"))
    if dates.tz is not None:
        raise ValueError("signal_dates must be timezone-naive")
    dates = dates.normalize()
    if dates.empty or dates.hasnans:
        raise ValueError("signal_dates cannot be empty or contain invalid dates")
    if dates.has_duplicates:
        raise ValueError("signal_dates cannot contain duplicates")
    return dates.sort_values()


def _normalise_active_factors(
    value: pd.DataFrame | Iterable[str],
) -> tuple[tuple[str, ...], Mapping[str, str]]:
    if isinstance(value, pd.DataFrame):
        if "factor_id" not in value.columns:
            raise ValueError("active factor registry missing factor_id")
        raw = value["factor_id"].tolist()
        source_raw = (
            value["source_definition_id"].tolist()
            if "source_definition_id" in value.columns
            else raw
        )
    elif isinstance(value, str):
        raw = [value]
        source_raw = raw
    else:
        raw = list(value)
        source_raw = raw
    factor_ids = tuple(_normalise_string(item, "factor_id") for item in raw)
    if not factor_ids:
        raise ValueError("active_factors cannot be empty")
    if len(set(factor_ids)) != len(factor_ids):
        raise ValueError("active_factors cannot contain duplicate factor_id")
    source_ids = tuple(
        factor_id
        if pd.isna(source_id) or not str(source_id).strip()
        else _normalise_string(source_id, "source_definition_id")
        for factor_id, source_id in zip(factor_ids, source_raw, strict=True)
    )
    source_map: dict[str, str] = {factor_id: factor_id for factor_id in factor_ids}
    for source_id, factor_id in zip(source_ids, factor_ids, strict=True):
        existing = source_map.get(source_id)
        if existing is not None and existing != factor_id:
            raise ValueError(
                f"source_definition_id maps to multiple active factors: {source_id}"
            )
        source_map[source_id] = factor_id
    return factor_ids, source_map


def _normalise_membership(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        if {"sid", "effective_from", "effective_to"}.issubset(value.columns):
            return PITMembership.from_intervals(value)
        if {"date", "sid"}.issubset(value.columns):
            return PITMembership.from_snapshots(value)
        raise ValueError(
            "membership DataFrame must contain interval or snapshot columns"
        )
    if not hasattr(value, "members_on") or not callable(value.members_on):
        raise TypeError("membership must expose members_on(date)")
    return value


def _members_for_signals(
    membership: Any, signals: pd.DatetimeIndex
) -> Mapping[pd.Timestamp, tuple[str, ...]]:
    result: dict[pd.Timestamp, tuple[str, ...]] = {}
    for date in signals:
        members = tuple(
            sorted(
                {
                    _normalise_string(value, "membership sid")
                    for value in membership.members_on(date)
                }
            )
        )
        if not members:
            raise ValueError(f"membership is empty on {date.date()}")
        result[pd.Timestamp(date)] = members
    return result


def _coverage_summary(frame: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    grouped = frame.groupby(list(keys), sort=True, dropna=False)
    result = grouped.agg(
        total_rows=("eligible", "size"),
        eligible_rows=("eligible", "sum"),
    ).reset_index()
    result["eligible_rows"] = result["eligible_rows"].astype(int)
    result["missing_rows"] = result["total_rows"] - result["eligible_rows"]
    result["coverage_rate"] = result["eligible_rows"] / result["total_rows"]
    return result


def _missing_reason_summary(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "factor_id",
        "missing_reason",
        "missing_rows",
        "factor_rows",
        "factor_missing_rows",
        "share_of_factor_rows",
        "share_of_factor_missing_rows",
    ]
    missing = frame.loc[~frame["eligible"], ["factor_id", "missing_reason"]]
    if missing.empty:
        return pd.DataFrame(columns=columns)
    counts = (
        missing.groupby(["factor_id", "missing_reason"], sort=True, dropna=False)
        .size()
        .rename("missing_rows")
        .reset_index()
    )
    factor_rows = frame.groupby("factor_id", sort=True).size()
    factor_missing = missing.groupby("factor_id", sort=True).size()
    counts["factor_rows"] = counts["factor_id"].map(factor_rows).astype(int)
    counts["factor_missing_rows"] = (
        counts["factor_id"].map(factor_missing).astype(int)
    )
    counts["share_of_factor_rows"] = (
        counts["missing_rows"] / counts["factor_rows"]
    )
    counts["share_of_factor_missing_rows"] = (
        counts["missing_rows"] / counts["factor_missing_rows"]
    )
    return counts.loc[:, columns]


def _comparison_panel(frame: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    data = frame.copy()
    if "signal_date" not in data.columns and isinstance(
        data.index, pd.MultiIndex
    ) and "signal_date" in data.index.names:
        data = data.reset_index()
    if "signal_date" not in data.columns:
        raise ValueError("comparison panel missing signal_date")
    data["signal_date"] = _date_column(data["signal_date"], "signal_date")
    data = data.loc[data["signal_date"] <= cutoff].copy()
    sort_keys = [column for column in KEY_COLUMNS if column in data.columns]
    if not sort_keys:
        sort_keys = ["signal_date"]
    return data.sort_values(sort_keys, kind="mergesort").reset_index(drop=True)


def _normalise_coverage_mapping(
    value: Mapping[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    missing = set(COVERAGE_FILENAMES).difference(value)
    extra = set(value).difference(COVERAGE_FILENAMES)
    if missing or extra:
        raise ValueError(
            f"coverage_qa keys mismatch; missing={sorted(missing)}, "
            f"extra={sorted(extra)}"
        )
    return {key: value[key].copy() for key in COVERAGE_FILENAMES}


def _parquet_metadata(path: Path) -> dict[str, Any]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:  # pragma: no cover - optional dependency guard
        raise RuntimeError(
            "pyarrow is required to inspect Parquet content manifests"
        ) from error
    parquet = pq.ParquetFile(path)
    schema = [
        {
            "name": field.name,
            "type": str(field.type),
            "nullable": bool(field.nullable),
        }
        for field in parquet.schema_arrow
    ]
    return {"rows": int(parquet.metadata.num_rows), "schema": schema}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(value: Mapping[str, Any], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _date_column(values: pd.Series, label: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    if parsed.isna().any():
        raise ValueError(f"{label} cannot contain missing or invalid dates")
    if getattr(parsed.dt, "tz", None) is not None:
        raise ValueError(f"{label} must be timezone-naive")
    return parsed.dt.normalize()


def _normalise_one_date(value: object, label: str) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{label} must be a valid date")
    result = pd.Timestamp(parsed)
    if result.tz is not None:
        raise ValueError(f"{label} must be timezone-naive")
    return result.normalize()


def _string_column(values: pd.Series, label: str) -> pd.Series:
    result = values.astype("string").str.strip()
    if result.isna().any() or result.eq("").any():
        raise ValueError(f"{label} cannot be blank")
    return result


def _normalise_string(value: object, label: str) -> str:
    if pd.isna(value):
        raise ValueError(f"{label} cannot be missing")
    result = str(value).strip()
    if not result:
        raise ValueError(f"{label} cannot be blank")
    return result


def _numeric_column(values: pd.Series, label: str) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    invalid = values.notna() & numeric.isna()
    if invalid.any():
        raise ValueError(f"{label} contains non-numeric values")
    if np.isinf(numeric.to_numpy()).any():
        raise ValueError(f"{label} cannot contain infinity")
    return numeric


def _coerce_boolean(values: pd.Series, label: str) -> pd.Series:
    if values.isna().any():
        raise ValueError(f"{label} cannot be missing")
    if pd.api.types.is_bool_dtype(values.dtype):
        return values.astype(bool)
    accepted = {True, False, 1, 0, "true", "false", "True", "False"}
    if not values.isin(accepted).all():
        raise ValueError(f"{label} must be boolean")
    mapping = {
        True: True,
        False: False,
        1: True,
        0: False,
        "true": True,
        "false": False,
        "True": True,
        "False": False,
    }
    return values.map(mapping).astype(bool)


# Short, discoverable aliases for callers that describe this operation as a
# merge rather than a database build.
merge_factor_panels = build_factor_database
build_unified_factor_panel = build_factor_database
future_invariance_check = assert_past_factor_invariance


__all__ = [
    "CORE_COLUMNS",
    "COVERAGE_FILENAMES",
    "KEY_COLUMNS",
    "assert_past_factor_invariance",
    "atomic_write_parquet",
    "build_content_manifest",
    "build_factor_coverage_qa",
    "build_factor_database",
    "build_unified_factor_panel",
    "future_invariance_check",
    "merge_factor_panels",
    "validate_factor_database",
    "write_factor_database_bundle",
]
