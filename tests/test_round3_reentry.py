from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from momentum_reversal.pipelines.round3_reentry import (
    DEFENSE,
    FULL_ARMED,
    RECOVERY_UNARMED,
    build_weekly_reentry_states,
    compute_daily_reentry_indicators,
)


class Round3ReentryTests(unittest.TestCase):
    def test_indicator_is_causal_and_q75_is_strictly_lagged(self) -> None:
        dates = pd.bdate_range("2010-01-01", periods=820)
        ret = 0.0002 + 0.01 * np.sin(np.arange(820) / 7)
        close = 100 * np.cumprod(1 + ret)
        market = pd.DataFrame({"session_date": dates, "tr_close": close})
        base = compute_daily_reentry_indicators(market)
        changed = market.copy()
        changed.loc[changed.index[-1], "tr_close"] *= 1.5
        altered = compute_daily_reentry_indicators(changed)
        pd.testing.assert_frame_equal(base.iloc[:-1], altered.iloc[:-1])
        i = 800
        expected = float(base["spy_rv21"].iloc[i - 756 : i].quantile(0.75))
        self.assertAlmostEqual(float(base.loc[i, "lagged_q75"]), expected)

    def test_two_close_recovery_requires_strict_consecutive_closes(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=30)
        close = np.r_[np.ones(21) * 100, [99, 100, 101, 102, 103, 104, 105, 106, 107]]
        frame = compute_daily_reentry_indicators(
            pd.DataFrame({"session_date": dates, "tr_close": close})
        )
        recovery = frame.loc[frame["two_close_recovery"], "session_date"]
        self.assertGreaterEqual(len(recovery), 1)
        for idx in frame.index[frame["two_close_recovery"]]:
            self.assertTrue(bool(frame.loc[idx, "above_sma21"]))
            self.assertTrue(bool(frame.loc[idx - 1, "above_sma21"]))

    def test_state_machine_has_one_week_defense_and_hysteresis(self) -> None:
        dates = pd.bdate_range("2005-01-03", periods=45)
        indicators = pd.DataFrame(
            {
                "session_date": dates,
                "spy_rv21": 0.20,
                "lagged_q75": 0.15,
                "sma21": 100.0,
                "above_sma21": False,
                "two_close_recovery": False,
                "defense_entry_signal": True,
            }
        )
        signals = dates[[9, 14, 19, 24, 29, 34]]
        executions = dates[[10, 15, 20, 25, 30, 35]]
        calendar = pd.DataFrame(
            {
                "week_id": [f"W{i}" for i in range(6)],
                "signal_session": signals,
                "execution_session": executions,
            }
        )
        indicators.loc[indicators["session_date"].isin(signals[[1, 2]]), "above_sma21"] = True
        indicators.loc[indicators["session_date"] == signals[2], "two_close_recovery"] = True
        indicators.loc[indicators["session_date"] == signals[3], ["spy_rv21", "defense_entry_signal"]] = [0.10, False]
        states = build_weekly_reentry_states(calendar, indicators, first_execution_year=2005, last_execution_year=2005)
        self.assertEqual(states.loc[0, "post_state"], DEFENSE)
        self.assertEqual(states.loc[0, "asymmetric_target_spy_weight"], 0.5)
        self.assertEqual(states.loc[1, "post_state"], DEFENSE)
        self.assertEqual(states.loc[2, "post_state"], RECOVERY_UNARMED)
        self.assertEqual(states.loc[2, "asymmetric_target_spy_weight"], 1.0)
        self.assertEqual(states.loc[3, "post_state"], FULL_ARMED)
        self.assertEqual(states.loc[3, "state_event"], "rearm")
        self.assertEqual(states.loc[4, "post_state"], DEFENSE)

    def test_equal_rv_does_not_enter(self) -> None:
        dates = pd.bdate_range("2005-01-03", periods=20)
        signal, execution = dates[-2], dates[-1]
        indicators = pd.DataFrame(
            {
                "session_date": dates,
                "spy_rv21": 0.15,
                "lagged_q75": 0.15,
                "sma21": 100.0,
                "above_sma21": True,
                "two_close_recovery": True,
                "defense_entry_signal": False,
            }
        )
        calendar = pd.DataFrame(
            {"week_id": ["W"], "signal_session": [signal], "execution_session": [execution]}
        )
        states = build_weekly_reentry_states(calendar, indicators, first_execution_year=2005, last_execution_year=2005)
        self.assertEqual(states.loc[0, "post_state"], FULL_ARMED)
        self.assertEqual(states.loc[0, "asymmetric_target_spy_weight"], 1.0)

    def test_future_lockbox_prices_cannot_change_development_state(self) -> None:
        dates = pd.bdate_range("2018-01-01", periods=1100)
        ret = 0.0003 + 0.012 * np.sin(np.arange(len(dates)) / 9)
        market = pd.DataFrame(
            {"session_date": dates, "tr_close": 100 * np.cumprod(1 + ret)}
        )
        indicators = compute_daily_reentry_indicators(market)
        signal_dates = dates[800:1000:5]
        execution_dates = dates[801:1001:5]
        calendar = pd.DataFrame(
            {
                "week_id": [f"W{i}" for i in range(len(signal_dates))],
                "signal_session": signal_dates,
                "execution_session": execution_dates,
            }
        )
        base = build_weekly_reentry_states(
            calendar, indicators, first_execution_year=2021, last_execution_year=2021
        )
        changed = market.copy()
        changed.loc[changed["session_date"] >= "2022-01-03", "tr_close"] *= 2.0
        altered = build_weekly_reentry_states(
            calendar,
            compute_daily_reentry_indicators(changed),
            first_execution_year=2021,
            last_execution_year=2021,
        )
        pd.testing.assert_frame_equal(base, altered)


if __name__ == "__main__":
    unittest.main()
