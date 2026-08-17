from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from momentum_reversal.data.round4_factors import (
    build_spy_volume_scores,
    eligibility_from_weekly,
    lagged_fred_at_signals,
    parse_fred_csv,
)
from momentum_reversal.pipelines.round4_experiments import build_t1_t2, replay_spy_cash


class Round4FactorDataTests(unittest.TestCase):
    def test_volume_scores_follow_frozen_formulas(self) -> None:
        dates = pd.date_range("2020-01-01", periods=300, freq="B")
        raw_close = pd.Series(np.linspace(100.0, 130.0, len(dates)))
        volume = pd.Series(np.linspace(1_000_000.0, 1_500_000.0, len(dates)))
        tr_close = raw_close * (1.0 + 0.01 * np.sin(np.arange(len(dates))))
        market = pd.DataFrame(
            {
                "session_date": dates,
                "raw_close": raw_close,
                "volume_raw": volume,
                "tr_close": tr_close,
            }
        )
        actual = build_spy_volume_scores(market).set_index("session_date")
        dv = pd.Series(raw_close.to_numpy() * volume.to_numpy(), index=dates)
        ret = np.log(pd.Series(tr_close.to_numpy(), index=dates) / pd.Series(tr_close.to_numpy(), index=dates).shift())
        expected_share = (
            (dv * ret.abs()).where(ret < 0, 0).rolling(21, min_periods=21).sum()
            / (dv * ret.abs()).rolling(21, min_periods=21).sum()
        )
        expected_shock = np.log(
            dv.rolling(21, min_periods=21).mean()
            / dv.rolling(252, min_periods=252).median()
        )
        np.testing.assert_allclose(
            actual["down_move_dv_share21"], expected_share, rtol=0, atol=0, equal_nan=True
        )
        np.testing.assert_allclose(
            actual["volume_shock21_252"], expected_shock, rtol=0, atol=0, equal_nan=True
        )

    def test_fred_parser_and_conservative_lag(self) -> None:
        payload = b"observation_date,DGS10,DGS2\n2020-01-02,2.0,1.5\n2020-01-03,2.1,.\n2020-01-06,2.2,1.7\n"
        source = parse_fred_csv(payload, ["DGS10", "DGS2"])
        sessions = pd.DatetimeIndex(pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]))
        calendar = pd.DataFrame(
            {
                "week_id": ["W1"],
                "signal_session": [pd.Timestamp("2020-01-07")],
            }
        )
        weekly = lagged_fred_at_signals(
            source,
            calendar,
            sessions,
            value_columns=["DGS10", "DGS2"],
            lag_sessions=1,
            max_staleness_sessions=5,
        )
        self.assertEqual(weekly.loc[0, "source_observation_date"], pd.Timestamp("2020-01-06"))
        self.assertEqual(weekly.loc[0, "DGS10"], 2.2)
        self.assertEqual(weekly.loc[0, "DGS2"], 1.7)

    def test_fred_lag_preserves_week_with_insufficient_history(self) -> None:
        source = parse_fred_csv(
            b"observation_date,DGS10\n2020-01-02,2.0\n", ["DGS10"]
        )
        calendar = pd.DataFrame(
            {"week_id": ["W0"], "signal_session": [pd.Timestamp("2020-01-02")]}
        )
        weekly = lagged_fred_at_signals(
            source,
            calendar,
            pd.DatetimeIndex([pd.Timestamp("2020-01-02")]),
            value_columns=["DGS10"],
            lag_sessions=1,
            max_staleness_sessions=5,
        )
        self.assertEqual(len(weekly), 1)
        self.assertTrue(np.isnan(weekly.loc[0, "DGS10"]))

    def test_eligibility_excludes_pre_inception_and_rejects_long_gap(self) -> None:
        dates = pd.date_range("2010-01-01", periods=12, freq="W-FRI")
        records = []
        for arm_id, valid in (
            ("GOOD", [False, False, True, True, False, True, True, True, True, True, True, True]),
            ("BAD", [True, True, False, False, False, False, False, True, True, True, True, True]),
        ):
            for date, flag in zip(dates, valid, strict=True):
                records.append(
                    {
                        "signal_session": date,
                        "arm_id": arm_id,
                        "value_available": flag,
                        "source_status": "available",
                    }
                )
        result = eligibility_from_weekly(
            pd.DataFrame(records),
            minimum_weeks=1,
            minimum_years=1,
            max_missing_fraction=0.20,
            max_consecutive_missing=4,
        ).set_index("arm_id")
        self.assertEqual(result.loc["GOOD", "eligibility_status"], "reference_eligible")
        self.assertEqual(result.loc["GOOD", "max_consecutive_missing_weeks"], 1)
        self.assertEqual(result.loc["BAD", "eligibility_status"], "invalid_data")
        self.assertEqual(result.loc["BAD", "max_consecutive_missing_weeks"], 5)

    def test_invalid_source_cannot_become_eligible(self) -> None:
        features = pd.DataFrame(
            {
                "signal_session": pd.date_range("2010-01-01", periods=10, freq="W-FRI"),
                "arm_id": "X",
                "value_available": True,
                "source_status": "invalid_no_vintage_asof",
            }
        )
        result = eligibility_from_weekly(
            features,
            minimum_weeks=1,
            minimum_years=1,
            max_missing_fraction=0.02,
            max_consecutive_missing=4,
        )
        self.assertEqual(result.loc[0, "eligibility_status"], "invalid_data")
        self.assertFalse(bool(result.loc[0, "data_gate_pass"]))

    def test_t1_starts_at_execution_open_and_censors_lockbox(self) -> None:
        dates = pd.to_datetime(["2021-12-27", "2021-12-28", "2021-12-29", "2021-12-30", "2021-12-31", "2022-01-03"])
        market = pd.DataFrame(
            {"session_date": dates, "tr_open": [100, 101, 102, 103, 104, 110]}
        )
        rf = pd.DataFrame(
            {"session_date": dates, "rf_log": 0.0, "rf_simple_decimal": 0.0}
        )
        calendar = pd.DataFrame(
            {
                "week_id": ["W"],
                "signal_session": [pd.Timestamp("2021-12-23")],
                "execution_session": [pd.Timestamp("2021-12-27")],
                "next_1w_execution": [pd.Timestamp("2022-01-03")],
            }
        )
        result = build_t1_t2(market, rf, calendar)
        self.assertFalse(bool(result.loc[0, "target_available"]))
        self.assertTrue(np.isnan(result.loc[0, "fwd_excess_logret_1w"]))

    def test_spy_cash_replay_has_no_leverage(self) -> None:
        dates = pd.date_range("2020-01-02", periods=5, freq="B")
        market = pd.DataFrame(
            {
                "session_date": dates,
                "tr_open": [100, 101, 102, 103, 104],
                "tr_close": [101, 102, 103, 104, 105],
            }
        )
        rf = pd.DataFrame(
            {"session_date": dates, "rf_simple_decimal": 0.0}
        )
        schedule = pd.DataFrame(
            {
                "execution_session": [dates[0], dates[3]],
                "target_spy_weight": [0.5, 1.0],
            }
        )
        result = replay_spy_cash(
            market, rf, schedule, start=dates[0], end=dates[-1], cost_bps=10
        )
        self.assertTrue(result["nav"].gt(0).all())
        self.assertTrue(result["spy_weight"].between(0, 1 + 1e-12).all())
        self.assertTrue(result["cash_weight"].between(0, 1 + 1e-12).all())


if __name__ == "__main__":
    unittest.main()
