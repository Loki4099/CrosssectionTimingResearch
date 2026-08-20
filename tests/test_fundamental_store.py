from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from momentum_reversal.data.fundamental_store import (
    canonicalize_annual_facts,
    load_sec_metric_registry,
)


def _registry() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"metric_id": "total_assets", "taxonomy": "us-gaap", "tag_priority": "Assets", "period_type": "instant", "unit_family": "USD"},
            {"metric_id": "revenue", "taxonomy": "us-gaap", "tag_priority": "RevenueNew|RevenueOld", "period_type": "duration_fy", "unit_family": "USD"},
            {"metric_id": "cost_of_goods_sold", "taxonomy": "us-gaap", "tag_priority": "CostOfRevenue", "period_type": "duration_fy", "unit_family": "USD"},
            {"metric_id": "gross_profit", "taxonomy": "us-gaap", "tag_priority": "GrossProfit", "period_type": "duration_fy", "unit_family": "USD"},
        ]
    )


def _filings() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"cik": "1", "accession": "a1", "accepted_at": "2020-02-01T20:00:00Z", "available_session": "2020-02-03", "sic": 3571},
            {"cik": "1", "accession": "a2", "accepted_at": "2021-02-01T20:00:00Z", "available_session": "2021-02-02", "sic": 3571},
        ]
    )


class FundamentalStoreTests(unittest.TestCase):
    def test_project_tax_payable_registry_has_observed_sec_fallbacks(self) -> None:
        registry = load_sec_metric_registry(
            Path(__file__).resolve().parents[1]
            / "config"
            / "research"
            / "cross_sectional_alpha"
            / "sec_metric_registry.csv"
        ).set_index("metric_id")
        tags = registry.loc["taxes_payable", "tag_priority"].split("|")
        self.assertIn("IncomeTaxesPayable", tags)
        self.assertIn("AccruedIncomeTaxesPayable", tags)
        self.assertIn("TaxesPayableCurrent", tags)
        self.assertIn("TaxesPayableCurrentAndNoncurrent", tags)
        self.assertIn("TaxesPayable", tags)
        self.assertLess(tags.index("AccruedIncomeTaxesPayable"), tags.index("TaxesPayableCurrent"))

    def test_direct_cogs_prevents_duplicate_synthetic_cogs(self) -> None:
        filings = _filings()
        raw = pd.DataFrame(
            [
                {
                    "cik": "1",
                    "accession": "a1",
                    "taxonomy": "us-gaap",
                    "tag": tag,
                    "unit": "USD",
                    "value": value,
                    "period_start": "2019-01-01",
                    "period_end": "2019-12-31",
                    "form": "10-K",
                }
                for tag, value in (
                    ("RevenueNew", 100.0),
                    ("CostOfRevenue", 60.0),
                    ("GrossProfit", 40.0),
                )
            ]
        )
        result = canonicalize_annual_facts(raw, filings, _registry())
        cogs = result.facts.loc[
            (result.facts["accession"] == "a1")
            & (result.facts["metric_id"] == "cost_of_goods_sold")
        ]
        self.assertEqual(len(cogs), 1)
        self.assertNotIn("synthetic", cogs.iloc[0]["tag"])

    def test_later_comparative_is_a_new_vintage_not_an_overwrite(self) -> None:
        rows = []
        for accession, value in (("a1", 100.0), ("a2", 110.0)):
            rows.append(
                {"cik": "1", "accession": accession, "taxonomy": "us-gaap", "tag": "Assets", "unit": "USD", "value": value, "period_end": "2019-12-31", "period_start": None, "form": "10-K"}
            )
        result = canonicalize_annual_facts(pd.DataFrame(rows), _filings(), _registry())
        facts = result.facts.query("metric_id == 'total_assets'")
        self.assertEqual(facts["value"].tolist(), [100.0, 110.0])
        self.assertTrue(facts["accepted_at"].is_monotonic_increasing)

    def test_tag_priority_is_deterministic(self) -> None:
        facts = pd.DataFrame(
            [
                {"cik": "1", "accession": "a1", "taxonomy": "us-gaap", "tag": "RevenueOld", "unit": "USD", "value": 90.0, "period_start": "2019-01-01", "period_end": "2019-12-31", "form": "10-K"},
                {"cik": "1", "accession": "a1", "taxonomy": "us-gaap", "tag": "RevenueNew", "unit": "USD", "value": 100.0, "period_start": "2019-01-01", "period_end": "2019-12-31", "form": "10-K"},
            ]
        )
        result = canonicalize_annual_facts(facts, _filings(), _registry())
        row = result.facts.iloc[0]
        self.assertEqual(row["tag"], "RevenueNew")
        self.assertEqual(row["value"], 100.0)

    def test_gross_profit_identity_can_override_narrow_high_priority_revenue(self) -> None:
        facts = pd.DataFrame(
            [
                {
                    "cik": "1",
                    "accession": "a1",
                    "taxonomy": "us-gaap",
                    "tag": tag,
                    "unit": "USD",
                    "value": value,
                    "period_start": "2019-01-01",
                    "period_end": "2019-12-31",
                    "form": "10-K",
                }
                for tag, value in (
                    ("RevenueNew", 25.0),
                    ("RevenueOld", 100.0),
                    ("CostOfRevenue", 60.0),
                    ("GrossProfit", 40.0),
                )
            ]
        )
        result = canonicalize_annual_facts(facts, _filings(), _registry())
        selected = result.facts.set_index("metric_id")
        self.assertEqual(selected.loc["revenue", "tag"], "RevenueOld")
        self.assertEqual(selected.loc["revenue", "value"], 100.0)
        self.assertEqual(
            selected.loc["cost_of_goods_sold", "tag"], "CostOfRevenue"
        )
        audit = result.audit.set_index("check_id")
        self.assertEqual(
            audit.loc["gross_profit_identity_alternate_tag_contexts", "count"],
            1,
        )
        self.assertEqual(
            audit.loc["gross_profit_identity_mismatch_contexts", "count"], 0
        )

    def test_irreconcilable_direct_cogs_is_replaced_by_audited_synthetic_cogs(self) -> None:
        facts = pd.DataFrame(
            [
                {
                    "cik": "1",
                    "accession": "a1",
                    "taxonomy": "us-gaap",
                    "tag": tag,
                    "unit": "USD",
                    "value": value,
                    "period_start": "2019-01-01",
                    "period_end": "2019-12-31",
                    "form": "10-K",
                }
                for tag, value in (
                    ("RevenueNew", 100.0),
                    ("CostOfRevenue", 70.0),
                    ("GrossProfit", 40.0),
                )
            ]
        )
        result = canonicalize_annual_facts(facts, _filings(), _registry())
        selected = result.facts.set_index("metric_id")
        cogs = selected.loc["cost_of_goods_sold"]
        self.assertEqual(cogs["value"], 60.0)
        self.assertTrue(str(cogs["tag"]).startswith("synthetic_"))
        self.assertEqual(
            selected.loc["revenue", "value"]
            - selected.loc["cost_of_goods_sold", "value"],
            selected.loc["gross_profit", "value"],
        )
        audit = result.audit.set_index("check_id")
        self.assertEqual(
            audit.loc["gross_profit_identity_mismatch_contexts", "count"], 1
        )
        self.assertEqual(
            audit.loc["gross_profit_identity_mismatch_contexts", "status"],
            "review",
        )
        self.assertEqual(
            audit.loc["gross_profit_identity_synthetic_cogs_contexts", "count"],
            1,
        )

    def test_quarterly_duration_is_rejected(self) -> None:
        facts = pd.DataFrame(
            [
                {"cik": "1", "accession": "a1", "taxonomy": "us-gaap", "tag": "RevenueNew", "unit": "USD", "value": 20.0, "period_start": "2019-10-01", "period_end": "2019-12-31", "form": "10-K"}
            ]
        )
        result = canonicalize_annual_facts(facts, _filings(), _registry())
        self.assertTrue(result.facts.empty)

    def test_gross_profit_can_fill_missing_cogs_in_same_accession(self) -> None:
        facts = pd.DataFrame(
            [
                {"cik": "1", "accession": "a1", "taxonomy": "us-gaap", "tag": "RevenueNew", "unit": "USD", "value": 100.0, "period_start": "2019-01-01", "period_end": "2019-12-31", "form": "10-K"},
                {"cik": "1", "accession": "a1", "taxonomy": "us-gaap", "tag": "GrossProfit", "unit": "USD", "value": 40.0, "period_start": "2019-01-01", "period_end": "2019-12-31", "form": "10-K"},
            ]
        )
        result = canonicalize_annual_facts(facts, _filings(), _registry())
        cogs = result.facts.query("metric_id == 'cost_of_goods_sold'").iloc[0]
        self.assertEqual(cogs["value"], 60.0)
        self.assertTrue(str(cogs["tag"]).startswith("synthetic_"))

    def test_unmatched_accession_is_reported_and_excluded(self) -> None:
        facts = pd.DataFrame(
            [
                {"cik": "1", "accession": "missing", "taxonomy": "us-gaap", "tag": "Assets", "unit": "USD", "value": 100.0, "period_end": "2019-12-31", "period_start": None, "form": "10-K"}
            ]
        )
        result = canonicalize_annual_facts(facts, _filings(), _registry())
        self.assertTrue(result.facts.empty)
        check = result.audit.set_index("check_id").loc["unmatched_accession_rows"]
        self.assertEqual(check["count"], 1)
        self.assertEqual(check["status"], "fail")


if __name__ == "__main__":
    unittest.main()
