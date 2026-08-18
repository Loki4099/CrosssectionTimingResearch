from __future__ import annotations
import numpy as np
import pandas as pd
import unittest
from momentum_reversal.pipelines.round7_experiments import FEATURE_IDS, TrainOnlyTransform, _bh_adjust, _fit_risk_model, _one_se_select

class Round7ExperimentTests(unittest.TestCase):
    def test_train_only_transform_is_finite(self) -> None:
        x = np.array([[1., np.nan], [2., 3.], [100., 4.]])
        transform = TrainOnlyTransform().fit(x)
        self.assertTrue(np.isfinite(transform.transform(x)).all())

    def test_monotone_lightgbm_is_repeatable_and_monotone(self) -> None:
        x = np.arange(200., dtype=float).reshape(-1, 1); y = np.sqrt(x[:, 0])
        spec = pd.Series({"family": "monotone_lightgbm", "max_depth": 2, "num_leaves": 4, "n_estimators": 50,
                          "learning_rate": .05, "min_child_samples": 52})
        a = _fit_risk_model(x, y, spec).predict(x)
        b = _fit_risk_model(x, y, spec).predict(x)
        self.assertTrue(np.array_equal(a, b)); self.assertTrue((np.diff(a) >= -1e-12).all())

    def test_one_se_prefers_lower_capacity(self) -> None:
        recipes = pd.DataFrame({"capacity_rank": [1, 2]}, index=["a", "b"])
        alternating = np.tile([.98, 1.02], 26)
        losses = {"a": [np.ones(52)], "b": [alternating]}
        selected, _ = _one_se_select(["a", "b"], losses, recipes)
        self.assertEqual(selected, "a")

    def test_bh_monotone(self) -> None:
        q = _bh_adjust(np.array([.001, .02, .5]))
        self.assertTrue(np.all(q >= np.array([.001, .02, .5])))

    def test_feature_tuple_is_used_as_explicit_column_list(self) -> None:
        frame = pd.DataFrame({name: [1.0] for name in FEATURE_IDS})
        self.assertEqual(frame[list(FEATURE_IDS)].shape, (1, 5))

if __name__ == "__main__": unittest.main()
