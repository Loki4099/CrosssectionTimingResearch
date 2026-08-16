from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from momentum_reversal.pipelines.round2_models import (
    _fit_transform,
    _moving_block_mean_se,
    replay_weekly_spy_cash,
)


class Round2ModelProtocolTests(unittest.TestCase):
    def test_training_transform_winsorizes_without_refitting(self) -> None:
        train = np.arange(90, dtype=float).reshape(10, 9)
        transform = _fit_transform(train)
        changed = train.copy()
        changed[-1] = 1e9
        self.assertTrue(np.allclose(transform.apply(train[:1]), transform.apply(train[:1])))
        self.assertTrue(np.allclose(transform.apply(changed[-1:]), transform.apply(train[-1:])))

    def test_moving_block_se_is_deterministic(self) -> None:
        values = np.sin(np.arange(120) / 7)
        left = _moving_block_mean_se(values, block=13, repetitions=200, seed=7)
        right = _moving_block_mean_se(values, block=13, repetitions=200, seed=7)
        self.assertEqual(left, right)
        self.assertGreater(left, 0)

    def test_replay_next_open_cost_and_cash(self) -> None:
        dates = pd.to_datetime(["2020-01-06", "2020-01-07"])
        market = pd.DataFrame(
            {
                "session_date": dates,
                "tr_open": [100.0, 110.0],
                "tr_close": [110.0, 110.0],
            }
        )
        rf = pd.DataFrame(
            {"session_date": dates, "rf_simple_decimal": [0.001, 0.001]}
        )
        schedule = pd.DataFrame(
            {"execution_session": [dates[0]], "target_spy_weight": [0.5]}
        )
        nav = replay_weekly_spy_cash(schedule, market, rf, cost_bps=10)
        self.assertAlmostEqual(float(nav.iloc[0].turnover), 0.5)
        self.assertAlmostEqual(float(nav.iloc[0].cost), 0.0005)
        self.assertGreater(float(nav.iloc[-1].nav), 1.0)
        self.assertAlmostEqual(float(nav.iloc[0].target_spy_weight), 0.5)


if __name__ == "__main__":
    unittest.main()
