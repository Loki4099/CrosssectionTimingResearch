from __future__ import annotations

import json
import gzip
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import pandas as pd

from momentum_reversal.data.sec_edgar import (
    FetchRecord,
    ImmutableFetchStore,
    SECClient,
    SECResponse,
)
from momentum_reversal.data.fundamental_store import load_sec_metric_registry
from momentum_reversal.data.sec_fundamental_pipeline import (
    build_sec_fundamental_tables,
)
from momentum_reversal.pipelines.cross_sectional_database import (
    CrossSectionalDatabaseError,
    DatabaseLayout,
    _browse_ticker_url,
    _cached_sec_json,
    _cached_sec_text,
    _companyfacts_not_applicable_resolution,
    _load_fundamental_freeze_fail_closed,
    _source_applicability_qa,
    _write_cik_bundle,
    build_accounting_identity_qa,
    build_market_volume_qa,
    evaluate_factor_readiness,
    load_sec_companyfacts_exceptions,
)


class CrossSectionalDatabaseTest(unittest.TestCase):
    @staticmethod
    def _write_layout_program(
        path: Path,
        *,
        market_version: str = "market-v1",
        raw_relative_path: str = "data/raw/cross_sectional_alpha",
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    "[versions]",
                    f'market_dataset = "{market_version}"',
                    'entity_bridge = "bridge-v1"',
                    'fundamentals = "fundamentals-v1"',
                    'factor_build = "factors-v1"',
                    'data_bundle = "bundle-v1"',
                    "[storage]",
                    f'raw_relative_path = "{raw_relative_path}"',
                    'curated_relative_path = "data/curated/cross_sectional_alpha"',
                    'derived_relative_path = "data/derived/cross_sectional_alpha"',
                    'catalog_relative_path = "cache/catalog/research.duckdb"',
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def test_database_layout_rejects_runtime_escape_and_unsafe_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            runtime = base / "runtime"
            market = runtime / "data" / "curated" / "market-v1"
            market.mkdir(parents=True)
            program = project / "data_program.toml"

            self._write_layout_program(program, raw_relative_path="../escape")
            with self.assertRaisesRegex(
                CrossSectionalDatabaseError, "escapes the configured runtime root"
            ):
                DatabaseLayout.load(
                    project_root=project,
                    runtime_root=runtime,
                    program_path=program,
                )

            self._write_layout_program(program, market_version="../market-v1")
            with self.assertRaisesRegex(
                CrossSectionalDatabaseError, "one safe path token"
            ):
                DatabaseLayout.load(
                    project_root=project,
                    runtime_root=runtime,
                    program_path=program,
                )

    def test_formal_fundamental_freeze_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            curated = Path(temporary)
            (curated / "FROZEN.json").write_text(
                json.dumps({"status": "frozen_complete"}), encoding="utf-8"
            )
            layout = SimpleNamespace(curated_root=curated)
            with self.assertRaisesRegex(
                CrossSectionalDatabaseError, "requiring a new fundamentals version"
            ):
                _load_fundamental_freeze_fail_closed(layout, "a" * 64)

    def test_incomplete_fundamental_freeze_remains_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            curated = Path(temporary)
            (curated / "FROZEN.json").write_text(
                json.dumps({"status": "incomplete_or_quality_gate_failed"}),
                encoding="utf-8",
            )
            layout = SimpleNamespace(curated_root=curated)
            self.assertIsNone(
                _load_fundamental_freeze_fail_closed(layout, "a" * 64)
            )

    @staticmethod
    def _submissions(
        cik10: str, *, form: str = "SC 13G"
    ) -> dict[str, object]:
        return {
            "cik": cik10,
            "name": "Synthetic issuer",
            "filings": {
                "recent": {
                    "accessionNumber": [
                        f"{int(cik10):010d}-23-000001"
                    ],
                    "form": [form],
                    "filingDate": ["2023-01-03"],
                    "reportDate": ["2022-12-31"],
                    "acceptanceDateTime": ["2023-01-03T20:00:00Z"],
                    "primaryDocument": ["filing.htm"],
                },
                "files": [],
            },
        }

    @staticmethod
    def _no_such_key_record(
        store: ImmutableFetchStore,
        cik10: str,
        *,
        object_cik10: str | None = None,
        content_type: str = "application/xml",
    ) -> tuple[str, FetchRecord]:
        url = (
            "https://data.sec.gov/api/xbrl/companyfacts/"
            f"CIK{cik10}.json"
        )
        key_cik = object_cik10 or cik10
        body = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<Error><Code>NoSuchKey</Code>"
            "<Message>The specified key does not exist.</Message>"
            f"<Key>api/xbrl/companyfacts/CIK{key_cik}.json</Key>"
            "</Error>"
        ).encode("utf-8")
        record = store.record(
            requested_url=url,
            response=SECResponse(
                status=404,
                url=url,
                headers={"Content-Type": content_type},
                body=body,
            ),
            retrieved_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )
        return url, record

    @staticmethod
    def _exception_registry() -> dict[str, dict[str, object]]:
        path = (
            Path(__file__).resolve().parents[1]
            / "config/research/cross_sectional_alpha/"
            "sec_companyfacts_exceptions.csv"
        )
        return load_sec_companyfacts_exceptions(path)

    def test_companyfacts_exception_registry_is_closed_and_exact(self) -> None:
        registry = self._exception_registry()
        self.assertEqual(set(registry), {"0001132979", "0001288784"})
        self.assertEqual(registry["0001132979"]["ticker"], "FRC")
        self.assertEqual(registry["0001288784"]["ticker"], "SBNY")

    def test_companyfacts_cached_no_such_key_resolves_with_zero_periodic_forms(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ImmutableFetchStore(Path(temporary))
            url, record = self._no_such_key_record(store, "0001132979")
            resolved = _companyfacts_not_applicable_resolution(
                cik10="0001132979",
                facts_url=url,
                facts_record=record,
                submissions_root=self._submissions("0001132979"),
                submissions_history={},
                exceptions=self._exception_registry(),
            )
            self.assertEqual(resolved["status"], "resolved_not_applicable")
            self.assertTrue(resolved["explicit_missing"])
            self.assertEqual(resolved["periodic_form_count"], 0)
            self.assertEqual(resolved["imputed_fact_rows"], 0)
            self.assertEqual(resolved["companyfacts_raw_sha256"], record.sha256)

            project = Path(__file__).resolve().parents[1]
            result = build_sec_fundamental_tables(
                self._submissions("0001132979"),
                {},
                {"cik": "0001132979", "facts": {}},
                load_sec_metric_registry(
                    project
                    / "config/research/cross_sectional_alpha/"
                    "sec_metric_registry.csv"
                ),
                pd.DataFrame(
                    {
                        "close": pd.to_datetime(
                            ["2023-01-03T21:00:00Z"], utc=True
                        )
                    },
                    index=pd.to_datetime(["2023-01-03"]),
                ),
            )
            self.assertEqual(len(result.filings), 1)
            self.assertTrue(result.facts.empty)
            self.assertTrue(result.canonical.empty)

            coverage = _source_applicability_qa(
                pd.DataFrame(
                    [
                        {
                            "check_id": "fact_signal_date_coverage",
                            "group": "facts",
                            "numerator": 0,
                            "denominator": 0,
                            "coverage": float("nan"),
                            "status": "review",
                        }
                    ]
                ),
                resolved,
            )
            destination = Path(temporary) / "bundle"
            _write_cik_bundle(
                destination,
                cik10="0001132979",
                filings=pd.DataFrame(),
                registered_facts=pd.DataFrame(),
                canonical=pd.DataFrame(),
                coverage_qa=coverage,
                raw_records=[record],
                build_signature="test-signature",
                source_applicability=resolved,
            )
            manifest = json.loads(
                (destination / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["source_applicability"]["status"],
                "resolved_not_applicable",
            )
            self.assertEqual(manifest["raw_records"][0]["status"], 404)
            self.assertEqual(
                manifest["raw_records"][0]["sha256"], record.sha256
            )

    def test_companyfacts_exception_fail_closed_matrix(self) -> None:
        exceptions = self._exception_registry()
        cases = (
            (
                "unreviewed_cik",
                "0000000001",
                "0000000001",
                "SC 13G",
                None,
                "not in the reviewed exception",
            ),
            (
                "submissions_cik_mismatch",
                "0001132979",
                "0001288784",
                "SC 13G",
                None,
                "CIK mismatch",
            ),
            (
                "periodic_amendment_present",
                "0001132979",
                "0001132979",
                "10-K/A",
                None,
                "periodic-form condition failed",
            ),
            (
                "wrong_s3_object_key",
                "0001132979",
                "0001132979",
                "SC 13G",
                "0001288784",
                "not the expected NoSuchKey",
            ),
        )
        for (
            label,
            cik10,
            submissions_cik,
            form,
            object_cik,
            message,
        ) in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                store = ImmutableFetchStore(Path(temporary))
                url, record = self._no_such_key_record(
                    store, cik10, object_cik10=object_cik
                )
                with self.assertRaisesRegex(CrossSectionalDatabaseError, message):
                    _companyfacts_not_applicable_resolution(
                        cik10=cik10,
                        facts_url=url,
                        facts_record=record,
                        submissions_root=self._submissions(
                            submissions_cik, form=form
                        ),
                        submissions_history={},
                        exceptions=exceptions,
                    )

    def test_companyfacts_exception_rejects_tampered_raw_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ImmutableFetchStore(Path(temporary))
            url, record = self._no_such_key_record(store, "0001288784")
            record.raw_path.write_bytes(b"tampered")
            with self.assertRaisesRegex(
                CrossSectionalDatabaseError, "raw evidence hash mismatch"
            ):
                _companyfacts_not_applicable_resolution(
                    cik10="0001288784",
                    facts_url=url,
                    facts_record=record,
                    submissions_root=self._submissions("0001288784"),
                    submissions_history={},
                    exceptions=self._exception_registry(),
                )

    def test_accounting_identity_distinguishes_direct_and_synthetic_cogs(self) -> None:
        rows = []
        for accession, cogs_tag, cogs in (
            ("a", "CostOfRevenue", 70.0),
            ("b", "synthetic_revenue_minus_GrossProfit", 75.0),
            ("c", "CostOfRevenue", 60.0),
        ):
            for metric, value, tag in (
                ("revenue", 100.0, "Revenues"),
                ("cost_of_goods_sold", cogs, cogs_tag),
                ("gross_profit", 30.0 if accession != "b" else 25.0, "GrossProfit"),
            ):
                rows.append(
                    {
                        "cik": "0000000001",
                        "accession": accession,
                        "period_end": "2024-12-31",
                        "metric_id": metric,
                        "value": value,
                        "unit": "USD",
                        "tag": tag,
                    }
                )
        qa, summary = build_accounting_identity_qa(pd.DataFrame(rows))
        statuses = dict(zip(qa["accession"], qa["status"], strict=True))
        self.assertEqual(statuses["a"], "pass")
        self.assertEqual(statuses["b"], "synthetic_identity")
        self.assertEqual(statuses["c"], "fail_scope_mismatch")
        self.assertFalse(summary["identity_gate_passed"])
        self.assertEqual(summary["direct_failure_count"], 1)

    def test_factor_readiness_uses_data_only_gate_and_mechanical_alternative(self) -> None:
        rows = []
        for date in pd.to_datetime(["2020-01-31", "2020-02-28"]):
            for sid in ("A", "B", "C"):
                rows.extend(
                    [
                        {"signal_date": date, "sid": sid, "factor_id": "F1", "eligible": False},
                        {"signal_date": date, "sid": sid, "factor_id": "F2", "eligible": True},
                        {"signal_date": date, "sid": sid, "factor_id": "F3", "eligible": sid != "C"},
                        {"signal_date": date, "sid": sid, "factor_id": "F4", "eligible": True},
                    ]
                )
        registry = pd.DataFrame(
            {
                "factor_id": ["F1", "F2", "F3", "F4"],
                "first_round_eligible": ["true", "false", "true", "false"],
                "data_family": ["fundamental", "fundamental", "market", "market"],
            }
        )
        result = evaluate_factor_readiness(
            pd.DataFrame(rows),
            registry,
            evaluation_start="2020-01-01",
            evaluation_end="2020-12-31",
            member_coverage_minimum=0.5,
            minimum_covered_signal_months=2,
            minimum_eligible_names=2,
            coverage_alternative={"F1": "F2"},
        ).set_index("factor_id")
        self.assertEqual(
            result.loc["F1", "selection_status"],
            "ready_coverage_alternative",
        )
        self.assertEqual(result.loc["F1", "selected_factor_id"], "F2")
        self.assertEqual(result.loc["F3", "selection_status"], "ready_first_round")
        self.assertEqual(
            result.loc["F4", "selection_status"],
            "available_expanded_not_first_round",
        )
        self.assertFalse(result["performance_used"].any())

    def test_market_volume_qa_uses_half_open_pit_membership(self) -> None:
        dates = pd.date_range("2020-01-02", periods=4, freq="B")
        prices = pd.DataFrame(
            {
                "date": list(dates) * 2,
                "sid": ["A"] * 4 + ["B"] * 4,
                "raw_close": [10.0] * 8,
                "tr_close": [10.0] * 8,
                "volume": [100.0] * 4 + [100.0, 100.0, -1.0, 100.0],
                "stock_splits": [0.0] * 8,
            }
        )
        membership = pd.DataFrame(
            {
                "sid": ["A", "B"],
                "effective_from": [dates[0], dates[0]],
                "effective_to": [pd.NaT, dates[2]],
            }
        )
        qa = build_market_volume_qa(
            prices,
            membership,
            history_start=dates[0],
            evaluation_end=dates[-1],
            minimum_coverage=1.0,
        )
        self.assertEqual(qa["member_session_rows"], 6)
        self.assertEqual(qa["negative_volume_rows"], 0)
        self.assertEqual(
            qa["gate_denominator"],
            "member_sessions_with_valid_raw_and_total_return_close",
        )
        self.assertTrue(qa["volume_qa_passed"])

    def test_market_volume_qa_detects_missing_member_session_row(self) -> None:
        dates = pd.date_range("2020-01-02", periods=3, freq="B")
        prices = pd.DataFrame(
            {
                "date": dates[:2],
                "sid": ["A", "A"],
                "raw_close": [10.0, 10.0],
                "tr_close": [10.0, 10.0],
                "volume": [100.0, 100.0],
                "stock_splits": [0.0, 0.0],
            }
        )
        membership = pd.DataFrame(
            {
                "sid": ["A"],
                "effective_from": [dates[0]],
                "effective_to": [pd.NaT],
            }
        )
        calendar = pd.DataFrame({"session_date": dates})
        with self.assertRaisesRegex(ValueError, "omit one or more expected"):
            build_market_volume_qa(
                prices,
                membership,
                calendar=calendar,
                history_start=dates[0],
                evaluation_end=dates[-1],
            )

    def test_browse_url_is_deterministic_and_encoded(self) -> None:
        url = _browse_ticker_url(
            "https://www.sec.gov/cgi-bin/browse-edgar", "BRK-B"
        )
        self.assertEqual(
            url,
            "https://www.sec.gov/cgi-bin/browse-edgar?"
            "action=getcompany&CIK=BRK-B&owner=exclude&count=10&output=atom",
        )

    def test_cached_json_avoids_second_transport_call(self) -> None:
        calls: list[str] = []

        def transport(url: str, **_: object) -> SECResponse:
            calls.append(url)
            return SECResponse(
                status=200,
                url=url,
                headers={"Content-Type": "application/json"},
                body=b'{"ok":true}',
            )

        with tempfile.TemporaryDirectory() as temporary:
            store = ImmutableFetchStore(Path(temporary))
            client = SECClient(
                user_agent="Example Research contact@example.com",
                raw_store=store,
                transport=transport,
                sleeper=lambda _: None,
                clock=lambda: 0.0,
            )
            url = "https://data.sec.gov/submissions/CIK0000000001.json"
            first, _ = _cached_sec_json(client, store, url, refresh=False)
            second, _ = _cached_sec_json(client, store, url, refresh=False)
            self.assertEqual(first, {"ok": True})
            self.assertEqual(second, {"ok": True})
            self.assertEqual(calls, [url])

    def test_cached_text_can_be_forced_to_refresh(self) -> None:
        calls: list[str] = []

        def transport(url: str, **_: object) -> SECResponse:
            calls.append(url)
            return SECResponse(
                status=200,
                url=url,
                headers={"Content-Type": "application/atom+xml"},
                body=f"response-{len(calls)}".encode(),
            )

        with tempfile.TemporaryDirectory() as temporary:
            store = ImmutableFetchStore(Path(temporary))
            client = SECClient(
                user_agent="Example Research contact@example.com",
                raw_store=store,
                transport=transport,
                sleeper=lambda _: None,
                clock=lambda: 0.0,
            )
            url = "https://www.sec.gov/example"
            first, _ = _cached_sec_text(client, store, url, refresh=False)
            second, _ = _cached_sec_text(client, store, url, refresh=True)
            self.assertEqual(first, "response-1")
            self.assertEqual(second, "response-2")
            self.assertEqual(calls, [url, url])

    def test_cached_response_preserves_content_encoding(self) -> None:
        calls: list[str] = []

        def transport(url: str, **_: object) -> SECResponse:
            calls.append(url)
            return SECResponse(
                status=200,
                url=url,
                headers={"Content-Encoding": "gzip"},
                body=gzip.compress(b'{"encoded":true}'),
            )

        with tempfile.TemporaryDirectory() as temporary:
            store = ImmutableFetchStore(Path(temporary))
            client = SECClient(
                user_agent="Example Research contact@example.com",
                raw_store=store,
                transport=transport,
                sleeper=lambda _: None,
                clock=lambda: 0.0,
            )
            url = "https://data.sec.gov/example.json"
            _cached_sec_json(client, store, url, refresh=False)
            cached, _ = _cached_sec_json(client, store, url, refresh=False)
            self.assertEqual(cached, {"encoded": True})
            self.assertEqual(calls, [url])

    def test_cached_sec_failure_is_not_retried_without_refresh(self) -> None:
        calls: list[str] = []

        def transport(url: str, **_: object) -> SECResponse:
            calls.append(url)
            return SECResponse(status=404, url=url, headers={}, body=b"missing")

        with tempfile.TemporaryDirectory() as temporary:
            store = ImmutableFetchStore(Path(temporary))
            client = SECClient(
                user_agent="Example Research contact@example.com",
                raw_store=store,
                transport=transport,
                sleeper=lambda _: None,
                clock=lambda: 0.0,
            )
            url = "https://data.sec.gov/missing"
            with self.assertRaises(Exception):
                _cached_sec_text(client, store, url, refresh=False)
            with self.assertRaisesRegex(Exception, "cached SEC request failed"):
                _cached_sec_text(client, store, url, refresh=False)
            self.assertEqual(calls, [url])


if __name__ == "__main__":
    unittest.main()
