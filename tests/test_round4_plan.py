from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLAN_LOCK = ROOT / "config/experiments/round4/PLAN_LOCK.json"
FROZEN_PLAN_LOCK_SHA256 = (
    "3cd84a0ffa762648ffc023267a56999fe439e758e74411e00871aef925393d40"
)


class Round4PlanLockTests(unittest.TestCase):
    def test_plan_lock_and_every_frozen_file_are_exact(self) -> None:
        self.assertEqual(
            hashlib.sha256(PLAN_LOCK.read_bytes()).hexdigest(),
            FROZEN_PLAN_LOCK_SHA256,
        )
        lock = json.loads(PLAN_LOCK.read_text(encoding="utf-8"))
        self.assertEqual(lock["program_id"], "defense_factor_audit_round4_v1")
        for relative_path, expected_hash in lock["files"].items():
            actual_hash = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
            self.assertEqual(actual_hash, expected_hash, relative_path)

    def test_catalog_and_stage_counts_are_bounded_and_unique(self) -> None:
        with (ROOT / "config/experiments/round4/factor_catalog_plan.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            arms = list(csv.DictReader(handle))
        with (ROOT / "experiments/round4_groups.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            batches = list(csv.DictReader(handle))
        with (ROOT / "experiments/round4_registry.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            registry = list(csv.DictReader(handle))

        self.assertEqual(len(arms), 20)
        self.assertEqual(len({row["arm_id"] for row in arms}), 20)
        self.assertEqual(sum(row["legacy_anchor"] == "true" for row in arms), 10)
        self.assertTrue(all(row["replacement_allowed"] == "false" for row in arms))
        self.assertEqual(len(batches), 4)
        self.assertEqual(len({row["batch_id"] for row in batches}), 4)
        self.assertEqual(len(registry), 4)
        self.assertEqual(len({row["experiment_id"] for row in registry}), 4)

    def test_only_r4a_data_work_is_authorized(self) -> None:
        config = tomllib.loads(
            (ROOT / "config/experiments/round4/plan.toml").read_text(
                encoding="utf-8"
            )
        )
        data_config = tomllib.loads(
            (ROOT / "config/data/round4/R4A_FACTOR_DATA.toml").read_text(
                encoding="utf-8"
            )
        )
        authorization = config["authorization"]
        data_authorization = data_config["authorization"]
        self.assertTrue(authorization["data"])
        self.assertTrue(data_authorization["network_acquisition"])
        self.assertTrue(data_authorization["normalization"])
        self.assertTrue(data_authorization["feature_input_construction"])
        for forbidden in (
            "target_materialization",
            "signal_evaluation",
            "strategy_nav",
            "event_outcomes",
            "lockbox",
            "mom255_transfer",
            "models",
            "position_search",
        ):
            self.assertFalse(authorization[forbidden], forbidden)
            self.assertFalse(data_authorization[forbidden], forbidden)

        with (ROOT / "experiments/round4_registry.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            registry = {row["experiment_id"]: row for row in csv.DictReader(handle)}
        r4a = registry["R4A__FREE_FACTOR_DATA"]
        self.assertEqual(r4a["development_authorized"], "true")
        self.assertEqual(r4a["target_materialization_authorized"], "false")
        self.assertEqual(r4a["signal_evaluation_authorized"], "false")
        self.assertEqual(r4a["event_outcomes_authorized"], "false")
        self.assertEqual(r4a["lockbox_authorized"], "false")
        self.assertEqual(r4a["mom255_transfer_authorized"], "false")
        prereg = ROOT / "config/experiments/round4/PREREG_LOCK.json"
        if prereg.exists():
            self.assertTrue(
                (ROOT / "config/experiments/round4/R4A_ACCEPTANCE.json").is_file()
            )


if __name__ == "__main__":
    unittest.main()
