"""Publish a compact, data-only cross-sectional database review bundle.

The publisher first runs the full read-only audit.  It then materializes only
an explicit CSV/JSON/README whitelist.  Full Parquet panels, DuckDB catalogs,
SEC payloads, fetch ledgers, and any other raw evidence can never be copied by
this script.  An existing destination is accepted only when its complete file
tree is byte-identical; drift is never overwritten.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
for candidate in (SRC_ROOT, SCRIPTS_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from audit_cross_sectional_database import (  # noqa: E402
    AuditInputs,
    CrossSectionalAuditError,
    audit_cross_sectional_database,
    resolve_bundle_component_paths,
)
from momentum_reversal.pipelines.cross_sectional_database import (  # noqa: E402
    DatabaseLayout,
)


_PROHIBITED_SUFFIXES = {".parquet", ".duckdb", ".bin", ".jsonl"}
_REQUIRED_COMPONENT_EXPORTS = {
    "factor_readiness": "factor_readiness.csv",
    "factor_coverage": "factor_coverage.csv",
    "factor_year_coverage": "factor_year_coverage.csv",
    "factor_missing_reason_coverage": "missing_reason_coverage.csv",
    "fundamental_accounting_identity": "accounting_identity_qa.csv",
    "entity_temporal_support": "entity_temporal_support_qa.csv",
}
_REQUIRED_COMPONENT_JSON_EXPORTS = {
    "causality_qa": "causality_qa.json",
    "deterministic_rebuild_qa": "deterministic_rebuild_qa.json",
    "entity_temporal_support_summary": "entity_temporal_support_summary.json",
}
_JSON_EXPORTS = {
    "identifier_qa.json": "identifier_qa_path",
    "fundamental_summary.json": "fundamental_summary_path",
    "market_volume_qa.json": "market_volume_qa_path",
    "market_factor_summary.json": "market_factor_summary_path",
    "data_quality_summary.json": "data_quality_summary_path",
}
_ALLOWED_FILENAMES = {
    "README.md",
    "manifest.json",
    "audit_summary.json",
    "evidence_index.json",
    *_JSON_EXPORTS,
    *_REQUIRED_COMPONENT_EXPORTS.values(),
    *_REQUIRED_COMPONENT_JSON_EXPORTS.values(),
}
_MAX_FILE_BYTES = 5 * 1024 * 1024


class CrossSectionalPublishError(RuntimeError):
    """Raised when compact publication would be unsafe or non-immutable."""


def publish_cross_sectional_database(
    inputs: AuditInputs,
    *,
    destination: str | Path,
    allowed_publish_root: str | Path,
) -> dict[str, Any]:
    """Audit and atomically publish the compact whitelist.

    Parameters are explicit so unit tests can use isolated fixtures.  The
    destination must be one child (or deeper descendant) of
    ``allowed_publish_root``; the root itself can never be replaced.
    """

    destination_path = Path(destination).resolve()
    allowed_root = Path(allowed_publish_root).resolve()
    _require_publish_destination(destination_path, allowed_root)
    try:
        audit = audit_cross_sectional_database(inputs)
        component_paths = resolve_bundle_component_paths(inputs)
    except CrossSectionalAuditError as exc:
        raise CrossSectionalPublishError(
            f"database audit failed; publication refused: {exc}"
        ) from exc

    allowed_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{destination_path.name}.building-", dir=allowed_root
        )
    ).resolve()
    _require_temporary_directory(temporary, allowed_root, destination_path.name)
    try:
        _write_json(temporary / "audit_summary.json", audit)
        _write_json(
            temporary / "evidence_index.json",
            _evidence_index(audit),
        )
        for output_name, field_name in _JSON_EXPORTS.items():
            source = Path(getattr(inputs, field_name))
            _write_json(
                temporary / output_name,
                _portable_json(_read_json_object(source, output_name)),
            )
        for component_id, output_name in _REQUIRED_COMPONENT_EXPORTS.items():
            path = component_paths.get(component_id)
            if path is None:
                raise CrossSectionalPublishError(
                    f"bundle lacks compact export component={component_id}"
                )
            frame = pd.read_parquet(path)
            frame = _stable_sort(frame)
            frame.to_csv(
                temporary / output_name,
                index=False,
                lineterminator="\n",
            )
        for component_id, output_name in (
            _REQUIRED_COMPONENT_JSON_EXPORTS.items()
        ):
            path = component_paths.get(component_id)
            if path is None:
                raise CrossSectionalPublishError(
                    f"bundle lacks compact export component={component_id}"
                )
            _write_json(
                temporary / output_name,
                _portable_json(_read_json_object(path, output_name)),
            )
        (temporary / "README.md").write_text(
            _readme(audit), encoding="utf-8", newline="\n"
        )
        _verify_compact_tree(temporary, expect_manifest=False)
        _write_json(temporary / "manifest.json", _publication_manifest(audit, temporary))
        _verify_compact_tree(temporary, expect_manifest=True)

        if destination_path.exists():
            if not destination_path.is_dir():
                raise CrossSectionalPublishError(
                    f"publication destination is not a directory: {destination_path}"
                )
            if _tree_index(destination_path) != _tree_index(temporary):
                raise CrossSectionalPublishError(
                    "published compact artifact differs; refusing overwrite"
                )
            return {
                "status": "already_published_identical",
                "data_bundle_id": str(audit["bundle"]["data_bundle_id"]),
                "file_count": len(_tree_index(destination_path)),
            }
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, destination_path)
        return {
            "status": "published",
            "data_bundle_id": str(audit["bundle"]["data_bundle_id"]),
            "file_count": len(_tree_index(destination_path)),
        }
    finally:
        if temporary.exists():
            _require_temporary_directory(
                temporary, allowed_root, destination_path.name
            )
            shutil.rmtree(temporary)


def _evidence_index(audit: Mapping[str, Any]) -> dict[str, Any]:
    bundle = dict(audit["bundle"])
    return {
        "schema_version": "cross_sectional_alpha.compact_evidence_index.v1",
        "data_bundle_id": str(bundle["data_bundle_id"]),
        "bundle_manifest_sha256": str(bundle["manifest_sha256"]),
        "raw_sec": dict(audit["raw_sec"]),
        "components": list(bundle["components"]),
        "evidence_policy": (
            "runtime Parquet/raw objects are authoritative; this Git artifact "
            "contains hashes and compact QA only"
        ),
    }


def _publication_manifest(
    audit: Mapping[str, Any], directory: Path
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.name == "manifest.json":
            continue
        item: dict[str, Any] = {
            "path": path.name,
            "sha256": _sha256_file(path),
            "size_bytes": int(path.stat().st_size),
        }
        if path.suffix.lower() == ".csv":
            item["row_count"] = int(len(pd.read_csv(path)))
        files.append(item)
    return {
        "schema_version": "cross_sectional_alpha.compact_publish.v1",
        "data_bundle_id": str(audit["bundle"]["data_bundle_id"]),
        "status": "data_ready_for_experiment_planning",
        "formal_eligible": False,
        "experiment_authorized": False,
        "performance_results_included": False,
        "bundle_manifest_sha256": str(audit["bundle"]["manifest_sha256"]),
        "raw_sec_content_index_sha256": str(
            audit["raw_sec"]["content_index_sha256"]
        ),
        "files": files,
    }


def _readme(audit: Mapping[str, Any]) -> str:
    gates = audit["quality_gates"]
    identifier = gates["identifier"]
    fundamental = gates["fundamental"]
    market = gates["market"]
    factor = audit["factor_database"]
    ready = ", ".join(f"`{value}`" for value in factor["ready_first_round_factor_ids"])
    blocked = ", ".join(
        f"`{value}`" for value in factor["blocked_first_round_factor_ids"]
    )
    ready = ready or "none"
    blocked = blocked or "none"
    return (
        "# Cross-sectional market + SEC data bundle\n\n"
        f"- Data bundle: `{audit['bundle']['data_bundle_id']}`\n"
        "- Status: `data_ready_for_experiment_planning`\n"
        "- Formal eligibility: `false`\n"
        "- Experiment authorization: `false`\n"
        "- Performance results used for readiness: `false`\n\n"
        "This directory is a compact review layer. Full market/SEC Parquet, "
        "DuckDB, raw provider payloads, fetch ledgers, portfolio returns and "
        "holdings remain outside Git. `evidence_index.json` anchors those "
        "runtime files by SHA256 and row count.\n\n"
        "## Data gates\n\n"
        f"- SID coverage: {identifier['mapped_security_count']}/"
        f"{identifier['security_count']} securities; member-session coverage "
        f"{float(identifier['member_session_coverage']):.6%}.\n"
        f"- SEC issuers: {fundamental['completed_cik_count']}/"
        f"{fundamental['requested_cik_count']} completed; failures "
        f"{fundamental['failed_cik_count']}.\n"
        "- SEC Company Facts applicability: "
        f"{fundamental['resolved_not_applicable_cik_count']} reviewed "
        "issuer(s) resolved not applicable; imputed fact rows 0.\n"
        f"- SEC rows: {fundamental['filing_rows']} filings, "
        f"{fundamental['registered_fact_rows']} registered facts, "
        f"{fundamental['canonical_fact_rows']} canonical annual facts.\n"
        f"- Market factors: {market['factor_count']} factors across "
        f"{market['signal_date_count']} signal dates; volume QA passed.\n"
        f"- Unified factor database: {factor['row_count']} rows, "
        f"{factor['factor_count']} factors, {factor['eligible_row_count']} "
        "eligible observations; primary-key duplicates 0.\n\n"
        "- Accounting identity, historical SID-to-CIK temporal support, actual "
        "future-input truncation and independent deterministic rebuild gates "
        "all passed.\n\n"
        "## Data-only factor readiness\n\n"
        f"Ready selected factor IDs: {ready}.\n\n"
        f"Registered first-round factors blocked by data gates: {blocked}.\n\n"
        "These decisions use coverage and availability only. They contain no "
        "forward returns, Top-K performance, model selection or P00 transfer "
        "result, and therefore do not authorize a numbered experiment.\n"
    )


def _stable_sort(frame: pd.DataFrame) -> pd.DataFrame:
    keys = [
        column
        for column in (
            "factor_id",
            "year",
            "signal_date",
            "missing_reason",
        )
        if column in frame.columns
    ]
    if not keys:
        return frame.reset_index(drop=True)
    return frame.sort_values(keys, kind="stable", ignore_index=True)


def _portable_json(value: object) -> object:
    if isinstance(value, Mapping):
        output: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key == "catalog_path" and isinstance(raw_value, str):
                output["catalog_filename"] = Path(raw_value).name
            else:
                output[key] = _portable_json(raw_value)
        return output
    if isinstance(value, list):
        return [_portable_json(item) for item in value]
    return value


def _verify_compact_tree(directory: Path, *, expect_manifest: bool) -> None:
    entries = list(directory.iterdir())
    if any(not path.is_file() for path in entries):
        raise CrossSectionalPublishError("compact publication must be a flat file tree")
    names = {path.name for path in entries}
    expected = _ALLOWED_FILENAMES if expect_manifest else _ALLOWED_FILENAMES - {"manifest.json"}
    if names != expected:
        raise CrossSectionalPublishError(
            f"compact whitelist mismatch; missing={sorted(expected - names)}, "
            f"extra={sorted(names - expected)}"
        )
    for path in entries:
        if path.suffix.lower() in _PROHIBITED_SUFFIXES:
            raise CrossSectionalPublishError(
                f"prohibited evidence type in compact publication: {path.name}"
            )
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise CrossSectionalPublishError(
                f"compact artifact exceeds {_MAX_FILE_BYTES} bytes: {path.name}"
            )


def _require_publish_destination(destination: Path, root: Path) -> None:
    try:
        relative = destination.relative_to(root)
    except ValueError as exc:
        raise CrossSectionalPublishError(
            "publication destination must be inside the allowed publish root"
        ) from exc
    if not relative.parts:
        raise CrossSectionalPublishError(
            "publication destination cannot be the publish root itself"
        )


def _require_temporary_directory(
    temporary: Path, root: Path, destination_name: str
) -> None:
    try:
        relative = temporary.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise CrossSectionalPublishError(
            "temporary publication directory escaped its allowed root"
        ) from exc
    prefix = f".{destination_name}.building-"
    if len(relative.parts) != 1 or not temporary.name.startswith(prefix):
        raise CrossSectionalPublishError(
            "refusing to remove an unrecognized temporary directory"
        )


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise CrossSectionalPublishError(f"compact JSON source is missing: {label}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CrossSectionalPublishError(
            f"compact JSON source is invalid: {label}"
        ) from exc
    if not isinstance(value, Mapping):
        raise CrossSectionalPublishError(
            f"compact JSON source must be an object: {label}"
        )
    return dict(value)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _tree_index(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256_file(path),
            "size_bytes": int(path.stat().st_size),
        }
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix())
        if path.is_file()
    ]


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
    parser.add_argument("--destination", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    layout = DatabaseLayout.load(
        project_root=args.project_root,
        runtime_root=args.runtime_root,
        program_path=args.program,
    )
    bundle_id = str(layout.program["versions"]["data_bundle"])
    allowed_root = (
        Path(args.project_root).resolve()
        / "results"
        / "published"
        / "cross_sectional_data"
    )
    destination = (
        args.destination.resolve()
        if args.destination is not None
        else allowed_root / bundle_id
    )
    result = publish_cross_sectional_database(
        AuditInputs.from_layout(layout),
        destination=destination,
        allowed_publish_root=allowed_root,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
