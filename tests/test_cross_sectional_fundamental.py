from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from momentum_reversal.factors.cross_sectional_fundamental import (
    FundamentalFactorDefinition as Factor,
    FundamentalMetric as Metric,
    compute_fundamental_factor_panel,
)


FLOW_METRICS = {
    Metric.REVENUE.value,
    Metric.COST_OF_GOODS_SOLD.value,
    Metric.DEPRECIATION.value,
    Metric.NET_INCOME.value,
    Metric.CASH_FLOW_FROM_OPERATIONS.value,
    Metric.COMMON_DIVIDENDS.value,
    Metric.COMMON_SHARE_REPURCHASES.value,
    Metric.COMMON_SHARE_ISSUANCE.value,
}


PRIOR_VALUES = {
    Metric.REVENUE.value: 150.0,
    Metric.COST_OF_GOODS_SOLD.value: 90.0,
    Metric.TOTAL_ASSETS.value: 100.0,
    Metric.CURRENT_ASSETS.value: 40.0,
    Metric.CASH.value: 10.0,
    Metric.CURRENT_LIABILITIES.value: 30.0,
    Metric.SHORT_TERM_DEBT.value: 5.0,
    Metric.TAXES_PAYABLE.value: 2.0,
    Metric.DEPRECIATION.value: 5.0,
    Metric.NET_INCOME.value: 10.0,
    Metric.CASH_FLOW_FROM_OPERATIONS.value: 8.0,
    Metric.COMMON_BOOK_EQUITY.value: 60.0,
    Metric.COMMON_DIVIDENDS.value: 1.0,
    Metric.COMMON_SHARE_REPURCHASES.value: 2.0,
    Metric.COMMON_SHARE_ISSUANCE.value: 0.5,
}

CURRENT_VALUES = {
    Metric.REVENUE.value: 200.0,
    Metric.COST_OF_GOODS_SOLD.value: 120.0,
    Metric.TOTAL_ASSETS.value: 120.0,
    Metric.CURRENT_ASSETS.value: 50.0,
    Metric.CASH.value: 12.0,
    Metric.CURRENT_LIABILITIES.value: 35.0,
    Metric.SHORT_TERM_DEBT.value: 6.0,
    Metric.TAXES_PAYABLE.value: 3.0,
    Metric.DEPRECIATION.value: 6.0,
    Metric.NET_INCOME.value: 16.0,
    Metric.CASH_FLOW_FROM_OPERATIONS.value: 12.0,
    Metric.COMMON_BOOK_EQUITY.value: 70.0,
    Metric.COMMON_DIVIDENDS.value: 2.0,
    Metric.COMMON_SHARE_REPURCHASES.value: 3.0,
    Metric.COMMON_SHARE_ISSUANCE.value: 1.0,
}


def _filing_rows(
    *,
    cik: str,
    accession: str,
    accepted_at: str,
    available_session: str,
    fiscal_year: int,
    period_start: str,
    period_end: str,
    values: dict[str, float],
    sic: int = 3571,
    sic_is_pit: bool = False,
    sic_provenance: str = "unverified_no_pit_sic",
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for metric_id, value in values.items():
        rows.append(
            {
                "cik": cik,
                "accession": accession,
                "accepted_at": accepted_at,
                "available_session": available_session,
                "fiscal_year": fiscal_year,
                "period_start": (
                    period_start if metric_id in FLOW_METRICS else pd.NaT
                ),
                "period_end": period_end,
                "metric_id": metric_id,
                "value": value,
                "unit": "USD",
                "sic": sic,
                "sic_is_pit": sic_is_pit,
                "sic_provenance": sic_provenance,
            }
        )
    return rows


def _base_facts(
    *,
    cik: str = "320193",
    sic: int = 3571,
    sic_is_pit: bool = False,
) -> pd.DataFrame:
    sic_provenance = (
        "synthetic_pit_filing_classification"
        if sic_is_pit
        else "unverified_no_pit_sic"
    )
    rows = _filing_rows(
        cik=cik,
        accession=f"{cik}-22",
        accepted_at="2023-02-15T16:30:00-05:00",
        available_session="2023-02-16",
        fiscal_year=2022,
        period_start="2022-01-01",
        period_end="2022-12-31",
        values=PRIOR_VALUES,
        sic=sic,
        sic_is_pit=sic_is_pit,
        sic_provenance=sic_provenance,
    )
    rows += _filing_rows(
        cik=cik,
        accession=f"{cik}-23",
        accepted_at="2024-03-14T16:30:00-04:00",
        available_session="2024-03-15",
        fiscal_year=2023,
        period_start="2023-01-01",
        period_end="2023-12-31",
        values=CURRENT_VALUES,
        sic=sic,
        sic_is_pit=sic_is_pit,
        sic_provenance=sic_provenance,
    )
    return pd.DataFrame(rows)


def _mapping(*pairs: tuple[str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "sid": sid,
                "cik": cik,
                "effective_from": "2020-01-01",
                "effective_to": pd.NaT,
            }
            for sid, cik in pairs
        ]
    )


def _market_equity(
    *, cik: str = "320193", audited: bool = True
) -> pd.DataFrame:
    observations = [
        ("2022-12-30", 180.0),
        ("2023-12-29", 200.0),
        ("2024-04-30", 250.0),
        ("2024-05-31", 260.0),
    ]
    return pd.DataFrame(
        [
            {
                "cik": cik,
                "measurement_date": date,
                "available_session": date,
                "issuer_market_equity": value,
                "unit": "USD",
                "all_share_classes_audited": audited,
            }
            for date, value in observations
        ]
    )


def _row(
    panel: pd.DataFrame,
    factor: Factor,
    *,
    date: str = "2024-04-30",
    sid: str = "S1",
) -> pd.Series:
    return panel.loc[(pd.Timestamp(date), sid, factor.value)]


class CrossSectionalFundamentalTests(unittest.TestCase):
    def test_raw_values_scores_signs_and_market_equity_denominators_are_exact(self) -> None:
        panel = compute_fundamental_factor_panel(
            _base_facts(),
            _mapping(("S1", "320193")),
            [pd.Timestamp("2024-04-30")],
            issuer_market_equity=_market_equity(),
        )

        gross_profitability = (200.0 - 120.0) / 120.0
        gp = _row(panel, Factor.GROSS_PROFITABILITY)
        self.assertAlmostEqual(gp["raw_value"], gross_profitability)
        self.assertAlmostEqual(gp["score"], gross_profitability)
        # Growing assets receive a negative score: higher means more
        # conservative investment.
        asset_growth = _row(panel, Factor.ASSET_GROWTH)
        self.assertAlmostEqual(asset_growth["raw_value"], 0.20)
        self.assertAlmostEqual(asset_growth["score"], -0.20)
        # ACC = [(10-2) - (5-1-1) - 6] = -1.  The buy-direction score is -ACC.
        sloan = _row(panel, Factor.SLOAN_ACCRUALS)
        self.assertAlmostEqual(sloan["raw_value"], -1.0 / 110.0)
        self.assertAlmostEqual(sloan["score"], 1.0 / 110.0)
        cfo = _row(panel, Factor.CFO_ACCRUALS_PROJECT_TRANSLATION)
        self.assertAlmostEqual(cfo["raw_value"], (16.0 - 12.0) / 110.0)
        self.assertAlmostEqual(cfo["score"], -(16.0 - 12.0) / 110.0)
        # BM uses current issuer ME; NPY uses issuer ME at fiscal year-end.
        bm = _row(panel, Factor.BOOK_TO_MARKET)
        self.assertAlmostEqual(bm["raw_value"], 70.0 / 250.0)
        self.assertAlmostEqual(bm["score"], 70.0 / 250.0)
        npy = _row(panel, Factor.NET_PAYOUT_YIELD)
        expected_npy = (2.0 + 3.0 - 1.0) / 200.0
        self.assertAlmostEqual(npy["raw_value"], expected_npy)
        self.assertAlmostEqual(npy["score"], expected_npy)
        self.assertEqual(
            _row(panel, Factor.CFO_ACCRUALS_PROJECT_TRANSLATION)[
                "definition_status"
            ],
            "project_translation",
        )

    def test_amendment_changes_only_signals_on_or_after_its_availability(self) -> None:
        facts = _base_facts()
        amended = dict(CURRENT_VALUES)
        amended.update(
            {
                Metric.REVENUE.value: 240.0,
                Metric.TOTAL_ASSETS.value: 125.0,
            }
        )
        facts = pd.concat(
            [
                facts,
                pd.DataFrame(
                    _filing_rows(
                        cik="320193",
                        accession="320193-23-A1",
                        accepted_at="2024-05-14T16:30:00-04:00",
                        available_session="2024-05-15",
                        fiscal_year=2023,
                        period_start="2023-01-01",
                        period_end="2023-12-31",
                        values=amended,
                    )
                ),
            ],
            ignore_index=True,
        )
        panel = compute_fundamental_factor_panel(
            facts,
            _mapping(("S1", "320193")),
            [pd.Timestamp("2024-04-30"), pd.Timestamp("2024-05-31")],
            issuer_market_equity=_market_equity(),
        )

        before = _row(panel, Factor.GROSS_PROFITABILITY, date="2024-04-30")
        after = _row(panel, Factor.GROSS_PROFITABILITY, date="2024-05-31")
        self.assertAlmostEqual(before["score"], 80.0 / 120.0)
        self.assertEqual(before["source_accession"], "320193-23")
        self.assertAlmostEqual(after["score"], 120.0 / 125.0)
        self.assertEqual(after["source_accession"], "320193-23-A1")

    def test_future_filing_cannot_change_past_panel(self) -> None:
        facts = _base_facts()
        baseline = compute_fundamental_factor_panel(
            facts,
            _mapping(("S1", "320193")),
            [pd.Timestamp("2024-04-30")],
            issuer_market_equity=_market_equity(),
        )
        future_values = dict(CURRENT_VALUES)
        future_values[Metric.REVENUE.value] = 9_999_999.0
        future_values[Metric.TOTAL_ASSETS.value] = 1.0
        contaminated = pd.concat(
            [
                facts,
                pd.DataFrame(
                    _filing_rows(
                        cik="320193",
                        accession="320193-24",
                        accepted_at="2025-03-14T16:30:00-04:00",
                        available_session="2025-03-17",
                        fiscal_year=2024,
                        period_start="2024-01-01",
                        period_end="2024-12-31",
                        values=future_values,
                    )
                ),
            ],
            ignore_index=True,
        )
        rerun = compute_fundamental_factor_panel(
            contaminated,
            _mapping(("S1", "320193")),
            [pd.Timestamp("2024-04-30")],
            issuer_market_equity=_market_equity(),
        )
        pd.testing.assert_frame_equal(baseline, rerun)

    def test_missing_depreciation_and_payout_are_not_filled_with_zero(self) -> None:
        facts = _base_facts()
        is_current = facts["fiscal_year"].eq(2023)
        facts = facts.loc[
            ~(
                is_current
                & facts["metric_id"].isin(
                    [
                        Metric.DEPRECIATION.value,
                        Metric.COMMON_SHARE_REPURCHASES.value,
                    ]
                )
            )
        ]
        panel = compute_fundamental_factor_panel(
            facts,
            _mapping(("S1", "320193")),
            [pd.Timestamp("2024-04-30")],
            issuer_market_equity=_market_equity(),
        )

        sloan = _row(panel, Factor.SLOAN_ACCRUALS)
        self.assertTrue(np.isnan(sloan["raw_value"]))
        self.assertTrue(np.isnan(sloan["score"]))
        self.assertIn("missing_metric:depreciation_and_amortization", sloan["missing_reason"])
        self.assertEqual(sloan["data_gate"], "blocked_missing_facts")
        # The separately named CFO translation does not depend on depreciation.
        cfo = _row(panel, Factor.CFO_ACCRUALS_PROJECT_TRANSLATION)
        self.assertTrue(np.isfinite(cfo["raw_value"]))
        self.assertTrue(np.isfinite(cfo["score"]))
        payout = _row(panel, Factor.NET_PAYOUT_YIELD)
        self.assertTrue(np.isnan(payout["raw_value"]))
        self.assertTrue(np.isnan(payout["score"]))
        self.assertIn("missing_metric:common_share_repurchases", payout["missing_reason"])

    def test_flow_duration_mismatch_is_a_hard_gate(self) -> None:
        facts = _base_facts()
        mask = facts["metric_id"].eq(Metric.COST_OF_GOODS_SOLD.value) & facts[
            "fiscal_year"
        ].eq(2023)
        facts.loc[mask, "period_start"] = "2023-02-01"
        panel = compute_fundamental_factor_panel(
            facts,
            _mapping(("S1", "320193")),
            [pd.Timestamp("2024-04-30")],
        )
        gp = _row(panel, Factor.GROSS_PROFITABILITY)
        self.assertTrue(np.isnan(gp["score"]))
        self.assertEqual(gp["missing_reason"], "flow_duration_mismatch")
        self.assertEqual(gp["data_gate"], "blocked_duration_integrity")

    def test_impossible_negative_balance_components_fail_closed(self) -> None:
        facts = _base_facts()
        mask = facts["metric_id"].eq(Metric.TAXES_PAYABLE.value) & facts[
            "fiscal_year"
        ].eq(2023)
        facts.loc[mask, "value"] = -1.0
        panel = compute_fundamental_factor_panel(
            facts,
            _mapping(("S1", "320193")),
            [pd.Timestamp("2024-04-30")],
        )
        sloan = _row(panel, Factor.SLOAN_ACCRUALS)
        self.assertTrue(np.isnan(sloan["score"]))
        self.assertEqual(sloan["missing_reason"], "negative_taxes_payable")
        self.assertEqual(sloan["data_gate"], "blocked_invalid_facts")

    def test_unaudited_single_share_class_market_cap_is_never_approximated(self) -> None:
        panel = compute_fundamental_factor_panel(
            _base_facts(),
            _mapping(("S1", "320193")),
            [pd.Timestamp("2024-04-30")],
            issuer_market_equity=_market_equity(audited=False),
        )
        for factor in (Factor.BOOK_TO_MARKET, Factor.NET_PAYOUT_YIELD):
            row = _row(panel, factor)
            self.assertTrue(np.isnan(row["score"]))
            self.assertEqual(
                row["missing_reason"],
                "issuer_market_equity_all_share_classes_not_audited",
            )
            self.assertEqual(
                row["data_gate"], "blocked_issuer_market_equity_audit"
            )

    def test_stale_market_equity_is_not_used_as_a_year_end_approximation(self) -> None:
        market_equity = _market_equity().iloc[[0]].copy()
        panel = compute_fundamental_factor_panel(
            _base_facts(),
            _mapping(("S1", "320193")),
            [pd.Timestamp("2024-04-30")],
            issuer_market_equity=market_equity,
        )
        for factor in (Factor.BOOK_TO_MARKET, Factor.NET_PAYOUT_YIELD):
            row = _row(panel, factor)
            self.assertTrue(np.isnan(row["score"]))
            self.assertEqual(row["missing_reason"], "issuer_market_equity_stale")
            self.assertEqual(
                row["data_gate"], "blocked_issuer_market_equity_unavailable"
            )

    def test_financial_applicability_mask_preserves_valid_book_to_market(self) -> None:
        panel = compute_fundamental_factor_panel(
            _base_facts(sic=6200, sic_is_pit=True),
            _mapping(("BANK", "320193")),
            [pd.Timestamp("2024-04-30")],
            issuer_market_equity=_market_equity(),
            apply_pit_financial_sector_filter=True,
        )
        for factor in (
            Factor.GROSS_PROFITABILITY,
            Factor.ASSET_GROWTH,
            Factor.SLOAN_ACCRUALS,
            Factor.CFO_ACCRUALS_PROJECT_TRANSLATION,
            Factor.NET_PAYOUT_YIELD,
        ):
            row = _row(panel, factor, sid="BANK")
            self.assertTrue(np.isnan(row["score"]))
            self.assertEqual(row["missing_reason"], "financial_sector_not_applicable")
            self.assertEqual(row["data_gate"], "not_applicable_financial")
            self.assertEqual(row["applicability_scope"], "pit_sic_financial_filter")
            self.assertEqual(
                row["applicability_provenance"],
                "synthetic_pit_filing_classification",
            )

        bm = _row(panel, Factor.BOOK_TO_MARKET, sid="BANK")
        self.assertAlmostEqual(bm["score"], 70.0 / 250.0)
        self.assertEqual(bm["data_gate"], "pass")

    def test_current_non_pit_sic_never_masks_default_historical_scores(self) -> None:
        panel = compute_fundamental_factor_panel(
            _base_facts(sic=6200, sic_is_pit=False),
            _mapping(("BANK", "320193")),
            [pd.Timestamp("2024-04-30")],
        )
        gross_profitability = _row(
            panel, Factor.GROSS_PROFITABILITY, sid="BANK"
        )
        self.assertTrue(np.isfinite(gross_profitability["score"]))
        self.assertEqual(
            gross_profitability["applicability_scope"],
            "unverified_no_pit_sic",
        )
        self.assertEqual(
            gross_profitability["applicability_provenance"],
            "unverified_no_pit_sic",
        )

    def test_cover_page_share_date_cannot_supersede_fiscal_year_filing(self) -> None:
        facts = _base_facts()
        cover_row = {
            "cik": "320193",
            "accession": "320193-23",
            "accepted_at": "2024-03-14T16:30:00-04:00",
            "available_session": "2024-03-15",
            "fiscal_year": 2023,
            "period_start": pd.NaT,
            "period_end": "2024-02-01",
            "metric_id": "shares_outstanding",
            "value": 1_000.0,
            "unit": "shares",
            "sic": 3571,
            "sic_is_pit": False,
            "sic_provenance": "unverified_no_pit_sic",
        }
        facts = pd.concat([facts, pd.DataFrame([cover_row])], ignore_index=True)

        panel = compute_fundamental_factor_panel(
            facts,
            _mapping(("S1", "320193")),
            [pd.Timestamp("2024-04-30")],
        )

        gross_profitability = _row(panel, Factor.GROSS_PROFITABILITY)
        self.assertAlmostEqual(gross_profitability["score"], 80.0 / 120.0)
        self.assertEqual(
            gross_profitability["source_period_end"],
            pd.Timestamp("2023-12-31"),
        )

    def test_requested_financial_filter_blocks_when_sic_is_not_pit(self) -> None:
        panel = compute_fundamental_factor_panel(
            _base_facts(sic=6200, sic_is_pit=False),
            _mapping(("BANK", "320193")),
            [pd.Timestamp("2024-04-30")],
            apply_pit_financial_sector_filter=True,
        )
        gross_profitability = _row(
            panel, Factor.GROSS_PROFITABILITY, sid="BANK"
        )
        self.assertTrue(np.isnan(gross_profitability["score"]))
        self.assertEqual(
            gross_profitability["data_gate"], "blocked_applicability_unknown"
        )
        self.assertEqual(
            gross_profitability["applicability_scope"],
            "unverified_no_pit_sic",
        )


if __name__ == "__main__":
    unittest.main()
