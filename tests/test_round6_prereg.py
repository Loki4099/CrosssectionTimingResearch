from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / "config/experiments/round6/program.toml"
FACTOR_REGISTRY = ROOT / "config/experiments/round6/factor_registry.csv"
TARGET_REGISTRY = ROOT / "config/experiments/round6/target_registry.csv"
PARENT_ACCEPTANCE = ROOT / "config/experiments/round6/PARENT_ACCEPTANCE.json"
PREREG_LOCK = ROOT / "config/experiments/round6/PREREG_LOCK.json"
R5_LOCK = ROOT / "config/experiments/round5/PREREG_LOCK.json"

# The test file is intentionally outside the lock, avoiding a self-reference
# cycle while still detecting replacement of the complete frozen lock.
FROZEN_R6_PREREG_LOCK_SHA256 = (
    "af00cccc159c3763671fded835a28eb3afffb3f0eac00a291ac7415a672d9a23"
)

EXPECTED_CURRENT_R5_LOCK_SHA256 = (
    "cc85d9a99b08bc8773096ec8c36b41cbd2e67c2ac844dae20fbce8a23bd9522d"
)
EXPECTED_RUNTIME_R5_LOCK_SHA256 = (
    "0d007b6c093f86a8eb93448531e1145c42d275d02b86116c984494d7485b607f"
)
EXPECTED_BATCH_IDS = (
    "R6A_ATTACK4_TARGET",
    "R6B_ATTACK4_SINGLE_FACTOR",
    "R6C_ATTACK4_ROLE_PROXY",
    "R6D_ATTACK4_ROBUSTNESS",
)
EXPECTED_MEMBERS = tuple(
    sorted(
        (
            "config/experiments/round6/PARENT_ACCEPTANCE.json",
            "config/experiments/round6/factor_registry.csv",
            "config/experiments/round6/program.toml",
            "config/experiments/round6/target_registry.csv",
            "docs/20_experiments/R6A_attack4_target/design.md",
            "docs/20_experiments/R6B_attack4_single_factor/design.md",
            "docs/20_experiments/R6C_attack4_role_proxy/design.md",
            "docs/20_experiments/R6D_attack4_robustness/design.md",
            "docs/30_defense_attack_dual_head_route_v1.md",
            "docs/31_round6_attack4_single_factor_program_v1.md",
            "experiments/round6_groups.csv",
            "experiments/round6_registry.csv",
            "scripts/build_round6_prereg_lock.py",
        )
    )
)
DELTA4_DEFINITION = (
    "source_defense_score[t-4_scheduled_weeks]-source_defense_score[t]; "
    "join endpoints on the full decision calendar before missing-value filtering"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise AssertionError(f"expected true/false, got {value!r}")
    return normalized == "true"


class Round6DraftSpecificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.program = tomllib.loads(PROGRAM.read_text(encoding="utf-8"))

    def test_registered_factors_are_exactly_twenty_as_seventeen_plus_three(self) -> None:
        rows = _csv_rows(FACTOR_REGISTRY)
        self.assertEqual(len(rows), 20)
        self.assertEqual(len({row["attack_arm_id"] for row in rows}), 20)
        levels = [row for row in rows if row["transform_kind"] == "negate_level"]
        deltas = [row for row in rows if row["transform_kind"] == "calendar_delta"]
        self.assertEqual((len(levels), len(deltas)), (17, 3))
        self.assertEqual(
            (
                self.program["factors"]["registered_arms"],
                self.program["factors"]["level_arms"],
                self.program["factors"]["delta4_arms"],
            ),
            (20, 17, 3),
        )
        self.assertTrue(all(_bool(row["high_means_attack"]) for row in rows))
        self.assertTrue(all(not _bool(row["replacement_allowed"]) for row in rows))

    def test_level_and_delta_definitions_are_frozen(self) -> None:
        rows = _csv_rows(FACTOR_REGISTRY)
        levels = [row for row in rows if row["transform_kind"] == "negate_level"]
        deltas = [row for row in rows if row["transform_kind"] == "calendar_delta"]
        for row in levels:
            self.assertEqual(row["lag_scheduled_weeks"], "0", row["attack_arm_id"])
            self.assertEqual(
                row["attack_score_definition"],
                "-source_defense_score",
                row["attack_arm_id"],
            )
        for row in deltas:
            self.assertEqual(row["lag_scheduled_weeks"], "4", row["attack_arm_id"])
            self.assertEqual(
                row["attack_score_definition"],
                DELTA4_DEFINITION,
                row["attack_arm_id"],
            )
        factors = self.program["factors"]
        self.assertEqual(
            factors["delta4_attack_transform"],
            "defense_score_at_t_minus_4_scheduled_weeks_minus_defense_score_at_t",
        )
        self.assertEqual(
            factors["delta4_calendar"],
            "complete_frozen_decision_calendar_before_missing_filter",
        )
        self.assertEqual(
            factors["delta4_missing_rule"],
            "either_endpoint_missing_means_missing_no_backfill",
        )

    def test_role_permissions_and_no_top_k_union_are_frozen(self) -> None:
        rows = _csv_rows(FACTOR_REGISTRY)
        direct = [row for row in rows if _bool(row["direct_eligible"])]
        conditional = [row for row in rows if _bool(row["conditional_eligible"])]
        context = [row for row in rows if _bool(row["context_only"])]
        self.assertEqual((len(direct), len(conditional), len(context)), (12, 6, 2))
        self.assertTrue(all(not _bool(row["direct_eligible"]) for row in context))
        self.assertTrue(all(not _bool(row["conditional_eligible"]) for row in context))
        model_input = self.program["qualification"]["model_input"]
        self.assertEqual(
            model_input["formula"],
            "robust_direct_attack OR economic_reference OR conditional_eligible",
        )
        self.assertTrue(model_input["no_top_k"])
        self.assertTrue(model_input["all_qualified_continue"])

    def test_primary_and_veto_inference_are_not_interchangeable(self) -> None:
        inference = self.program["inference"]
        self.assertEqual(inference["block_weeks"], 4)
        self.assertEqual(inference["veto_sensitivity_block_weeks"], 8)
        self.assertEqual(inference["fdr_method"], "benjamini_hochberg")
        self.assertEqual(inference["fdr_level"], 0.10)
        self.assertEqual(inference["alert_budget"], 0.25)

    def test_continuous_target_is_primary_and_other_targets_are_guardrails(self) -> None:
        rows = {row["target_id"]: row for row in _csv_rows(TARGET_REGISTRY)}
        self.assertEqual(set(rows), {"A4_CONTINUOUS", "A4_POSITIVE", "W4_VETO"})
        self.assertTrue(_bool(rows["A4_CONTINUOUS"]["primary"]))
        self.assertTrue(_bool(rows["A4_CONTINUOUS"]["selection_authority"]))
        self.assertFalse(_bool(rows["A4_POSITIVE"]["primary"]))
        self.assertTrue(_bool(rows["A4_POSITIVE"]["diagnostic_guardrail"]))
        self.assertFalse(_bool(rows["A4_POSITIVE"]["selection_authority"]))
        self.assertTrue(_bool(rows["W4_VETO"]["veto_only"]))
        self.assertFalse(_bool(rows["W4_VETO"]["selection_authority"]))

        target = self.program["target"]
        self.assertEqual(target["primary_name"], "fwd_excess_logret_4w")
        self.assertEqual(target["binary_name"], "sustainable_attack_4w")
        self.assertTrue(target["binary_diagnostic_only"])
        self.assertFalse(target["binary_can_promote"])
        self.assertEqual(target["worst_path_name"], "fwd_worst_excess_4w")
        self.assertTrue(target["worst_path_guardrail_only"])
        self.assertFalse(target["worst_path_can_promote"])
        self.assertFalse(target["alternative_target_selection"])

    def test_authorization_is_development_only_and_fail_closed(self) -> None:
        authorization = self.program["authorization"]
        for allowed in (
            "target_identity_and_materialization",
            "signal_evaluation",
            "fixed_economic_proxy",
            "conditional_role_audit",
            "robustness_audit",
        ):
            self.assertTrue(authorization[allowed], allowed)
        for forbidden in (
            "models",
            "model_selection",
            "bagging",
            "stacking",
            "final_state_machine",
            "state_machine_search",
            "lockbox",
            "mom255_transfer",
            "alternative_target",
            "unregistered_factor_additions",
            "window_search",
            "position_search",
        ):
            self.assertFalse(authorization[forbidden], forbidden)
        self.assertFalse(self.program["formal_eligible"])
        self.assertEqual(
            self.program["hard_stop"]["after_batch"],
            "R6D_ATTACK4_ROBUSTNESS",
        )
        for key in (
            "automatic_champion",
            "automatic_model_stage",
            "automatic_state_machine",
            "automatic_lockbox",
            "automatic_mom255_transfer",
        ):
            self.assertFalse(self.program["hard_stop"][key], key)

    def test_parent_acceptance_closes_the_round5_attestation_gap(self) -> None:
        acceptance = json.loads(PARENT_ACCEPTANCE.read_text(encoding="utf-8"))
        current = _sha(R5_LOCK)
        self.assertEqual(current, EXPECTED_CURRENT_R5_LOCK_SHA256)
        self.assertEqual(
            self.program["parent"]["r5_current_prereg_lock_sha256"], current
        )
        self.assertEqual(
            self.program["parent"]["r5_runtime_recorded_prereg_lock_sha256"],
            EXPECTED_RUNTIME_R5_LOCK_SHA256,
        )
        attestation = acceptance["round5_attestation"]
        self.assertEqual(
            attestation["current_repository_prereg_lock_sha256"], current
        )
        self.assertEqual(
            attestation["runtime_recorded_prereg_lock_sha256"],
            EXPECTED_RUNTIME_R5_LOCK_SHA256,
        )
        self.assertFalse(attestation["experimental_semantics_changed"])
        self.assertFalse(attestation["data_or_result_bytes_changed"])
        self.assertEqual(acceptance["round5_audit"]["status"], "passed")
        self.assertTrue(acceptance["round5_audit"]["all_lockbox_read_false"])
        self.assertEqual(len(acceptance["round5_audit"]["batches"]), 4)
        firewall = acceptance["firewall"]
        for key in (
            "round6_lockbox_read_authorized",
            "round6_model_training_authorized",
            "round6_final_state_machine_authorized",
            "round6_mom255_transfer_authorized",
        ):
            self.assertFalse(firewall[key], key)

    def test_batch_registries_are_bounded_and_ordered(self) -> None:
        groups = _csv_rows(ROOT / "experiments/round6_groups.csv")
        registry = _csv_rows(ROOT / "experiments/round6_registry.csv")
        self.assertEqual(tuple(row["batch_id"] for row in groups), EXPECTED_BATCH_IDS)
        self.assertEqual(tuple(row["batch_id"] for row in registry), EXPECTED_BATCH_IDS)
        self.assertEqual(len({row["experiment_id"] for row in registry}), 4)


@unittest.skipUnless(PREREG_LOCK.is_file(), "Round 6 lock has not been generated")
class Round6PreregLockTests(unittest.TestCase):
    def test_lock_self_hash_after_freeze_constant_is_filled(self) -> None:
        if FROZEN_R6_PREREG_LOCK_SHA256.startswith("REPLACE_AFTER_"):
            self.skipTest("fill FROZEN_R6_PREREG_LOCK_SHA256 after first freeze")
        self.assertEqual(_sha(PREREG_LOCK), FROZEN_R6_PREREG_LOCK_SHA256)

    def test_lock_member_allowlist_and_hashes_are_exact(self) -> None:
        lock = json.loads(PREREG_LOCK.read_text(encoding="utf-8"))
        self.assertEqual(lock["schema_version"], 1)
        self.assertEqual(lock["program_id"], "attack4_single_factor_round6_v1")
        self.assertEqual(tuple(lock["files"]), EXPECTED_MEMBERS)
        for relative, expected in lock["files"].items():
            self.assertEqual(_sha(ROOT / relative), expected, relative)

    def test_lock_parent_counts_target_and_authorization_are_exact(self) -> None:
        lock = json.loads(PREREG_LOCK.read_text(encoding="utf-8"))
        self.assertEqual(
            lock["parent_r5_prereg_lock_sha256"],
            EXPECTED_CURRENT_R5_LOCK_SHA256,
        )
        self.assertEqual(
            lock["parent"]["acceptance_sha256"], _sha(PARENT_ACCEPTANCE)
        )
        self.assertEqual(
            lock["counts"],
            {
                "registered_factor_arms": 20,
                "level_arms": 17,
                "delta4_arms": 3,
                "registered_targets": 3,
                "registered_batches": 4,
                "direct_eligible_arms": 12,
                "conditional_eligible_arms": 6,
                "context_only_arms": 2,
            },
        )
        self.assertEqual(lock["target"]["primary_name"], "fwd_excess_logret_4w")
        self.assertTrue(lock["target"]["binary_diagnostic_only"])
        self.assertTrue(lock["target"]["worst_path_guardrail_only"])
        for key in (
            "models",
            "model_selection",
            "final_state_machine",
            "lockbox",
            "mom255_transfer",
        ):
            self.assertFalse(lock["authorization"][key], key)


if __name__ == "__main__":
    unittest.main()
