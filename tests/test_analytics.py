from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from momentum_reversal.analytics import (
    benchmark_returns_from_total_return_prices,
    performance_summary,
    relative_performance_summary,
)


class PerformanceSummaryTests(unittest.TestCase):
    def test_sortino_and_longest_underwater_run(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=4)
        returns = pd.Series([0.10, -0.05, -0.05, 0.10], index=index)

        result = performance_summary(returns)

        downside_deviation = np.sqrt((0.05**2 + 0.05**2) / 4.0)
        expected_sortino = returns.mean() / downside_deviation * np.sqrt(252.0)
        self.assertAlmostEqual(result["sortino"], expected_sortino)
        self.assertEqual(result["max_drawdown_duration"], 3.0)

    def test_short_constant_positive_sample_has_defined_edges(self) -> None:
        returns = pd.Series([0.01], index=pd.DatetimeIndex(["2024-01-02"]))

        result = performance_summary(returns)

        self.assertTrue(np.isnan(result["annualized_volatility"]))
        self.assertTrue(np.isnan(result["sharpe_zero_rf"]))
        self.assertTrue(np.isnan(result["sharpe_excess_rf"]))
        self.assertNotIn("sharpe", result.index)
        self.assertTrue(np.isnan(result["sortino"]))
        self.assertEqual(result["max_drawdown_duration"], 0.0)

    def test_optional_benchmark_appends_relative_metrics(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=3)
        strategy = pd.Series([0.02, 0.01, -0.01], index=index)
        benchmark = pd.Series([0.01, 0.00, -0.02], index=index)

        result = performance_summary(strategy, benchmark_returns=benchmark)

        self.assertIn("information_ratio", result.index)
        self.assertEqual(result["relative_observations"], 3.0)

    def test_explicit_rf_is_separate_from_zero_rf_sharpe(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=4)
        returns = pd.Series([0.01, -0.005, 0.02, 0.0], index=index)
        risk_free = pd.Series([0.001, 0.001, 0.001, 0.001], index=index)

        result = performance_summary(returns, risk_free_daily=risk_free)

        expected_zero = returns.mean() / returns.std(ddof=1) * np.sqrt(252.0)
        excess = returns - risk_free
        expected_excess = excess.mean() / excess.std(ddof=1) * np.sqrt(252.0)
        self.assertAlmostEqual(result["sharpe_zero_rf"], expected_zero)
        self.assertAlmostEqual(result["sharpe_excess_rf"], expected_excess)
        self.assertNotEqual(result["sharpe_zero_rf"], result["sharpe_excess_rf"])

    def test_explicit_rf_cannot_silently_fill_missing_dates_with_zero(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=3)
        returns = pd.Series([0.01, 0.02, -0.01], index=index)
        incomplete_rf = pd.Series([0.001, 0.001], index=index[:2])

        with self.assertRaisesRegex(ValueError, "cover every strategy observation"):
            performance_summary(returns, risk_free_daily=incomplete_rf)


class RelativePerformanceTests(unittest.TestCase):
    def test_relative_metrics_match_direct_calculation(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=4)
        strategy = pd.Series([0.02, 0.01, -0.01, 0.03], index=index)
        benchmark = pd.Series([0.01, 0.00, -0.02, 0.01], index=index)

        result = relative_performance_summary(strategy, benchmark)
        active = strategy - benchmark
        expected_geometric = (
            (1.0 + strategy).prod() / (1.0 + benchmark).prod()
        ) ** (252.0 / len(strategy)) - 1.0

        self.assertAlmostEqual(
            result["annualized_excess_return"], active.mean() * 252.0
        )
        self.assertAlmostEqual(result["geometric_excess_return"], expected_geometric)
        self.assertAlmostEqual(
            result["tracking_error"], active.std(ddof=1) * np.sqrt(252.0)
        )
        self.assertAlmostEqual(
            result["information_ratio"],
            active.mean() / active.std(ddof=1) * np.sqrt(252.0),
        )
        self.assertAlmostEqual(
            result["beta"], strategy.cov(benchmark) / benchmark.var(ddof=1)
        )
        expected_alpha_zero = (
            strategy.mean() - result["beta"] * benchmark.mean()
        ) * 252.0
        self.assertAlmostEqual(result["annualized_alpha_zero_rf"], expected_alpha_zero)
        self.assertTrue(np.isnan(result["annualized_alpha_excess_rf"]))

    def test_index_mismatch_is_rejected_instead_of_intersected(self) -> None:
        strategy = pd.Series([0.01, 0.02], index=pd.DatetimeIndex(["2024-01-02", "2024-01-03"]))
        benchmark = pd.Series([0.01, 0.02], index=pd.DatetimeIndex(["2024-01-03", "2024-01-02"]))

        with self.assertRaisesRegex(ValueError, "exactly the same index"):
            relative_performance_summary(strategy, benchmark)

    def test_missing_values_are_removed_only_as_aligned_pairs(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=3)
        strategy = pd.Series([0.01, np.nan, np.inf], index=index)
        benchmark = pd.Series([0.00, 0.01, 0.02], index=index)

        result = relative_performance_summary(strategy, benchmark)

        self.assertEqual(result["relative_observations"], 1.0)
        self.assertAlmostEqual(result["annualized_excess_return"], 2.52)
        self.assertTrue(np.isnan(result["beta"]))
        self.assertTrue(np.isnan(result["tracking_error"]))

    def test_constant_benchmark_and_active_return_do_not_divide_by_zero(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=4)
        benchmark = pd.Series(0.01, index=index)
        strategy = benchmark.copy()

        result = relative_performance_summary(strategy, benchmark)

        self.assertTrue(np.isnan(result["beta"]))
        self.assertTrue(np.isnan(result["annualized_alpha_zero_rf"]))
        self.assertTrue(np.isnan(result["annualized_alpha_excess_rf"]))
        self.assertEqual(result["tracking_error"], 0.0)
        self.assertTrue(np.isnan(result["information_ratio"]))
        self.assertAlmostEqual(result["geometric_excess_return"], 0.0)


class BenchmarkReturnConstructionTests(unittest.TestCase):
    def test_first_return_is_next_open_to_close_then_close_to_close(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=4)
        prices = pd.DataFrame(
            {
                "date": dates,
                "tr_open": [98.0, 100.0, 104.0, 102.0],
                "tr_close": [99.0, 102.0, 103.0, 106.0],
            }
        )
        strategy = pd.Series([0.0, 0.0, 0.0], index=dates[1:])

        result = benchmark_returns_from_total_return_prices(prices, strategy.index)

        expected = pd.Series(
            [102.0 / 100.0 - 1.0, 103.0 / 102.0 - 1.0, 106.0 / 103.0 - 1.0],
            index=strategy.index,
            name="benchmark_return",
        )
        pd.testing.assert_series_equal(result, expected)

    def test_missing_strategy_session_is_rejected(self) -> None:
        prices = pd.DataFrame(
            {"tr_open": [100.0], "tr_close": [101.0]},
            index=pd.DatetimeIndex(["2024-01-02"]),
        )
        strategy = pd.Series(
            [0.0, 0.0], index=pd.DatetimeIndex(["2024-01-02", "2024-01-03"])
        )

        with self.assertRaisesRegex(ValueError, "cover every strategy session"):
            benchmark_returns_from_total_return_prices(prices, strategy)


if __name__ == "__main__":
    unittest.main()
