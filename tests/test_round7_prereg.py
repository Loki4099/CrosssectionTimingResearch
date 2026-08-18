from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tomllib
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "config/experiments/round7/PREREG_LOCK.json"
FROZEN_R7_LOCK_SHA256 = "794760ae76a2bd07f79bdf5fe7a532c33255bcf743e9576b52a4e8ba6f37e4ae"


class Round7PreregTests(unittest.TestCase):
    def test_lock_self_hash_and_members(self) -> None:
        self.assertNotEqual(FROZEN_R7_LOCK_SHA256, "TO_BE_FILLED_AFTER_FREEZE")
        self.assertEqual(hashlib.sha256(LOCK.read_bytes()).hexdigest(), FROZEN_R7_LOCK_SHA256)
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        for relative, expected in lock["files"].items():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), expected)

    def test_registry_counts_and_roles(self) -> None:
        bundles = pd.read_csv(ROOT / "config/experiments/round7/feature_bundles.csv")
        recipes = pd.read_csv(ROOT / "config/experiments/round7/model_recipes.csv")
        processes = pd.read_csv(ROOT / "config/experiments/round7/process_registry.csv")
        attacks = pd.read_csv(ROOT / "config/experiments/round7/attack_registry.csv")
        events = pd.read_csv(ROOT / "config/experiments/round7/event_registry.csv")
        self.assertEqual((len(bundles), len(recipes), len(processes), len(attacks)), (9, 12, 27, 3))
        self.assertTrue(bundles.feature_arm_ids.str.contains("R4B__RSP_SPY63", regex=False).all())
        self.assertEqual(attacks.formal_hypothesis.sum(), 1)
        self.assertEqual(attacks.loc[attacks.formal_hypothesis, "attack_process_id"].item(), "AX02_RSP_A4_MONOTONE")
        self.assertEqual(events.episode_id.tolist(), ["E014", "E017", "E022", "E024", "E025", "E028"])

    def test_folds_and_firewall(self) -> None:
        folds = json.loads((ROOT / "config/experiments/round7/folds.json").read_text(encoding="utf-8"))
        self.assertEqual(len(folds["outer_folds"]), 8)
        self.assertEqual(sum(x["test_weeks"] for x in folds["outer_folds"]), 404)
        self.assertEqual(folds["outer_folds"][-1]["test_end_signal"], "2021-09-24")
        self.assertTrue(all(len(x["inner_folds"]) >= 3 for x in folds["outer_folds"]))
        program = tomllib.loads((ROOT / "config/experiments/round7/program.toml").read_text(encoding="utf-8"))
        self.assertFalse(program["authorization"]["lockbox"])
        self.assertFalse(program["authorization"]["final_state_machine"])
        self.assertFalse(program["authorization"]["strategy_nav"])

    def test_lightgbm_invalid_objective_is_excluded(self) -> None:
        recipes = pd.read_csv(ROOT / "config/experiments/round7/model_recipes.csv")
        tree = recipes[recipes.family.eq("monotone_lightgbm")]
        self.assertTrue(tree.objective.eq("regression").all())
        self.assertFalse(tree.objective.eq("regression_l1").any())
        self.assertTrue(recipes.capacity_rank.notna().all())


if __name__ == "__main__":
    unittest.main()
