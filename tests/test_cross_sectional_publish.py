from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audit_cross_sectional_database import (  # noqa: E402
    AuditInputs,
    CrossSectionalAuditError,
    audit_cross_sectional_database,
)
from publish_cross_sectional_database import (  # noqa: E402
    CrossSectionalPublishError,
    publish_cross_sectional_database,
)
from momentum_reversal.data.research_catalog import (  # noqa: E402
    rebuild_research_catalog,
)


HAS_DATA_DEPS = all(
    importlib.util.find_spec(name) is not None for name in ("duckdb", "pyarrow")
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(HAS_DATA_DEPS, "duckdb and pyarrow are required")
class CrossSectionalAuditAndPublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "project"
        self.runtime = self.root / "runtime"
        self.project.mkdir()
        self.runtime.mkdir()
        self.inputs = self._build_fixture()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _build_fixture(self) -> AuditInputs:
        sec_store = self.runtime / "sec"
        body = b'{"fixture":true}'
        body_sha = hashlib.sha256(body).hexdigest()
        raw_path = sec_store / "raw" / "sha256" / body_sha[:2] / f"{body_sha}.bin"
        raw_path.parent.mkdir(parents=True)
        raw_path.write_bytes(body)
        requested_url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json"
        status = 200
        record_id = hashlib.sha256(
            f"{requested_url}\n{status}\n{body_sha}".encode("utf-8")
        ).hexdigest()
        ledger_row = {
            "record_id": record_id,
            "requested_url": requested_url,
            "response_url": requested_url,
            "status": status,
            "retrieved_at_utc": "2026-08-20T00:00:00+00:00",
            "sha256": body_sha,
            "size_bytes": len(body),
            "raw_path": raw_path.relative_to(sec_store).as_posix(),
            "response_headers": {"content-type": "application/json"},
        }
        sec_store.mkdir(parents=True, exist_ok=True)
        (sec_store / "fetch_ledger.jsonl").write_text(
            json.dumps(ledger_row, sort_keys=True) + "\n", encoding="utf-8"
        )

        evidence = self.runtime / "evidence"
        evidence.mkdir()
        factor = pd.DataFrame(
            {
                "signal_date": pd.to_datetime(["2024-01-31", "2024-01-31"]),
                "sid": ["A", "B"],
                "factor_id": ["F1", "F1"],
                "raw_value": [1.0, np.nan],
                "score": [1.0, np.nan],
                "eligible": [True, False],
                "missing_reason": pd.Series([pd.NA, "no_source_factor_row"], dtype="string"),
                "rank": pd.Series([1, pd.NA], dtype="Int64"),
                "percentile": [1.0, np.nan],
                "source_panel": pd.Series(["market", "missing"], dtype="string"),
            }
        )
        readiness = pd.DataFrame(
            {
                "factor_id": ["F1"],
                "data_family": ["market"],
                "registered_first_round": [True],
                "evaluation_rows": [2],
                "eligible_rows": [1],
                "evaluation_member_coverage": [0.5],
                "covered_signal_months": [1],
                "total_signal_months": [1],
                "minimum_eligible_names": [1],
                "median_eligible_names": [1.0],
                "coverage_gate_passed": [True],
                "coverage_alternative_factor_id": pd.Series([pd.NA], dtype="string"),
                "coverage_alternative_passed": [False],
                "selection_status": ["ready_first_round"],
                "selected_factor_id": ["F1"],
                "performance_used": [False],
            }
        )
        coverage = pd.DataFrame(
            {
                "factor_id": ["F1"],
                "total_rows": [2],
                "eligible_rows": [1],
                "missing_rows": [1],
                "coverage_rate": [0.5],
            }
        )
        year = coverage.assign(year=2024).loc[
            :, ["year", "factor_id", "total_rows", "eligible_rows", "missing_rows", "coverage_rate"]
        ]
        missing_reason = pd.DataFrame(
            {
                "factor_id": ["F1"],
                "missing_reason": ["no_source_factor_row"],
                "missing_rows": [1],
            }
        )
        frames = {
            "factor_values": (factor, "v_factor_values"),
            "factor_readiness": (readiness, "v_factor_readiness"),
            "factor_coverage": (coverage, "v_factor_coverage"),
            "factor_year_coverage": (year, "v_factor_year_coverage"),
            "factor_missing_reason_coverage": (
                missing_reason,
                "v_factor_missing_reason_coverage",
            ),
            "fundamental_accounting_identity": (
                pd.DataFrame(
                    {
                        "cik": ["0000000001"],
                        "accession": ["0000000001-24-000001"],
                        "status": ["pass"],
                    }
                ),
                "v_fundamental_accounting_identity",
            ),
            "sec_source_applicability": (
                pd.DataFrame(
                    {
                        "cik10": ["0000000001"],
                        "ticker": pd.Series([pd.NA], dtype="string"),
                        "source": ["sec_companyfacts"],
                        "status": ["available"],
                        "reason_code": ["companyfacts_http_success"],
                        "explicit_missing": [False],
                        "fact_value_state": ["observed_source_available"],
                        "imputation_policy": ["none"],
                        "imputation_applied": [False],
                        "imputed_fact_rows": [0],
                        "submissions_cik10": ["0000000001"],
                        "periodic_form_count": pd.Series(
                            [pd.NA], dtype="Int64"
                        ),
                        "periodic_form_bases": ["10-K|10-Q|20-F|40-F"],
                        "periodic_form_amendments_included": [True],
                        "companyfacts_http_status": [200],
                        "companyfacts_error_code": pd.Series(
                            [pd.NA], dtype="string"
                        ),
                        "companyfacts_object_key": pd.Series(
                            [pd.NA], dtype="string"
                        ),
                        "companyfacts_raw_record_id": [record_id],
                        "companyfacts_raw_sha256": [body_sha],
                        "companyfacts_raw_size_bytes": [len(body)],
                        "exception_review_status": pd.Series(
                            [pd.NA], dtype="string"
                        ),
                        "exception_reviewed_date": pd.Series(
                            [pd.NA], dtype="string"
                        ),
                    }
                ),
                "v_sec_source_applicability",
            ),
            "entity_temporal_support": (
                pd.DataFrame(
                    {
                        "sid": ["sec::A"],
                        "cik10": ["0000000001"],
                        "research_interval_days": [365],
                        "issuer_periodic_filing_count": [1],
                        "source_applicability_status": ["available"],
                        "temporal_support_status": [
                            "supported_by_periodic_filing"
                        ],
                        "temporal_support_passed": [True],
                    }
                ),
                "v_entity_temporal_support",
            ),
        }
        components: list[dict[str, object]] = []
        for component_id, (frame, view_name) in frames.items():
            path = evidence / f"{component_id}.parquet"
            frame.to_parquet(path, index=False)
            components.append(
                {
                    "component_id": component_id,
                    "component_kind": "parquet",
                    "path": str(path.resolve()),
                    "view_name": view_name,
                    "sha256": _sha256(path),
                    "row_count": len(frame),
                    "source_version": "fixture-v1",
                }
            )
        for component_id, payload in {
            "deterministic_rebuild_qa": {
                "deterministic_rebuild_passed": True
            },
            "causality_qa": {
                "actual_future_input_invariance_passed": True
            },
            "entity_temporal_support_summary": {
                "interval_count": 1,
                "long_interval_count": 1,
                "resolved_not_applicable_interval_count": 0,
                "failed_interval_count": 0,
                "temporal_support_gate_passed": True,
            },
        }.items():
            path = evidence / f"{component_id}.json"
            path.write_text(
                json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
            )
            components.append(
                {
                    "component_id": component_id,
                    "component_kind": "json",
                    "path": str(path.resolve()),
                    "sha256": _sha256(path),
                    "row_count": None,
                    "source_version": "fixture-v1",
                }
            )
        bundle_manifest = evidence / "data_bundle_manifest.json"
        stable_manifest = {
            "schema_version": "cross_sectional_alpha.data_bundle_manifest.v1",
            "data_bundle_id": "fixture-bundle-v1",
            "formal_eligible": False,
            "components": components,
        }
        content_sha = hashlib.sha256(
            json.dumps(
                stable_manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        bundle_manifest.write_text(
            json.dumps(
                {
                    **stable_manifest,
                    "content_sha256": content_sha,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        registry = self.project / "active_factor_registry.csv"
        registry.write_text(
            "factor_id,display_name\nF1,Fixture factor\n", encoding="utf-8"
        )
        catalog = self.runtime / "catalog.duckdb"
        rebuild_research_catalog(
            catalog_path=catalog,
            bundle_manifest_path=bundle_manifest,
            factor_registry_path=registry,
        )

        qa = self.runtime / "qa"
        qa.mkdir()
        manifest_sha = _sha256(bundle_manifest)
        values = {
            "identifier_qa.json": {
                "coverage_gate_passed": True,
                "security_count": 2,
                "mapped_security_count": 2,
                "minimum_member_session_coverage": 0.98,
                "member_session_coverage": {
                    "coverage": 1.0,
                    "member_sessions": 2,
                    "mapped_member_sessions": 2,
                    "unmapped_member_sessions": 0,
                },
            },
            "fundamental_summary.json": {
                "requested_cik_count": 1,
                "completed_cik_count": 1,
                "failed_cik_count": 0,
                "source_applicability_cik_count": 1,
                "source_applicability_counts": {"available": 1},
                "resolved_not_applicable_cik_count": 0,
                "resolved_not_applicable_ciks": [],
                "not_applicable_imputed_fact_rows": 0,
                "limited_smoke_build": False,
                "filing_rows": 3,
                "registered_fact_rows": 4,
                "canonical_fact_rows": 2,
                "accounting_identity": {"identity_gate_passed": True},
                "entity_temporal_support": {
                    "interval_count": 1,
                    "long_interval_count": 1,
                    "resolved_not_applicable_interval_count": 0,
                    "failed_interval_count": 0,
                    "temporal_support_gate_passed": True,
                },
                "fundamental_quality_gate_passed": True,
            },
            "market_volume_qa.json": {"volume_qa_passed": True},
            "market_factor_summary.json": {
                "factor_count": 1,
                "signal_date_count": 1,
            },
            "data_quality_summary.json": {
                "data_bundle_id": "fixture-bundle-v1",
                "formal_eligible": False,
                "performance_used_for_readiness": False,
                "deterministic_rebuild_passed": True,
                "actual_future_input_invariance_passed": True,
                "bundle_manifest_sha256": manifest_sha,
                "catalog_path": str(catalog),
            },
            "factor_freeze.json": {
                "status": "frozen_data_ready",
                "formal_eligible": False,
                "data_bundle_id": "fixture-bundle-v1",
                "bundle_manifest_sha256": manifest_sha,
            },
        }
        for name, value in values.items():
            (qa / name).write_text(
                json.dumps(value, sort_keys=True) + "\n", encoding="utf-8"
            )
        return AuditInputs(
            project_root=self.project,
            runtime_root=self.runtime,
            sec_store_root=sec_store,
            bundle_manifest_path=bundle_manifest,
            catalog_path=catalog,
            identifier_qa_path=qa / "identifier_qa.json",
            fundamental_summary_path=qa / "fundamental_summary.json",
            market_volume_qa_path=qa / "market_volume_qa.json",
            market_factor_summary_path=qa / "market_factor_summary.json",
            data_quality_summary_path=qa / "data_quality_summary.json",
            factor_freeze_path=qa / "factor_freeze.json",
        )

    def _refresh_manifest_anchors_and_catalog(self) -> None:
        manifest = json.loads(
            self.inputs.bundle_manifest_path.read_text(encoding="utf-8")
        )
        for item in manifest["components"]:
            path = Path(item["path"])
            item["sha256"] = _sha256(path)
            if item["component_kind"] == "parquet":
                item["row_count"] = len(pd.read_parquet(path))
        stable_manifest = dict(manifest)
        stable_manifest.pop("content_sha256", None)
        stable_manifest.pop("generated_at_utc", None)
        manifest["content_sha256"] = hashlib.sha256(
            json.dumps(
                stable_manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.inputs.bundle_manifest_path.write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        manifest_sha = _sha256(self.inputs.bundle_manifest_path)
        for path in (
            self.inputs.data_quality_summary_path,
            self.inputs.factor_freeze_path,
        ):
            value = json.loads(path.read_text(encoding="utf-8"))
            value["bundle_manifest_sha256"] = manifest_sha
            path.write_text(
                json.dumps(value, sort_keys=True) + "\n", encoding="utf-8"
            )
        registry = self.project / "active_factor_registry.csv"
        rebuild_research_catalog(
            catalog_path=self.inputs.catalog_path,
            bundle_manifest_path=self.inputs.bundle_manifest_path,
            factor_registry_path=registry,
        )

    def test_read_only_audit_verifies_raw_bundle_factor_and_catalog(self) -> None:
        result = audit_cross_sectional_database(self.inputs)

        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["read_only"])
        self.assertTrue(result["raw_sec"]["all_objects_size_and_sha256_verified"])
        self.assertTrue(result["bundle"]["all_component_hashes_verified"])
        self.assertTrue(result["factor_database"]["factor_contract_verified"])
        self.assertFalse(result["factor_database"]["readiness_performance_used"])
        self.assertTrue(result["catalog"]["all_catalog_row_counts_verified"])
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn(str(self.root), encoded)

    def test_audit_fails_on_raw_or_bundle_hash_drift(self) -> None:
        ledger = json.loads(
            (self.inputs.sec_store_root / "fetch_ledger.jsonl").read_text(
                encoding="utf-8"
            )
        )
        raw = self.inputs.sec_store_root / ledger["raw_path"]
        raw.write_bytes(raw.read_bytes() + b"tamper")
        with self.assertRaisesRegex(
            CrossSectionalAuditError, "raw object size/hash mismatch"
        ):
            audit_cross_sectional_database(self.inputs)

        # Restore raw evidence, then independently exercise bundle hashing.
        raw.write_bytes(b'{"fixture":true}')
        manifest = json.loads(
            self.inputs.bundle_manifest_path.read_text(encoding="utf-8")
        )
        factor_path = Path(
            next(
                item["path"]
                for item in manifest["components"]
                if item["component_id"] == "factor_values"
            )
        )
        factor_path.write_bytes(factor_path.read_bytes() + b"tamper")
        with self.assertRaisesRegex(
            CrossSectionalAuditError, "component SHA256 mismatch"
        ):
            audit_cross_sectional_database(self.inputs)

    def test_audit_rejects_performance_used_readiness(self) -> None:
        manifest = json.loads(
            self.inputs.bundle_manifest_path.read_text(encoding="utf-8")
        )
        readiness_path = Path(
            next(
                item["path"]
                for item in manifest["components"]
                if item["component_id"] == "factor_readiness"
            )
        )
        readiness = pd.read_parquet(readiness_path)
        readiness.loc[:, "performance_used"] = True
        readiness.to_parquet(readiness_path, index=False)
        self._refresh_manifest_anchors_and_catalog()

        with self.assertRaisesRegex(
            CrossSectionalAuditError, "readiness used performance"
        ):
            audit_cross_sectional_database(self.inputs)

    def test_audit_rejects_unreviewed_companyfacts_resolution(self) -> None:
        ledger_path = self.inputs.sec_store_root / "fetch_ledger.jsonl"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["status"] = 404
        ledger["record_id"] = hashlib.sha256(
            (
                f"{ledger['requested_url']}\n404\n{ledger['sha256']}"
            ).encode("utf-8")
        ).hexdigest()
        ledger_path.write_text(
            json.dumps(ledger, sort_keys=True) + "\n", encoding="utf-8"
        )
        manifest = json.loads(
            self.inputs.bundle_manifest_path.read_text(encoding="utf-8")
        )
        source_path = Path(
            next(
                item["path"]
                for item in manifest["components"]
                if item["component_id"] == "sec_source_applicability"
            )
        )
        source = pd.read_parquet(source_path)
        source.loc[:, "ticker"] = "UNREVIEWED"
        source.loc[:, "status"] = "resolved_not_applicable"
        source.loc[:, "reason_code"] = (
            "cached_404_no_such_key_and_zero_periodic_forms"
        )
        source.loc[:, "explicit_missing"] = True
        source.loc[:, "fact_value_state"] = "missing_source_not_applicable"
        source.loc[:, "periodic_form_count"] = 0
        source.loc[:, "companyfacts_http_status"] = 404
        source.loc[:, "companyfacts_error_code"] = "NoSuchKey"
        source.loc[:, "companyfacts_object_key"] = (
            "api/xbrl/companyfacts/CIK0000000001.json"
        )
        source.loc[:, "companyfacts_raw_record_id"] = ledger["record_id"]
        source.loc[:, "exception_review_status"] = "approved"
        source.loc[:, "exception_reviewed_date"] = "2026-08-20"
        source.to_parquet(source_path, index=False)
        summary = json.loads(
            self.inputs.fundamental_summary_path.read_text(encoding="utf-8")
        )
        summary["source_applicability_counts"] = {
            "resolved_not_applicable": 1
        }
        summary["resolved_not_applicable_cik_count"] = 1
        summary["resolved_not_applicable_ciks"] = ["0000000001"]
        self.inputs.fundamental_summary_path.write_text(
            json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8"
        )
        self._refresh_manifest_anchors_and_catalog()
        with self.assertRaisesRegex(
            CrossSectionalAuditError,
            "resolved-not-applicable SEC source evidence",
        ):
            audit_cross_sectional_database(self.inputs)

    def test_compact_publish_is_whitelisted_portable_and_immutable(self) -> None:
        allowed = self.project / "results" / "published" / "cross_sectional_data"
        destination = allowed / "fixture-bundle-v1"
        first = publish_cross_sectional_database(
            self.inputs,
            destination=destination,
            allowed_publish_root=allowed,
        )
        self.assertEqual(first["status"], "published")
        names = {path.name for path in destination.iterdir()}
        self.assertEqual(
            names,
            {
                "README.md",
                "manifest.json",
                "audit_summary.json",
                "evidence_index.json",
                "identifier_qa.json",
                "fundamental_summary.json",
                "market_volume_qa.json",
                "market_factor_summary.json",
                "data_quality_summary.json",
                "factor_readiness.csv",
                "factor_coverage.csv",
                "factor_year_coverage.csv",
                "missing_reason_coverage.csv",
                "accounting_identity_qa.csv",
                "entity_temporal_support_qa.csv",
                "entity_temporal_support_summary.json",
                "causality_qa.json",
                "deterministic_rebuild_qa.json",
            },
        )
        self.assertFalse(
            any(
                path.suffix.lower() in {".parquet", ".duckdb", ".bin", ".jsonl"}
                for path in destination.iterdir()
            )
        )
        all_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in destination.iterdir()
            if path.suffix.lower() in {".json", ".md", ".csv"}
        )
        self.assertNotIn(str(self.root), all_text)
        second = publish_cross_sectional_database(
            self.inputs,
            destination=destination,
            allowed_publish_root=allowed,
        )
        self.assertEqual(second["status"], "already_published_identical")

        manifest_before = _sha256(destination / "manifest.json")
        mapping = json.loads(
            self.inputs.identifier_qa_path.read_text(encoding="utf-8")
        )
        mapping["security_count"] = 3
        self.inputs.identifier_qa_path.write_text(
            json.dumps(mapping, sort_keys=True) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(
            CrossSectionalPublishError, "differs; refusing overwrite"
        ):
            publish_cross_sectional_database(
                self.inputs,
                destination=destination,
                allowed_publish_root=allowed,
            )
        self.assertEqual(manifest_before, _sha256(destination / "manifest.json"))

    def test_publisher_rejects_destination_outside_allowed_root(self) -> None:
        allowed = self.project / "results" / "published" / "cross_sectional_data"
        with self.assertRaisesRegex(
            CrossSectionalPublishError, "inside the allowed publish root"
        ):
            publish_cross_sectional_database(
                self.inputs,
                destination=self.project / "unsafe",
                allowed_publish_root=allowed,
            )


if __name__ == "__main__":
    unittest.main()
