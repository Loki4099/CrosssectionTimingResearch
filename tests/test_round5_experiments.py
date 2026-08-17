from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from momentum_reversal.pipelines.round5_experiments import build_mae13_targets


class Round5TargetTests(unittest.TestCase):
    def test_entry_anchored_mae_and_deadzone(self) -> None:
        dates = pd.bdate_range("2020-01-06", periods=90)
        close = np.ones(len(dates))
        close[10] = 0.92
        market = pd.DataFrame({"session_date": dates, "tr_open": 1.0, "tr_close": close})
        rf = pd.DataFrame({"session_date": dates, "rf_log": 0.0, "rf_simple_decimal": 0.0})
        calendar = pd.DataFrame(
            {
                "week_id": [f"W{i:02d}" for i in range(14)],
                "signal_session": dates[[0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65]],
                "execution_session": dates[[1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56, 61, 66]],
            }
        )
        result = build_mae13_targets(market, rf, calendar)
        first = result.iloc[0]
        self.assertAlmostEqual(first["raw_mae13"], 0.08)
        self.assertAlmostEqual(first["excess_mae13_deadzone5"], 0.03)
        self.assertEqual(first["excess_mae13_deadzone10"], 0.0)

    def test_future_peak_to_trough_is_not_entry_mae(self) -> None:
        dates = pd.bdate_range("2020-01-06", periods=90)
        close = np.ones(len(dates)) * 1.05
        close[10] = 1.20
        market = pd.DataFrame({"session_date": dates, "tr_open": 1.0, "tr_close": close})
        rf = pd.DataFrame({"session_date": dates, "rf_log": 0.0, "rf_simple_decimal": 0.0})
        calendar = pd.DataFrame(
            {
                "week_id": [f"W{i:02d}" for i in range(14)],
                "signal_session": dates[[0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65]],
                "execution_session": dates[[1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56, 61, 66]],
            }
        )
        result = build_mae13_targets(market, rf, calendar)
        self.assertEqual(result.iloc[0]["raw_mae13"], 0.0)


if __name__ == "__main__":
    unittest.main()
