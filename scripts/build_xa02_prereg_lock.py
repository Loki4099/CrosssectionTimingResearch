"""Build or verify the XA02 factor/state-atlas preregistration lock."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import tomllib


MEMBERS = (
    "config/experiments/xa02/factor_registry.csv",
    "config/experiments/xa02/program.toml",
    "config/experiments/xa02/state_registry.csv",
    "docs/20_experiments/XA02_factor_market_state_atlas/design.md",
    "docs/45_xa02_factor_market_state_atlas_program_v1.md",
    "scripts/build_xa02_prereg_lock.py",
)
LOCK = "config/experiments/xa02/PREREG_LOCK.json"

FACTOR_IDS = (
    "XS001_MOM_255_0",
    "XS002_MOM_12_1",
    "XS003_MOM_12_7",
    "XS004_HIGH_52W",
    "XS007_ST_REV_21",
    "XS008_SAME_MONTH_5Y",
    "XS013_LOW_BETA_FP",
    "XS015_MAX_21",
    "XS018_AMIHUD_252",
    "XS019_PRICE_DELAY_52W",
    "XS020_VOLUME_SHOCK_50D",
    "XS032_GROSS_PROFIT_AT",
    "XS041_ASSET_GROWTH",
    "XS056_CFO_ACCRUALS_PT",
)
PRIMARY_STATE_IDS = (
    "MKT_TREND126",
    "MKT_LOG_RV21",
    "MKT_DD252_SEVERITY",
    "MKT_BREADTH_RSP63",
    "MKT_XS_DISP21",
    "MKT_AVG_CORR63",
)
SHADOW_STATE_IDS = (
    "SHADOW_SMA50_200",
    "SHADOW_LOG_RV_RATIO",
    "SHADOW_BREADTH_SMA200",
)
PAIR_IDS = (
    "trend_x_volatility",
    "breadth_x_volatility",
    "dispersion_x_correlation",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_parent_evidence(root: Path, program: dict[str, object]) -> None:
    parent = program["parent"]
    assert isinstance(parent, dict)

    repo_hashes = {
        "xa01_prereg_lock_sha256": "config/experiments/xa01/PREREG_LOCK.json",
        "xa01_runtime_manifest_sha256": (
            "results/published/cross_sectional_alpha/XA01/manifest.json"
        ),
        "xa01_publication_manifest_sha256": (
            "results/published/cross_sectional_alpha/XA01/publication_manifest.json"
        ),
        "xa01_factor_registry_sha256": "config/experiments/xa01/factor_registry.csv",
        "cross_sectional_publication_manifest_sha256": (
            "results/published/cross_sectional_data/"
            "xs-market-sec-bundle-20260820-v1/manifest.json"
        ),
        "cross_sectional_evidence_index_sha256": (
            "results/published/cross_sectional_data/"
            "xs-market-sec-bundle-20260820-v1/evidence_index.json"
        ),
        "r10a_acceptance_sha256": "config/experiments/round10/R10A_ACCEPTANCE.json",
        "r10a_publication_manifest_sha256": (
            "results/published/round10/R10A/manifest.json"
        ),
    }
    for field, relative in repo_hashes.items():
        path = root / relative
        _require(path.is_file(), f"missing XA02 parent evidence: {relative}")
        _require(parent[field] == _sha(path), f"XA02 parent hash mismatch: {field}")

    xa01_manifest = _json(
        root / "results/published/cross_sectional_alpha/XA01/manifest.json"
    )
    xa01_files = xa01_manifest["files"]
    assert isinstance(xa01_files, dict)
    _require(
        parent["xa01_factor_values_sha256"]
        == xa01_files["factor_values_weekly_monthly.parquet"]["sha256"],
        "XA01 factor-values parent hash mismatch",
    )
    _require(
        parent["xa01_target_ledger_sha256"]
        == xa01_files["target_ledger.parquet"]["sha256"],
        "XA01 target-ledger parent hash mismatch",
    )

    evidence = _json(
        root
        / "results/published/cross_sectional_data/"
        "xs-market-sec-bundle-20260820-v1/evidence_index.json"
    )
    _require(
        parent["cross_sectional_runtime_bundle_manifest_sha256"]
        == evidence["bundle_manifest_sha256"],
        "cross-sectional runtime bundle hash mismatch",
    )
    components = {
        item["component_id"]: item
        for item in evidence["components"]
        if isinstance(item, dict)
    }
    direct_market = {
        "market_prices_daily_sha256": "market_prices_daily",
        "market_calendar_sha256": "market_calendar",
        "market_membership_sha256": "market_membership",
        "market_benchmark_sha256": "market_benchmark",
    }
    for field, component_id in direct_market.items():
        _require(
            parent[field] == components[component_id]["sha256"],
            f"direct market parent hash mismatch: {field}",
        )

    with (root / "config/experiments/round9/program.toml").open("rb") as handle:
        round9_program = tomllib.load(handle)
    round9_parent = round9_program["parent"]
    _require(
        parent["market_corporate_actions_sha256"]
        == round9_parent["corporate_actions_sha256"],
        "corporate-actions parent hash mismatch",
    )
    _require(
        parent["market_risk_free_sha256"] == round9_parent["risk_free_sha256"],
        "risk-free parent hash mismatch",
    )

    r10a_published = _json(root / "results/published/round10/R10A/manifest.json")
    r10a_files = {
        item["path"]: item
        for item in r10a_published["files"]
        if isinstance(item, dict)
    }
    _require(
        parent["r10a_rsp_daily_sha256"] == r10a_files["rsp_daily.parquet"]["sha256"],
        "R10A RSP daily hash mismatch",
    )
    acceptance = _json(root / "config/experiments/round10/R10A_ACCEPTANCE.json")
    _require(
        parent["r10a_runtime_manifest_sha256"]
        == acceptance["r10a_manifest_sha256"],
        "R10A accepted runtime manifest mismatch",
    )
    round10_rows = _rows(root / "experiments/round10_results.csv")
    r10a_result = next(
        row for row in round10_rows if row["batch_id"] == "R10A_RSP_LOCKBOX_FEATURE"
    )
    _require(
        parent["r10a_runtime_manifest_sha256"] == r10a_result["manifest_sha256"],
        "R10A results ledger manifest mismatch",
    )


def _validate_design(root: Path, program: dict[str, object]) -> None:
    _require(program["program_id"] == "xa02_factor_market_state_atlas_v1", "program id")
    _require(program["status"] == "preregistered_authorized", "program status")
    _require(program["formal_eligible"] is False, "formal eligibility must be false")

    paths = program["paths"]
    assert isinstance(paths, dict)
    _require(paths["factor_count"] == 14, "factor count")
    _require(paths["top_k"] == [5, 10, 20, 50], "TopK grid")
    _require(paths["cost_scenarios_bps"] == [0, 5, 10, 20], "cost grid")
    _require(paths["signal_path_count"] == 112, "signal path count")
    _require(paths["factor_cost_path_count"] == 448, "cost path count")
    _require(
        program["batches"]["order"] == ["XA02A", "XA02B", "XA02C", "XA02D"],
        "batch order",
    )
    _require(
        program["runtime"]["run_directory_template"]
        == "results/experiments/xa02/{batch_id}/runs/{run_id}",
        "runtime path template",
    )

    factor_rows = _rows(root / str(paths["factor_registry"]))
    _require(tuple(row["factor_id"] for row in factor_rows) == FACTOR_IDS, "factor universe")
    _require(all(row["atlas_required"] == "true" for row in factor_rows), "atlas coverage")
    _require(all(row["model_authorized"] == "false" for row in factor_rows), "model flag")

    states = program["states"]
    assert isinstance(states, dict)
    state_rows = _rows(root / str(states["registry"]))
    primary = tuple(row["state_id"] for row in state_rows if row["role"] == "primary")
    shadow = tuple(row["state_id"] for row in state_rows if row["role"] == "shadow")
    _require(primary == PRIMARY_STATE_IDS, "primary state universe")
    _require(shadow == SHADOW_STATE_IDS, "shadow state universe")
    _require(states["primary_count"] == len(primary), "primary state count")
    _require(states["shadow_count"] == len(shadow), "shadow state count")
    state_by_id = {row["state_id"]: row for row in state_rows}
    _require(state_by_id["MKT_TREND126"]["raw_min_history_sessions"] == "127", "trend history")
    _require(state_by_id["MKT_BREADTH_RSP63"]["raw_min_history_sessions"] == "64", "breadth history")
    _require(state_by_id["MKT_XS_DISP21"]["raw_min_history_sessions"] == "22", "dispersion history")
    _require(
        state_by_id["MKT_AVG_CORR63"]["atlas_authority"]
        == "formal_1d_and_fixed_2d",
        "correlation 2D authority",
    )
    _require(
        "product(explicit_positive_split_ratio" in state_by_id["SHADOW_BREADTH_SMA200"]["raw_formula"],
        "causal split-adjustment formula",
    )
    _require(
        states["causal_percentile_method"]
        == "(count_less+0.5*count_equal)/finite_prior_count",
        "causal percentile method",
    )

    atlas_2d = program["atlas_2d"]
    assert isinstance(atlas_2d, dict)
    _require(tuple(atlas_2d["pair_ids"]) == PAIR_IDS, "fixed two-dimensional pairs")
    _require(atlas_2d["formal_test_grid"].startswith("2x2_"), "2D formal grid")

    inference = program["inference"]
    assert isinstance(inference, dict)
    _require(inference["one_dimensional_tests_per_frequency_outcome"] == 84, "1D m")
    _require(inference["two_dimensional_tests_per_frequency_outcome"] == 42, "2D m")
    _require(inference["insufficient_sample_p_for_fixed_bh_family"] == 1.0, "fixed m")
    _require(
        inference["hac_covariance"]
        == "newey_west_bartlett_no_finite_sample_correction",
        "HAC implementation",
    )

    metrics = program["metrics"]
    assert isinstance(metrics, dict)
    _require(metrics["minimum_names_for_rank_ic"] == 100, "RankIC name gate")
    _require(metrics["annualization_weekly"] == 52, "weekly annualization")
    _require(metrics["annualization_monthly"] == 12, "monthly annualization")
    roles = program["roles"]
    assert isinstance(roles, dict)
    _require(roles["role_tags_are_nonexclusive"] is True, "role tag semantics")
    _require(
        roles["primary_role_priority"][0] == "conditional_sign_switch",
        "primary role priority",
    )
    contrasts = program["role_contrasts"]
    assert isinstance(contrasts, dict)
    _require(contrasts["leave_one_year_out_may_not_reselect_bins"] is True, "LOYO bins")
    _require(
        contrasts["single_year_contribution"]
        == "sum_y_in_best/n_best_full-sum_y_in_worst/n_worst_full",
        "year contribution formula",
    )

    authorization = program["authorization"]
    assert isinstance(authorization, dict)
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
        _require(authorization[field] is False, f"authorization must be false: {field}")
    _require(program["hard_stop"]["after_batch"] == "XA02D", "XA02 hard stop")
    _require(program["execution_provenance"]["git_clean_commit_required"] is True, "clean Git")
    _require(
        program["execution_provenance"]["unregistered_dependency_installation_authorized"]
        is False,
        "dependency authorization",
    )

    _validate_parent_evidence(root, program)


def build(project_root: Path) -> dict[str, object]:
    program_path = project_root / "config/experiments/xa02/program.toml"
    if not program_path.is_file():
        raise FileNotFoundError(program_path)
    with program_path.open("rb") as handle:
        program = tomllib.load(handle)
    _validate_design(project_root, program)

    files: list[dict[str, object]] = []
    for name in sorted(MEMBERS):
        path = project_root / name
        if not path.is_file():
            raise FileNotFoundError(path)
        files.append(
            {"path": name, "sha256": _sha(path), "size_bytes": path.stat().st_size}
        )
    return {
        "schema_version": "xa02.prereg_lock.v1",
        "program_id": "xa02_factor_market_state_atlas_v1",
        "status": "locked_authorized",
        "formal_eligible": False,
        "models_authorized": False,
        "factor_aggregation_authorized": False,
        "lockbox_authorized": False,
        "hard_stop_after": "XA02D",
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()

    root = args.project_root.resolve()
    expected = build(root)
    lock_path = root / LOCK
    encoded = (json.dumps(expected, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if args.write:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_bytes(encoded)
        print(_sha(lock_path))
        return 0
    if not lock_path.is_file() or lock_path.read_bytes() != encoded:
        raise SystemExit("XA02 preregistration lock mismatch")
    print(_sha(lock_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
