from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from momentum_reversal.pipelines.round2_signals import (
    CORE_FEATURES,
    TARGET_COLUMNS,
    build_weekly_development_targets,
    build_weekly_features,
)


R2A = Path(
    r"C:\Users\17866\QuantWork\MomentumRversionMethod-runtime\data\round2\staging\R2A_DATA\r2a-long-free-20260816-v1"
)


@unittest.skipUnless(R2A.is_dir(), "local immutable R2A candidate is unavailable")
class Round2SignalConstructionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.market = pd.read_parquet(R2A / "curated/market_daily.parquet")
        cls.rf = pd.read_parquet(R2A / "curated/risk_free_daily.parquet")
        cls.calendar = pd.read_parquet(R2A / "curated/decision_calendar.parquet")

    def test_feature_boundary_and_current_close_causality(self) -> None:
        features = build_weekly_features(self.market, self.calendar)
        first = features.loc[features.feature_complete].iloc[0]
        self.assertEqual(first.signal_session, pd.Timestamp("1994-01-28"))
        self.assertTrue(np.isfinite(first[list(CORE_FEATURES)].to_numpy(float)).all())
        market = self.market.copy()
        cutoff = pd.Timestamp("2010-01-29")
        baseline = features.loc[features.signal_session.le(cutoff), list(CORE_FEATURES)]
        market.loc[pd.to_datetime(market.session_date).gt(cutoff), "tr_close"] *= 9.0
        changed = build_weekly_features(market, self.calendar)
        pd.testing.assert_frame_equal(
            baseline.reset_index(drop=True),
            changed.loc[changed.signal_session.le(cutoff), list(CORE_FEATURES)].reset_index(
                drop=True
            ),
        )

    def test_targets_use_execution_open_and_withhold_lockbox(self) -> None:
        targets = build_weekly_development_targets(
            self.market,
            self.rf,
            self.calendar,
            lockbox_start_signal=pd.Timestamp("2021-12-31"),
        )
        lockbox = targets.withheld_lockbox
        self.assertEqual(int(lockbox.sum()), 235)
        self.assertTrue(targets.loc[lockbox, list(TARGET_COLUMNS)].isna().all().all())
        development = targets.target_available
        self.assertTrue(
            targets.loc[
                development, ["fwd_excess_logret_1w", "cash_wins_1w"]
            ].notna().all().all()
        )
        development_t3 = targets.t3_available
        self.assertTrue(
            targets.loc[development_t3, "fwd_worst_excess_4w"].notna().all()
        )
        self.assertTrue(
            (targets.loc[development_t3, "fwd_worst_excess_4w"] <= 0).all()
        )
        crossing = targets.loc[
            targets.signal_session.lt(pd.Timestamp("2021-12-31"))
            & targets.next_4w_execution.gt(pd.Timestamp("2021-12-31"))
        ]
        self.assertTrue(crossing["fwd_worst_excess_4w"].isna().all())
        row = targets.loc[targets.signal_session.eq(pd.Timestamp("2020-02-21"))].iloc[0]
        self.assertEqual(row.execution_session, pd.Timestamp("2020-02-24"))


if __name__ == "__main__":
    unittest.main()
