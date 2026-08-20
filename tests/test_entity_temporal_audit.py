from __future__ import annotations

import unittest

import pandas as pd

from momentum_reversal.data.entity_temporal_audit import (
    build_entity_temporal_support_qa,
)


class EntityTemporalAuditTests(unittest.TestCase):
    def test_long_current_cik_backfill_fails_without_periodic_support(self) -> None:
        intervals = pd.DataFrame(
            {
                "sid": ["sec::OLD"],
                "cik10": ["0000000002"],
                "effective_from": [pd.Timestamp("2013-01-01")],
                "effective_to": [pd.Timestamp("2019-01-01")],
            }
        )
        filings = pd.DataFrame(
            {
                "cik": ["0000000002"],
                "form": ["10-K"],
                "filed_date": [pd.Timestamp("2022-02-01")],
            }
        )
        qa, summary = build_entity_temporal_support_qa(
            filings,
            intervals,
            history_start="2013-01-02",
            evaluation_end="2026-06-30",
        )
        self.assertFalse(summary["temporal_support_gate_passed"])
        self.assertEqual(
            qa.iloc[0]["temporal_support_status"],
            "long_interval_without_periodic_support",
        )

    def test_contemporaneous_filing_passes(self) -> None:
        intervals = pd.DataFrame(
            {
                "sid": ["sec::LIVE"],
                "cik10": ["10"],
                "effective_from": [pd.Timestamp("2018-01-01")],
                "effective_to": [pd.NaT],
            }
        )
        filings = pd.DataFrame(
            {
                "cik": ["0000000010"],
                "form": ["10-Q"],
                "filed_date": [pd.Timestamp("2018-05-01")],
            }
        )
        _, summary = build_entity_temporal_support_qa(
            filings,
            intervals,
            history_start="2013-01-02",
            evaluation_end="2026-06-30",
        )
        self.assertTrue(summary["temporal_support_gate_passed"])

    def test_reviewed_official_absence_is_explicit_exception(self) -> None:
        intervals = pd.DataFrame(
            {
                "sid": ["sec::BANK"],
                "cik10": ["20"],
                "effective_from": [pd.Timestamp("2019-01-01")],
                "effective_to": [pd.Timestamp("2023-01-01")],
            }
        )
        filings = pd.DataFrame(columns=["cik", "form", "filed_date"])
        applicability = pd.DataFrame(
            {
                "cik10": ["0000000020"],
                "source_applicability_status": ["resolved_not_applicable"],
            }
        )
        qa, summary = build_entity_temporal_support_qa(
            filings,
            intervals,
            history_start="2013-01-02",
            evaluation_end="2026-06-30",
            source_applicability=applicability,
        )
        self.assertTrue(summary["temporal_support_gate_passed"])
        self.assertEqual(
            qa.iloc[0]["temporal_support_status"],
            "reviewed_source_not_applicable",
        )

    def test_short_new_episode_is_reported_but_not_rejected(self) -> None:
        intervals = pd.DataFrame(
            {
                "sid": ["sec::SPIN"],
                "cik10": ["30"],
                "effective_from": [pd.Timestamp("2026-06-01")],
                "effective_to": [pd.NaT],
            }
        )
        filings = pd.DataFrame(columns=["cik", "form", "filed_date"])
        qa, summary = build_entity_temporal_support_qa(
            filings,
            intervals,
            history_start="2013-01-02",
            evaluation_end="2026-06-30",
        )
        self.assertTrue(summary["temporal_support_gate_passed"])
        self.assertEqual(
            qa.iloc[0]["temporal_support_status"],
            "short_interval_no_periodic_required",
        )


if __name__ == "__main__":
    unittest.main()
