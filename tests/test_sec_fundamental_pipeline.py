from __future__ import annotations

import copy
import json
import unittest

import pandas as pd

from momentum_reversal.data.sec_edgar import SECParseError
from momentum_reversal.data.sec_fundamental_pipeline import (
    build_sec_fundamental_tables,
    filter_companyfacts_to_metric_registry,
    map_accepted_at_to_signal_date,
    map_accepted_at_to_signal_dates,
)


CIK = "0000320193"
ORIGINAL = "0000320193-24-000001"
AMENDMENT = "0000320193-24-000002"
FUTURE = "0000320193-25-000001"


def _schedule() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": pd.to_datetime(
                [
                    "2024-03-15T13:30:00Z",
                    "2024-03-18T13:30:00Z",
                    "2024-03-19T13:30:00Z",
                    "2025-03-14T13:30:00Z",
                    "2025-03-17T13:30:00Z",
                ],
                utc=True,
            ),
            "close": pd.to_datetime(
                [
                    "2024-03-15T20:00:00Z",
                    "2024-03-18T20:00:00Z",
                    "2024-03-19T20:00:00Z",
                    "2025-03-14T20:00:00Z",
                    "2025-03-17T20:00:00Z",
                ],
                utc=True,
            ),
        },
        index=pd.to_datetime(
            ["2024-03-15", "2024-03-18", "2024-03-19", "2025-03-14", "2025-03-17"]
        ),
    )


def _submission_columns(rows: list[dict[str, object]]) -> dict[str, list[object]]:
    fields = (
        "accessionNumber",
        "form",
        "filingDate",
        "reportDate",
        "acceptanceDateTime",
        "primaryDocument",
    )
    return {field: [row[field] for row in rows] for field in fields}


def _filing(
    accession: str,
    form: str,
    accepted_at: str,
    filed: str,
    report_date: str,
) -> dict[str, object]:
    return {
        "accessionNumber": accession,
        "form": form,
        "filingDate": filed,
        "reportDate": report_date,
        "acceptanceDateTime": accepted_at,
        "primaryDocument": f"{accession}.htm",
    }


def _payloads(*, include_future: bool = False) -> tuple[dict[str, object], dict[str, object]]:
    original = _filing(
        ORIGINAL,
        "10-K",
        "2024-03-15T19:00:00Z",
        "2024-03-15",
        "2023-12-31",
    )
    amendment = _filing(
        AMENDMENT,
        "10-K/A",
        "2024-03-16T15:00:00Z",
        "2024-03-16",
        "2023-12-31",
    )
    recent = [amendment]
    if include_future:
        recent.append(
            _filing(
                FUTURE,
                "10-K",
                "2025-03-14T21:00:00Z",
                "2025-03-14",
                "2024-12-31",
            )
        )
    root = {
        "cik": 320193,
        "sic": "3571",
        "filings": {
            "recent": _submission_columns(recent),
            "files": [{"name": "CIK0000320193-submissions-001.json"}],
        },
    }
    history = _submission_columns([original])
    return root, history


def _observation(
    accession: str,
    form: str,
    filed: str,
    start: str | None,
    end: str,
    value: float,
) -> dict[str, object]:
    row: dict[str, object] = {
        "end": end,
        "val": value,
        "accn": accession,
        "fy": int(end[:4]),
        "fp": "FY",
        "form": form,
        "filed": filed,
    }
    if start is not None:
        row["start"] = start
    return row


def _companyfacts(*, include_future: bool = False) -> dict[str, object]:
    assets = [
        _observation(ORIGINAL, "10-K", "2024-03-15", None, "2023-12-31", 100.0),
        _observation(AMENDMENT, "10-K/A", "2024-03-16", None, "2023-12-31", 110.0),
    ]
    revenue = [
        _observation(
            ORIGINAL,
            "10-K",
            "2024-03-15",
            "2023-01-01",
            "2023-12-31",
            50.0,
        )
    ]
    if include_future:
        assets.append(
            _observation(FUTURE, "10-K", "2025-03-14", None, "2024-12-31", 9999.0)
        )
        revenue.append(
            _observation(
                FUTURE,
                "10-K",
                "2025-03-14",
                "2024-01-01",
                "2024-12-31",
                9999.0,
            )
        )
    return {
        "cik": 320193,
        "entityName": "Synthetic",
        "facts": {
            "us-gaap": {
                "Assets": {
                    "label": "Assets",
                    "description": "Assets",
                    "units": {"USD": assets},
                },
                "Revenues": {
                    "label": "Revenue",
                    "description": "Revenue",
                    "units": {"USD": revenue},
                },
            }
        },
    }


def _registry() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "metric_id": "total_assets",
                "taxonomy": "us-gaap",
                "tag_priority": "Assets",
                "period_type": "instant",
                "unit_family": "USD",
            },
            {
                "metric_id": "revenue",
                "taxonomy": "us-gaap",
                "tag_priority": "Revenues",
                "period_type": "duration_fy",
                "unit_family": "USD",
            },
        ]
    )


def _build(*, include_future: bool = False):
    root, history = _payloads(include_future=include_future)
    return build_sec_fundamental_tables(
        root,
        {"CIK0000320193-submissions-001.json": history},
        _companyfacts(include_future=include_future),
        _registry(),
        _schedule(),
    )


class SECSignalDateTests(unittest.TestCase):
    def test_buffer_boundary_after_close_and_weekend_use_actual_sessions(self) -> None:
        schedule = _schedule().iloc[:3]
        accepted = pd.Series(
            [
                "2024-03-15T19:50:00Z",  # ready exactly at Friday close
                "2024-03-15T19:50:00.001Z",  # ready after Friday close
                "2024-03-16T12:00:00Z",  # Saturday
            ],
            index=["boundary", "after", "weekend"],
        )
        result = map_accepted_at_to_signal_dates(accepted, schedule)
        self.assertEqual(result.loc["boundary"], pd.Timestamp("2024-03-15"))
        self.assertEqual(result.loc["after"], pd.Timestamp("2024-03-18"))
        self.assertEqual(result.loc["weekend"], pd.Timestamp("2024-03-18"))
        self.assertEqual(
            map_accepted_at_to_signal_date(
                "2024-03-15T19:50:00Z",
                schedule,
            ),
            pd.Timestamp("2024-03-15"),
        )

    def test_timezone_naive_acceptance_fails_closed(self) -> None:
        with self.assertRaisesRegex(SECParseError, "timezone-aware"):
            map_accepted_at_to_signal_date("2024-03-15 19:00:00", _schedule())


class SECFundamentalPipelineTests(unittest.TestCase):
    def test_registered_tag_filter_preserves_only_declared_xbrl_branches(self) -> None:
        payload = _companyfacts()
        payload["facts"]["us-gaap"]["UnregisteredConcept"] = {
            "label": "Unused",
            "description": "Unused",
            "units": {"USD": []},
        }
        filtered = filter_companyfacts_to_metric_registry(payload, _registry())
        concepts = set(filtered["facts"]["us-gaap"])
        self.assertEqual(concepts, {"Assets", "Revenues"})
        self.assertIn("UnregisteredConcept", payload["facts"]["us-gaap"])

    def test_root_history_companyfacts_and_amendment_remain_separate_vintages(self) -> None:
        result = _build()

        self.assertEqual(result.filings["accession"].tolist(), [ORIGINAL, AMENDMENT])
        self.assertEqual(
            result.filings["signal_date"].tolist(),
            [pd.Timestamp("2024-03-15"), pd.Timestamp("2024-03-18")],
        )
        self.assertTrue(result.filings["available_session"].equals(result.filings["signal_date"]))
        self.assertTrue(result.filings["sic"].isna().all())
        self.assertEqual(result.filings["sec_current_sic"].unique().tolist(), ["3571"])
        self.assertFalse(result.filings["sic_is_pit"].any())
        self.assertEqual(
            result.filings["sic_provenance"].unique().tolist(),
            ["unverified_no_pit_sic"],
        )
        self.assertEqual(result.facts["cik"].unique().tolist(), [CIK])
        self.assertTrue(result.facts["sic"].isna().all())
        self.assertEqual(result.facts["sec_current_sic"].unique().tolist(), ["3571"])
        self.assertTrue(
            {"tag", "period_start", "period_end", "signal_date"}.issubset(
                result.facts.columns
            )
        )

        assets = result.canonical.query("metric_id == 'total_assets'")
        self.assertEqual(assets["accession"].tolist(), [ORIGINAL, AMENDMENT])
        self.assertEqual(assets["value"].tolist(), [100.0, 110.0])
        self.assertEqual(
            assets["signal_date"].tolist(),
            [pd.Timestamp("2024-03-15"), pd.Timestamp("2024-03-18")],
        )
        self.assertTrue(assets["sic"].isna().all())
        self.assertFalse(assets["sic_is_pit"].any())
        self.assertEqual(
            assets["sic_provenance"].unique().tolist(),
            ["unverified_no_pit_sic"],
        )
        qa = result.coverage_qa.set_index("check_id")
        self.assertEqual(qa.loc["fact_filing_vintage_coverage", "coverage"], 1.0)
        self.assertEqual(qa.loc["fact_signal_date_coverage", "status"], "pass")
        self.assertIn(
            "canonicalize:gross_profit_identity_tested_contexts", qa.index
        )
        self.assertIn(
            "canonicalize:gross_profit_identity_mismatch_contexts", qa.index
        )

    def test_submissions_quarterly_form_cannot_be_promoted_by_companyfacts(self) -> None:
        root, history = _payloads()
        history["form"][0] = "10-Q"
        result = build_sec_fundamental_tables(
            root,
            [history],
            _companyfacts(),
            _registry(),
            _schedule(),
        )
        original = result.facts.loc[result.facts["accession"].eq(ORIGINAL)]
        self.assertFalse(original.empty)
        self.assertTrue(original["fact_form"].eq("10-K").all())
        self.assertTrue(original["filing_form"].eq("10-Q").all())
        self.assertTrue(original["form"].eq("10-Q").all())
        self.assertTrue(original["form_mismatch"].all())
        self.assertFalse(result.canonical["accession"].eq(ORIGINAL).any())
        qa = result.coverage_qa.set_index("check_id")
        self.assertGreater(
            qa.loc["companyfacts_submissions_form_mismatches", "numerator"], 0
        )
        self.assertEqual(
            qa.loc["companyfacts_submissions_form_mismatches", "status"],
            "review",
        )

    def test_future_period_end_is_preserved_as_evidence_but_blocked_from_canonical(self) -> None:
        root, history = _payloads()
        facts = _companyfacts()
        facts["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]["end"] = (
            "2024-03-16"
        )
        # The amendment is accepted on Saturday and becomes available Monday.
        # A measurement dated Monday is not after available_session, but it is
        # still unknowable at Saturday acceptance and must remain blocked.
        facts["facts"]["us-gaap"]["Assets"]["units"]["USD"][1]["end"] = (
            "2024-03-18"
        )
        result = build_sec_fundamental_tables(
            root,
            [history],
            facts,
            _registry(),
            _schedule(),
        )
        future = result.facts.loc[
            result.facts["accession"].eq(ORIGINAL)
            & result.facts["concept"].eq("Assets")
        ].iloc[0]
        self.assertTrue(future["period_end_after_accepted_at"])
        self.assertTrue(future["period_end_after_available_session"])
        self.assertFalse(future["canonical_timing_eligible"])
        amendment_future = result.facts.loc[
            result.facts["accession"].eq(AMENDMENT)
            & result.facts["concept"].eq("Assets")
        ].iloc[0]
        self.assertTrue(amendment_future["period_end_after_accepted_at"])
        self.assertFalse(
            amendment_future["period_end_after_available_session"]
        )
        self.assertFalse(amendment_future["canonical_timing_eligible"])
        self.assertFalse(
            (
                result.canonical["accession"].eq(ORIGINAL)
                & result.canonical["metric_id"].eq("total_assets")
            ).any()
        )
        qa = result.coverage_qa.set_index("check_id")
        self.assertEqual(
            qa.loc["companyfacts_period_end_after_acceptance", "numerator"], 2
        )
        self.assertEqual(
            qa.loc["companyfacts_period_end_after_available_session", "numerator"],
            1,
        )
        self.assertEqual(
            qa.loc["canonical_timing_gate_excluded_rows", "numerator"], 2
        )

    def test_orphan_companyfact_accession_fails_closed(self) -> None:
        root, history = _payloads()
        facts = _companyfacts()
        orphan = copy.deepcopy(facts)
        orphan["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]["accn"] = (
            "0000320193-24-999999"
        )
        with self.assertRaisesRegex(SECParseError, "missing from filing ledger"):
            build_sec_fundamental_tables(
                root,
                [history],
                orphan,
                _registry(),
                _schedule(),
            )

    def test_exact_orphan_alias_is_dropped_and_recorded_in_qa(self) -> None:
        root, history = _payloads()
        facts = _companyfacts()
        known = facts["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]
        orphan = copy.deepcopy(known)
        orphan["accn"] = "0000320193-24-999999"
        orphan["fy"] = 1900
        orphan["fp"] = "Q4"
        orphan["frame"] = "CY1900Q4I"
        facts["facts"]["us-gaap"]["Assets"]["units"]["USD"].append(orphan)

        result = build_sec_fundamental_tables(
            root,
            [history],
            facts,
            _registry(),
            _schedule(),
        )

        self.assertNotIn("0000320193-24-999999", set(result.facts["accession"]))
        retained = result.facts.loc[
            result.facts["accession"].eq(ORIGINAL)
            & result.facts["concept"].eq("Assets")
        ].iloc[0]
        self.assertEqual(retained["accepted_at"], pd.Timestamp("2024-03-15T19:00:00Z"))
        qa = result.coverage_qa.set_index("check_id").loc[
            "companyfacts_orphan_duplicate_resolved_count"
        ]
        self.assertEqual(qa["orphan_duplicate_resolved_count"], 1)
        self.assertEqual(qa["numerator"], 1)
        self.assertEqual(qa["cik"], CIK)
        self.assertEqual(qa["status"], "review")
        aliases = json.loads(qa["accessions"])
        self.assertEqual(
            aliases,
            [
                {
                    "ledger_accepted_at": "2024-03-15T19:00:00+00:00",
                    "ledger_accession": ORIGINAL,
                    "orphan_accession": "0000320193-24-999999",
                    "resolved_observation_count": 1,
                }
            ],
        )

    def test_future_filing_cannot_change_past_vintages(self) -> None:
        baseline = _build(include_future=False)
        with_future = _build(include_future=True)
        cutoff = pd.Timestamp("2024-12-31", tz="UTC")

        for table_name in ("filings", "facts", "canonical"):
            expected = getattr(baseline, table_name)
            actual = getattr(with_future, table_name)
            expected_past = expected.loc[expected["accepted_at"] <= cutoff].reset_index(drop=True)
            actual_past = actual.loc[actual["accepted_at"] <= cutoff].reset_index(drop=True)
            pd.testing.assert_frame_equal(expected_past, actual_past)


if __name__ == "__main__":
    unittest.main()
