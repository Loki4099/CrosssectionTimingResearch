from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from momentum_reversal.pipelines.xa02_experiments import (
    _bootstrap_did, _causal_percentile, _hac_fit, _joint_episode_count,
    _nested_topk_audit, _tercile,
)


class XA02ExperimentTests(unittest.TestCase):
    def test_causal_percentile_uses_strictly_past_values_and_midrank(self) -> None:
        series = pd.Series([1.0, 2.0, 2.0, 4.0, 3.0])
        result = _causal_percentile(series, 3)
        self.assertTrue(result.iloc[:3].isna().all())
        self.assertAlmostEqual(result.iloc[3], 1.0)
        self.assertAlmostEqual(result.iloc[4], 0.75)

    def test_tercile_boundaries_are_frozen(self) -> None:
        result = _tercile(pd.Series([1 / 3, 2 / 3, .9, np.nan]))
        self.assertEqual(result.iloc[:3].tolist(), ["low", "mid", "high"])
        self.assertIsNone(result.iloc[3])

    def test_hac_detects_a_large_state_difference(self) -> None:
        y = np.r_[np.zeros(40), np.ones(40), np.full(40, 3.0)]
        bins = np.array(["mid"] * 40 + ["low"] * 40 + ["high"] * 40)
        x = np.column_stack([np.ones(len(y)), bins == "low", bins == "high"]).astype(float)
        beta, _, p = _hac_fit(y, x, np.ones(len(y), dtype=bool), 2, [1, 2])
        self.assertAlmostEqual(beta[1], 1.0)
        self.assertAlmostEqual(beta[2], 3.0)
        self.assertLess(p, .01)

    def test_did_bootstrap_has_expected_sign(self) -> None:
        a = np.array((["low"] * 20 + ["high"] * 20) * 2)
        b = np.array(["low"] * 40 + ["high"] * 40)
        y = ((a == "high") & (b == "high")).astype(float)
        low, high = _bootstrap_did(y, a, b, 2, 200, 20260821)
        self.assertGreater(low, 0)
        self.assertGreater(high, 0)

    def test_joint_episode_count_respects_gaps_in_the_schedule(self) -> None:
        frame = pd.DataFrame({"left": ["low", "low", "low"],
                              "right": ["high", "high", "high"]}, index=[0, 1, 4])
        self.assertEqual(_joint_episode_count(frame, "left", "right"), 2)

    def test_topk_audit_requires_exact_nested_sets(self) -> None:
        rows = []
        for width in (5, 10, 20, 50):
            for sid in range(width):
                rows.append({"factor_id": "F", "frequency": "weekly",
                             "signal_date": pd.Timestamp("2020-01-03"),
                             "top_k": width, "sid": f"S{sid:02d}"})
        result = _nested_topk_audit(pd.DataFrame(rows))
        self.assertTrue(result["nested_passed"].all())


if __name__ == "__main__":
    unittest.main()
