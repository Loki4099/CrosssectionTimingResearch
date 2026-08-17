from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tomllib
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


class Round5PreregLockTests(unittest.TestCase):
    def test_lock_and_frozen_files_are_exact(self) -> None:
        lock = json.loads(
            (ROOT / "config/experiments/round5/PREREG_LOCK.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(lock["program_id"], "defense_mae13_single_factor_round5_v1")
        for relative, expected in lock["files"].items():
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, relative)

    def test_factor_registry_is_the_frozen_seventeen(self) -> None:
        registry = pd.read_csv(ROOT / "config/experiments/round5/factor_registry.csv")
        self.assertEqual(len(registry), 17)
        self.assertEqual(registry["arm_id"].nunique(), 17)
        self.assertTrue(registry["eligible"].all())
        self.assertTrue(registry["high_means_defense"].all())
        self.assertFalse(registry["replacement_allowed"].any())
        excluded = {"R4B__HY_OAS_LEVEL", "R4B__HY_OAS_CHANGE21", "R4B__NFCI"}
        self.assertTrue(excluded.isdisjoint(set(registry["arm_id"])))

    def test_target_and_firewall_are_fail_closed(self) -> None:
        program = tomllib.loads(
            (ROOT / "config/experiments/round5/program.toml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(program["target"]["horizon_scheduled_weeks"], 13)
        self.assertEqual(program["target"]["deadzone_simple_decimal"], 0.05)
        self.assertEqual(program["development"]["maximum_target_signal"], "2021-09-24")
        self.assertFalse(program["authorization"]["lockbox"])
        self.assertFalse(program["authorization"]["models"])
        self.assertFalse(program["authorization"]["factor_additions"])
        self.assertFalse(program["authorization"]["position_search"])

    def test_parent_round4_lock_and_r4a_anchor_are_exact(self) -> None:
        program = tomllib.loads(
            (ROOT / "config/experiments/round5/program.toml").read_text(
                encoding="utf-8"
            )
        )
        actual = hashlib.sha256(
            (ROOT / "config/experiments/round4/PREREG_LOCK.json").read_bytes()
        ).hexdigest()
        self.assertEqual(actual, program["parent"]["r4_prereg_lock_sha256"])
        self.assertEqual(
            program["parent"]["r4a_manifest_sha256"],
            "1b0b27f689bb3966a34ca94076467be7dad209afa8910a516827b0419514dd7f",
        )


if __name__ == "__main__":
    unittest.main()
