from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from certify_cross_sectional_source_data import (  # noqa: E402
    SourceDataCertificationError,
    certify_cross_sectional_source_data,
    publish_source_data_certification,
)
from momentum_reversal.data.entity_temporal_audit import (  # noqa: E402
    build_entity_temporal_support_qa,
)
from momentum_reversal.pipelines.cross_sectional_database import (  # noqa: E402
    DatabaseLayout,
    build_accounting_identity_qa,
)


HAS_PYARROW = importlib.util.find_spec("pyarrow") is not None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _directory_manifest(root: Path, schema: str, **extra: object) -> dict[str, object]:
    files = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if (
            not path.is_file()
            or path.name in {"manifest.json", "FROZEN.json", "build_summary.json"}
            or path.name.startswith(".")
        ):
            continue
        files.append(
            {
                "path": path.name,
                "sha256": _sha(path),
                "size_bytes": path.stat().st_size,
            }
        )
    stable = {"schema_version": schema, "files": files, **extra}
    content = hashlib.sha256(
        json.dumps(
            stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return {**stable, "content_sha256": content, "generated_at_utc": "fixture"}


@unittest.skipUnless(HAS_PYARROW, "pyarrow is required")
class CrossSectionalSourceCertificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.project = self.base / "project"
        self.runtime = self.base / "runtime"
        self.project.mkdir()
        self.runtime.mkdir()
        self.layout, self.raw_path = self._build_fixture()
        self.signature_patcher = patch(
            "certify_cross_sectional_source_data._fundamental_build_signature",
            return_value="a" * 64,
        )
        self.signature_mock = self.signature_patcher.start()

    def tearDown(self) -> None:
        self.signature_patcher.stop()
        self.temporary.cleanup()

    def _build_fixture(self) -> tuple[DatabaseLayout, Path]:
        version = "market-fixture-v1"
        fundamental_version = "fundamental-fixture-v1"
        config_dir = self.project / "config" / "research" / "cross_sectional_alpha"
        config_dir.mkdir(parents=True)
        (config_dir / "sec_companyfacts_exceptions.csv").write_text(
            "cik10,ticker,resolution,required_http_status,required_error_code,"
            "periodic_form_bases,include_amendments,required_periodic_form_count,"
            "review_status,reviewed_date\n",
            encoding="utf-8",
        )
        program = config_dir / "data_program.toml"
        program.write_text(
            "\n".join(
                [
                    'program_id = "fixture"',
                    "formal_eligible = false",
                    "[versions]",
                    f'market_dataset = "{version}"',
                    'entity_bridge = "bridge-fixture-v1"',
                    f'fundamentals = "{fundamental_version}"',
                    'factor_build = "unused-factor-fixture-v1"',
                    'data_bundle = "unused-bundle-fixture-v1"',
                    "[sample]",
                    'history_start = "2020-01-01"',
                    'evaluation_end = "2020-12-31"',
                    "[sec]",
                    'companyfacts_url_template = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"',
                    "[identifier_resolution]",
                    "minimum_temporal_support_interval_days = 365",
                    "minimum_member_session_coverage = 0.98",
                    "[fundamentals]",
                    'companyfacts_exceptions = "config/research/cross_sectional_alpha/sec_companyfacts_exceptions.csv"',
                    "[storage]",
                    'raw_relative_path = "data/raw/cross_sectional_alpha"',
                    'curated_relative_path = "data/curated/cross_sectional_alpha"',
                    'derived_relative_path = "data/derived/cross_sectional_alpha"',
                    'catalog_relative_path = "cache/catalog/unused.duckdb"',
                    "[quality]",
                    "gross_profit_identity_relative_tolerance = 0.01",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        market_root = self.runtime / "data" / "curated" / version
        market_root.mkdir(parents=True)
        sessions = pd.to_datetime(["2020-01-02", "2020-06-30", "2020-12-31"])
        pd.DataFrame({"session_date": sessions}).to_parquet(
            market_root / "calendar.parquet", index=False
        )
        pd.DataFrame(
            {
                "sid": ["sec::A"],
                "effective_from": pd.to_datetime(["2020-01-01"]),
                "effective_to": pd.to_datetime([None]),
            }
        ).to_parquet(market_root / "membership.parquet", index=False)
        manifest_dir = self.runtime / "data" / "manifests"
        manifest_dir.mkdir(parents=True)
        market_files = []
        for path in sorted(market_root.iterdir()):
            market_files.append(
                {
                    "path": f"curated/{version}/{path.name}",
                    "sha256": _sha(path),
                    "size_bytes": path.stat().st_size,
                }
            )
        market_manifest = manifest_dir / f"{version}.json"
        _write_json(
            market_manifest,
            {"dataset_version": version, "files": market_files},
        )
        quality_dir = self.runtime / "data" / "quality" / version
        quality_dir.mkdir(parents=True)
        quality_anchors: dict[str, object] = {"all_gates_passed": True}
        for stem in ("gate_results", "build_provenance", "test_results"):
            path = quality_dir / f"{stem}.json"
            _write_json(path, {"status": "pass", "name": stem})
            quality_anchors[f"{stem}_path"] = path.relative_to(self.runtime).as_posix()
            quality_anchors[f"{stem}_sha256"] = _sha(path)
        _write_json(
            market_root / "FROZEN.json",
            {
                "dataset_version": version,
                "freeze_status": "frozen_for_free_research",
                "formal_eligible": False,
                "manifest": {
                    "path": market_manifest.relative_to(self.runtime).as_posix(),
                    "sha256": _sha(market_manifest),
                    "file_records": len(market_files),
                },
                "quality": quality_anchors,
            },
        )

        raw_root = (
            self.runtime
            / "data"
            / "raw"
            / "cross_sectional_alpha"
            / fundamental_version
        )
        identifier_root = raw_root / "identifiers"
        identifier_root.mkdir(parents=True)
        bridge = pd.DataFrame(
            {
                "sid": ["sec::A"],
                "cik10": ["0000000001"],
                "effective_from": pd.to_datetime(["2020-01-01"]),
                "effective_to": pd.to_datetime([None]),
            }
        )
        intervals = bridge.copy()
        bridge.to_parquet(identifier_root / "entity_bridge.parquet", index=False)
        intervals.to_parquet(
            identifier_root / "entity_cik_intervals.parquet", index=False
        )
        mapping_qa = {
            "coverage_gate_passed": True,
            "security_count": 1,
            "mapped_security_count": 1,
            "entity_cik_interval_count": 1,
            "market_dataset": version,
            "minimum_member_session_coverage": 0.98,
            "member_session_coverage": {
                "member_sessions": 3,
                "mapped_member_sessions": 3,
                "unmapped_member_sessions": 0,
                "coverage": 1.0,
            },
        }
        _write_json(identifier_root / "mapping_qa.json", mapping_qa)
        _write_json(
            identifier_root / "manifest.json",
            _directory_manifest(
                identifier_root,
                "cross_sectional_alpha.identifier_manifest.v1",
                entity_bridge_version="bridge-fixture-v1",
            ),
        )

        sec_root = raw_root / "sec"
        body = b'{"fixture":true}'
        body_sha = hashlib.sha256(body).hexdigest()
        raw_path = sec_root / "raw" / "sha256" / body_sha[:2] / f"{body_sha}.bin"
        raw_path.parent.mkdir(parents=True)
        raw_path.write_bytes(body)
        url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json"
        record_id = hashlib.sha256(f"{url}\n200\n{body_sha}".encode()).hexdigest()
        ledger_record = {
            "record_id": record_id,
            "requested_url": url,
            "response_url": url,
            "status": 200,
            "sha256": body_sha,
            "size_bytes": len(body),
            "raw_path": raw_path.relative_to(sec_root).as_posix(),
            "retrieved_at_utc": "2026-08-20T00:00:00+00:00",
            "response_headers": {"content-type": "application/json"},
        }
        sec_root.mkdir(parents=True, exist_ok=True)
        (sec_root / "fetch_ledger.jsonl").write_text(
            json.dumps(ledger_record, sort_keys=True) + "\n", encoding="utf-8"
        )

        curated = (
            self.runtime
            / "data"
            / "curated"
            / "cross_sectional_alpha"
            / fundamental_version
        )
        curated.mkdir(parents=True)
        filings = pd.DataFrame(
            {
                "cik": ["0000000001"],
                "form": ["10-K"],
                "filed_date": pd.to_datetime(["2020-06-30"]),
                "accepted_at": pd.to_datetime(["2020-06-30 12:00:00"], utc=True),
                "accession": ["0000000001-20-000001"],
            }
        )
        canonical = pd.DataFrame(
            {
                "cik": ["0000000001"] * 3,
                "accession": ["0000000001-20-000001"] * 3,
                "period_end": pd.to_datetime(["2019-12-31"] * 3),
                "metric_id": ["revenue", "cost_of_goods_sold", "gross_profit"],
                "value": [100.0, 60.0, 40.0],
                "unit": ["USD"] * 3,
                "tag": ["Revenue", "CostOfRevenue", "GrossProfit"],
            }
        )
        registered = canonical.copy()
        coverage = pd.DataFrame({"cik": ["0000000001"], "check_id": ["fixture"]})
        source = pd.DataFrame(
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
                "periodic_form_count": pd.Series([pd.NA], dtype="Int64"),
                "periodic_form_bases": ["10-K|10-Q|20-F|40-F"],
                "periodic_form_amendments_included": [True],
                "companyfacts_http_status": [200],
                "companyfacts_error_code": pd.Series([pd.NA], dtype="string"),
                "companyfacts_object_key": pd.Series([pd.NA], dtype="string"),
                "companyfacts_raw_record_id": [record_id],
                "companyfacts_raw_sha256": [body_sha],
                "companyfacts_raw_size_bytes": [len(body)],
                "exception_review_status": pd.Series([pd.NA], dtype="string"),
                "exception_reviewed_date": pd.Series([pd.NA], dtype="string"),
            }
        )
        accounting_qa, accounting_summary = build_accounting_identity_qa(canonical)
        temporal_qa, temporal_summary = build_entity_temporal_support_qa(
            filings,
            intervals,
            history_start="2020-01-01",
            evaluation_end="2020-12-31",
            source_applicability=source.rename(
                columns={"status": "source_applicability_status"}
            ),
            minimum_long_interval_days=365,
        )
        tables = {
            "filings.parquet": filings,
            "registered_facts.parquet": registered,
            "canonical_annual_facts.parquet": canonical,
            "coverage_qa.parquet": coverage,
            "source_applicability.parquet": source,
            "fetch_failures.parquet": pd.DataFrame(
                columns=["cik10", "failure_type", "failure_message"]
            ),
            "entity_bridge.parquet": bridge,
            "entity_cik_intervals.parquet": intervals,
            "accounting_identity_qa.parquet": accounting_qa,
            "entity_temporal_support_qa.parquet": temporal_qa,
        }
        for name, frame in tables.items():
            frame.to_parquet(curated / name, index=False)
        _write_json(curated / "accounting_identity_summary.json", accounting_summary)
        _write_json(curated / "entity_temporal_support_summary.json", temporal_summary)

        signature = "a" * 64
        by_cik = curated / "by_cik" / "0000000001"
        by_cik.mkdir(parents=True)
        for name, frame in {
            "filings.parquet": filings,
            "registered_facts.parquet": registered,
            "canonical_annual_facts.parquet": canonical,
            "coverage_qa.parquet": coverage,
        }.items():
            frame.to_parquet(by_cik / name, index=False)
        source_payload = {
            key: (None if pd.isna(value) else value.item() if hasattr(value, "item") else value)
            for key, value in source.iloc[0].to_dict().items()
        }
        _write_json(by_cik / "source_applicability.json", source_payload)
        raw_anchor = {
            key: ledger_record[key]
            for key in (
                "record_id",
                "requested_url",
                "response_url",
                "status",
                "sha256",
                "size_bytes",
            )
        }
        cik_manifest = _directory_manifest(
            by_cik,
            "cross_sectional_alpha.fundamental_cik_manifest.v1",
            cik10="0000000001",
            fundamental_build_signature=signature,
            raw_records=[raw_anchor],
            source_applicability=source_payload,
        )
        _write_json(by_cik / "manifest.json", cik_manifest)
        _write_json(
            curated / "cik_manifest_index.json",
            {
                "schema_version": "cross_sectional_alpha.fundamental_cik_manifest_index.v1",
                "fundamental_build_signature": signature,
                "cik_count": 1,
                "ciks": [
                    {
                        "cik10": "0000000001",
                        "content_sha256": cik_manifest["content_sha256"],
                        "fundamental_build_signature": signature,
                        "source_applicability": "available",
                    }
                ],
            },
        )
        summary = {
            "requested_cik_count": 1,
            "completed_cik_count": 1,
            "failed_cik_count": 0,
            "limited_smoke_build": False,
            "fundamental_quality_gate_passed": True,
            "fundamental_build_signature": signature,
            "filing_rows": len(filings),
            "registered_fact_rows": len(registered),
            "canonical_fact_rows": len(canonical),
            "source_applicability_cik_count": 1,
            "source_applicability_counts": {"available": 1},
            "resolved_not_applicable_cik_count": 0,
            "resolved_not_applicable_ciks": [],
            "not_applicable_imputed_fact_rows": 0,
            "accounting_identity": accounting_summary,
            "entity_temporal_support": temporal_summary,
        }
        _write_json(curated / "build_summary.json", summary)
        fundamental_manifest = _directory_manifest(
            curated,
            "cross_sectional_alpha.fundamental_manifest.v1",
            fundamental_version=fundamental_version,
            market_dataset=version,
            fundamental_build_signature=signature,
        )
        _write_json(curated / "manifest.json", fundamental_manifest)
        _write_json(
            curated / "FROZEN.json",
            {
                "status": "frozen_complete",
                "formal_eligible": False,
                "fundamental_build_signature": signature,
                "manifest_sha256": _sha(curated / "manifest.json"),
                "content_sha256": fundamental_manifest["content_sha256"],
                "build_summary": summary,
            },
        )
        return (
            DatabaseLayout.load(
                project_root=self.project,
                runtime_root=self.runtime,
                program_path=program,
            ),
            raw_path,
        )

    def test_certifies_without_factor_stage_and_publishes_identically(self) -> None:
        certificate_path = self.runtime / "certification.json"
        certificate = certify_cross_sectional_source_data(
            self.layout, certification_path=certificate_path
        )

        self.assertEqual(certificate["status"], "source_data_certified")
        self.assertFalse(certificate["factor_stage_required"])
        self.assertEqual(
            certificate["quality_gates"]["aggregate_fetch_failure_rows"], 0
        )
        self.assertTrue(
            certificate["quality_gates"]["accounting_identity"][
                "recomputed_value_equal"
            ]
        )
        self.assertTrue(
            certificate["quality_gates"]["entity_temporal_support"][
                "recomputed_value_equal"
            ]
        )
        encoded = certificate_path.read_text(encoding="utf-8")
        self.assertNotIn(str(self.base), encoded)

        allowed = self.project / "results" / "published"
        destination = allowed / "source-fixture-v1"
        first = publish_source_data_certification(
            self.layout,
            certificate,
            destination=destination,
            allowed_publish_root=allowed,
        )
        second = publish_source_data_certification(
            self.layout,
            certificate,
            destination=destination,
            allowed_publish_root=allowed,
        )
        self.assertEqual(first["status"], "published")
        self.assertEqual(second["status"], "already_published_identical")
        self.assertFalse(any(destination.rglob("*.parquet")))
        self.assertFalse(any(destination.rglob("*.jsonl")))

    def test_current_fundamental_build_signature_drift_is_rejected(self) -> None:
        self.signature_mock.return_value = "b" * 64
        with self.assertRaisesRegex(
            SourceDataCertificationError,
            "FROZEN/manifest/build_summary anchors differ",
        ):
            certify_cross_sectional_source_data(self.layout)

    def test_raw_drift_and_publication_drift_are_rejected(self) -> None:
        certificate = certify_cross_sectional_source_data(self.layout)
        allowed = self.project / "results" / "published"
        destination = allowed / "source-fixture-v1"
        publish_source_data_certification(
            self.layout,
            certificate,
            destination=destination,
            allowed_publish_root=allowed,
        )
        (destination / "README.md").write_text("drift\n", encoding="utf-8")
        with self.assertRaisesRegex(
            SourceDataCertificationError, "differs; refusing overwrite"
        ):
            publish_source_data_certification(
                self.layout,
                certificate,
                destination=destination,
                allowed_publish_root=allowed,
            )

        self.raw_path.write_bytes(b"tampered")
        with self.assertRaisesRegex(
            SourceDataCertificationError, "raw object hash/size mismatch"
        ):
            certify_cross_sectional_source_data(self.layout)

    def test_extra_and_missing_by_cik_directories_are_rejected(self) -> None:
        by_cik_root = self.layout.curated_root / "by_cik"
        extra = by_cik_root / "9999999999"
        extra.mkdir()
        with self.assertRaisesRegex(
            SourceDataCertificationError,
            "by-CIK directory set differs from CIK manifest index",
        ):
            certify_cross_sectional_source_data(self.layout)

        extra.rmdir()
        expected = by_cik_root / "0000000001"
        backup = self.base / "missing-cik-backup"
        expected.rename(backup)
        with self.assertRaisesRegex(
            SourceDataCertificationError,
            "by-CIK directory set differs from CIK manifest index",
        ):
            certify_cross_sectional_source_data(self.layout)

    def test_temporal_qa_is_independently_recomputed_after_valid_reanchor(self) -> None:
        path = self.layout.curated_root / "entity_temporal_support_qa.parquet"
        frame = pd.read_parquet(path)
        frame.loc[:, "issuer_periodic_filing_count"] = 0
        frame.to_parquet(path, index=False)

        old_manifest = json.loads(
            (self.layout.curated_root / "manifest.json").read_text(encoding="utf-8")
        )
        refreshed = _directory_manifest(
            self.layout.curated_root,
            "cross_sectional_alpha.fundamental_manifest.v1",
            fundamental_version=old_manifest["fundamental_version"],
            market_dataset=old_manifest["market_dataset"],
            fundamental_build_signature=old_manifest["fundamental_build_signature"],
        )
        _write_json(self.layout.curated_root / "manifest.json", refreshed)
        freeze_path = self.layout.curated_root / "FROZEN.json"
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        freeze["manifest_sha256"] = _sha(self.layout.curated_root / "manifest.json")
        freeze["content_sha256"] = refreshed["content_sha256"]
        _write_json(freeze_path, freeze)

        with self.assertRaisesRegex(
            SourceDataCertificationError,
            "entity temporal-support QA differs value-for-value",
        ):
            certify_cross_sectional_source_data(self.layout)


if __name__ == "__main__":
    unittest.main()
