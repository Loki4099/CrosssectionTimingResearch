from __future__ import annotations

from pathlib import Path
import csv
import hashlib
import json
import tomllib
import unittest

from momentum_reversal.pipelines.round2_protocol import build_round2_fold_manifest


R2A = Path(
    r"C:\Users\17866\QuantWork\MomentumRversionMethod-runtime\data\round2\staging\R2A_DATA\r2a-long-free-20260816-v1"
)
ROOT = Path(__file__).resolve().parents[1]


class Round2PreregistrationTests(unittest.TestCase):
    def test_lock_hashes_and_trial_budget_are_exact(self) -> None:
        lock = json.loads(
            (ROOT / "config/experiments/round2/PREREG_LOCK.json").read_text(
                encoding="utf-8"
            )
        )
        for key in (
            "program",
            "amendment_1",
            "r2b_design",
            "r2c_design",
            "machine_config",
            "fold_manifest",
        ):
            record = lock[key]
            path = ROOT / record["path"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])
        with (ROOT / "experiments/round2_registry.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            arms = list(csv.DictReader(handle))
        self.assertEqual(len(arms), 17)
        self.assertEqual(len({row["arm_id"] for row in arms}), 17)
        self.assertEqual({row["selection_target"] for row in arms}, {"cash_wins_1w"})
        config = tomllib.loads(
            (ROOT / "config/experiments/round2/program.toml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(config["model_protocol"]["total_registered_arms"], 17)
        self.assertFalse(config["features"]["f3_variance_risk_gap_enabled"])
        self.assertFalse(config["features"]["pit_features_enabled"])
        self.assertFalse(config["authorization"]["r2c_development_models"])
        self.assertFalse(config["authorization"]["r2c_lockbox"])


@unittest.skipUnless(R2A.is_dir(), "local immutable R2A candidate is unavailable")
class Round2ProtocolTests(unittest.TestCase):
    def test_absolute_outer_and_lockbox_boundaries(self) -> None:
        result = build_round2_fold_manifest(R2A)
        self.assertEqual(
            result["first_feature_complete"]["signal"], "1994-01-28"
        )
        self.assertEqual(result["development"]["first_outer_year"], 2005)
        self.assertEqual(result["development"]["last_outer_year"], 2021)
        self.assertEqual(len(result["development"]["outer_folds"]), 17)
        self.assertEqual(
            result["mechanical_lockbox"]["start_signal"], "2021-12-31"
        )
        self.assertEqual(
            result["mechanical_lockbox"]["start_execution"], "2022-01-03"
        )
        self.assertEqual(result["mechanical_lockbox"]["weeks"], 235)

    def test_every_outer_fold_has_fixed_purge_and_inner_blocks(self) -> None:
        result = build_round2_fold_manifest(R2A)
        for fold in result["development"]["outer_folds"]:
            self.assertGreaterEqual(fold["train_weeks"], 520)
            self.assertGreaterEqual(len(fold["inner_folds"]), 3)
            self.assertLessEqual(len(fold["inner_folds"]), 5)
            for inner in fold["inner_folds"]:
                self.assertGreaterEqual(inner["train_weeks"], 260)
                self.assertEqual(inner["validation_weeks"], 52)


if __name__ == "__main__":
    unittest.main()
