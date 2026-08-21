from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import tomllib
import unittest

from scripts.build_xa03_prereg_lock import (
    FACTOR_IDS,
    MEMBERS,
    NO_RSP_PROCESS_IDS,
    PROCESS_LAYER_COUNTS,
    ROLE5_IDS,
    S2_IDS,
    S6_IDS,
    build,
)


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "config/experiments/xa03/PREREG_LOCK.json"
EXPECTED_LOCK_SHA256 = "e69b6494719f5f6a8922d1c583c158655bfa0c0645d8906fc435e6fa117caa21"


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class XA03PreregTests(unittest.TestCase):
    def setUp(self) -> None:
        with (ROOT / "config/experiments/xa03/program.toml").open("rb") as handle:
            self.program = tomllib.load(handle)

    def test_factor_bundles_and_model_recipes_are_frozen(self) -> None:
        factors = _csv_rows(ROOT / "config/experiments/xa03/factor_registry.csv")
        bundles = _csv_rows(ROOT / "config/experiments/xa03/feature_bundles.csv")
        recipes = _csv_rows(ROOT / "config/experiments/xa03/model_recipes.csv")
        self.assertEqual(tuple(row["factor_id"] for row in factors), FACTOR_IDS)
        self.assertEqual(
            tuple(row["factor_id"] for row in factors if row["role5_member"] == "true"),
            ROLE5_IDS,
        )
        by_bundle = {row["bundle_id"]: row for row in bundles}
        self.assertEqual(tuple(by_bundle["ROLE5_S2"]["state_ids"].split("|")), S2_IDS)
        self.assertEqual(tuple(by_bundle["ALL14_S6"]["state_ids"].split("|")), S6_IDS)
        self.assertEqual(by_bundle["ROLE5_S1_NO_RSP"]["rsp_ablation_of"], "ROLE5_S2")
        self.assertEqual(by_bundle["ALL14_S5_NO_RSP"]["rsp_ablation_of"], "ALL14_S6")
        self.assertEqual(
            tuple(row["recipe_id"] for row in recipes),
            (
                "DIRECT_RANK",
                "STATIC_EQUAL_RANK",
                "STATIC_DIMENSION_EQUAL_RANK",
                "RIDGE_A01",
                "RIDGE_A1",
                "RIDGE_A10",
                "RIDGE_A100",
                "LGBM_D2_N50",
                "LGBM_D2_N100",
            ),
        )

    def test_process_and_path_cardinalities_are_frozen(self) -> None:
        processes = _csv_rows(ROOT / "config/experiments/xa03/process_registry.csv")
        self.assertEqual(len(processes), 57)
        self.assertEqual(Counter(row["layer"] for row in processes), PROCESS_LAYER_COUNTS)
        self.assertEqual(sum(row["primary_candidate"] == "true" for row in processes), 53)
        self.assertEqual(sum(row["no_rsp_diagnostic"] == "true" for row in processes), 4)
        self.assertEqual(
            tuple(
                row["process_id"]
                for row in processes
                if row["no_rsp_diagnostic"] == "true"
            ),
            NO_RSP_PROCESS_IDS,
        )
        self.assertEqual(
            sum(
                row["layer"] in {"factor_state_model", "rsp_ablation"}
                for row in processes
            ),
            8,
        )
        self.assertEqual(self.program["processes"]["total_process_frequency_cells"], 114)
        self.assertEqual(self.program["paths"]["topk_signal_paths"], 456)
        self.assertEqual(self.program["paths"]["cost_paths"], 1824)
        self.assertEqual(self.program["paths"]["common_ew_cost_control_paths"], 8)

    def test_comparison_families_and_fixed_parents_are_frozen(self) -> None:
        comparisons = _csv_rows(
            ROOT / "config/experiments/xa03/comparison_registry.csv"
        )
        self.assertEqual(Counter(row["family"] for row in comparisons), {
            "paired_promotion": 39,
            "rsp_ablation": 4,
        })
        paired = {row["candidate_process_id"]: row for row in comparisons if row["family"] == "paired_promotion"}
        self.assertEqual(paired["FO_RIDGE__ALL14"]["parent_process_id"], "STATIC_DIM6_ALL14")
        self.assertEqual(paired["FO_LGBM__ALL14"]["parent_process_id"], "STATIC_DIM6_ALL14")
        self.assertTrue(all(row["bh_family_size_per_frequency"] == "39" for row in paired.values()))
        rsp = [row for row in comparisons if row["family"] == "rsp_ablation"]
        self.assertTrue(all(row["bh_family_size_per_frequency"] == "4" for row in rsp))
        self.assertTrue(all(row["promotion_authority"] == "false" for row in rsp))
        self.assertEqual(self.program["inference"]["absolute_candidate_tests_per_frequency"], 53)
        self.assertEqual(self.program["inference"]["paired_promotion_tests_per_frequency"], 39)
        self.assertEqual(self.program["inference"]["rsp_ablation_tests_per_frequency"], 4)

    def test_target_missing_and_walk_forward_rules_are_frozen(self) -> None:
        target = self.program["training_targets"]
        self.assertEqual(
            target["transform"],
            "2*(average_rank-1)/(finite_rank_universe_count-1)-1",
        )
        self.assertEqual(
            target["availability_rule"],
            "target_available_at<=prediction_signal_close",
        )
        self.assertTrue(target["overlap_with_xa01_must_be_exact"])
        self.assertTrue(target["xa01_forward_rank_may_not_be_used_as_model_target"])
        self.assertTrue(target["prediction_universe_may_not_use_target_valid"])
        self.assertEqual(self.program["common_universe"]["minimum_available_factors"], 10)
        features = self.program["features"]
        self.assertEqual(features["ridge_missing_policy"], "neutral_zero_no_missing_indicator")
        self.assertEqual(
            features["lightgbm_missing_policy"],
            "neutral_zero_no_native_nan_no_missing_indicator",
        )
        self.assertFalse(features["missing_indicators_authorized"])
        self.assertEqual(features["state_current_missing_policy"], "fail_closed")
        self.assertFalse(features["state_training_missing_imputation_authorized"])
        walk = self.program["walk_forward"]
        self.assertEqual(walk["minimum_complete_training_dates_weekly"], 156)
        self.assertEqual(walk["minimum_complete_training_dates_monthly"], 36)
        self.assertEqual(walk["model_refit_cadence"], "monthly")
        selector = self.program["annual_recipe_selection"]
        self.assertEqual(
            selector["recipe_selection_year_key"],
            "first_execution_open_calendar_year",
        )
        self.assertEqual(
            selector["one_se_method"],
            "moving_block_bootstrap_of_best_minus_candidate_date_rank_ic",
        )
        self.assertEqual(selector["one_se_rng"], "numpy_default_rng_pcg64")
        self.assertTrue(selector["selected_recipe_frozen_for_calendar_year"])
        self.assertTrue(selector["state_process_inherits_matched_factor_only_recipe"])
        self.assertTrue(selector["state_process_may_not_reselect_recipe"])
        self.assertEqual(
            self.program["models"]["lightgbm_minimum_independent_calendar_years_per_leaf"],
            2,
        )
        self.assertEqual(self.program["models"]["ridge_solver"], "cholesky")

    def test_outer_inference_and_qualification_authority_are_frozen(self) -> None:
        inference = self.program["inference"]
        self.assertEqual(
            inference["bootstrap_method"],
            "circular_moving_block_on_complete_scheduled_calendar",
        )
        self.assertEqual(
            inference["bh_families"],
            [
                "absolute_economic_by_frequency",
                "absolute_rank_ic_diagnostic_by_frequency",
                "paired_economic_by_frequency",
                "paired_rank_ic_diagnostic_by_frequency",
                "rsp_economic_by_frequency",
                "rsp_rank_ic_diagnostic_by_frequency",
            ],
        )
        self.assertTrue(inference["economic_bh_controls_advancement"])
        self.assertTrue(inference["rank_ic_bh_is_diagnostic_not_alternative_advancement"])
        absolute = self.program["absolute_qualification"]
        self.assertEqual(
            absolute["terminal_wealth_ratio_candidate_to_common_ew_must_exceed"],
            1.0,
        )
        self.assertEqual(absolute["topk_widths_for_direction"], [10, 20, 50])
        self.assertEqual(absolute["minimum_topk_widths_same_direction"], 2)
        self.assertTrue(absolute["top20_must_have_same_direction"])
        roles = self.program["roles"]
        self.assertTrue(roles["no_rsp_diagnostic_cannot_receive_qualified_status"])
        self.assertTrue(roles["predictive_only_cannot_rescue_economic_failure"])

    def test_authorization_and_hard_stop_are_frozen(self) -> None:
        authorization = self.program["authorization"]
        for field in (
            "supplemental_training_targets",
            "single_factor_models",
            "factor_only_aggregation",
            "factor_state_aggregation",
            "rsp_no_rsp_ablation",
            "portfolio_backtests",
            "paired_model_comparisons",
        ):
            self.assertTrue(authorization[field])
        for field in (
            "target_search",
            "factor_additions",
            "state_additions",
            "state_window_search",
            "state_threshold_search",
            "hyperparameter_search_outside_registry",
            "stacking",
            "bagging",
            "p00_transfer",
            "defensive_overlay",
            "lockbox",
            "external_data_acquisition",
        ):
            self.assertFalse(authorization[field])
        self.assertEqual(self.program["hard_stop"]["after_batch"], "XA03E")
        self.assertTrue(self.program["hard_stop"]["user_review_required"])
        self.assertFalse(self.program["hard_stop"]["automatic_p00"])
        self.assertFalse(self.program["hard_stop"]["automatic_stacking"])

    def test_parent_evidence_and_lock_members_are_closed(self) -> None:
        payload = build(ROOT)
        self.assertEqual(len(payload["files"]), len(MEMBERS))
        self.assertEqual(payload["processes_per_frequency"], 57)
        self.assertEqual(payload["absolute_candidates_per_frequency"], 53)
        self.assertEqual(payload["no_rsp_diagnostics_per_frequency"], 4)
        self.assertEqual(payload["hard_stop_after"], "XA03E")
        self.assertFalse(payload["p00_authorized"])
        self.assertFalse(payload["lockbox_authorized"])

    def test_checked_in_lock_is_canonical_and_has_frozen_hash(self) -> None:
        payload = build(ROOT)
        expected = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        actual = LOCK_PATH.read_bytes()
        self.assertEqual(actual, expected)
        self.assertEqual(hashlib.sha256(actual).hexdigest(), EXPECTED_LOCK_SHA256)


if __name__ == "__main__":
    unittest.main()
