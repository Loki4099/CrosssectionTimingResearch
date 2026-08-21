from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from momentum_reversal.pipelines.xa03_experiments import (
    _bh,
    _derived_seed,
    _engine_period_frame,
    _common_ew_period_ledger,
    _noncircular_mbb_se,
    centered_cross_sectional_rank,
)


class XA03ExperimentTests(unittest.TestCase):
    def test_centered_rank_is_symmetric_and_keeps_ties(self) -> None:
        values = pd.Series([4.0, 1.0, 1.0, 3.0, np.nan])
        result = centered_cross_sectional_rank(values)
        self.assertTrue(np.isnan(result.iloc[4]))
        self.assertAlmostEqual(result.iloc[1], result.iloc[2])
        self.assertAlmostEqual(float(result.dropna().min()), -2.0 / 3.0)
        self.assertEqual(float(result.dropna().max()), 1.0)

    def test_centered_rank_respects_explicit_validity(self) -> None:
        result = centered_cross_sectional_rank(
            pd.Series([1.0, 2.0, 3.0]), pd.Series([True, False, True])
        )
        self.assertEqual(result.tolist()[0], -1.0)
        self.assertTrue(np.isnan(result.tolist()[1]))
        self.assertEqual(result.tolist()[2], 1.0)

    def test_bh_is_monotone_in_p_order(self) -> None:
        p = pd.Series([0.04, 0.001, 0.03, 0.2])
        q = _bh(p)
        ordered = pd.DataFrame({"p": p, "q": q}).sort_values("p")
        self.assertTrue(ordered["q"].is_monotonic_increasing)

    def test_one_se_bootstrap_and_seed_are_deterministic(self) -> None:
        values = np.linspace(-0.01, 0.02, 40)
        seed = _derived_seed(20260821, "inner_one_se", "P", "weekly", 2018)
        self.assertEqual(seed, _derived_seed(20260821, "inner_one_se", "P", "weekly", 2018))
        first = _noncircular_mbb_se(values, 13, 200, seed)
        second = _noncircular_mbb_se(values, 13, 200, seed)
        self.assertEqual(first, second)
        self.assertGreater(first, 0.0)

    def test_engine_period_frame_uses_pretrade_nav_and_exact_terminal_cost(self) -> None:
        result = SimpleNamespace(rebalances=pd.DataFrame({
            "signal_date": pd.to_datetime(["2020-01-03", "2020-01-10"]),
            "execution_date": pd.to_datetime(["2020-01-06", "2020-01-13"]),
            "pretrade_nav": [1.0, 1.10],
            "l1_turnover": [1.0, 0.5],
            "filled_selected_count": [2, 2],
            "unfilled_selected_count": [0, 0],
        }))
        proxy = pd.DataFrame({
            "signal_date": pd.to_datetime(["2020-01-03", "2020-01-10"]),
            "execution_date": pd.to_datetime(["2020-01-06", "2020-01-13"]),
            "label_end_execution_date": pd.to_datetime(["2020-01-13", "2020-01-21"]),
            "gross_return": [0.09, 0.20],
            "selected_count": [2, 2],
        })
        frame = _engine_period_frame(result, proxy, 10)
        self.assertAlmostEqual(float(frame.iloc[0]["net_return"]), 0.10)
        self.assertAlmostEqual(
            float(frame.iloc[1]["net_return"]), (1.0 - 0.5 * 10 / 10_000) * 1.20 - 1.0
        )
        self.assertEqual(frame["selected_count"].tolist(), [2, 2])

    def test_common_control_keeps_execution_interval_metadata(self) -> None:
        target = pd.DataFrame({
            "signal_date": pd.to_datetime(["2020-01-03", "2020-01-03"]),
            "execution_date": pd.to_datetime(["2020-01-06", "2020-01-06"]),
            "label_end_execution_date": pd.to_datetime(["2020-01-13", "2020-01-13"]),
            "sid": ["A", "B"],
            "forward_total_return": [0.10, -0.02],
            "target_valid": [True, True],
        })
        frame, _ = _common_ew_period_ledger(target)
        self.assertEqual(frame.loc[0, "execution_date"], pd.Timestamp("2020-01-06"))
        self.assertEqual(frame.loc[0, "label_end_execution_date"], pd.Timestamp("2020-01-13"))
        self.assertEqual(int(frame.loc[0, "selected_count"]), 2)


if __name__ == "__main__":
    unittest.main()
