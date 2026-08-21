from __future__ import annotations

import csv
import json
from pathlib import Path
import tomllib
import unittest

from scripts.build_xa02_prereg_lock import (
    FACTOR_IDS,
    MEMBERS,
    PAIR_IDS,
    PRIMARY_STATE_IDS,
    SHADOW_STATE_IDS,
    build,
)


ROOT = Path(__file__).resolve().parents[1]


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class XA02PreregTests(unittest.TestCase):
    def setUp(self) -> None:
        with (ROOT / "config/experiments/xa02/program.toml").open("rb") as handle:
            self.program = tomllib.load(handle)

    def test_complete_factor_and_state_universes(self) -> None:
        factors = _csv_rows(ROOT / "config/experiments/xa02/factor_registry.csv")
        states = _csv_rows(ROOT / "config/experiments/xa02/state_registry.csv")
        self.assertEqual(tuple(row["factor_id"] for row in factors), FACTOR_IDS)
        self.assertTrue(all(row["atlas_required"] == "true" for row in factors))
        self.assertTrue(all(row["model_authorized"] == "false" for row in factors))
        self.assertEqual(
            tuple(row["state_id"] for row in states if row["role"] == "primary"),
            PRIMARY_STATE_IDS,
        )
        self.assertEqual(
            tuple(row["state_id"] for row in states if row["role"] == "shadow"),
            SHADOW_STATE_IDS,
        )
        by_id = {row["state_id"]: row for row in states}
        self.assertEqual(by_id["MKT_TREND126"]["raw_min_history_sessions"], "127")
        self.assertEqual(by_id["MKT_BREADTH_RSP63"]["raw_min_history_sessions"], "64")
        self.assertEqual(by_id["MKT_XS_DISP21"]["raw_min_history_sessions"], "22")
        self.assertEqual(
            by_id["MKT_AVG_CORR63"]["atlas_authority"],
            "formal_1d_and_fixed_2d",
        )
        self.assertIn(
            "product(explicit_positive_split_ratio",
            by_id["SHADOW_BREADTH_SMA200"]["raw_formula"],
        )

    def test_path_grid_and_primary_paths_are_frozen(self) -> None:
        paths = self.program["paths"]
        self.assertEqual(paths["signal_path_count"], 112)
        self.assertEqual(paths["factor_cost_path_count"], 448)
        self.assertEqual(paths["top_k"], [5, 10, 20, 50])
        self.assertEqual(paths["primary_width"], 20)
        self.assertEqual(paths["weekly_primary_cost_bps"], 10)
        self.assertEqual(paths["monthly_primary_cost_bps"], 5)
        self.assertEqual(
            self.program["batches"]["order"], ["XA02A", "XA02B", "XA02C", "XA02D"]
        )
        self.assertEqual(
            self.program["runtime"]["run_directory_template"],
            "results/experiments/xa02/{batch_id}/runs/{run_id}",
        )

    def test_fixed_atlas_and_multiplicity(self) -> None:
        atlas = self.program["atlas_2d"]
        inference = self.program["inference"]
        self.assertEqual(tuple(atlas["pair_ids"]), PAIR_IDS)
        self.assertEqual(atlas["descriptive_grid"], "3x3_causal_terciles")
        self.assertTrue(atlas["formal_test_grid"].startswith("2x2_"))
        self.assertEqual(atlas["weekly_minimum_corner_cell"], 24)
        self.assertEqual(atlas["monthly_minimum_corner_cell"], 8)
        self.assertEqual(inference["one_dimensional_tests_per_frequency_outcome"], 84)
        self.assertEqual(inference["two_dimensional_tests_per_frequency_outcome"], 42)
        self.assertEqual(inference["insufficient_sample_p_for_fixed_bh_family"], 1.0)
        self.assertEqual(
            inference["hac_covariance"],
            "newey_west_bartlett_no_finite_sample_correction",
        )
        self.assertEqual(
            self.program["states"]["causal_percentile_method"],
            "(count_less+0.5*count_equal)/finite_prior_count",
        )
        self.assertEqual(
            self.program["episodes"]["calendar"],
            "frequency_specific_scheduled_signal_sequence",
        )
        self.assertEqual(self.program["metrics"]["minimum_names_for_rank_ic"], 100)
        self.assertEqual(self.program["metrics"]["annualization_weekly"], 52)
        self.assertEqual(self.program["metrics"]["annualization_monthly"], 12)
        self.assertTrue(self.program["roles"]["role_tags_are_nonexclusive"])
        self.assertTrue(
            self.program["role_contrasts"]["leave_one_year_out_may_not_reselect_bins"]
        )
        self.assertEqual(
            self.program["atlas_2d"]["descriptive_weekly_minimum_per_cell"], 20
        )
        self.assertEqual(
            self.program["atlas_2d"]["descriptive_monthly_minimum_per_cell"], 6
        )

    def test_models_and_automatic_progression_are_closed(self) -> None:
        authorization = self.program["authorization"]
        for field in (
            "models",
            "factor_aggregation",
            "strategy_selection",
            "market_state_classifier",
            "target_revision",
            "p00_transfer",
            "lockbox",
            "external_data_acquisition",
            "state_window_search",
            "state_threshold_search",
        ):
            self.assertFalse(authorization[field])
        self.assertEqual(self.program["hard_stop"]["after_batch"], "XA02D")
        self.assertFalse(self.program["hard_stop"]["automatic_xa03"])
        self.assertTrue(self.program["execution_provenance"]["git_clean_commit_required"])
        self.assertFalse(
            self.program["execution_provenance"][
                "unregistered_dependency_installation_authorized"
            ]
        )

    def test_parent_evidence_and_lock_members_are_closed(self) -> None:
        payload = build(ROOT)
        self.assertEqual(len(payload["files"]), len(MEMBERS))
        self.assertEqual(payload["hard_stop_after"], "XA02D")
        self.assertFalse(payload["models_authorized"])
        self.assertFalse(payload["factor_aggregation_authorized"])

    def test_checked_in_lock_is_canonical(self) -> None:
        payload = build(ROOT)
        expected = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        self.assertEqual(
            (ROOT / "config/experiments/xa02/PREREG_LOCK.json").read_bytes(),
            expected,
        )


if __name__ == "__main__":
    unittest.main()
