"""Read-only integrity audit for the cross-sectional research database.

The audit never repairs or rewrites evidence.  It verifies the immutable SEC
fetch store, every component declared by the data-bundle manifest, the factor
database/readiness contract, and the row counts exposed by the rebuildable
DuckDB catalog.  Its JSON result deliberately contains logical paths only, so
it can be committed as a compact review artifact without publishing runtime
locations or source data.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from momentum_reversal.data.factor_database import (  # noqa: E402
    CORE_COLUMNS,
    validate_factor_database,
)
from momentum_reversal.pipelines.cross_sectional_database import (  # noqa: E402
    DatabaseLayout,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CrossSectionalAuditError(RuntimeError):
    """Raised when immutable database evidence fails an audit check."""


@dataclass(frozen=True)
class AuditInputs:
    """Explicit paths consumed by the read-only audit."""

    project_root: Path
    runtime_root: Path
    sec_store_root: Path
    bundle_manifest_path: Path
    catalog_path: Path
    identifier_qa_path: Path
    fundamental_summary_path: Path
    market_volume_qa_path: Path
    market_factor_summary_path: Path
    data_quality_summary_path: Path
    factor_freeze_path: Path

    @classmethod
    def from_layout(cls, layout: DatabaseLayout) -> "AuditInputs":
        return cls(
            project_root=layout.project_root.resolve(),
            runtime_root=layout.runtime_root.resolve(),
            sec_store_root=layout.sec_store_root.resolve(),
            bundle_manifest_path=(
                layout.derived_root / "data_bundle_manifest.json"
            ).resolve(),
            catalog_path=layout.catalog_path.resolve(),
            identifier_qa_path=(
                layout.identifier_root / "mapping_qa.json"
            ).resolve(),
            fundamental_summary_path=(
                layout.curated_root / "build_summary.json"
            ).resolve(),
            market_volume_qa_path=(
                layout.derived_root / "market_volume_qa.json"
            ).resolve(),
            market_factor_summary_path=(
                layout.derived_root / "market_factor_build_summary.json"
            ).resolve(),
            data_quality_summary_path=(
                layout.derived_root / "data_quality_summary.json"
            ).resolve(),
            factor_freeze_path=(layout.derived_root / "FROZEN.json").resolve(),
        )

    @property
    def allowed_roots(self) -> tuple[tuple[str, Path], ...]:
        return (
            ("runtime", self.runtime_root.resolve()),
            ("project", self.project_root.resolve()),
        )


@dataclass(frozen=True)
class _Component:
    component_id: str
    component_kind: str
    path: Path
    sha256: str
    row_count: int | None
    source_version: str
    view_name: str | None
    logical_path: str


def audit_cross_sectional_database(inputs: AuditInputs) -> dict[str, Any]:
    """Run every database audit and return a portable deterministic summary."""

    raw_sec = _audit_raw_sec_store(inputs.sec_store_root)
    bundle, components = _audit_bundle_manifest(inputs)
    factor = _audit_factor_contract(components, inputs.data_quality_summary_path)
    catalog = _audit_catalog(
        inputs.catalog_path,
        bundle_id=str(bundle["data_bundle_id"]),
        components=components,
    )
    gates = _audit_quality_records(
        inputs,
        components=components,
        bundle_id=str(bundle["data_bundle_id"]),
        bundle_manifest_sha256=str(bundle["manifest_sha256"]),
    )
    return {
        "schema_version": "cross_sectional_alpha.database_audit.v1",
        "status": "pass",
        "read_only": True,
        "raw_sec": raw_sec,
        "bundle": bundle,
        "factor_database": factor,
        "catalog": catalog,
        "quality_gates": gates,
    }


def resolve_bundle_component_paths(inputs: AuditInputs) -> dict[str, Path]:
    """Resolve manifest component paths without exposing them in audit JSON.

    Callers should invoke :func:`audit_cross_sectional_database` first.  This
    helper exists for the compact publisher, which then reads only a fixed
    whitelist of already-verified coverage/readiness components.
    """

    _, components = _audit_bundle_manifest(inputs)
    return {component_id: item.path for component_id, item in components.items()}


def _audit_raw_sec_store(root: Path) -> dict[str, Any]:
    root = root.resolve()
    ledger = root / "fetch_ledger.jsonl"
    if not ledger.is_file():
        raise CrossSectionalAuditError(f"SEC fetch ledger is missing: {ledger}")
    lines = ledger.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    verified_objects: dict[Path, tuple[str, int]] = {}
    failed_status_count = 0
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CrossSectionalAuditError(
                f"invalid SEC ledger JSON at line {line_number}"
            ) from exc
        if not isinstance(value, Mapping):
            raise CrossSectionalAuditError(
                f"SEC ledger line {line_number} is not an object"
            )
        try:
            record_id = str(value["record_id"])
            requested_url = str(value["requested_url"])
            status = int(value["status"])
            expected_sha = str(value["sha256"]).lower()
            expected_size = int(value["size_bytes"])
            raw_value = Path(str(value["raw_path"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise CrossSectionalAuditError(
                f"invalid SEC ledger fields at line {line_number}"
            ) from exc
        if record_id in seen_ids:
            raise CrossSectionalAuditError(
                f"duplicate SEC ledger record_id={record_id}"
            )
        if not _SHA256_RE.fullmatch(expected_sha) or expected_size < 0:
            raise CrossSectionalAuditError(
                f"invalid SEC object digest/size at line {line_number}"
            )
        expected_record_id = hashlib.sha256(
            f"{requested_url}\n{status}\n{expected_sha}".encode("utf-8")
        ).hexdigest()
        if record_id != expected_record_id:
            raise CrossSectionalAuditError(
                f"SEC ledger record identity mismatch at line {line_number}"
            )
        path = (
            raw_value.resolve()
            if raw_value.is_absolute()
            else (root / raw_value).resolve()
        )
        _require_within(path, root, label="SEC raw object")
        expected_path = (
            root
            / "raw"
            / "sha256"
            / expected_sha[:2]
            / f"{expected_sha}.bin"
        ).resolve()
        if path != expected_path:
            raise CrossSectionalAuditError(
                f"SEC raw object has a noncanonical content address: {path.name}"
            )
        if not path.is_file():
            raise CrossSectionalAuditError(f"SEC raw object is missing: {path.name}")
        observed = verified_objects.get(path)
        if observed is None:
            observed = (_sha256_file(path), int(path.stat().st_size))
            verified_objects[path] = observed
        if observed != (expected_sha, expected_size):
            raise CrossSectionalAuditError(
                f"SEC raw object size/hash mismatch: {path.name}"
            )
        if path.name != f"{expected_sha}.bin":
            raise CrossSectionalAuditError(
                f"SEC raw object is not content-addressed: {path.name}"
            )
        if not 200 <= status < 300:
            failed_status_count += 1
        seen_ids.add(record_id)
        records.append(
            {
                "record_id": record_id,
                "requested_url": requested_url,
                "status": status,
                "sha256": expected_sha,
                "size_bytes": expected_size,
            }
        )
    if not records:
        raise CrossSectionalAuditError("SEC fetch ledger is empty")
    canonical = json.dumps(
        sorted(records, key=lambda item: item["record_id"]),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "ledger_sha256": _sha256_file(ledger),
        "content_index_sha256": hashlib.sha256(canonical).hexdigest(),
        "record_count": len(records),
        "unique_object_count": len(verified_objects),
        "failed_http_record_count": failed_status_count,
        "verified_byte_count": int(
            sum(size for _, size in verified_objects.values())
        ),
        "all_objects_size_and_sha256_verified": True,
    }


def _audit_bundle_manifest(
    inputs: AuditInputs,
) -> tuple[dict[str, Any], dict[str, _Component]]:
    manifest_path = inputs.bundle_manifest_path.resolve()
    manifest = _read_json_object(manifest_path, "data bundle manifest")
    bundle_id = str(manifest.get("data_bundle_id", "")).strip()
    raw_components = manifest.get("components")
    if not bundle_id:
        raise CrossSectionalAuditError("data bundle ID is blank")
    if not isinstance(raw_components, Sequence) or isinstance(
        raw_components, (str, bytes)
    ):
        raise CrossSectionalAuditError("bundle components must be a list")
    components: dict[str, _Component] = {}
    seen_paths: set[Path] = set()
    seen_views: set[str] = set()
    portable: list[dict[str, Any]] = []
    for raw in raw_components:
        if not isinstance(raw, Mapping):
            raise CrossSectionalAuditError("every bundle component must be an object")
        component_id = str(raw.get("component_id", "")).strip()
        kind = str(raw.get("component_kind", "")).strip()
        raw_path = str(raw.get("path", "")).strip()
        expected_sha = str(raw.get("sha256", "")).lower()
        source_version = str(raw.get("source_version", ""))
        if not _IDENTIFIER_RE.fullmatch(component_id):
            raise CrossSectionalAuditError(
                f"invalid bundle component ID={component_id!r}"
            )
        if component_id in components:
            raise CrossSectionalAuditError(
                f"duplicate bundle component ID={component_id}"
            )
        if kind not in {
            "parquet",
            "manifest",
            "json",
            "csv",
            "toml",
            "python",
        }:
            raise CrossSectionalAuditError(
                f"unsupported bundle component kind={kind}"
            )
        if not raw_path or not _SHA256_RE.fullmatch(expected_sha):
            raise CrossSectionalAuditError(
                f"component {component_id} lacks a valid path/SHA256"
            )
        path_value = Path(raw_path)
        path = (
            path_value.resolve()
            if path_value.is_absolute()
            else (manifest_path.parent / path_value).resolve()
        )
        logical_path = _logical_path(path, inputs.allowed_roots)
        if path in seen_paths:
            raise CrossSectionalAuditError(
                f"bundle repeats a component path: {logical_path}"
            )
        if not path.is_file():
            raise CrossSectionalAuditError(
                f"bundle component is missing: {component_id}"
            )
        if _sha256_file(path) != expected_sha:
            raise CrossSectionalAuditError(
                f"bundle component SHA256 mismatch: {component_id}"
            )
        row_count: int | None = None
        if kind == "parquet":
            if path.suffix.lower() != ".parquet":
                raise CrossSectionalAuditError(
                    f"Parquet component has wrong suffix: {component_id}"
                )
            try:
                import pyarrow.parquet as pq
            except ImportError as exc:  # pragma: no cover - environment guard
                raise CrossSectionalAuditError("pyarrow is required for audit") from exc
            row_count = int(pq.ParquetFile(path).metadata.num_rows)
            try:
                declared_rows = int(raw["row_count"])
            except (KeyError, TypeError, ValueError) as exc:
                raise CrossSectionalAuditError(
                    f"Parquet component lacks row_count: {component_id}"
                ) from exc
            if row_count != declared_rows:
                raise CrossSectionalAuditError(
                    f"bundle component row_count mismatch: {component_id}"
                )
        view_name_value = raw.get("view_name")
        view_name = None
        if kind == "parquet":
            view_name = str(view_name_value or component_id).strip()
            if not _IDENTIFIER_RE.fullmatch(view_name):
                raise CrossSectionalAuditError(
                    f"invalid catalog view name={view_name!r}"
                )
            if view_name in seen_views:
                raise CrossSectionalAuditError(
                    f"duplicate catalog view name={view_name}"
                )
            seen_views.add(view_name)
        item = _Component(
            component_id=component_id,
            component_kind=kind,
            path=path,
            sha256=expected_sha,
            row_count=row_count,
            source_version=source_version,
            view_name=view_name,
            logical_path=logical_path,
        )
        components[component_id] = item
        seen_paths.add(path)
        portable.append(
            {
                "component_id": component_id,
                "component_kind": kind,
                "logical_path": logical_path,
                "sha256": expected_sha,
                "row_count": row_count,
                "source_version": source_version,
                **({"view_name": view_name} if view_name else {}),
            }
        )
    if not components:
        raise CrossSectionalAuditError("data bundle has no components")
    declared_content = str(manifest.get("content_sha256", "")).lower()
    stable_manifest = dict(manifest)
    stable_manifest.pop("content_sha256", None)
    stable_manifest.pop("generated_at_utc", None)
    observed_content = hashlib.sha256(
        json.dumps(
            stable_manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if declared_content != observed_content:
        raise CrossSectionalAuditError("bundle content_sha256 mismatch")
    return (
        {
            "data_bundle_id": bundle_id,
            "manifest_sha256": _sha256_file(manifest_path),
            "component_count": len(components),
            "all_component_hashes_verified": True,
            "all_parquet_row_counts_verified": True,
            "content_sha256": observed_content,
            "components": sorted(portable, key=lambda item: item["component_id"]),
        },
        components,
    )


def _audit_factor_contract(
    components: Mapping[str, _Component], data_quality_summary_path: Path
) -> dict[str, Any]:
    required = {
        "factor_values",
        "factor_readiness",
        "deterministic_rebuild_qa",
        "causality_qa",
        "fundamental_accounting_identity",
    }
    missing = required.difference(components)
    if missing:
        raise CrossSectionalAuditError(
            f"bundle lacks factor audit components: {sorted(missing)}"
        )
    factor_path = components["factor_values"].path
    readiness_path = components["factor_readiness"].path
    try:
        factor = pd.read_parquet(factor_path, columns=list(CORE_COLUMNS))
        readiness = pd.read_parquet(readiness_path)
    except Exception as exc:
        raise CrossSectionalAuditError("cannot read factor audit Parquets") from exc
    try:
        validate_factor_database(factor)
    except (TypeError, ValueError) as exc:
        raise CrossSectionalAuditError(f"factor database contract failed: {exc}") from exc
    required_readiness = {
        "factor_id",
        "registered_first_round",
        "selection_status",
        "selected_factor_id",
        "performance_used",
    }
    missing_columns = required_readiness.difference(readiness.columns)
    if missing_columns:
        raise CrossSectionalAuditError(
            f"factor readiness lacks columns: {sorted(missing_columns)}"
        )
    factor_ids = sorted(factor["factor_id"].astype(str).unique())
    readiness_ids = readiness["factor_id"].astype("string").str.strip()
    if readiness_ids.isna().any() or readiness_ids.eq("").any():
        raise CrossSectionalAuditError("factor readiness contains blank factor IDs")
    if readiness_ids.duplicated().any():
        raise CrossSectionalAuditError("factor readiness factor IDs are not unique")
    if set(factor_ids) != set(readiness_ids.astype(str)):
        raise CrossSectionalAuditError(
            "factor readiness IDs do not match factor database IDs"
        )
    performance_flags = readiness["performance_used"].map(_parse_boolean)
    if performance_flags.any():
        raise CrossSectionalAuditError(
            "factor readiness used performance results"
        )
    statuses = readiness["selection_status"].astype("string").str.strip()
    selected = readiness["selected_factor_id"].astype("string").str.strip()
    allowed_statuses = {
        "ready_first_round",
        "ready_coverage_alternative",
        "blocked_data_readiness",
        "available_expanded_not_first_round",
        "blocked_expanded_data_readiness",
    }
    invalid_statuses = sorted(
        set(statuses.dropna().astype(str)).difference(allowed_statuses)
    )
    if statuses.isna().any() or statuses.eq("").any() or invalid_statuses:
        raise CrossSectionalAuditError(
            f"factor readiness contains invalid selection statuses: {invalid_statuses}"
        )
    ready_mask = statuses.isin(
        ["ready_first_round", "ready_coverage_alternative"]
    )
    if (ready_mask & (selected.isna() | selected.eq(""))).any():
        raise CrossSectionalAuditError(
            "ready factor rows require selected_factor_id"
        )
    summary = _read_json_object(
        data_quality_summary_path, "factor data-quality summary"
    )
    if _parse_boolean(summary.get("performance_used_for_readiness")):
        raise CrossSectionalAuditError(
            "data-quality summary says performance affected readiness"
        )
    if _parse_boolean(summary.get("formal_eligible")):
        raise CrossSectionalAuditError(
            "cross-sectional data bundle must remain formal_eligible=false"
        )
    deterministic = _read_json_object(
        components["deterministic_rebuild_qa"].path,
        "deterministic rebuild QA",
    )
    causality = _read_json_object(
        components["causality_qa"].path,
        "actual causality QA",
    )
    if not _parse_boolean(
        deterministic.get("deterministic_rebuild_passed")
    ) or not _parse_boolean(summary.get("deterministic_rebuild_passed")):
        raise CrossSectionalAuditError("deterministic rebuild gate did not pass")
    if not _parse_boolean(
        causality.get("actual_future_input_invariance_passed")
    ) or not _parse_boolean(
        summary.get("actual_future_input_invariance_passed")
    ):
        raise CrossSectionalAuditError(
            "actual future-input invariance gate did not pass"
        )
    registered = readiness["registered_first_round"].map(_parse_boolean)
    blocked_mask = registered & ~ready_mask
    ready_ids = sorted(selected.loc[ready_mask].dropna().astype(str).unique())
    blocked_ids = sorted(readiness_ids.loc[blocked_mask].astype(str).unique())
    return {
        "row_count": int(len(factor)),
        "eligible_row_count": int(factor["eligible"].map(_parse_boolean).sum()),
        "factor_count": len(factor_ids),
        "signal_date_count": int(
            pd.to_datetime(factor["signal_date"]).nunique()
        ),
        "primary_key_duplicates": int(
            factor.duplicated(["signal_date", "sid", "factor_id"]).sum()
        ),
        "factor_contract_verified": True,
        "readiness_performance_used": False,
        "deterministic_rebuild_passed": True,
        "actual_future_input_invariance_passed": True,
        "ready_first_round_factor_ids": ready_ids,
        "blocked_first_round_factor_ids": blocked_ids,
    }


def _audit_catalog(
    catalog_path: Path,
    *,
    bundle_id: str,
    components: Mapping[str, _Component],
) -> dict[str, Any]:
    if not catalog_path.is_file():
        raise CrossSectionalAuditError(f"DuckDB catalog is missing: {catalog_path}")
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - environment guard
        raise CrossSectionalAuditError("duckdb is required for catalog audit") from exc
    connection = duckdb.connect(str(catalog_path.resolve()), read_only=True)
    view_counts: dict[str, int] = {}
    try:
        metadata = dict(
            connection.execute("SELECT key, value FROM catalog_metadata").fetchall()
        )
        if str(metadata.get("bundle_id", "")) != bundle_id:
            raise CrossSectionalAuditError("catalog bundle_id does not match manifest")
        rows = connection.execute(
            "SELECT component_id, path, sha256, row_count FROM data_component"
        ).fetchall()
        catalog_components = {str(row[0]): row for row in rows}
        if set(catalog_components) != set(components):
            raise CrossSectionalAuditError(
                "catalog component IDs do not match bundle manifest"
            )
        for component_id, component in components.items():
            row = catalog_components[component_id]
            if Path(str(row[1])).resolve() != component.path:
                raise CrossSectionalAuditError(
                    f"catalog path mismatch: {component_id}"
                )
            if str(row[2]) != component.sha256:
                raise CrossSectionalAuditError(
                    f"catalog SHA256 mismatch: {component_id}"
                )
            stored_rows = None if row[3] is None else int(row[3])
            if stored_rows != component.row_count:
                raise CrossSectionalAuditError(
                    f"catalog row_count mismatch: {component_id}"
                )
            if component.component_kind != "parquet":
                continue
            assert component.view_name is not None
            observed = int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{component.view_name}"'
                ).fetchone()[0]
            )
            if observed != component.row_count:
                raise CrossSectionalAuditError(
                    f"catalog view row_count mismatch: {component.view_name}"
                )
            view_counts[component.view_name] = observed
        factor_definition_rows = int(
            connection.execute("SELECT COUNT(*) FROM factor_definition").fetchone()[0]
        )
    except CrossSectionalAuditError:
        raise
    except Exception as exc:
        raise CrossSectionalAuditError("DuckDB catalog audit failed") from exc
    finally:
        connection.close()
    return {
        "component_count": len(components),
        "parquet_view_count": len(view_counts),
        "factor_definition_rows": factor_definition_rows,
        "all_catalog_row_counts_verified": True,
        "view_row_counts": dict(sorted(view_counts.items())),
    }


def _audit_source_applicability(
    sec_store_root: Path,
    components: Mapping[str, _Component],
) -> dict[str, Any]:
    component = components.get("sec_source_applicability")
    if component is None or component.component_kind != "parquet":
        raise CrossSectionalAuditError(
            "bundle lacks SEC source-applicability evidence"
        )
    try:
        frame = pd.read_parquet(component.path)
    except Exception as exc:
        raise CrossSectionalAuditError(
            "cannot read SEC source-applicability evidence"
        ) from exc
    required = {
        "cik10",
        "ticker",
        "source",
        "status",
        "reason_code",
        "explicit_missing",
        "fact_value_state",
        "imputation_policy",
        "imputation_applied",
        "imputed_fact_rows",
        "submissions_cik10",
        "periodic_form_count",
        "periodic_form_bases",
        "periodic_form_amendments_included",
        "companyfacts_http_status",
        "companyfacts_error_code",
        "companyfacts_object_key",
        "companyfacts_raw_record_id",
        "companyfacts_raw_sha256",
        "companyfacts_raw_size_bytes",
        "exception_review_status",
        "exception_reviewed_date",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise CrossSectionalAuditError(
            "SEC source-applicability evidence lacks columns: "
            f"{sorted(missing)}"
        )
    if frame["cik10"].astype(str).duplicated().any():
        raise CrossSectionalAuditError(
            "SEC source-applicability evidence repeats a CIK"
        )

    ledger_path = sec_store_root.resolve() / "fetch_ledger.jsonl"
    raw_records: set[tuple[str, str, int, str, int]] = set()
    try:
        ledger_lines = ledger_path.read_text(encoding="utf-8").splitlines()
        for line in ledger_lines:
            if not line.strip():
                continue
            record = json.loads(line)
            raw_records.add(
                (
                    str(record["record_id"]),
                    str(record["requested_url"]),
                    int(record["status"]),
                    str(record["sha256"]).lower(),
                    int(record["size_bytes"]),
                )
            )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CrossSectionalAuditError(
            "cannot index SEC ledger for source-applicability audit"
        ) from exc

    reviewed = {
        "0001132979": "FRC",
        "0001288784": "SBNY",
    }
    resolved_ciks: list[str] = []
    statuses: list[str] = []
    for row in frame.itertuples(index=False):
        cik10 = str(row.cik10).strip()
        submissions_cik10 = str(row.submissions_cik10).strip()
        status = str(row.status).strip()
        if not re.fullmatch(r"\d{10}", cik10) or submissions_cik10 != cik10:
            raise CrossSectionalAuditError(
                "SEC source-applicability evidence has a CIK mismatch"
            )
        if str(row.source).strip() != "sec_companyfacts":
            raise CrossSectionalAuditError(
                "SEC source-applicability evidence has an invalid source"
            )
        if status not in {"available", "resolved_not_applicable"}:
            raise CrossSectionalAuditError(
                "SEC source-applicability evidence has an invalid status"
            )
        if (
            str(row.imputation_policy).strip() != "none"
            or _parse_boolean(row.imputation_applied)
            or int(row.imputed_fact_rows) != 0
        ):
            raise CrossSectionalAuditError(
                "SEC source-applicability evidence contains imputation"
            )
        http_status = int(row.companyfacts_http_status)
        raw_record_id = str(row.companyfacts_raw_record_id).strip()
        raw_sha256 = str(row.companyfacts_raw_sha256).strip().lower()
        raw_size = int(row.companyfacts_raw_size_bytes)
        requested_url = (
            "https://data.sec.gov/api/xbrl/companyfacts/"
            f"CIK{cik10}.json"
        )
        if (
            not _SHA256_RE.fullmatch(raw_record_id)
            or not _SHA256_RE.fullmatch(raw_sha256)
            or raw_size <= 0
            or (
                raw_record_id,
                requested_url,
                http_status,
                raw_sha256,
                raw_size,
            )
            not in raw_records
        ):
            raise CrossSectionalAuditError(
                "SEC source-applicability raw-record anchor is invalid"
            )

        explicit_missing = _parse_boolean(row.explicit_missing)
        if status == "resolved_not_applicable":
            if (
                reviewed.get(cik10) != str(getattr(row, "ticker", "")).strip()
                or http_status != 404
                or str(row.companyfacts_error_code).strip() != "NoSuchKey"
                or str(row.companyfacts_object_key).strip()
                != f"api/xbrl/companyfacts/CIK{cik10}.json"
                or int(row.periodic_form_count) != 0
                or str(row.periodic_form_bases).strip()
                != "10-K|10-Q|20-F|40-F"
                or not _parse_boolean(row.periodic_form_amendments_included)
                or not explicit_missing
                or str(row.fact_value_state).strip()
                != "missing_source_not_applicable"
                or str(row.reason_code).strip()
                != "cached_404_no_such_key_and_zero_periodic_forms"
                or str(getattr(row, "exception_review_status", "")).strip()
                != "approved"
            ):
                raise CrossSectionalAuditError(
                    "resolved-not-applicable SEC source evidence is invalid"
                )
            resolved_ciks.append(cik10)
        elif (
            not 200 <= http_status < 300
            or explicit_missing
            or str(row.fact_value_state).strip()
            != "observed_source_available"
            or str(row.reason_code).strip() != "companyfacts_http_success"
        ):
            raise CrossSectionalAuditError(
                "available SEC source evidence is invalid"
            )
        statuses.append(status)
    status_counts = {
        str(key): int(value)
        for key, value in pd.Series(statuses, dtype="string").value_counts(
            sort=False
        ).sort_index().items()
    }
    return {
        "cik_count": int(len(frame)),
        "status_counts": status_counts,
        "resolved_ciks": sorted(resolved_ciks),
        "raw_records_verified": True,
    }


def _audit_entity_temporal_support(
    components: Mapping[str, _Component],
) -> dict[str, Any]:
    qa_component = components.get("entity_temporal_support")
    summary_component = components.get("entity_temporal_support_summary")
    if qa_component is None or qa_component.component_kind != "parquet":
        raise CrossSectionalAuditError(
            "bundle lacks entity temporal-support QA"
        )
    if summary_component is None or summary_component.component_kind != "json":
        raise CrossSectionalAuditError(
            "bundle lacks entity temporal-support summary"
        )
    try:
        qa = pd.read_parquet(qa_component.path)
    except Exception as exc:
        raise CrossSectionalAuditError(
            "cannot read entity temporal-support QA"
        ) from exc
    required = {
        "sid",
        "cik10",
        "research_interval_days",
        "issuer_periodic_filing_count",
        "source_applicability_status",
        "temporal_support_status",
        "temporal_support_passed",
    }
    missing = required.difference(qa.columns)
    if missing:
        raise CrossSectionalAuditError(
            f"entity temporal-support QA lacks columns: {sorted(missing)}"
        )
    passed = qa["temporal_support_passed"].map(_parse_boolean)
    if not passed.all():
        raise CrossSectionalAuditError(
            "entity temporal-support gate did not pass"
        )
    summary = _read_json_object(
        summary_component.path, "entity temporal-support summary"
    )
    if (
        not _parse_boolean(summary.get("temporal_support_gate_passed"))
        or int(summary.get("interval_count", -1)) != len(qa)
        or int(summary.get("failed_interval_count", -1)) != 0
    ):
        raise CrossSectionalAuditError(
            "entity temporal-support summary is inconsistent"
        )
    resolved = qa["source_applicability_status"].astype(str).eq(
        "resolved_not_applicable"
    )
    if int(summary.get("resolved_not_applicable_interval_count", -1)) != int(
        resolved.sum()
    ):
        raise CrossSectionalAuditError(
            "entity temporal-support exception count is inconsistent"
        )
    return {
        "interval_count": int(len(qa)),
        "long_interval_count": int(summary.get("long_interval_count", 0)),
        "resolved_not_applicable_interval_count": int(resolved.sum()),
        "failed_interval_count": 0,
        "temporal_support_gate_passed": True,
    }


def _audit_quality_records(
    inputs: AuditInputs,
    *,
    components: Mapping[str, _Component],
    bundle_id: str,
    bundle_manifest_sha256: str,
) -> dict[str, Any]:
    identifier = _read_json_object(inputs.identifier_qa_path, "identifier QA")
    fundamental = _read_json_object(
        inputs.fundamental_summary_path, "fundamental summary"
    )
    volume = _read_json_object(inputs.market_volume_qa_path, "market volume QA")
    market = _read_json_object(
        inputs.market_factor_summary_path, "market factor summary"
    )
    factor = _read_json_object(
        inputs.data_quality_summary_path, "factor data-quality summary"
    )
    freeze = _read_json_object(inputs.factor_freeze_path, "factor freeze")
    if not _parse_boolean(identifier.get("coverage_gate_passed")):
        raise CrossSectionalAuditError("identifier coverage gate did not pass")
    member_coverage = identifier.get("member_session_coverage")
    if not isinstance(member_coverage, Mapping):
        raise CrossSectionalAuditError("identifier member-session QA is missing")
    coverage = float(member_coverage.get("coverage", float("nan")))
    minimum = float(identifier.get("minimum_member_session_coverage", 1.0))
    if not coverage >= minimum:
        raise CrossSectionalAuditError("identifier coverage is below its minimum")
    requested = int(fundamental.get("requested_cik_count", 0))
    completed = int(fundamental.get("completed_cik_count", -1))
    failed = int(fundamental.get("failed_cik_count", -1))
    if (
        _parse_boolean(fundamental.get("limited_smoke_build"))
        or requested <= 0
        or completed != requested
        or failed != 0
    ):
        raise CrossSectionalAuditError(
            "fundamental build is limited, incomplete, or has failures"
        )
    applicability_evidence = _audit_source_applicability(
        inputs.sec_store_root,
        components,
    )
    applicability_count = int(
        fundamental.get("source_applicability_cik_count", -1)
    )
    applicability_counts = fundamental.get("source_applicability_counts")
    if not isinstance(applicability_counts, Mapping):
        raise CrossSectionalAuditError(
            "fundamental source-applicability counts are missing"
        )
    normalized_applicability_counts = {
        str(key): int(value) for key, value in applicability_counts.items()
    }
    allowed_applicability_states = {"available", "resolved_not_applicable"}
    if (
        applicability_count != completed
        or applicability_count != applicability_evidence["cik_count"]
        or set(normalized_applicability_counts).difference(
            allowed_applicability_states
        )
        or any(value < 0 for value in normalized_applicability_counts.values())
        or sum(normalized_applicability_counts.values()) != completed
        or normalized_applicability_counts
        != applicability_evidence["status_counts"]
    ):
        raise CrossSectionalAuditError(
            "fundamental source-applicability counts are inconsistent"
        )
    resolved_count = int(
        fundamental.get("resolved_not_applicable_cik_count", -1)
    )
    raw_resolved_ciks = fundamental.get("resolved_not_applicable_ciks")
    if not isinstance(raw_resolved_ciks, list):
        raise CrossSectionalAuditError(
            "resolved-not-applicable CIK list is missing"
        )
    resolved_ciks = sorted(str(value) for value in raw_resolved_ciks)
    reviewed_exception_ciks = {"0001132979", "0001288784"}
    if (
        resolved_count != len(resolved_ciks)
        or resolved_count
        != normalized_applicability_counts.get("resolved_not_applicable", 0)
        or len(set(resolved_ciks)) != len(resolved_ciks)
        or not set(resolved_ciks).issubset(reviewed_exception_ciks)
        or resolved_ciks != applicability_evidence["resolved_ciks"]
        or int(fundamental.get("not_applicable_imputed_fact_rows", -1)) != 0
    ):
        raise CrossSectionalAuditError(
            "resolved-not-applicable Company Facts evidence is inconsistent"
        )
    accounting = fundamental.get("accounting_identity")
    if not isinstance(accounting, Mapping) or not _parse_boolean(
        accounting.get("identity_gate_passed")
    ):
        raise CrossSectionalAuditError(
            "fundamental accounting identity gate did not pass"
        )
    temporal_support = _audit_entity_temporal_support(components)
    fundamental_temporal = fundamental.get("entity_temporal_support")
    if (
        not isinstance(fundamental_temporal, Mapping)
        or not _parse_boolean(
            fundamental_temporal.get("temporal_support_gate_passed")
        )
        or int(fundamental_temporal.get("interval_count", -1))
        != temporal_support["interval_count"]
        or not _parse_boolean(
            fundamental.get("fundamental_quality_gate_passed")
        )
    ):
        raise CrossSectionalAuditError(
            "fundamental temporal-support or aggregate quality gate did not pass"
        )
    if not _parse_boolean(volume.get("volume_qa_passed")):
        raise CrossSectionalAuditError("market volume gate did not pass")
    factor_bundle_id = str(factor.get("data_bundle_id", ""))
    freeze_bundle_id = str(freeze.get("data_bundle_id", ""))
    if (
        not factor_bundle_id
        or factor_bundle_id != freeze_bundle_id
        or factor_bundle_id != bundle_id
    ):
        raise CrossSectionalAuditError("factor summary/freeze bundle IDs differ")
    if str(factor.get("bundle_manifest_sha256", "")) != bundle_manifest_sha256:
        raise CrossSectionalAuditError(
            "factor summary does not anchor the bundle manifest"
        )
    if str(freeze.get("bundle_manifest_sha256", "")) != bundle_manifest_sha256:
        raise CrossSectionalAuditError(
            "factor freeze does not anchor the bundle manifest"
        )
    if str(freeze.get("status", "")) != "frozen_data_ready":
        raise CrossSectionalAuditError("factor database is not frozen_data_ready")
    if _parse_boolean(freeze.get("formal_eligible")):
        raise CrossSectionalAuditError("factor freeze must remain formal_eligible=false")
    return {
        "identifier": {
            "security_count": int(identifier.get("security_count", 0)),
            "mapped_security_count": int(
                identifier.get("mapped_security_count", 0)
            ),
            "member_session_coverage": coverage,
            "coverage_gate_passed": True,
        },
        "fundamental": {
            "requested_cik_count": requested,
            "completed_cik_count": completed,
            "failed_cik_count": failed,
            "source_applicability_cik_count": applicability_count,
            "source_applicability_counts": normalized_applicability_counts,
            "resolved_not_applicable_cik_count": resolved_count,
            "resolved_not_applicable_ciks": resolved_ciks,
            "not_applicable_imputed_fact_rows": 0,
            "source_applicability_raw_records_verified": True,
            "filing_rows": int(fundamental.get("filing_rows", 0)),
            "registered_fact_rows": int(
                fundamental.get("registered_fact_rows", 0)
            ),
            "canonical_fact_rows": int(
                fundamental.get("canonical_fact_rows", 0)
            ),
            "limited_smoke_build": False,
            "accounting_identity_gate_passed": True,
            "entity_temporal_support": temporal_support,
            "fundamental_quality_gate_passed": True,
        },
        "market": {
            "volume_qa_passed": True,
            "factor_count": int(market.get("factor_count", 0)),
            "signal_date_count": int(market.get("signal_date_count", 0)),
        },
        "factor": {
            "data_bundle_id": factor_bundle_id,
            "status": "frozen_data_ready",
            "formal_eligible": False,
            "performance_used_for_readiness": False,
            "deterministic_rebuild_passed": True,
            "actual_future_input_invariance_passed": True,
        },
    }


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise CrossSectionalAuditError(f"{label} is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CrossSectionalAuditError(f"{label} is invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise CrossSectionalAuditError(f"{label} must be a JSON object")
    return dict(value)


def _parse_boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        raise CrossSectionalAuditError("required boolean value is missing")
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise CrossSectionalAuditError(f"invalid boolean value={value!r}")


def _logical_path(path: Path, roots: Sequence[tuple[str, Path]]) -> str:
    resolved = path.resolve()
    for label, root in roots:
        try:
            relative = resolved.relative_to(root.resolve())
        except ValueError:
            continue
        return f"{label}/{relative.as_posix()}"
    raise CrossSectionalAuditError(
        f"bundle component is outside project/runtime roots: {path.name}"
    )


def _require_within(path: Path, root: Path, *, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise CrossSectionalAuditError(
            f"{label} resolves outside its evidence root"
        ) from exc


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--program", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    layout = DatabaseLayout.load(
        project_root=args.project_root,
        runtime_root=args.runtime_root,
        program_path=args.program,
    )
    result = audit_cross_sectional_database(AuditInputs.from_layout(layout))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
