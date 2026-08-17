from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tomllib
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


class Round4PreregLockTests(unittest.TestCase):
    def test_prereg_lock_and_files_are_exact(self) -> None:
        path = ROOT / "config/experiments/round4/PREREG_LOCK.json"
        lock = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(lock["program_id"], "defense_factor_audit_round4_v1")
        for relative, expected in lock["files"].items():
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, relative)

    def test_resolved_registry_and_authorization(self) -> None:
        registry = pd.read_csv(
            ROOT / "config/experiments/round4/factor_registry_resolved.csv"
        )
        self.assertEqual(len(registry), 20)
        self.assertEqual(registry["arm_id"].nunique(), 20)
        self.assertEqual(int(registry["reference_eligible" == registry["eligibility_status"]].shape[0]), 17)
        self.assertEqual(int(registry["invalid_data" == registry["eligibility_status"]].shape[0]), 3)
        program = tomllib.loads(
            (ROOT / "config/experiments/round4/program.toml").read_text(encoding="utf-8")
        )
        self.assertTrue(program["authorization"]["target_materialization"])
        self.assertTrue(program["authorization"]["event_outcomes"])
        self.assertFalse(program["authorization"]["lockbox"])
        self.assertFalse(program["authorization"]["models"])
        self.assertFalse(program["authorization"]["mom255_transfer"])

    def test_r4a_acceptance_preserves_firewall(self) -> None:
        acceptance = json.loads(
            (ROOT / "config/experiments/round4/R4A_ACCEPTANCE.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(acceptance["double_build_canonical_equal"])
        self.assertTrue(acceptance["immutable_rerun_rejected"])
        for key in (
            "targets_materialized",
            "signal_evaluation_run",
            "strategy_nav_run",
            "event_outcomes_run",
            "lockbox_read",
        ):
            self.assertFalse(acceptance[key])


if __name__ == "__main__":
    unittest.main()
