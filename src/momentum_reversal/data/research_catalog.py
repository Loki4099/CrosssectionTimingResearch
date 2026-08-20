"""Rebuildable DuckDB catalog for cross-sectional research artifacts.

Parquet files and their manifests remain the evidence.  The DuckDB file is a
local convenience index that can be deleted and rebuilt without changing any
research result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
from typing import Any


class ResearchCatalogError(RuntimeError):
    """Raised when a catalog cannot be rebuilt from its declared evidence."""


def rebuild_research_catalog(
    *,
    catalog_path: str | Path,
    bundle_manifest_path: str | Path,
    factor_registry_path: str | Path,
    factor_definition_registry_path: str | Path | None = None,
    metric_registry_path: str | Path | None = None,
    data_program_path: str | Path | None = None,
) -> Path:
    """Atomically rebuild the local DuckDB catalog from a bundle manifest."""

    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - environment guard
        raise ResearchCatalogError(
            "duckdb is required; install the project's data optional dependency"
        ) from exc

    catalog = Path(catalog_path).resolve()
    manifest_path = Path(bundle_manifest_path).resolve()
    registry_path = Path(factor_registry_path).resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    if not registry_path.is_file():
        raise FileNotFoundError(registry_path)
    definition_path = _optional_existing_path(factor_definition_registry_path)
    metric_path = _optional_existing_path(metric_registry_path)
    program_path = _optional_existing_path(data_program_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    components = _validated_components(manifest, manifest_path.parent)
    catalog.parent.mkdir(parents=True, exist_ok=True)
    temporary = catalog.with_name(f".{catalog.name}.building")
    if temporary.exists():
        temporary.unlink()

    connection = duckdb.connect(str(temporary))
    try:
        connection.execute(
            """
            CREATE TABLE catalog_metadata (
                key VARCHAR PRIMARY KEY,
                value VARCHAR NOT NULL
            )
            """
        )
        metadata = {
            "schema_version": "cross_sectional_alpha.catalog.v1",
            "bundle_manifest": str(manifest_path),
            "bundle_id": str(manifest.get("data_bundle_id", "")),
            "bundle_content_sha256": str(manifest.get("content_sha256", "")),
            "evidence_policy": "external parquet and manifests are authoritative",
        }
        connection.executemany(
            "INSERT INTO catalog_metadata VALUES (?, ?)", sorted(metadata.items())
        )

        connection.execute(
            """
            CREATE TABLE data_component (
                component_id VARCHAR PRIMARY KEY,
                component_kind VARCHAR NOT NULL,
                path VARCHAR NOT NULL,
                sha256 VARCHAR,
                row_count BIGINT,
                source_version VARCHAR
            )
            """
        )
        connection.executemany(
            "INSERT INTO data_component VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    item["component_id"],
                    item["component_kind"],
                    str(item["resolved_path"]),
                    item.get("sha256"),
                    item.get("row_count"),
                    item.get("source_version"),
                )
                for item in components
            ],
        )

        _create_csv_table(connection, "active_factor_registry", registry_path)
        # Backwards-compatible alias for callers written before the catalog
        # distinguished the active implementation registry from the full
        # literature definition registry.
        connection.execute(
            "CREATE VIEW factor_definition AS "
            "SELECT * FROM active_factor_registry"
        )
        if definition_path is not None:
            _create_csv_table(
                connection, "factor_definition_registry", definition_path
            )
        if metric_path is not None:
            _create_csv_table(connection, "sec_metric_registry", metric_path)
        if program_path is not None:
            connection.execute(
                "INSERT INTO catalog_metadata VALUES (?, ?)",
                ("data_program", str(program_path)),
            )
        for item in components:
            if item["component_kind"] != "parquet":
                continue
            view_name = item.get("view_name") or item["component_id"]
            _validate_identifier(view_name)
            escaped = str(item["resolved_path"]).replace("'", "''")
            connection.execute(
                f'CREATE VIEW "{view_name}" AS SELECT * FROM read_parquet(\'{escaped}\')'
            )
        connection.execute(
            """
            CREATE VIEW v_catalog_components AS
            SELECT component_id, component_kind, path, sha256, row_count,
                   source_version
            FROM data_component
            ORDER BY component_id
            """
        )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()

    os.replace(temporary, catalog)
    return catalog


def _validated_components(
    manifest: Mapping[str, Any], base: Path
) -> list[dict[str, Any]]:
    raw = manifest.get("components")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ResearchCatalogError("bundle manifest components must be a list")
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for value in raw:
        if not isinstance(value, Mapping):
            raise ResearchCatalogError("every component must be an object")
        item = dict(value)
        component_id = str(item.get("component_id", "")).strip()
        component_kind = str(item.get("component_kind", "")).strip()
        raw_path = str(item.get("path", "")).strip()
        _validate_identifier(component_id)
        if component_id in seen:
            raise ResearchCatalogError(f"duplicate component_id={component_id}")
        if component_kind not in {
            "parquet",
            "manifest",
            "json",
            "csv",
            "toml",
            "python",
        }:
            raise ResearchCatalogError(
                f"unsupported component_kind={component_kind}"
            )
        if not raw_path:
            raise ResearchCatalogError(f"component path is blank: {component_id}")
        path = Path(raw_path)
        if not path.is_absolute():
            path = (base / path).resolve()
        else:
            path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        expected_sha = str(item.get("sha256", "")).strip()
        if expected_sha and _sha256_path(path) != expected_sha:
            raise ResearchCatalogError(
                f"component sha256 mismatch: {component_id}"
            )
        expected_rows = item.get("row_count")
        if component_kind == "parquet" and expected_rows is not None:
            try:
                import pyarrow.parquet as pq
            except ImportError as exc:  # pragma: no cover - environment guard
                raise ResearchCatalogError(
                    "pyarrow is required to verify parquet row counts"
                ) from exc
            observed_rows = int(pq.ParquetFile(path).metadata.num_rows)
            if observed_rows != int(expected_rows):
                raise ResearchCatalogError(
                    f"component row_count mismatch: {component_id}"
                )
        item["component_id"] = component_id
        item["component_kind"] = component_kind
        item["resolved_path"] = path
        seen.add(component_id)
        result.append(item)
    return sorted(result, key=lambda item: item["component_id"])


def _create_csv_table(connection: Any, table_name: str, path: Path) -> None:
    _validate_identifier(table_name)
    escaped = str(path).replace("'", "''")
    connection.execute(
        f'CREATE TABLE "{table_name}" AS '
        f"SELECT * FROM read_csv_auto('{escaped}', header=true, all_varchar=true)"
    )


def _optional_existing_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _sha256_path(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def _validate_identifier(value: str) -> None:
    if not value or not value.replace("_", "").isalnum() or value[0].isdigit():
        raise ResearchCatalogError(f"invalid catalog identifier={value!r}")
