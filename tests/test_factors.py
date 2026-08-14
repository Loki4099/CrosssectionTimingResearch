from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from momentum_reversal.experiments import (
    VolatilityTargetSpec,
    common_forecast_activation_date,
    forecast_target_allocation,
    hysteresis_high_volatility_state,
    individual_realized_volatility,
    risk_adjusted_momentum_scores,
    rolling_volatility_forecasts,
    rolling_empirical_percentile,
    spy_realized_volatility,
    volatility_target_allocation,
)
from momentum_reversal.factors import (
    MomentumDefinition,
    compute_momentum_scores,
    compute_reversal_scores,
)
from momentum_reversal.portfolio import rank_and_select

from tests.synthetic import make_prices


class MomentumFactorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prices = make_prices(sessions=340, assets=60)
        self.dates = self.prices.index.get_level_values("date").unique()

    def test_session_lag_formulas_are_exact(self) -> None:
        date = self.dates[300]
        sid = "S059"
        close = self.prices["tr_close"].unstack("sid")
        score0 = compute_momentum_scores(
            self.prices, [date], MomentumDefinition.MOM_255_0
        ).loc[(date, sid)]
        score21 = compute_momentum_scores(
            self.prices, [date], MomentumDefinition.MOM_255_21
        ).loc[(date, sid)]
        self.assertAlmostEqual(score0, np.log(close.loc[date, sid] / close.iloc[45][sid]))
        self.assertAlmostEqual(score21, np.log(close.iloc[279][sid] / close.iloc[45][sid]))

    def test_calendar_signal_is_constant_within_month(self) -> None:
        dates = pd.DatetimeIndex([self.dates[310], self.dates[315]])
        self.assertEqual(dates[0].to_period("M"), dates[1].to_period("M"))
        scores = compute_momentum_scores(self.prices, dates, "mom_12_1")
        pd.testing.assert_series_equal(
            scores.xs(dates[0], level="signal_date"),
            scores.xs(dates[1], level="signal_date"),
        )

    def test_future_mutation_does_not_change_past_score(self) -> None:
        date = self.dates[300]
        original = compute_momentum_scores(self.prices, [date], "mom_255_0")
        changed = self.prices.copy()
        future = changed.index.get_level_values("date") > date
        changed.loc[future, "tr_close"] *= 10.0
        rerun = compute_momentum_scores(changed, [date], "mom_255_0")
        pd.testing.assert_series_equal(original, rerun)

    def test_reversal_score_uses_exact_lag_and_is_causal(self) -> None:
        date = self.dates[300]
        sid = "S059"
        close = self.prices["tr_close"].unstack("sid")
        score = compute_reversal_scores(
            self.prices,
            [date],
            lookback=5,
            sessions=self.dates,
        ).loc[(date, sid)]
        self.assertAlmostEqual(
            score, -np.log(close.loc[date, sid] / close.iloc[295][sid])
        )
        changed = self.prices.copy()
        changed.loc[changed.index.get_level_values("date") > date, "tr_close"] *= 3.0
        rerun = compute_reversal_scores(
            changed,
            [date],
            lookback=5,
            sessions=self.dates,
        ).loc[(date, sid)]
        self.assertAlmostEqual(score, rerun)

    def test_empirical_percentile_and_hysteresis_are_exact(self) -> None:
        dates = pd.date_range("2024-01-01", periods=5)
        values = pd.Series([1.0, 2.0, 3.0, 2.0, 1.0], index=dates)
        percentile = rolling_empirical_percentile(
            values, lookback=3, min_history=2
        )
        self.assertTrue(pd.isna(percentile.iloc[0]))
        self.assertAlmostEqual(percentile.iloc[1], 1.0)
        self.assertAlmostEqual(percentile.iloc[3], 2.0 / 3.0)
        self.assertAlmostEqual(percentile.iloc[4], 1.0 / 3.0)

        explicit = pd.Series([0.80, 0.81, 0.60, 0.59], index=dates[:4])
        state = hysteresis_high_volatility_state(explicit)
        self.assertEqual(state.tolist(), [False, True, True, False])

        changed = values.copy()
        changed.iloc[-1] = 100.0
        revised = rolling_empirical_percentile(
            changed, lookback=3, min_history=2
        )
        pd.testing.assert_series_equal(percentile.iloc[:-1], revised.iloc[:-1])

    def test_v4_forecasts_are_causal_and_har_labels_are_purged(self) -> None:
        dates = pd.bdate_range("2020-01-02", periods=700)
        returns = pd.Series(
            0.002 + 0.01 * np.sin(np.arange(len(dates)) / 13.0),
            index=dates,
        )
        forecasts = rolling_volatility_forecasts(returns)
        self.assertEqual(forecasts["har_rv"].first_valid_index(), dates[544])
        expected_persistence = np.sqrt(
            252.0 * np.mean(np.square(returns.iloc[580:601]))
        )
        self.assertAlmostEqual(
            forecasts.loc[dates[600], "persistence"], expected_persistence
        )

        changed = returns.copy()
        changed.iloc[601:] *= 20.0
        revised = rolling_volatility_forecasts(changed)
        pd.testing.assert_frame_equal(
            forecasts.loc[: dates[600], ["persistence", "ewma_094", "har_rv"]],
            revised.loc[: dates[600], ["persistence", "ewma_094", "har_rv"]],
        )

    def test_v4_common_activation_and_allocation_are_frozen(self) -> None:
        dates = pd.bdate_range("2023-01-02", periods=5)
        forecasts = pd.DataFrame(
            {
                "persistence": [0.2, 0.2, 0.2, 0.3, 0.1],
                "ewma_094": [0.2, 0.2, 0.2, 0.3, 0.1],
                "har_rv": [np.nan, np.nan, 0.2, 0.3, 0.1],
            },
            index=dates,
        )
        activation = common_forecast_activation_date(forecasts, dates)
        self.assertEqual(activation, dates[2])
        allocation = forecast_target_allocation(
            forecasts["persistence"],
            dates,
            activation_date=activation,
            target_volatility=0.15,
            max_exposure=1.5,
        )
        np.testing.assert_allclose(allocation, [1.0, 1.0, 0.75, 0.5, 1.5])

    def test_spy_realized_volatility_is_causal(self) -> None:
        dates = pd.bdate_range("2023-01-02", periods=80)
        close = 100.0 * np.cumprod(1.0 + np.linspace(-0.01, 0.01, len(dates)))
        benchmark = pd.DataFrame({"date": dates, "benchmark_tr_close": close})
        original = spy_realized_volatility(benchmark, window=20)
        changed = benchmark.copy()
        changed.loc[changed.index[-10:], "benchmark_tr_close"] *= np.linspace(1, 2, 10)
        revised = spy_realized_volatility(changed, window=20)
        cutoff = dates[-11]
        pd.testing.assert_series_equal(
            original.loc[:cutoff], revised.loc[:cutoff], check_names=False
        )
        spec = VolatilityTargetSpec(20, 0.15, 1.0)
        allocation = volatility_target_allocation(original, dates[30:40], spec)
        self.assertTrue(allocation.between(0.0, 1.0).all())

    def test_individual_rv_uses_exact_causal_session_window(self) -> None:
        signal_date = self.dates[300]
        rv = individual_realized_volatility(
            self.prices,
            pd.DatetimeIndex([signal_date]),
            window=20,
            sessions=self.dates,
        )
        sid = "S059"
        close = self.prices["tr_close"].unstack("sid")[sid]
        expected = (
            close.pct_change(fill_method=None).iloc[281:301].std(ddof=1)
            * np.sqrt(252.0)
        )
        self.assertAlmostEqual(rv.loc[(signal_date, sid)], expected)

        changed = self.prices.copy()
        future = changed.index.get_level_values("date") > signal_date
        changed.loc[future, "tr_close"] *= 10.0
        revised = individual_realized_volatility(
            changed,
            pd.DatetimeIndex([signal_date]),
            window=20,
            sessions=self.dates,
        )
        pd.testing.assert_series_equal(rv, revised)

    def test_risk_adjusted_momentum_is_ratio_and_preserves_missingness(self) -> None:
        index = pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2024-01-31"), "A"),
                (pd.Timestamp("2024-01-31"), "B"),
            ],
            names=["signal_date", "sid"],
        )
        momentum = pd.Series([0.4, -0.2], index=index)
        volatility = pd.Series([0.2, np.nan], index=index)
        adjusted = risk_adjusted_momentum_scores(momentum, volatility)
        self.assertAlmostEqual(
            adjusted.loc[(pd.Timestamp("2024-01-31"), "A")], 2.0
        )
        self.assertTrue(
            pd.isna(adjusted.loc[(pd.Timestamp("2024-01-31"), "B")])
        )

    def test_explicit_calendar_prevents_missing_session_lag_compression(self) -> None:
        date = self.dates[300]
        sid = "S059"
        # Remove one whole observed date before the denominator.  With no
        # authoritative reindex, an observation-based shift would move the
        # denominator back by one extra exchange session.
        missing_date = self.dates[20]
        gapped = self.prices.drop(index=missing_date, level="date")
        score = compute_momentum_scores(
            gapped,
            [date],
            "mom_255_0",
            sessions=self.dates,
        ).loc[(date, sid)]
        close = self.prices["tr_close"].unstack("sid")
        expected = np.log(close.loc[date, sid] / close.loc[self.dates[45], sid])
        self.assertAlmostEqual(score, expected)

    def test_price_date_outside_explicit_calendar_is_rejected(self) -> None:
        rogue_date = self.dates[-1] + pd.Timedelta(days=1)
        rogue = self.prices.xs(self.dates[-1], level="date", drop_level=False).reset_index()
        rogue["date"] = rogue_date
        rogue = rogue.set_index(["date", "sid"])
        contaminated = pd.concat([self.prices, rogue]).sort_index()
        with self.assertRaisesRegex(ValueError, "authoritative session calendar"):
            compute_momentum_scores(
                contaminated,
                [self.dates[300]],
                "mom_255_0",
                sessions=self.dates,
            )

    def test_top_sets_are_nested_with_deterministic_ties(self) -> None:
        scores = pd.Series(1.0, index=[f"S{i:03d}" for i in range(60)])
        members = tuple(reversed(scores.index.tolist()))
        top10 = set(rank_and_select(scores, members, 10).query("selected").index)
        top20 = set(rank_and_select(scores, members, 20).query("selected").index)
        top50 = set(rank_and_select(scores, members, 50).query("selected").index)
        self.assertTrue(top10 < top20 < top50)
        self.assertEqual(top10, {f"S{i:03d}" for i in range(10)})


if __name__ == "__main__":
    unittest.main()
