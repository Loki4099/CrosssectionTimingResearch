from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from momentum_reversal.pipelines.xa01_experiments import _bh, _block_sign_p


class XA01ExperimentTests(unittest.TestCase):
    def test_bh_is_monotone_in_p_order(self) -> None:
        p = pd.Series([0.04, 0.001, 0.02])
        q = _bh(p)
        ordered = pd.DataFrame({"p": p, "q": q}).sort_values("p")
        self.assertTrue(ordered["q"].is_monotonic_increasing)
        self.assertTrue((q >= p).all())

    def test_block_sign_test_respects_direction(self) -> None:
        positive = np.linspace(0.01, 0.20, 40)
        negative = -positive
        self.assertLess(_block_sign_p(positive, 4), 0.05)
        self.assertGreater(_block_sign_p(negative, 4), 0.95)


if __name__ == "__main__":
    unittest.main()
