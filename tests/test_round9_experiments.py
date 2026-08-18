from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from momentum_reversal.backtest import BaselineBacktester
from momentum_reversal.pipelines.round9_experiments import simulate_union_event_book
from tests.synthetic import StaticMembership, make_prices


class Round9UnionEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prices = make_prices(sessions=12, assets=3)
        self.sessions = pd.DatetimeIndex(self.prices.index.get_level_values("date").unique())
        self.engine = BaselineBacktester(
            self.prices,
            StaticMembership(("S000", "S001", "S002")),
            sessions=self.sessions,
        )
        self.base = pd.DataFrame(
            {
                "execution_date": [self.sessions[0], self.sessions[0], self.sessions[6], self.sessions[6]],
                "sid": ["S000", "S001", "S001", "S002"],
                "target_weight": [0.5, 0.5, 0.5, 0.5],
            }
        )
        self.overlay = pd.Series([1.0, 0.5, 1.0], index=[self.sessions[0], self.sessions[3], self.sessions[9]])
        self.rf = pd.Series(0.0, index=self.sessions)

    def test_union_calendar_does_not_rerank_on_overlay_only_day(self) -> None:
        result = simulate_union_event_book(
            engine=self.engine,
            base_targets=self.base,
            overlay_schedule=self.overlay,
            risk_free_daily=self.rf,
            start=self.sessions[0],
            end=self.sessions[-1],
            cost_bps=10,
            path_type="p00_overlay",
        )
        events = result["events"]
        self.assertEqual(len(events), 4)
        self.assertEqual(events.iloc[0].event_kind, "base_and_overlay")
        overlay_only = events[events.event_kind.eq("overlay")]
        self.assertTrue((~overlay_only.base_reranked).all())
        targets = result["targets"]
        day = self.sessions[3]
        self.assertEqual(set(targets.loc[targets.execution_date.eq(day), "sid"]), {"S000", "S001"})
        self.assertAlmostEqual(targets.loc[targets.execution_date.eq(day), "target_weight"].sum(), 0.5)

    def test_naked_ignores_overlay_only_dates(self) -> None:
        result = simulate_union_event_book(
            engine=self.engine,
            base_targets=self.base,
            overlay_schedule=self.overlay,
            risk_free_daily=self.rf,
            start=self.sessions[0],
            end=self.sessions[-1],
            cost_bps=0,
            path_type="naked",
        )
        self.assertEqual(result["events"].execution_date.tolist(), [self.sessions[0], self.sessions[6]])
        self.assertTrue(result["events"].target_allocation.eq(1.0).all())

    def test_future_price_change_does_not_change_prior_ledger(self) -> None:
        original = simulate_union_event_book(
            engine=self.engine,
            base_targets=self.base,
            overlay_schedule=self.overlay,
            risk_free_daily=self.rf,
            start=self.sessions[0],
            end=self.sessions[-1],
            cost_bps=5,
            path_type="p00_overlay",
        )
        changed = self.prices.copy()
        cutoff = self.sessions[6]
        changed.loc[changed.index.get_level_values("date") > cutoff, ["tr_open", "tr_close"]] *= 3.0
        changed_engine = BaselineBacktester(
            changed,
            StaticMembership(("S000", "S001", "S002")),
            sessions=self.sessions,
        )
        mutated = simulate_union_event_book(
            engine=changed_engine,
            base_targets=self.base,
            overlay_schedule=self.overlay,
            risk_free_daily=self.rf,
            start=self.sessions[0],
            end=self.sessions[-1],
            cost_bps=5,
            path_type="p00_overlay",
        )
        left = original["nav"].loc[original["nav"].date.le(cutoff), "nav"].to_numpy()
        right = mutated["nav"].loc[mutated["nav"].date.le(cutoff), "nav"].to_numpy()
        np.testing.assert_allclose(left, right, rtol=0, atol=0)


if __name__ == "__main__":
    unittest.main()
