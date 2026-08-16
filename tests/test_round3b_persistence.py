from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from momentum_reversal.pipelines.round3b_persistence import (
    build_four_week_attack_targets,
    build_persistence_confirmed_states,
)


class Round3BPersistenceTests(unittest.TestCase):
    def test_four_week_target_uses_execution_opens_and_rf_half_open(self) -> None:
        dates = pd.bdate_range("2020-01-06", periods=25)
        market = pd.DataFrame({"session_date": dates, "tr_open": 100 * 1.001 ** np.arange(25)})
        rf = pd.DataFrame({"session_date": dates, "rf_log": np.log1p(0.0001)})
        calendar = pd.DataFrame({
            "week_id": ["W"], "signal_session": [dates[0]], "execution_session": [dates[1]], "next_4w_execution": [dates[21]]
        })
        out = build_four_week_attack_targets(calendar, market, rf)
        expected = np.log(market.tr_open.iloc[21] / market.tr_open.iloc[1]) - 20 * np.log1p(0.0001)
        self.assertAlmostEqual(float(out.loc[0, "fwd_excess_logret_4w"]), expected)
        self.assertEqual(float(out.loc[0, "sustainable_attack_4w"]), float(expected > 0))

    def test_lockbox_target_is_never_materialized(self) -> None:
        dates = pd.bdate_range("2021-12-31", periods=25)
        market = pd.DataFrame({"session_date": dates, "tr_open": np.linspace(100, 110, 25)})
        rf = pd.DataFrame({"session_date": dates, "rf_log": 0.0})
        calendar = pd.DataFrame({
            "week_id": ["W"], "signal_session": [dates[0]], "execution_session": [dates[1]], "next_4w_execution": [dates[21]]
        })
        out = build_four_week_attack_targets(calendar, market, rf)
        self.assertTrue(bool(out.loc[0, "withheld_lockbox"]))
        self.assertTrue(pd.isna(out.loc[0, "fwd_excess_logret_4w"]))

    def test_pre_lockbox_signal_with_crossing_terminal_is_withheld(self) -> None:
        dates = pd.bdate_range("2021-12-01", periods=30)
        market = pd.DataFrame({"session_date": dates, "tr_open": np.linspace(100, 110, 30)})
        rf = pd.DataFrame({"session_date": dates, "rf_log": 0.0})
        calendar = pd.DataFrame({
            "week_id": ["W"],
            "signal_session": [pd.Timestamp("2021-12-10")],
            "execution_session": [pd.Timestamp("2021-12-13")],
            "next_4w_execution": [pd.Timestamp("2022-01-10")],
        })
        out = build_four_week_attack_targets(calendar, market, rf)
        self.assertTrue(bool(out.loc[0, "withheld_lockbox"]))
        self.assertTrue(pd.isna(out.loc[0, "sustainable_attack_4w"]))

    def test_probability_must_strictly_exceed_base_rate(self) -> None:
        dates = pd.bdate_range("2005-01-03", periods=15)
        price = pd.DataFrame({
            "week_id": ["W1", "W2"], "signal_session": dates[[4, 9]], "execution_session": dates[[5, 10]],
            "spy_rv21": [0.2, 0.2], "lagged_q75": [0.1, 0.1], "sma21": [100.0, 100.0],
            "above_sma21": [True, True], "two_close_recovery": [True, True], "defense_entry_signal": [True, True],
            "pre_state": ["FULL_ARMED", "DEFENSE"], "post_state": ["DEFENSE", "RECOVERY_UNARMED"],
            "state_event": ["enter_defense", "exit_to_recovery"], "asymmetric_target_spy_weight": [0.5, 1.0],
            "symmetric_target_spy_weight": [0.5, 0.5],
        })
        predictions = pd.DataFrame({
            "signal_session": dates[[4, 9]], "p_sustainable_attack_4w": [0.6, 0.6], "train_base_rate": [0.6, 0.6], "model_recovery": [False, False]
        })
        daily = pd.DataFrame({"session_date": dates})
        out = build_persistence_confirmed_states(price, predictions, daily)
        self.assertEqual(out.loc[0, "r3b_target_spy_weight"], 0.5)
        self.assertEqual(out.loc[1, "r3b_target_spy_weight"], 0.5)


if __name__ == "__main__":
    unittest.main()
