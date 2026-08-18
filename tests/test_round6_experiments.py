from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from momentum_reversal.pipelines.round6_experiments import _bh_adjust, build_attack_scores


class Round6ExperimentTests(unittest.TestCase):
    def test_delta_uses_full_calendar_before_missing_filter(self) -> None:
        dates = pd.date_range("2020-01-03", periods=7, freq="W-FRI")
        features = pd.DataFrame({"week_id": [f"W{i}" for i in range(7)], "signal_session": dates,
            "source_arm_id": "SRC", "source_defense_score": [10.0, np.nan, 8.0, 7.0, 6.0, 5.0, 4.0]})
        registry = pd.DataFrame([{"attack_arm_id": "DELTA", "source_arm_id": "SRC", "transform_kind": "calendar_delta", "lag_scheduled_weeks": 4}])
        result = build_attack_scores(features, registry)
        self.assertTrue(result.loc[:3, "attack_score"].isna().all())
        self.assertEqual(result.loc[4, "attack_score"], 4.0)
        self.assertTrue(np.isnan(result.loc[5, "attack_score"]))
        self.assertEqual(result.loc[6, "attack_score"], 4.0)

    def test_level_is_mechanical_negative(self) -> None:
        features = pd.DataFrame({"week_id": ["W1"], "signal_session": ["2020-01-03"], "source_arm_id": ["SRC"], "source_defense_score": [2.5]})
        registry = pd.DataFrame([{"attack_arm_id": "LEVEL", "source_arm_id": "SRC", "transform_kind": "negate_level", "lag_scheduled_weeks": 0}])
        self.assertEqual(build_attack_scores(features, registry).loc[0, "attack_score"], -2.5)

    def test_bh_is_monotone_in_rank(self) -> None:
        p = np.array([.04, .001, .03, .9])
        q = _bh_adjust(p)
        order = np.argsort(p)
        self.assertTrue(np.all(np.diff(q[order]) >= -1e-12))
        self.assertTrue(np.all(q >= p))


if __name__ == "__main__":
    unittest.main()
