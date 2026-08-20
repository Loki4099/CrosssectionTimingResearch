"""Certify the frozen cross-sectional market and SEC source-data layers.

This command deliberately does not read or require the factor stage.  It
verifies the frozen parent market bundle, the SID-to-CIK bridge, the immutable
SEC fetch ledger, and the complete fundamental bundle.  Accounting-identity
and historical entity-support QA are independently recomputed from the
authoritative aggregate tables and compared value-for-value with the stored
artifacts.

The runtime certificate is deterministic and atomically replaced only after
all gates pass.  An optional Git-facing publication contains a fixed, compact
JSON/CSV/README whitelist; Parquet, raw payloads, ledgers, and DuckDB files are
never copied.  Existing publications are accepted only when byte-identical.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from momentum_reversal.data.entity_bridge import (  # noqa: E402
    member_session_mapping_coverage,
)
from momentum_reversal.data.entity_temporal_audit import (  # noqa: E402
    build_entity_temporal_support_qa,
)
from momentum_reversal.pipelines.cross_sectional_database import (  # noqa: E402
    DatabaseLayout,
    _fundamental_build_signature,
    build_accounting_identity_qa,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROHIBITED_PUBLISH_SUFFIXES = {".bin", ".duckdb", ".jsonl", ".parquet"}
_PUBLISH_FILENAMES = {
    "README.md",
    "manifest.json",
    "source_data_certification.json",
    "market_freeze.json",
    "identifier_qa.json",
    "fundamental_freeze.json",
    "fundamental_summary.json",
    "accounting_identity_summary.json",
    "entity_temporal_support_summary.json",
    "source_applicability.csv",
    "accounting_identity_qa.csv",
    "entity_temporal_support_qa.csv",
    "fetch_failures.csv",
}
_MAX_PUBLISH_FILE_BYTES = 5 * 1024 * 1024


class SourceDataCertificationError(RuntimeError):
    """Raised when source evidence is incomplete, inconsistent, or mutable."""


def certify_cross_sectional_source_data(
    layout: DatabaseLayout,
    *,
    certification_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the source-only gates and atomically write a portable certificate."""

    market = _certify_market_parent(layout)
    identifier, identifier_frames = _certify_identifier(
        layout, market_sessions=market.pop("_sessions"),
        market_membership=market.pop("_membership")
    )
    raw_sec, ledger_index = _certify_sec_raw_ledger(layout.sec_store_root)
    fundamental, fundamental_frames = _certify_fundamental_bundle(
        layout,
        identifier_intervals=identifier_frames["intervals"],
        ledger_index=ledger_index,
    )
    applicability = _certify_source_applicability(
        layout,
        fundamental_frames["source_applicability"],
        expected_ciks=fundamental_frames["expected_ciks"],
        ledger_index=ledger_index,
        fundamental_summary=fundamental_frames["summary"],
    )
    accounting = _certify_accounting_identity(
        layout,
        canonical=fundamental_frames["canonical"],
        stored=fundamental_frames["accounting_qa"],
        stored_summary=fundamental_frames["accounting_summary"],
        fundamental_summary=fundamental_frames["summary"],
    )
    temporal = _certify_temporal_support(
        layout,
        filings=fundamental_frames["filings"],
        intervals=fundamental_frames["intervals"],
        source_applicability=fundamental_frames["source_applicability"],
        stored=fundamental_frames["temporal_qa"],
        stored_summary=fundamental_frames["temporal_summary"],
        fundamental_summary=fundamental_frames["summary"],
    )

    certificate: dict[str, Any] = {
        "schema_version": "cross_sectional_alpha.source_data_certification.v1",
        "status": "source_data_certified",
        "read_only_source_validation": True,
        "factor_stage_required": False,
        "formal_eligible": False,
        "experiment_authorized": False,
        "program": {
            "program_id": str(layout.program.get("program_id", "")),
            "program_sha256": _sha256_file(layout.program_path),
            "market_dataset": str(layout.program["versions"]["market_dataset"]),
            "entity_bridge_version": str(
                layout.program["versions"]["entity_bridge"]
            ),
            "fundamental_version": str(
                layout.program["versions"]["fundamentals"]
            ),
        },
        "market_parent": market,
        "identifier": identifier,
        "fundamental": fundamental,
        "sec_raw": raw_sec,
        "quality_gates": {
            "market_frozen_parent_anchor_verified": True,
            "identifier_manifest_members_verified": True,
            "identifier_member_session_coverage_recomputed": True,
            "fundamental_freeze_manifest_summary_exactly_anchored": True,
            "fundamental_manifest_members_verified": True,
            "per_cik_manifests_and_raw_record_anchors_verified": True,
            "aggregate_fetch_failure_rows": 0,
            "source_applicability": applicability,
            "accounting_identity": accounting,
            "entity_temporal_support": temporal,
            "source_data_certification_passed": True,
        },
    }

    selected_path = (
        Path(certification_path).expanduser().resolve()
        if certification_path is not None
        else (
            layout.runtime_root
            / "data"
            / "certifications"
            / "cross_sectional_alpha"
            / str(layout.program["versions"]["fundamentals"])
            / "source_data_certification.json"
        ).resolve()
    )
    _atomic_json(certificate, selected_path)
    return certificate


def publish_source_data_certification(
    layout: DatabaseLayout,
    certificate: Mapping[str, Any],
    *,
    destination: str | Path,
    allowed_publish_root: str | Path,
) -> dict[str, Any]:
    """Publish the fixed compact source-QA whitelist atomically."""

    destination_path = Path(destination).expanduser().resolve()
    allowed_root = Path(allowed_publish_root).expanduser().resolve()
    _require_publish_destination(destination_path, allowed_root)
    allowed_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination_path.name}.building-", dir=allowed_root)
    ).resolve()
    _require_temporary(temporary, allowed_root, destination_path.name)
    try:
        _write_json(temporary / "source_data_certification.json", certificate)
        _copy_portable_json(layout.market_root / "FROZEN.json", temporary / "market_freeze.json")
        _copy_portable_json(layout.identifier_root / "mapping_qa.json", temporary / "identifier_qa.json")
        _copy_portable_json(layout.curated_root / "FROZEN.json", temporary / "fundamental_freeze.json")
        _copy_portable_json(layout.curated_root / "build_summary.json", temporary / "fundamental_summary.json")
        _copy_portable_json(
            layout.curated_root / "accounting_identity_summary.json",
            temporary / "accounting_identity_summary.json",
        )
        _copy_portable_json(
            layout.curated_root / "entity_temporal_support_summary.json",
            temporary / "entity_temporal_support_summary.json",
        )
        _parquet_to_stable_csv(
            layout.curated_root / "source_applicability.parquet",
            temporary / "source_applicability.csv",
            keys=("cik10",),
        )
        _parquet_to_stable_csv(
            layout.curated_root / "accounting_identity_qa.parquet",
            temporary / "accounting_identity_qa.csv",
            keys=("cik", "period_end", "accession", "unit"),
        )
        _parquet_to_stable_csv(
            layout.curated_root / "entity_temporal_support_qa.parquet",
            temporary / "entity_temporal_support_qa.csv",
            keys=("sid", "effective_from", "cik10"),
        )
        _parquet_to_stable_csv(
            layout.curated_root / "fetch_failures.parquet",
            temporary / "fetch_failures.csv",
            keys=("cik10",),
        )
        (temporary / "README.md").write_text(
            _publication_readme(certificate), encoding="utf-8", newline="\n"
        )
        _verify_publish_tree(temporary, expect_manifest=False)
        _write_json(
            temporary / "manifest.json",
            _publication_manifest(certificate, temporary),
        )
        _verify_publish_tree(temporary, expect_manifest=True)

        if destination_path.exists():
            if not destination_path.is_dir():
                raise SourceDataCertificationError(
                    f"publication destination is not a directory: {destination_path}"
                )
            if _tree_index(destination_path) != _tree_index(temporary):
                raise SourceDataCertificationError(
                    "existing source-data publication differs; refusing overwrite"
                )
            return {
                "status": "already_published_identical",
                "file_count": len(_tree_index(destination_path)),
            }
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, destination_path)
        return {
            "status": "published",
            "file_count": len(_tree_index(destination_path)),
        }
    finally:
        if temporary.exists():
            _require_temporary(temporary, allowed_root, destination_path.name)
            shutil.rmtree(temporary)


def _certify_market_parent(layout: DatabaseLayout) -> dict[str, Any]:
    freeze_path = layout.market_root / "FROZEN.json"
    freeze = _read_json(freeze_path, "market FROZEN")
    version = str(layout.program["versions"]["market_dataset"])
    if (
        str(freeze.get("dataset_version", "")) != version
        or str(freeze.get("freeze_status", "")) != "frozen_for_free_research"
        or bool(freeze.get("formal_eligible", True))
    ):
        raise SourceDataCertificationError("market FROZEN contract is invalid")
    manifest_anchor = freeze.get("manifest")
    if not isinstance(manifest_anchor, Mapping):
        raise SourceDataCertificationError("market FROZEN lacks manifest anchor")
    manifest_path = _runtime_reference(
        layout.runtime_root, manifest_anchor.get("path"), "market manifest"
    )
    expected_manifest_sha = _valid_sha(
        manifest_anchor.get("sha256"), "market manifest SHA256"
    )
    if _sha256_file(manifest_path) != expected_manifest_sha:
        raise SourceDataCertificationError("market parent manifest hash mismatch")
    manifest = _read_json(manifest_path, "market manifest")
    if str(manifest.get("dataset_version", "")) != version:
        raise SourceDataCertificationError("market manifest version mismatch")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, Sequence) or isinstance(raw_files, (str, bytes)):
        raise SourceDataCertificationError("market manifest files are invalid")
    if int(manifest_anchor.get("file_records", -1)) != len(raw_files):
        raise SourceDataCertificationError("market parent manifest record count differs")
    suffix = f"curated/{version}/"
    records: dict[str, Mapping[str, Any]] = {}
    for raw in raw_files:
        if not isinstance(raw, Mapping):
            continue
        normalized = str(raw.get("path", "")).replace("\\", "/")
        if suffix in normalized:
            name = normalized.rsplit(suffix, 1)[-1]
            if "/" not in name:
                records[name] = raw
    actual = {
        path.name: path
        for path in layout.market_root.iterdir()
        if path.is_file() and path.name != "FROZEN.json" and not path.name.startswith(".")
    }
    if set(actual) != set(records):
        raise SourceDataCertificationError(
            "market curated files do not exactly match the frozen parent manifest"
        )
    for name, path in actual.items():
        record = records[name]
        if (
            _sha256_file(path) != _valid_sha(record.get("sha256"), f"market {name}")
            or int(path.stat().st_size) != int(record.get("size_bytes", -1))
        ):
            raise SourceDataCertificationError(f"market member drift: {name}")
    quality = freeze.get("quality")
    if not isinstance(quality, Mapping) or not bool(quality.get("all_gates_passed")):
        raise SourceDataCertificationError("market quality parent gate did not pass")
    quality_hashes: dict[str, str] = {}
    for path_key, sha_key in (
        ("gate_results_path", "gate_results_sha256"),
        ("build_provenance_path", "build_provenance_sha256"),
        ("test_results_path", "test_results_sha256"),
    ):
        anchored = _runtime_reference(layout.runtime_root, quality.get(path_key), path_key)
        expected = _valid_sha(quality.get(sha_key), sha_key)
        if _sha256_file(anchored) != expected:
            raise SourceDataCertificationError(f"market quality anchor drift: {path_key}")
        quality_hashes[path_key] = expected
    calendar = pd.read_parquet(layout.market_root / "calendar.parquet")
    membership = pd.read_parquet(layout.market_root / "membership.parquet")
    if "session_date" not in calendar or calendar.empty or membership.empty:
        raise SourceDataCertificationError("market calendar/membership is empty")
    return {
        "dataset_version": version,
        "freeze_sha256": _sha256_file(freeze_path),
        "manifest_sha256": expected_manifest_sha,
        "manifest_file_record_count": int(manifest_anchor.get("file_records", -1)),
        "curated_member_count": len(actual),
        "curated_member_hashes_verified": True,
        "quality_anchor_hashes": quality_hashes,
        "all_parent_quality_gates_passed": True,
        "_sessions": calendar["session_date"],
        "_membership": membership,
    }


def _certify_identifier(
    layout: DatabaseLayout,
    *,
    market_sessions: pd.Series,
    market_membership: pd.DataFrame,
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    manifest = _verify_directory_manifest(
        layout.identifier_root,
        layout.identifier_root / "manifest.json",
        expected_schema="cross_sectional_alpha.identifier_manifest.v1",
    )
    required = {"mapping_qa.json", "entity_bridge.parquet", "entity_cik_intervals.parquet"}
    if not required.issubset(set(manifest["member_names"])):
        raise SourceDataCertificationError("identifier manifest lacks required members")
    qa = _read_json(layout.identifier_root / "mapping_qa.json", "identifier QA")
    bridge = pd.read_parquet(layout.identifier_root / "entity_bridge.parquet")
    intervals = pd.read_parquet(layout.identifier_root / "entity_cik_intervals.parquet")
    if (
        not bool(qa.get("coverage_gate_passed"))
        or int(qa.get("security_count", -1)) != len(bridge)
        or int(qa.get("mapped_security_count", -1)) != int(bridge["cik10"].notna().sum())
        or int(qa.get("entity_cik_interval_count", -1)) != len(intervals)
        or str(qa.get("market_dataset", ""))
        != str(layout.program["versions"]["market_dataset"])
    ):
        raise SourceDataCertificationError("identifier QA counts/gates are inconsistent")
    recomputed = member_session_mapping_coverage(
        intervals,
        market_membership,
        market_sessions,
        start=pd.Timestamp(layout.program["sample"]["history_start"]),
        end=pd.Timestamp(layout.program["sample"]["evaluation_end"]),
    )
    declared = qa.get("member_session_coverage")
    if not isinstance(declared, Mapping):
        raise SourceDataCertificationError("identifier member-session QA is missing")
    for key in ("member_sessions", "mapped_member_sessions", "unmapped_member_sessions"):
        if int(declared.get(key, -1)) != int(recomputed[key]):
            raise SourceDataCertificationError(f"identifier coverage mismatch: {key}")
    if float(declared.get("coverage", float("nan"))) != float(recomputed["coverage"]):
        raise SourceDataCertificationError("identifier coverage ratio mismatch")
    minimum = float(qa.get("minimum_member_session_coverage", 1.0))
    if float(recomputed["coverage"]) < minimum:
        raise SourceDataCertificationError("identifier coverage is below its minimum")
    return (
        {
            "manifest_sha256": manifest["manifest_sha256"],
            "content_sha256": manifest["content_sha256"],
            "manifest_member_count": manifest["member_count"],
            "security_count": len(bridge),
            "mapped_security_count": int(bridge["cik10"].notna().sum()),
            "entity_cik_interval_count": len(intervals),
            "member_session_coverage": recomputed,
            "coverage_gate_passed": True,
        },
        {"bridge": bridge, "intervals": intervals},
    )


def _certify_sec_raw_ledger(
    root: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    root = root.resolve()
    ledger_path = root / "fetch_ledger.jsonl"
    if not ledger_path.is_file():
        raise SourceDataCertificationError("SEC fetch ledger is missing")
    records: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    objects: dict[Path, tuple[str, int]] = {}
    failed_http_count = 0
    for line_number, line in enumerate(
        ledger_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            record_id = str(raw["record_id"])
            requested_url = str(raw["requested_url"])
            response_url = str(raw["response_url"])
            status = int(raw["status"])
            sha = _valid_sha(raw["sha256"], f"SEC ledger line {line_number}")
            size = int(raw["size_bytes"])
            raw_value = Path(str(raw["raw_path"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SourceDataCertificationError(
                f"invalid SEC ledger record at line {line_number}"
            ) from exc
        expected_id = hashlib.sha256(
            f"{requested_url}\n{status}\n{sha}".encode("utf-8")
        ).hexdigest()
        if record_id != expected_id or record_id in index or size < 0:
            raise SourceDataCertificationError(
                f"SEC ledger identity is invalid at line {line_number}"
            )
        path = raw_value.resolve() if raw_value.is_absolute() else (root / raw_value).resolve()
        _require_within(path, root, "SEC raw object")
        expected_path = root / "raw" / "sha256" / sha[:2] / f"{sha}.bin"
        if path != expected_path.resolve() or not path.is_file():
            raise SourceDataCertificationError("SEC raw object path is noncanonical")
        observed = objects.get(path)
        if observed is None:
            observed = (_sha256_file(path), int(path.stat().st_size))
            objects[path] = observed
        if observed != (sha, size):
            raise SourceDataCertificationError("SEC raw object hash/size mismatch")
        item = {
            "record_id": record_id,
            "requested_url": requested_url,
            "response_url": response_url,
            "status": status,
            "sha256": sha,
            "size_bytes": size,
        }
        records.append(item)
        index[record_id] = item
        if not 200 <= status < 300:
            failed_http_count += 1
    if not records:
        raise SourceDataCertificationError("SEC fetch ledger is empty")
    disk_objects = {path.resolve() for path in (root / "raw" / "sha256").glob("*/*.bin")}
    if disk_objects != set(objects):
        raise SourceDataCertificationError("SEC raw object store has unindexed/missing objects")
    portable = [
        {
            "record_id": item["record_id"],
            "requested_url": item["requested_url"],
            "status": item["status"],
            "sha256": item["sha256"],
            "size_bytes": item["size_bytes"],
        }
        for item in sorted(records, key=lambda value: value["record_id"])
    ]
    content_index = hashlib.sha256(
        json.dumps(portable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return (
        {
            "ledger_sha256": _sha256_file(ledger_path),
            "content_index_sha256": content_index,
            "record_count": len(records),
            "unique_object_count": len(objects),
            "failed_http_record_count": failed_http_count,
            "verified_byte_count": int(sum(value[1] for value in objects.values())),
            "all_ledger_objects_size_and_sha256_verified": True,
            "object_store_exactly_indexed": True,
        },
        index,
    )


def _certify_fundamental_bundle(
    layout: DatabaseLayout,
    *,
    identifier_intervals: pd.DataFrame,
    ledger_index: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    freeze_path = layout.curated_root / "FROZEN.json"
    manifest_path = layout.curated_root / "manifest.json"
    summary_path = layout.curated_root / "build_summary.json"
    freeze = _read_json(freeze_path, "fundamental FROZEN")
    summary = _read_json(summary_path, "fundamental summary")
    manifest = _verify_directory_manifest(
        layout.curated_root,
        manifest_path,
        expected_schema="cross_sectional_alpha.fundamental_manifest.v1",
    )
    signature = str(freeze.get("fundamental_build_signature", ""))
    current_signature = _fundamental_build_signature(layout)
    version = str(layout.program["versions"]["fundamentals"])
    if (
        str(freeze.get("status", "")) != "frozen_complete"
        or bool(freeze.get("formal_eligible", True))
        or str(freeze.get("fundamental_version", version)) != version
        or str(summary.get("fundamental_version", version)) != version
        or str(manifest["payload"].get("fundamental_version", "")) != version
        or freeze.get("build_summary") != summary
        or str(freeze.get("manifest_sha256", "")) != manifest["manifest_sha256"]
        or str(freeze.get("content_sha256", "")) != manifest["content_sha256"]
        or signature != str(summary.get("fundamental_build_signature", ""))
        or signature != str(manifest["payload"].get("fundamental_build_signature", ""))
        or signature != current_signature
    ):
        raise SourceDataCertificationError(
            "fundamental FROZEN/manifest/build_summary anchors differ"
        )
    required_members = {
        "filings.parquet",
        "registered_facts.parquet",
        "canonical_annual_facts.parquet",
        "coverage_qa.parquet",
        "source_applicability.parquet",
        "fetch_failures.parquet",
        "entity_bridge.parquet",
        "entity_cik_intervals.parquet",
        "cik_manifest_index.json",
        "accounting_identity_qa.parquet",
        "accounting_identity_summary.json",
        "entity_temporal_support_qa.parquet",
        "entity_temporal_support_summary.json",
    }
    if not required_members.issubset(set(manifest["member_names"])):
        raise SourceDataCertificationError("fundamental manifest lacks required members")
    requested = int(summary.get("requested_cik_count", 0))
    completed = int(summary.get("completed_cik_count", -1))
    failures = pd.read_parquet(layout.curated_root / "fetch_failures.parquet")
    if (
        bool(summary.get("limited_smoke_build"))
        or requested <= 0
        or completed != requested
        or int(summary.get("failed_cik_count", -1)) != 0
        or not failures.empty
        or not bool(summary.get("fundamental_quality_gate_passed"))
    ):
        raise SourceDataCertificationError(
            "fundamental build is limited, incomplete, failed, or uncertified"
        )
    filings = pd.read_parquet(layout.curated_root / "filings.parquet")
    registered = pd.read_parquet(layout.curated_root / "registered_facts.parquet")
    canonical = pd.read_parquet(layout.curated_root / "canonical_annual_facts.parquet")
    if (
        int(summary.get("filing_rows", -1)) != len(filings)
        or int(summary.get("registered_fact_rows", -1)) != len(registered)
        or int(summary.get("canonical_fact_rows", -1)) != len(canonical)
    ):
        raise SourceDataCertificationError("fundamental aggregate row counts differ")
    aggregate_intervals = pd.read_parquet(layout.curated_root / "entity_cik_intervals.parquet")
    _assert_frames_exact(
        identifier_intervals.reset_index(drop=True),
        aggregate_intervals.reset_index(drop=True),
        "identifier/fundamental entity intervals",
    )

    expected_interval_ciks = _research_interval_ciks(
        aggregate_intervals,
        history_start=layout.program["sample"]["history_start"],
        evaluation_end=layout.program["sample"]["evaluation_end"],
    )
    cik_index = _read_json(layout.curated_root / "cik_manifest_index.json", "CIK manifest index")
    if str(cik_index.get("schema_version", "")) != (
        "cross_sectional_alpha.fundamental_cik_manifest_index.v1"
    ):
        raise SourceDataCertificationError("CIK manifest index schema differs")
    raw_ciks = cik_index.get("ciks")
    if not isinstance(raw_ciks, Sequence) or isinstance(raw_ciks, (str, bytes)):
        raise SourceDataCertificationError("CIK manifest index is invalid")
    indexed_ciks: list[str] = []
    for raw in raw_ciks:
        if not isinstance(raw, Mapping):
            raise SourceDataCertificationError("CIK manifest index contains non-object")
        cik10 = str(raw.get("cik10", ""))
        if not re.fullmatch(r"\d{10}", cik10) or cik10 in indexed_ciks:
            raise SourceDataCertificationError("CIK manifest index has invalid/duplicate CIK")
        indexed_ciks.append(cik10)
    if (
        len(indexed_ciks) != requested
        or int(cik_index.get("cik_count", -1)) != requested
        or str(cik_index.get("fundamental_build_signature", "")) != signature
    ):
        raise SourceDataCertificationError("CIK manifest index count/signature differs")
    if set(indexed_ciks) != expected_interval_ciks:
        raise SourceDataCertificationError(
            "CIK manifest index set differs from research-interval identifier CIKs"
        )

    by_cik_root = layout.curated_root / "by_cik"
    if not by_cik_root.is_dir():
        raise SourceDataCertificationError("fundamental by-CIK directory is missing")
    by_cik_entries = list(by_cik_root.iterdir())
    if any(
        entry.is_symlink()
        or not entry.is_dir()
        or not re.fullmatch(r"\d{10}", entry.name)
        for entry in by_cik_entries
    ):
        raise SourceDataCertificationError(
            "fundamental by-CIK root contains a non-CIK directory entry"
        )
    directory_ciks = {entry.name for entry in by_cik_entries}
    if directory_ciks != set(indexed_ciks):
        raise SourceDataCertificationError(
            "fundamental by-CIK directory set differs from CIK manifest index"
        )

    expected_ciks: list[str] = []
    for raw in raw_ciks:
        cik10 = str(raw["cik10"])
        directory = layout.curated_root / "by_cik" / cik10
        cik_manifest = _verify_directory_manifest(
            directory,
            directory / "manifest.json",
            expected_schema="cross_sectional_alpha.fundamental_cik_manifest.v1",
        )
        payload = cik_manifest["payload"]
        source_path = directory / "source_applicability.json"
        source_payload = _read_json(source_path, f"source applicability {cik10}")
        if (
            str(payload.get("cik10", "")) != cik10
            or str(payload.get("fundamental_build_signature", "")) != signature
            or str(raw.get("fundamental_build_signature", "")) != signature
            or str(raw.get("content_sha256", "")) != cik_manifest["content_sha256"]
            or payload.get("source_applicability") != source_payload
            or str(raw.get("source_applicability", "")) != str(source_payload.get("status", ""))
        ):
            raise SourceDataCertificationError(f"per-CIK manifest anchor differs: {cik10}")
        raw_records = payload.get("raw_records")
        if not isinstance(raw_records, Sequence) or isinstance(raw_records, (str, bytes)):
            raise SourceDataCertificationError(f"per-CIK raw records missing: {cik10}")
        for record in raw_records:
            if not isinstance(record, Mapping):
                raise SourceDataCertificationError("per-CIK raw-record anchor is invalid")
            ledger = ledger_index.get(str(record.get("record_id", "")))
            keys = ("requested_url", "response_url", "status", "sha256", "size_bytes")
            if ledger is None or any(str(ledger[key]) != str(record.get(key)) for key in keys):
                raise SourceDataCertificationError(
                    f"per-CIK raw-record anchor is absent from SEC ledger: {cik10}"
                )
        expected_ciks.append(cik10)
    source = pd.read_parquet(layout.curated_root / "source_applicability.parquet")
    accounting_qa = pd.read_parquet(layout.curated_root / "accounting_identity_qa.parquet")
    temporal_qa = pd.read_parquet(layout.curated_root / "entity_temporal_support_qa.parquet")
    accounting_summary = _read_json(
        layout.curated_root / "accounting_identity_summary.json", "accounting summary"
    )
    temporal_summary = _read_json(
        layout.curated_root / "entity_temporal_support_summary.json", "temporal summary"
    )
    return (
        {
            "fundamental_version": str(layout.program["versions"]["fundamentals"]),
            "freeze_sha256": _sha256_file(freeze_path),
            "manifest_sha256": manifest["manifest_sha256"],
            "content_sha256": manifest["content_sha256"],
            "build_summary_sha256": _sha256_file(summary_path),
            "fundamental_build_signature": signature,
            "manifest_member_count": manifest["member_count"],
            "requested_cik_count": requested,
            "completed_cik_count": completed,
            "failed_cik_count": 0,
            "aggregate_fetch_failure_rows": 0,
            "filing_rows": len(filings),
            "registered_fact_rows": len(registered),
            "canonical_fact_rows": len(canonical),
            "per_cik_manifest_count": len(expected_ciks),
        },
        {
            "freeze": freeze,
            "summary": summary,
            "filings": filings,
            "registered": registered,
            "canonical": canonical,
            "intervals": aggregate_intervals,
            "source_applicability": source,
            "accounting_qa": accounting_qa,
            "accounting_summary": accounting_summary,
            "temporal_qa": temporal_qa,
            "temporal_summary": temporal_summary,
            "expected_ciks": set(expected_ciks),
        },
    )


def _research_interval_ciks(
    intervals: pd.DataFrame,
    *,
    history_start: object,
    evaluation_end: object,
) -> set[str]:
    required = {"cik10", "effective_from", "effective_to"}
    missing = required.difference(intervals.columns)
    if missing:
        raise SourceDataCertificationError(
            f"entity intervals lack research-CIK columns: {sorted(missing)}"
        )
    interval_start = pd.to_datetime(
        intervals["effective_from"], errors="coerce"
    ).dt.normalize()
    interval_end = pd.to_datetime(
        intervals["effective_to"], errors="coerce"
    ).dt.normalize()
    research_start = pd.Timestamp(history_start).normalize()
    research_end = pd.Timestamp(evaluation_end).normalize()
    overlaps_research = interval_start.le(research_end) & (
        interval_end.isna() | interval_end.gt(research_start)
    )
    raw_ciks = intervals.loc[
        overlaps_research & intervals["cik10"].notna(), "cik10"
    ].astype(str)
    ciks = set(raw_ciks)
    if not ciks or any(not re.fullmatch(r"\d{10}", cik10) for cik10 in ciks):
        raise SourceDataCertificationError(
            "research-interval identifier CIK set is empty or invalid"
        )
    return ciks


def _certify_source_applicability(
    layout: DatabaseLayout,
    frame: pd.DataFrame,
    *,
    expected_ciks: set[str],
    ledger_index: Mapping[str, Mapping[str, Any]],
    fundamental_summary: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "cik10", "ticker", "source", "status", "reason_code", "explicit_missing",
        "fact_value_state", "imputation_policy", "imputation_applied",
        "imputed_fact_rows", "submissions_cik10", "periodic_form_count",
        "periodic_form_bases", "periodic_form_amendments_included",
        "companyfacts_http_status", "companyfacts_error_code",
        "companyfacts_object_key", "companyfacts_raw_record_id",
        "companyfacts_raw_sha256", "companyfacts_raw_size_bytes",
        "exception_review_status", "exception_reviewed_date",
    }
    missing = required.difference(frame.columns)
    if missing or frame["cik10"].astype(str).duplicated().any():
        raise SourceDataCertificationError(
            f"source-applicability schema/uniqueness failure: {sorted(missing)}"
        )
    observed_ciks = set(frame["cik10"].astype(str))
    if observed_ciks != expected_ciks:
        raise SourceDataCertificationError("source-applicability CIK set differs")
    exceptions = _load_companyfacts_exceptions(layout)
    statuses: list[str] = []
    resolved: list[str] = []
    url_template = str(layout.program["sec"]["companyfacts_url_template"])
    for row in frame.itertuples(index=False):
        cik10 = str(row.cik10)
        status = str(row.status)
        if (
            str(row.source) != "sec_companyfacts"
            or str(row.submissions_cik10) != cik10
            or status not in {"available", "resolved_not_applicable"}
            or str(row.imputation_policy) != "none"
            or _as_bool(row.imputation_applied)
            or int(row.imputed_fact_rows) != 0
        ):
            raise SourceDataCertificationError(f"invalid source applicability: {cik10}")
        record_id = str(row.companyfacts_raw_record_id)
        ledger = ledger_index.get(record_id)
        expected_url = url_template.format(cik10=cik10)
        if (
            ledger is None
            or str(ledger["requested_url"]) != expected_url
            or int(ledger["status"]) != int(row.companyfacts_http_status)
            or str(ledger["sha256"]) != str(row.companyfacts_raw_sha256)
            or int(ledger["size_bytes"]) != int(row.companyfacts_raw_size_bytes)
        ):
            raise SourceDataCertificationError(
                f"source applicability lacks exact raw-ledger anchor: {cik10}"
            )
        if status == "available":
            if (
                not 200 <= int(row.companyfacts_http_status) < 300
                or _as_bool(row.explicit_missing)
                or str(row.fact_value_state) != "observed_source_available"
                or str(row.reason_code) != "companyfacts_http_success"
            ):
                raise SourceDataCertificationError(f"invalid available source state: {cik10}")
        else:
            exception = exceptions.get(cik10)
            if (
                exception is None
                or str(row.ticker).strip().upper()
                != str(exception["ticker"]).strip().upper()
                or int(row.companyfacts_http_status) != int(exception["required_http_status"])
                or str(row.companyfacts_error_code) != str(exception["required_error_code"])
                or str(row.companyfacts_object_key)
                != f"api/xbrl/companyfacts/CIK{cik10}.json"
                or int(row.periodic_form_count) != int(exception["required_periodic_form_count"])
                or str(row.periodic_form_bases) != str(exception["periodic_form_bases"])
                or str(exception["include_amendments"]).strip().lower()
                not in {"true", "1"}
                or not _as_bool(row.periodic_form_amendments_included)
                or not _as_bool(row.explicit_missing)
                or str(row.fact_value_state) != "missing_source_not_applicable"
                or str(row.reason_code) != "cached_404_no_such_key_and_zero_periodic_forms"
                or str(row.exception_review_status) != "approved"
                or str(row.exception_reviewed_date) != str(exception["reviewed_date"])
            ):
                raise SourceDataCertificationError(
                    f"invalid reviewed source-not-applicable state: {cik10}"
                )
            resolved.append(cik10)
        statuses.append(status)
    counts = {
        str(key): int(value)
        for key, value in pd.Series(statuses, dtype="string").value_counts(sort=False).sort_index().items()
    }
    declared_counts = {
        str(key): int(value)
        for key, value in dict(fundamental_summary.get("source_applicability_counts", {})).items()
    }
    if (
        counts != declared_counts
        or int(fundamental_summary.get("source_applicability_cik_count", -1)) != len(frame)
        or int(fundamental_summary.get("resolved_not_applicable_cik_count", -1)) != len(resolved)
        or sorted(map(str, fundamental_summary.get("resolved_not_applicable_ciks", []))) != sorted(resolved)
        or int(fundamental_summary.get("not_applicable_imputed_fact_rows", -1)) != 0
    ):
        raise SourceDataCertificationError("source-applicability summary differs")
    return {
        "cik_count": len(frame),
        "status_counts": counts,
        "resolved_not_applicable_ciks": sorted(resolved),
        "raw_record_anchors_verified": True,
        "imputed_fact_rows": 0,
        "gate_passed": True,
    }


def _certify_accounting_identity(
    layout: DatabaseLayout,
    *,
    canonical: pd.DataFrame,
    stored: pd.DataFrame,
    stored_summary: Mapping[str, Any],
    fundamental_summary: Mapping[str, Any],
) -> dict[str, Any]:
    recomputed, summary = build_accounting_identity_qa(
        canonical,
        relative_tolerance=float(
            layout.program.get("quality", {}).get(
                "gross_profit_identity_relative_tolerance", 0.01
            )
        ),
    )
    _assert_frames_exact(recomputed, stored, "accounting identity QA")
    if (
        summary != stored_summary
        or fundamental_summary.get("accounting_identity") != stored_summary
        or not bool(summary.get("identity_gate_passed"))
    ):
        raise SourceDataCertificationError("accounting identity summary/gate differs")
    return {
        "context_count": int(summary["context_count"]),
        "direct_failure_count": int(summary["direct_failure_count"]),
        "identity_gate_passed": True,
        "recomputed_value_equal": True,
    }


def _certify_temporal_support(
    layout: DatabaseLayout,
    *,
    filings: pd.DataFrame,
    intervals: pd.DataFrame,
    source_applicability: pd.DataFrame,
    stored: pd.DataFrame,
    stored_summary: Mapping[str, Any],
    fundamental_summary: Mapping[str, Any],
) -> dict[str, Any]:
    recomputed, summary = build_entity_temporal_support_qa(
        filings,
        intervals,
        history_start=layout.program["sample"]["history_start"],
        evaluation_end=layout.program["sample"]["evaluation_end"],
        source_applicability=source_applicability.rename(
            columns={"status": "source_applicability_status"}
        ),
        minimum_long_interval_days=int(
            layout.program["identifier_resolution"].get(
                "minimum_temporal_support_interval_days", 365
            )
        ),
    )
    _assert_frames_exact(recomputed, stored, "entity temporal-support QA")
    if (
        summary != stored_summary
        or fundamental_summary.get("entity_temporal_support") != stored_summary
        or not bool(summary.get("temporal_support_gate_passed"))
        or int(summary.get("failed_interval_count", -1)) != 0
    ):
        raise SourceDataCertificationError("entity temporal-support summary/gate differs")
    return {
        "interval_count": int(summary["interval_count"]),
        "long_interval_count": int(summary["long_interval_count"]),
        "failed_interval_count": 0,
        "temporal_support_gate_passed": True,
        "recomputed_value_equal": True,
    }


def _verify_directory_manifest(
    root: Path,
    manifest_path: Path,
    *,
    expected_schema: str,
) -> dict[str, Any]:
    root = root.resolve()
    payload = _read_json(manifest_path, expected_schema)
    if str(payload.get("schema_version", "")) != expected_schema:
        raise SourceDataCertificationError(f"manifest schema mismatch: {manifest_path}")
    raw_files = payload.get("files")
    if not isinstance(raw_files, Sequence) or isinstance(raw_files, (str, bytes)):
        raise SourceDataCertificationError(f"manifest files are invalid: {manifest_path}")
    names: list[str] = []
    for record in raw_files:
        if not isinstance(record, Mapping):
            raise SourceDataCertificationError("manifest file record is not an object")
        name = str(record.get("path", ""))
        if not name or Path(name).name != name or name in names:
            raise SourceDataCertificationError("manifest member path is unsafe/duplicate")
        path = (root / name).resolve()
        _require_within(path, root, "manifest member")
        if not path.is_file():
            raise SourceDataCertificationError(f"manifest member is missing: {name}")
        if (
            _sha256_file(path) != _valid_sha(record.get("sha256"), name)
            or int(path.stat().st_size) != int(record.get("size_bytes", -1))
        ):
            raise SourceDataCertificationError(f"manifest member drift: {name}")
        names.append(name)
    actual = {
        path.name
        for path in root.iterdir()
        if path.is_file()
        and path.name not in {"manifest.json", "FROZEN.json", "build_summary.json"}
        and not path.name.startswith(".")
    }
    if actual != set(names):
        raise SourceDataCertificationError("manifest membership set differs from directory")
    stable = dict(payload)
    stable.pop("content_sha256", None)
    stable.pop("generated_at_utc", None)
    content = hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if str(payload.get("content_sha256", "")) != content:
        raise SourceDataCertificationError("manifest content_sha256 mismatch")
    return {
        "payload": payload,
        "manifest_sha256": _sha256_file(manifest_path),
        "content_sha256": content,
        "member_count": len(names),
        "member_names": sorted(names),
    }


def _load_companyfacts_exceptions(layout: DatabaseLayout) -> dict[str, dict[str, str]]:
    configured = Path(str(layout.program["fundamentals"]["companyfacts_exceptions"]))
    path = configured if configured.is_absolute() else layout.project_root / configured
    if not path.is_file():
        raise SourceDataCertificationError("Company Facts exception registry is missing")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    output: dict[str, dict[str, str]] = {}
    for row in frame.to_dict("records"):
        cik10 = str(row.get("cik10", "")).zfill(10)
        if not re.fullmatch(r"\d{10}", cik10) or cik10 in output:
            raise SourceDataCertificationError("Company Facts exception registry is invalid")
        if (
            str(row.get("resolution", "")) != "resolved_not_applicable"
            or str(row.get("review_status", "")) != "approved"
        ):
            raise SourceDataCertificationError("Company Facts exception is not approved")
        output[cik10] = {str(key): str(value) for key, value in row.items()}
    return output


def _assert_frames_exact(left: pd.DataFrame, right: pd.DataFrame, label: str) -> None:
    try:
        pd.testing.assert_frame_equal(
            left.reset_index(drop=True),
            right.reset_index(drop=True),
            check_dtype=False,
            check_exact=True,
            check_freq=False,
        )
    except AssertionError as exc:
        raise SourceDataCertificationError(f"{label} differs value-for-value") from exc


def _publication_manifest(
    certificate: Mapping[str, Any], directory: Path
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.name == "manifest.json":
            continue
        record: dict[str, Any] = {
            "path": path.name,
            "sha256": _sha256_file(path),
            "size_bytes": int(path.stat().st_size),
        }
        if path.suffix.lower() == ".csv":
            record["row_count"] = int(len(pd.read_csv(path)))
        files.append(record)
    return {
        "schema_version": "cross_sectional_alpha.source_data_compact_publish.v1",
        "status": "source_data_certified",
        "formal_eligible": False,
        "experiment_authorized": False,
        "factor_stage_included": False,
        "source_data_certification_sha256": hashlib.sha256(
            _json_bytes(certificate)
        ).hexdigest(),
        "fundamental_manifest_sha256": str(
            certificate["fundamental"]["manifest_sha256"]
        ),
        "sec_raw_content_index_sha256": str(
            certificate["sec_raw"]["content_index_sha256"]
        ),
        "files": files,
    }


def _publication_readme(certificate: Mapping[str, Any]) -> str:
    identifier = certificate["identifier"]
    fundamental = certificate["fundamental"]
    applicability = certificate["quality_gates"]["source_applicability"]
    return (
        "# Cross-sectional source-data certification\n\n"
        "This compact directory certifies only the frozen market, identifier, "
        "and SEC fundamental source layers. It does not require or include the "
        "factor database, portfolio results, or experiment authorization.\n\n"
        f"- Status: `{certificate['status']}`\n"
        f"- Market dataset: `{certificate['program']['market_dataset']}`\n"
        f"- SID-to-CIK coverage: {float(identifier['member_session_coverage']['coverage']):.6%}\n"
        f"- SEC CIKs completed: {fundamental['completed_cik_count']}/{fundamental['requested_cik_count']}\n"
        f"- Aggregate fetch failures: {fundamental['aggregate_fetch_failure_rows']}\n"
        f"- Source applicability states: `{json.dumps(applicability['status_counts'], sort_keys=True)}`\n"
        "- Accounting identity: recomputed and value-equal; gate passed.\n"
        "- Historical entity support: recomputed and value-equal; gate passed.\n\n"
        "Runtime Parquet tables, immutable SEC payloads, the SEC fetch ledger, "
        "and DuckDB remain outside Git. Their SHA256 anchors are recorded in "
        "`source_data_certification.json`.\n"
    )


def _verify_publish_tree(directory: Path, *, expect_manifest: bool) -> None:
    entries = list(directory.iterdir())
    if any(not path.is_file() for path in entries):
        raise SourceDataCertificationError("compact publication must be flat")
    expected = _PUBLISH_FILENAMES if expect_manifest else _PUBLISH_FILENAMES - {"manifest.json"}
    if {path.name for path in entries} != expected:
        raise SourceDataCertificationError("compact publication whitelist differs")
    for path in entries:
        if path.suffix.lower() in _PROHIBITED_PUBLISH_SUFFIXES:
            raise SourceDataCertificationError("prohibited source-data file in publication")
        if int(path.stat().st_size) > _MAX_PUBLISH_FILE_BYTES:
            raise SourceDataCertificationError(f"compact publication file too large: {path.name}")


def _require_publish_destination(destination: Path, allowed_root: Path) -> None:
    if destination == allowed_root:
        raise SourceDataCertificationError("publication cannot replace its allowed root")
    _require_within(destination, allowed_root, "publication destination")


def _require_temporary(path: Path, allowed_root: Path, destination_name: str) -> None:
    _require_within(path, allowed_root, "temporary publication")
    if not path.name.startswith(f".{destination_name}.building-"):
        raise SourceDataCertificationError("temporary publication name is unsafe")


def _tree_index(directory: Path) -> list[tuple[str, str, int]]:
    if any(not path.is_file() for path in directory.iterdir()):
        raise SourceDataCertificationError("publication tree must be flat")
    return [
        (path.name, _sha256_file(path), int(path.stat().st_size))
        for path in sorted(directory.iterdir(), key=lambda item: item.name)
    ]


def _parquet_to_stable_csv(source: Path, destination: Path, *, keys: tuple[str, ...]) -> None:
    frame = pd.read_parquet(source)
    selected = [key for key in keys if key in frame.columns]
    if selected and not frame.empty:
        frame = frame.sort_values(selected, kind="stable", ignore_index=True)
    frame.to_csv(destination, index=False, lineterminator="\n")


def _copy_portable_json(source: Path, destination: Path) -> None:
    _write_json(destination, _read_json(source, source.name))


def _runtime_reference(root: Path, value: object, label: str) -> Path:
    raw = Path(str(value or ""))
    if not str(raw):
        raise SourceDataCertificationError(f"{label} path is blank")
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    _require_within(path, root.resolve(), label)
    if not path.is_file():
        raise SourceDataCertificationError(f"{label} is missing")
    return path


def _require_within(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise SourceDataCertificationError(f"{label} escapes its allowed root") from exc


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise SourceDataCertificationError(f"{label} is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceDataCertificationError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise SourceDataCertificationError(f"{label} must be a JSON object")
    return value


def _valid_sha(value: object, label: str) -> str:
    result = str(value or "").lower()
    if not _SHA256_RE.fullmatch(result):
        raise SourceDataCertificationError(f"{label} is not SHA256")
    return result


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)
        + "\n"
    ).encode("utf-8")


def _atomic_json(value: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.building")
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_bytes(_json_bytes(value))


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0", "", "nan", "none"}:
        return False
    raise SourceDataCertificationError(f"invalid boolean value: {value!r}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--program")
    parser.add_argument("--certification-path")
    parser.add_argument("--publish-destination")
    parser.add_argument(
        "--allowed-publish-root",
        default=str(PROJECT_ROOT / "results" / "published" / "cross_sectional_data"),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    layout = DatabaseLayout.load(
        project_root=args.project_root,
        runtime_root=args.runtime_root,
        program_path=args.program,
    )
    certificate = certify_cross_sectional_source_data(
        layout, certification_path=args.certification_path
    )
    output: dict[str, Any] = {
        "status": certificate["status"],
        "fundamental_manifest_sha256": certificate["fundamental"]["manifest_sha256"],
        "sec_raw_content_index_sha256": certificate["sec_raw"]["content_index_sha256"],
    }
    if args.publish_destination:
        output["publication"] = publish_source_data_certification(
            layout,
            certificate,
            destination=args.publish_destination,
            allowed_publish_root=args.allowed_publish_root,
        )
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
