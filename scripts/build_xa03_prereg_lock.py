"""Build or verify the XA03 walk-forward aggregation preregistration lock."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import tomllib


MEMBERS = (
    "config/experiments/xa03/comparison_registry.csv",
    "config/experiments/xa03/factor_registry.csv",
    "config/experiments/xa03/feature_bundles.csv",
    "config/experiments/xa03/model_recipes.csv",
    "config/experiments/xa03/process_registry.csv",
    "config/experiments/xa03/program.toml",
    "docs/20_experiments/XA03_cross_sectional_aggregation/design.md",
    "docs/46_xa03_cross_sectional_aggregation_program_v1.md",
    "scripts/build_xa03_prereg_lock.py",
)
LOCK = "config/experiments/xa03/PREREG_LOCK.json"
EVIDENCE_PARENT_GIT_COMMIT = "ad7ea2848a2e57a08379e8f157e9e56c526d8e72"

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
ROLE5_IDS = (
    "XS002_MOM_12_1",
    "XS003_MOM_12_7",
    "XS008_SAME_MONTH_5Y",
    "XS041_ASSET_GROWTH",
    "XS056_CFO_ACCRUALS_PT",
)
FACTOR_DIMENSIONS = {
    "XS001_MOM_255_0": "trend_price_path",
    "XS002_MOM_12_1": "trend_price_path",
    "XS003_MOM_12_7": "trend_price_path",
    "XS004_HIGH_52W": "trend_price_path",
    "XS007_ST_REV_21": "reversal_calendar",
    "XS008_SAME_MONTH_5Y": "reversal_calendar",
    "XS013_LOW_BETA_FP": "low_risk_lottery",
    "XS015_MAX_21": "low_risk_lottery",
    "XS018_AMIHUD_252": "liquidity_attention",
    "XS019_PRICE_DELAY_52W": "liquidity_attention",
    "XS020_VOLUME_SHOCK_50D": "liquidity_attention",
    "XS032_GROSS_PROFIT_AT": "operating_quality_cash",
    "XS041_ASSET_GROWTH": "investment_conservatism",
    "XS056_CFO_ACCRUALS_PT": "operating_quality_cash",
}
S2_IDS = ("MKT_TREND126", "MKT_BREADTH_RSP63")
S6_IDS = (
    "MKT_TREND126",
    "MKT_LOG_RV21",
    "MKT_DD252_SEVERITY",
    "MKT_BREADTH_RSP63",
    "MKT_XS_DISP21",
    "MKT_AVG_CORR63",
)
S5_NO_RSP_IDS = tuple(item for item in S6_IDS if item != "MKT_BREADTH_RSP63")

PROCESS_LAYER_COUNTS = {
    "raw_control": 14,
    "single_factor_model": 28,
    "static_aggregation": 3,
    "factor_only_model": 4,
    "factor_state_model": 4,
    "rsp_ablation": 4,
}
STATIC_PROCESS_IDS = (
    "STATIC_EQ_ROLE5",
    "STATIC_EQ_ALL14",
    "STATIC_DIM6_ALL14",
)
FACTOR_ONLY_PROCESS_IDS = (
    "FO_RIDGE__ROLE5",
    "FO_LGBM__ROLE5",
    "FO_RIDGE__ALL14",
    "FO_LGBM__ALL14",
)
WITH_RSP_PROCESS_IDS = (
    "FS_RIDGE__ROLE5_S2",
    "FS_LGBM__ROLE5_S2",
    "FS_RIDGE__ALL14_S6",
    "FS_LGBM__ALL14_S6",
)
NO_RSP_PROCESS_IDS = (
    "AB_RIDGE__ROLE5_S1_NO_RSP",
    "AB_LGBM__ROLE5_S1_NO_RSP",
    "AB_RIDGE__ALL14_S5_NO_RSP",
    "AB_LGBM__ALL14_S5_NO_RSP",
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


def _split_ids(value: str) -> tuple[str, ...]:
    return tuple(item for item in value.split("|") if item)


def _validate_parent_evidence(root: Path, program: dict[str, object]) -> None:
    parent = program["parent"]
    assert isinstance(parent, dict)
    _require(
        parent["evidence_parent_git_commit"] == EVIDENCE_PARENT_GIT_COMMIT,
        "evidence-parent Git commit mismatch",
    )
    _require(
        parent["historical_parent_outputs_must_not_be_overwritten"] is True,
        "historical parent immutability",
    )

    repo_hashes = {
        "xa01_prereg_lock_sha256": "config/experiments/xa01/PREREG_LOCK.json",
        "xa01_runtime_manifest_sha256": (
            "results/published/cross_sectional_alpha/XA01/manifest.json"
        ),
        "xa01_publication_manifest_sha256": (
            "results/published/cross_sectional_alpha/XA01/publication_manifest.json"
        ),
        "xa01_factor_registry_sha256": "config/experiments/xa01/factor_registry.csv",
        "xa02_prereg_lock_sha256": "config/experiments/xa02/PREREG_LOCK.json",
        "xa02_state_registry_sha256": "config/experiments/xa02/state_registry.csv",
        "xa02_publication_manifest_sha256": (
            "results/published/cross_sectional_alpha/XA02/publication_manifest.json"
        ),
        "cross_sectional_publication_manifest_sha256": (
            "results/published/cross_sectional_data/"
            "xs-market-sec-bundle-20260820-v1/manifest.json"
        ),
        "cross_sectional_evidence_index_sha256": (
            "results/published/cross_sectional_data/"
            "xs-market-sec-bundle-20260820-v1/evidence_index.json"
        ),
        "r10a_acceptance_sha256": "config/experiments/round10/R10A_ACCEPTANCE.json",
        "r10a_publication_manifest_sha256": "results/published/round10/R10A/manifest.json",
    }
    for field, relative in repo_hashes.items():
        path = root / relative
        _require(path.is_file(), f"missing XA03 parent evidence: {relative}")
        _require(parent[field] == _sha(path), f"XA03 parent hash mismatch: {field}")

    xa01_manifest = _json(
        root / "results/published/cross_sectional_alpha/XA01/manifest.json"
    )
    xa01_files = xa01_manifest["files"]
    assert isinstance(xa01_files, dict)
    _require(
        xa01_manifest["run_id"] == parent["xa01_run_id"],
        "XA01 parent run id mismatch",
    )
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
    _require(
        parent["xa01_signal_dates_sha256"]
        == xa01_files["signal_dates.parquet"]["sha256"],
        "XA01 signal-date parent hash mismatch",
    )
    xa01_publication = _json(
        root / "results/published/cross_sectional_alpha/XA01/publication_manifest.json"
    )
    _require(
        xa01_publication["files"]["manifest.json"]["sha256"]
        == parent["xa01_runtime_manifest_sha256"],
        "XA01 publication/runtime manifest mismatch",
    )

    xa02_publication_path = (
        root / "results/published/cross_sectional_alpha/XA02/publication_manifest.json"
    )
    xa02_publication = _json(xa02_publication_path)
    audit = xa02_publication["audit"]
    assert isinstance(audit, dict)
    _require(audit["status"] == "passed", "XA02 parent publication did not pass")
    _require(audit["hard_stop"] == "XA02D", "XA02 parent hard stop mismatch")
    _require(audit["lockbox_read"] is False, "XA02 parent read a lockbox")

    batch_fields = {
        "XA02A": "xa02a_runtime_manifest_sha256",
        "XA02B": "xa02b_runtime_manifest_sha256",
        "XA02C": "xa02c_runtime_manifest_sha256",
        "XA02D": "xa02d_runtime_manifest_sha256",
    }
    for batch_id, field in batch_fields.items():
        expected_hash = parent[field]
        relative = f"results/published/cross_sectional_alpha/XA02/{batch_id}/manifest.json"
        path = root / relative
        _require(path.is_file(), f"missing XA02 batch manifest: {relative}")
        _require(_sha(path) == expected_hash, f"XA02 runtime hash mismatch: {field}")
        _require(
            audit["manifests"][batch_id] == expected_hash,
            f"XA02 audit manifest mismatch: {batch_id}",
        )
        publication_entry = xa02_publication["files"][f"{batch_id}/manifest.json"]
        _require(
            publication_entry["sha256"] == expected_hash,
            f"XA02 publication manifest mismatch: {batch_id}",
        )

    xa02a = _json(
        root / "results/published/cross_sectional_alpha/XA02/XA02A/manifest.json"
    )
    direct = xa02a["direct_input_hashes"]
    assert isinstance(direct, dict)
    direct_market = (
        "market_prices_daily_sha256",
        "market_calendar_sha256",
        "market_membership_sha256",
        "market_benchmark_sha256",
        "market_corporate_actions_sha256",
        "market_risk_free_sha256",
    )
    for field in direct_market:
        _require(parent[field] == direct[field], f"direct market hash mismatch: {field}")
    for field in (
        "xa01_prereg_lock_sha256",
        "xa01_runtime_manifest_sha256",
        "xa01_publication_manifest_sha256",
        "xa01_factor_values_sha256",
        "xa01_target_ledger_sha256",
    ):
        _require(parent[field] == direct[field], f"XA01/XA02A hash mismatch: {field}")

    xa02b = _json(
        root / "results/published/cross_sectional_alpha/XA02/XA02B/manifest.json"
    )
    _require(
        parent["xa02b_market_state_daily_sha256"]
        == xa02b["files"]["market_state_daily.parquet"]["sha256"],
        "XA02B market-state daily parent hash mismatch",
    )
    _require(
        parent["xa02b_market_state_features_sha256"]
        == xa02b["files"]["market_state_features.parquet"]["sha256"],
        "XA02B market-state feature parent hash mismatch",
    )
    _require(
        xa02b["dependencies"]["XA02A"] == parent["xa02a_runtime_manifest_sha256"],
        "XA02B dependency hash mismatch",
    )

    xa02d = _json(
        root / "results/published/cross_sectional_alpha/XA02/XA02D/manifest.json"
    )
    xa02d_artifacts = {
        "xa02d_role_assessment_sha256": "factor_state_role_assessment.csv",
        "xa02d_factor_state_relationship_assessment_sha256": (
            "factor_state_relationship_assessment.csv"
        ),
        "xa02d_empirical_clusters_sha256": "empirical_clusters.csv",
    }
    xa02d_dir = root / "results/published/cross_sectional_alpha/XA02/XA02D"
    for field, filename in xa02d_artifacts.items():
        _require(
            parent[field] == xa02d["files"][filename]["sha256"] == _sha(xa02d_dir / filename),
            f"XA02D parent artifact hash mismatch: {field}",
        )

    bundle_dir = (
        root
        / "results/published/cross_sectional_data/"
        "xs-market-sec-bundle-20260820-v1"
    )
    bundle_publication = _json(bundle_dir / "manifest.json")
    evidence = _json(bundle_dir / "evidence_index.json")
    _require(
        parent["cross_sectional_bundle_id"] == bundle_publication["data_bundle_id"],
        "cross-sectional bundle id mismatch",
    )
    _require(
        parent["cross_sectional_runtime_bundle_manifest_sha256"]
        == bundle_publication["bundle_manifest_sha256"]
        == evidence["bundle_manifest_sha256"],
        "cross-sectional runtime bundle hash mismatch",
    )
    publication_files = {
        item["path"]: item
        for item in bundle_publication["files"]
        if isinstance(item, dict)
    }
    _require(
        publication_files["evidence_index.json"]["sha256"]
        == parent["cross_sectional_evidence_index_sha256"],
        "cross-sectional evidence/publication mismatch",
    )
    components = {
        item["component_id"]: item
        for item in evidence["components"]
        if isinstance(item, dict)
    }
    component_fields = {
        "market_prices_daily_sha256": "market_prices_daily",
        "market_calendar_sha256": "market_calendar",
        "market_membership_sha256": "market_membership",
        "market_benchmark_sha256": "market_benchmark",
        "cross_sectional_factor_values_sha256": "factor_values",
        "cross_sectional_factor_content_manifest_sha256": "factor_content_manifest",
    }
    for field, component_id in component_fields.items():
        _require(
            parent[field] == components[component_id]["sha256"],
            f"cross-sectional component hash mismatch: {field}",
        )

    acceptance = _json(root / "config/experiments/round10/R10A_ACCEPTANCE.json")
    r10a_published = _json(root / "results/published/round10/R10A/manifest.json")
    r10a_files = {
        item["path"]: item for item in r10a_published["files"] if isinstance(item, dict)
    }
    _require(parent["r10a_run_id"] == acceptance["run_id"], "R10A run id mismatch")
    _require(
        parent["r10a_run_id"] == r10a_published["run_id"],
        "R10A publication run id mismatch",
    )
    _require(
        parent["r10a_runtime_manifest_sha256"]
        == acceptance["r10a_manifest_sha256"],
        "R10A accepted runtime manifest mismatch",
    )
    _require(
        parent["r10a_rsp_daily_sha256"]
        == acceptance["rsp_daily_sha256"]
        == r10a_files["rsp_daily.parquet"]["sha256"],
        "R10A RSP daily hash mismatch",
    )
    round10_rows = _rows(root / "experiments/round10_results.csv")
    r10a_result = next(
        row for row in round10_rows if row["batch_id"] == "R10A_RSP_LOCKBOX_FEATURE"
    )
    _require(
        parent["r10a_runtime_manifest_sha256"] == r10a_result["manifest_sha256"],
        "R10A results-ledger manifest mismatch",
    )


def _validate_factor_and_feature_registries(
    root: Path, program: dict[str, object]
) -> None:
    registries = program["registries"]
    assert isinstance(registries, dict)
    factors = _rows(root / str(registries["factor_registry"]))
    _require(tuple(row["factor_id"] for row in factors) == FACTOR_IDS, "factor universe")
    _require(
        {row["factor_id"]: row["dimension"] for row in factors} == FACTOR_DIMENSIONS,
        "factor mechanism dimensions",
    )
    _require(all(row["raw_control_authorized"] == "true" for row in factors), "raw flags")
    _require(
        all(row["single_factor_model_authorized"] == "true" for row in factors),
        "single-factor model flags",
    )
    _require(
        tuple(row["factor_id"] for row in factors if row["role5_member"] == "true")
        == ROLE5_IDS,
        "ROLE5 registry membership",
    )
    _require(all(row["all14_member"] == "true" for row in factors), "ALL14 registry")
    _require(
        all(row["common10_missing_aware"] == "true" for row in factors),
        "COMMON10 missing-awareness flags",
    )

    feature_sets = program["feature_sets"]
    assert isinstance(feature_sets, dict)
    _require(tuple(feature_sets["role5"]) == ROLE5_IDS, "ROLE5 feature set")
    _require(tuple(feature_sets["all14"]) == FACTOR_IDS, "ALL14 feature set")
    _require(tuple(feature_sets["s2"]) == S2_IDS, "S2 feature set")
    _require(tuple(feature_sets["s2_no_rsp"]) == ("MKT_TREND126",), "S2 no-RSP")
    _require(tuple(feature_sets["s6"]) == S6_IDS, "S6 feature set")
    _require(tuple(feature_sets["s6_no_rsp"]) == S5_NO_RSP_IDS, "S6 no-RSP")

    bundles = _rows(root / str(registries["feature_bundles"]))
    by_bundle = {row["bundle_id"]: row for row in bundles}
    _require(len(by_bundle) == len(bundles) == 21, "feature bundle count/uniqueness")
    for factor_id in FACTOR_IDS:
        row = by_bundle[f"ATOM_{factor_id}"]
        _require(_split_ids(row["factor_ids"]) == (factor_id,), f"atomic {factor_id}")
        _require(row["factor_count"] == "1", f"atomic factor count: {factor_id}")
    expected_bundles = {
        "ROLE5": (ROLE5_IDS, (), "5", "0", "false"),
        "ALL14": (FACTOR_IDS, (), "14", "0", "false"),
        "DIM6_ALL14": (FACTOR_IDS, (), "14", "0", "false"),
        "ROLE5_S2": (ROLE5_IDS, S2_IDS, "5", "2", "true"),
        "ROLE5_S1_NO_RSP": (
            ROLE5_IDS,
            ("MKT_TREND126",),
            "5",
            "1",
            "false",
        ),
        "ALL14_S6": (FACTOR_IDS, S6_IDS, "14", "6", "true"),
        "ALL14_S5_NO_RSP": (FACTOR_IDS, S5_NO_RSP_IDS, "14", "5", "false"),
    }
    for bundle_id, (factor_ids, state_ids, factor_n, state_n, rsp) in expected_bundles.items():
        row = by_bundle[bundle_id]
        _require(_split_ids(row["factor_ids"]) == factor_ids, f"bundle factors: {bundle_id}")
        _require(_split_ids(row["state_ids"]) == state_ids, f"bundle states: {bundle_id}")
        _require(row["factor_count"] == factor_n, f"bundle factor count: {bundle_id}")
        _require(row["state_count"] == state_n, f"bundle state count: {bundle_id}")
        _require(row["rsp_included"] == rsp, f"bundle RSP flag: {bundle_id}")
        _require(row["common_universe_id"] == "COMMON10_OF_14", "bundle universe")
    _require(
        by_bundle["ROLE5_S1_NO_RSP"]["rsp_ablation_of"] == "ROLE5_S2",
        "ROLE5 no-RSP pair",
    )
    _require(
        by_bundle["ALL14_S5_NO_RSP"]["rsp_ablation_of"] == "ALL14_S6",
        "ALL14 no-RSP pair",
    )


def _expected_process_ids() -> tuple[str, ...]:
    raw = tuple(f"RAW__{factor_id}" for factor_id in FACTOR_IDS)
    single = tuple(
        f"UNI_{family}__{factor_id}"
        for family in ("RIDGE", "LGBM")
        for factor_id in FACTOR_IDS
    )
    return (
        *raw,
        *single,
        *STATIC_PROCESS_IDS,
        *FACTOR_ONLY_PROCESS_IDS,
        *WITH_RSP_PROCESS_IDS,
        *NO_RSP_PROCESS_IDS,
    )


def _validate_model_and_process_registries(
    root: Path, program: dict[str, object]
) -> None:
    registries = program["registries"]
    assert isinstance(registries, dict)
    recipes = _rows(root / str(registries["model_recipes"]))
    recipe_ids = tuple(row["recipe_id"] for row in recipes)
    _require(
        recipe_ids
        == (
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
        "model recipe universe",
    )
    by_recipe = {row["recipe_id"]: row for row in recipes}
    _require(
        tuple(float(by_recipe[item]["alpha"]) for item in ("RIDGE_A01", "RIDGE_A1", "RIDGE_A10", "RIDGE_A100"))
        == (0.1, 1.0, 10.0, 100.0),
        "Ridge alpha grid",
    )
    _require(
        tuple(int(by_recipe[item]["capacity_rank"]) for item in ("RIDGE_A100", "RIDGE_A10", "RIDGE_A1", "RIDGE_A01"))
        == (1, 2, 3, 4),
        "Ridge capacity order",
    )
    for recipe_id, estimators, capacity in (
        ("LGBM_D2_N50", "50", "1"),
        ("LGBM_D2_N100", "100", "2"),
    ):
        row = by_recipe[recipe_id]
        _require(row["max_depth"] == "2" and row["num_leaves"] == "4", "LGBM depth")
        _require(row["n_estimators"] == estimators, f"LGBM estimators: {recipe_id}")
        _require(row["min_child_samples"] == "100", "LGBM minimum child samples")
        _require(row["seed"] == "20260821" and row["n_jobs"] == "1", "LGBM determinism")
        _require(row["deterministic"] == "true" and row["force_col_wise"] == "true", "LGBM deterministic flags")
        _require(row["capacity_rank"] == capacity, "LGBM capacity order")

    processes = _rows(root / str(registries["process_registry"]))
    process_ids = tuple(row["process_id"] for row in processes)
    _require(process_ids == _expected_process_ids(), "process universe/order")
    _require(len(set(process_ids)) == len(process_ids) == 57, "process count/uniqueness")
    _require(Counter(row["layer"] for row in processes) == PROCESS_LAYER_COUNTS, "layer counts")
    _require(all(row["frequency_scope"] == "weekly|monthly" for row in processes), "frequency scope")
    _require(all(row["common_universe_id"] == "COMMON10_OF_14" for row in processes), "process universe")
    _require(all(row["eligible"] == "true" for row in processes), "process eligibility")
    _require(sum(row["primary_candidate"] == "true" for row in processes) == 53, "53 candidates")
    _require(sum(row["no_rsp_diagnostic"] == "true" for row in processes) == 4, "4 no-RSP diagnostics")
    _require(
        tuple(row["process_id"] for row in processes if row["no_rsp_diagnostic"] == "true")
        == NO_RSP_PROCESS_IDS,
        "no-RSP process universe",
    )
    _require(
        all(
            (row["primary_candidate"] == "true")
            == (row["no_rsp_diagnostic"] == "false")
            for row in processes
        ),
        "candidate/no-RSP partition",
    )
    _require(
        sum(row["layer"] in {"factor_state_model", "rsp_ablation"} for row in processes)
        == 8,
        "eight A2 processes per frequency",
    )

    modeled_layers = {"single_factor_model", "factor_only_model", "factor_state_model", "rsp_ablation"}
    selected_layers = {"single_factor_model", "factor_only_model"}
    inherited_layers = {"factor_state_model", "rsp_ablation"}
    for row in processes:
        if row["layer"] in modeled_layers:
            _require(row["target_id"] == "Y_XS_FREQ_RANK", "modeled target id")
            _require(
                row["refit_rule"] == "monthly_refit_annual_recipe_freeze",
                "monthly refit rule",
            )
            _require(
                row["training_memory"] == "latest_260_weekly_or_60_monthly_complete_dates",
                "training memory",
            )
        else:
            _require(row["target_id"] == "NONE", "non-model target id")
        if row["layer"] in selected_layers:
            _require(row["annual_recipe_selection"] == "true", "annual selector flag")
            _require(row["recipe_inherit_from"] == "", "selector inheritance")
        elif row["layer"] in inherited_layers:
            _require(row["annual_recipe_selection"] == "false", "inherited selector flag")
            _require(row["selector_recipe_ids"] == "INHERIT", "recipe inheritance")
            _require(row["recipe_inherit_from"].startswith("FO_"), "A2 parent recipe")

    declared = program["processes"]
    assert isinstance(declared, dict)
    _require(declared["raw_control_processes"] == 14, "declared raw controls")
    _require(declared["single_factor_model_processes"] == 28, "declared S1")
    _require(declared["static_aggregation_processes"] == 3, "declared A0")
    _require(declared["factor_only_aggregation_processes"] == 4, "declared A1")
    _require(declared["factor_state_processes"] == 4, "declared with-RSP A2")
    _require(declared["rsp_ablation_processes"] == 4, "declared no-RSP A2")
    _require(declared["total_processes"] == 57, "declared process count")
    _require(declared["frequencies_per_process"] == 2, "declared frequencies")
    _require(declared["total_process_frequency_cells"] == 114, "declared cells")


def _expected_paired_comparisons() -> dict[str, str]:
    parents: dict[str, str] = {}
    for family in ("RIDGE", "LGBM"):
        for factor_id in FACTOR_IDS:
            parents[f"UNI_{family}__{factor_id}"] = f"RAW__{factor_id}"
    for process_id in STATIC_PROCESS_IDS:
        parents[process_id] = "RAW__XS003_MOM_12_7"
    parents.update(
        {
            "FO_RIDGE__ROLE5": "STATIC_EQ_ROLE5",
            "FO_LGBM__ROLE5": "STATIC_EQ_ROLE5",
            "FO_RIDGE__ALL14": "STATIC_DIM6_ALL14",
            "FO_LGBM__ALL14": "STATIC_DIM6_ALL14",
            "FS_RIDGE__ROLE5_S2": "FO_RIDGE__ROLE5",
            "FS_LGBM__ROLE5_S2": "FO_LGBM__ROLE5",
            "FS_RIDGE__ALL14_S6": "FO_RIDGE__ALL14",
            "FS_LGBM__ALL14_S6": "FO_LGBM__ALL14",
        }
    )
    return parents


def _validate_comparison_registry(root: Path, program: dict[str, object]) -> None:
    registries = program["registries"]
    assert isinstance(registries, dict)
    rows = _rows(root / str(registries["comparison_registry"]))
    _require(len(rows) == 43, "comparison count")
    _require(len({row["comparison_id"] for row in rows}) == 43, "comparison ids")
    paired = [row for row in rows if row["family"] == "paired_promotion"]
    rsp = [row for row in rows if row["family"] == "rsp_ablation"]
    _require(len(paired) == 39, "paired-promotion family count")
    _require(len(rsp) == 4, "RSP-ablation family count")

    expected_paired = _expected_paired_comparisons()
    _require(len(expected_paired) == 39, "internal paired comparison count")
    observed_paired = {row["candidate_process_id"]: row["parent_process_id"] for row in paired}
    _require(observed_paired == expected_paired, "paired candidate-parent mapping")
    for row in paired:
        _require(row["comparison_id"] == f"PAIR__{row['candidate_process_id']}", "paired id")
        _require(row["frequency_scope"] == "weekly|monthly", "paired frequency")
        _require(row["bh_family_size_per_frequency"] == "39", "paired BH m")
        _require(row["promotion_authority"] == "true", "paired promotion authority")

    expected_rsp = {
        "FS_RIDGE__ROLE5_S2": "AB_RIDGE__ROLE5_S1_NO_RSP",
        "FS_LGBM__ROLE5_S2": "AB_LGBM__ROLE5_S1_NO_RSP",
        "FS_RIDGE__ALL14_S6": "AB_RIDGE__ALL14_S5_NO_RSP",
        "FS_LGBM__ALL14_S6": "AB_LGBM__ALL14_S5_NO_RSP",
    }
    observed_rsp = {row["candidate_process_id"]: row["parent_process_id"] for row in rsp}
    _require(observed_rsp == expected_rsp, "RSP/no-RSP comparison mapping")
    for row in rsp:
        _require(row["comparison_id"] == f"RSP__{row['candidate_process_id']}", "RSP id")
        _require(row["frequency_scope"] == "weekly|monthly", "RSP frequency")
        _require(row["bh_family_size_per_frequency"] == "4", "RSP BH m")
        _require(row["promotion_authority"] == "false", "RSP diagnostic authority")


def _validate_docs(root: Path) -> None:
    design = (
        root / "docs/20_experiments/XA03_cross_sectional_aggregation/design.md"
    ).read_text(encoding="utf-8")
    program_doc = (
        root / "docs/46_xa03_cross_sectional_aggregation_program_v1.md"
    ).read_text(encoding="utf-8")
    for fragment in (
        "57 prediction processes per frequency",
        "114 prediction processes",
        "456 Top-K paths",
        "1,824",
        "Main absolute family: 53 per frequency",
        "Parent-child incremental family: 39 per frequency",
        "RSP incremental family: 4 per frequency",
        "P00 is not run",
    ):
        _require(fragment in design, f"design document missing: {fragment}")
    for fragment in (
        "57 prediction processes per frequency",
        "114 across weekly and",
        "456 signal paths",
        "1,824",
        "No-RSP A2 processes are mechanism controls",
        "cannot run P00",
    ):
        _require(fragment in program_doc, f"program document missing: {fragment}")


def _validate_design(root: Path, program: dict[str, object]) -> None:
    _require(
        program["program_id"] == "xa03_cross_sectional_walkforward_aggregation_v1",
        "program id",
    )
    _require(program["status"] == "preregistered_authorized", "program status")
    _require(program["formal_eligible"] is False, "formal eligibility must be false")
    _require(
        program["batches"]["order"] == ["XA03A", "XA03B", "XA03C", "XA03D", "XA03E"],
        "batch order",
    )
    _require(
        program["runtime"]["run_directory_template"]
        == "results/experiments/xa03/{batch_id}/runs/{run_id}",
        "runtime path template",
    )

    sample = program["sample"]
    assert isinstance(sample, dict)
    _require(sample["evaluation_start_open"] == "2018-01-02", "evaluation start")
    _require(sample["evaluation_end_close"] == "2026-06-30", "evaluation end")
    _require(sample["frequencies"] == ["weekly", "monthly"], "frequencies")
    _require(sample["static_train_test_split"] is False, "static split closed")
    _require(sample["lockbox"] is False, "sample lockbox closed")

    target = program["training_targets"]
    assert isinstance(target, dict)
    _require(target["supplemental_target_build"] is True, "supplemental target")
    _require(target["overlap_with_xa01_must_be_exact"] is True, "target overlap")
    _require(target["terminal_incomplete_labels_forbidden"] is True, "terminal labels")
    _require(
        target["availability_rule"] == "target_available_at<=prediction_signal_close",
        "target availability timestamp rule",
    )
    _require(target["weekly_target_id"] == "Y_XS_1W_RANK", "weekly target")
    _require(target["monthly_target_id"] == "Y_XS_1M_RANK", "monthly target")
    expected_identity = [
        "forward_total_return",
        "forward_cash_return",
        "forward_excess_cash",
        "target_available_at",
        "target_valid",
    ]
    _require(target["source_columns"] == expected_identity, "target source columns")
    _require(
        target["stock_return_formula"]
        == "tr_open(label_end_execution_date)/tr_open(execution_date)-1",
        "stock target return formula",
    )
    _require(
        target["cash_return_formula"]
        == "product(1+rf_return[d] for execution_date<=d<label_end_execution_date)-1",
        "cash target return formula",
    )
    _require(
        target["excess_return_formula"] == "forward_total_return-forward_cash_return",
        "target excess-return formula",
    )
    _require(
        target["target_available_timestamp_semantics"] == "label_end_execution_open",
        "target-availability semantics",
    )
    _require(target["parent_overlap_identity_columns"] == expected_identity, "target identity")
    _require(target["rank_method"] == "average", "target tie method")
    _require(
        target["rank_order"] == "ascending_forward_return_low_minus_one_high_plus_one",
        "target rank order",
    )
    _require(
        target["transform"] == "2*(average_rank-1)/(finite_rank_universe_count-1)-1",
        "target rank transform",
    )
    _require(target["costs_in_target"] is False, "target costs")
    _require(target["xa01_forward_rank_may_not_be_used_as_model_target"] is True, "rank reuse")
    _require(target["prediction_universe_may_not_use_target_valid"] is True, "future target filter")

    common = program["common_universe"]
    assert isinstance(common, dict)
    _require(common["universe_id"] == "COMMON10_OF_14", "common universe id")
    _require(common["factor_count"] == 14, "common universe factor count")
    _require(
        common["factor_availability_rule"]
        == "eligible==true_and_percentile_is_finite_at_signal_close",
        "common-universe factor availability",
    )
    _require(common["minimum_available_factors"] == 10, "COMMON10 threshold")
    _require(common["minimum_names_per_signal"] == 100, "common name threshold")
    _require(common["future_target_validity_may_filter_prediction"] is False, "prediction filter")
    _require(
        common["prediction_eligibility_may_use_only"]
        == [
            "pit_membership_at_signal_close",
            "stable_sid",
            "factor_eligible_at_signal_close",
            "finite_factor_percentile_at_signal_close",
        ],
        "prediction eligibility information set",
    )
    _require(
        common["next_open_price_or_execution_success_may_filter_prediction"] is False,
        "future execution filter",
    )
    _require(
        common["execution_failure_policy"]
        == "use_frozen_xa01_execution_accounting_and_never_rerank_or_backdelete",
        "execution-failure accounting",
    )
    _require(common["common_base_universe_for_all_processes"] is True, "common base universe")
    _require(common["common_ew_control_for_all_processes"] is True, "common EW control")
    _require(
        common["single_factor_focal_finite_overlay_is_process_specific"] is True,
        "single-factor focal-finite universe overlay",
    )
    _require(
        common["single_factor_parent_child_name_sets_must_match"] is True,
        "single-factor parent/child name-set identity",
    )

    features = program["features"]
    assert isinstance(features, dict)
    _require(features["factor_input"] == "2*cross_sectional_percentile-1", "factor input")
    _require(
        features["single_factor_training_selection_and_prediction_requires_factor_finite"]
        is True,
        "S1 focal factor must be finite for training, selection, and prediction",
    )
    _require(features["multifactor_missing_policy"] == "neutral_zero_after_centering", "missing policy")
    _require(features["ridge_missing_policy"] == "neutral_zero_no_missing_indicator", "Ridge missing")
    _require(
        features["lightgbm_missing_policy"]
        == "neutral_zero_no_native_nan_no_missing_indicator",
        "LightGBM missing",
    )
    _require(features["missing_indicators_authorized"] is False, "missing indicators")
    _require(features["state_transform_fit_on_training_only"] is True, "state transform")
    _require(features["state_quantile_method"] == "linear", "state quantile method")
    _require(
        features["state_clip_train_validation_and_prediction_to_training_q01_q99"]
        is True,
        "state train-window clipping",
    )
    _require(
        features["state_standardization_mean"]
        == "arithmetic_mean_of_clipped_unique_training_dates",
        "state standardization mean",
    )
    _require(
        features["state_standardization_std"]
        == "population_std_ddof0_of_clipped_unique_training_dates",
        "state standardization std",
    )
    _require(
        features["state_zero_or_nonfinite_std_policy"] == "process_fit_invalid",
        "state standardization failure policy",
    )
    _require(features["state_inner_fold_recomputes_transform"] is True, "inner state transform")
    _require(features["state_current_missing_policy"] == "fail_closed", "current state missing")
    _require(features["state_training_missing_date_policy"] == "exclude_complete_date", "training state missing")
    _require(features["state_training_missing_imputation_authorized"] is False, "state imputation")
    _require(features["shadow_states_authorized"] is False, "shadow states")
    _require(features["discrete_state_classifier_authorized"] is False, "state classifier")
    _require(features["ridge_state_main_effects_authorized"] is False, "state main effects")
    _require(
        features["ridge_state_feature_rule"] == "bundle_specific_frozen_interactions_below",
        "bundle-specific Ridge interaction rule",
    )
    _require(
        features["ridge_role5_s2_interactions"]
        == [
            "XS002_MOM_12_1*MKT_BREADTH_RSP63",
            "XS002_MOM_12_1*centered_square(MKT_BREADTH_RSP63)",
            "XS008_SAME_MONTH_5Y*MKT_TREND126",
            "XS008_SAME_MONTH_5Y*centered_square(MKT_TREND126)",
        ],
        "ROLE5+S2 Ridge interactions",
    )
    _require(
        features["ridge_role5_s1_no_rsp_interactions"]
        == [
            "XS008_SAME_MONTH_5Y*MKT_TREND126",
            "XS008_SAME_MONTH_5Y*centered_square(MKT_TREND126)",
        ],
        "ROLE5 trend-only Ridge interactions",
    )
    _require(
        features["ridge_all14_s6_interaction_rule"]
        == "full_cartesian_linear_factor_x_state",
        "ALL14+S6 Ridge interactions",
    )
    _require(
        features["ridge_all14_s5_no_rsp_interaction_rule"]
        == "full_cartesian_linear_factor_x_state_excluding_MKT_BREADTH_RSP63",
        "ALL14 no-RSP Ridge interactions",
    )
    _require(
        features["ridge_quadratic_state_centering"]
        == "standardized_state_squared_minus_unique_training_date_mean_squared",
        "Ridge quadratic-state centering",
    )

    walk = program["walk_forward"]
    assert isinstance(walk, dict)
    _require(walk["maximum_training_dates_weekly"] == 260, "weekly maximum memory")
    _require(walk["maximum_training_dates_monthly"] == 60, "monthly maximum memory")
    _require(walk["minimum_complete_training_dates_weekly"] == 156, "weekly minimum")
    _require(walk["minimum_complete_training_dates_monthly"] == 36, "monthly minimum")
    _require(walk["model_refit_cadence"] == "monthly", "refit cadence")
    _require(
        walk["weekly_refit_rule"]
        == "fit_at_each_monthly_signal_close_apply_on_same_date_only_if_it_is_also_weekly_else_first_subsequent_weekly_signal_then_carry_until_next_monthly_refit",
        "weekly monthly-refit rule",
    )
    _require(walk["monthly_refit_rule"] == "every_monthly_signal", "monthly refit")
    _require(walk["prediction_requires_all_labels_used_in_fit_to_be_mature"] is True, "mature labels")

    selector = program["annual_recipe_selection"]
    assert isinstance(selector, dict)
    _require(selector["enabled"] is True, "annual recipe selection")
    _require(selector["selection_time"] == "before_first_oos_signal_of_each_calendar_year", "annual timing")
    _require(
        selector["recipe_selection_year_key"] == "first_execution_open_calendar_year",
        "execution-open recipe year key",
    )
    _require(selector["weekly_validation_block_dates"] == 26, "weekly inner block")
    _require(selector["monthly_validation_block_dates"] == 6, "monthly inner block")
    _require(selector["minimum_inner_training_dates_weekly"] == 104, "weekly inner train")
    _require(selector["minimum_inner_training_dates_monthly"] == 24, "monthly inner train")
    _require(selector["minimum_legal_validation_blocks"] == 3, "validation blocks")
    _require(
        selector["one_se_method"]
        == "moving_block_bootstrap_of_best_minus_candidate_date_rank_ic",
        "one-SE method",
    )
    _require(
        selector["one_se_best_tie_break"]
        == "lowest_capacity_rank_then_recipe_id_among_exact_equal_mean_ic",
        "one-SE best tie break",
    )
    _require(selector["one_se_weekly_block_dates"] == 13, "weekly one-SE block")
    _require(selector["one_se_monthly_block_dates"] == 3, "monthly one-SE block")
    _require(selector["one_se_bootstrap_draws"] == 5000, "one-SE draws")
    _require(selector["one_se_global_seed"] == 20260821, "one-SE seed")
    _require(selector["one_se_rng"] == "numpy_default_rng_pcg64", "one-SE RNG")
    _require(
        selector["one_se_seed_derivation"]
        == "uint32_from_first_8_hex_sha256(global_seed|inner_one_se|process_id|frequency|execution_year)",
        "one-SE seed derivation",
    )
    _require(
        selector["one_se_resample"]
        == "sample_all_non_circular_contiguous_blocks_with_replacement_concatenate_and_truncate_to_n_dates",
        "one-SE resampling",
    )
    _require(
        selector["one_se_standard_error"]
        == "sample_std_ddof1_of_bootstrap_mean_differences",
        "one-SE standard error",
    )
    _require(
        selector["one_se_inclusion_rule"]
        == "mean(best_minus_candidate)<=standard_error(best_minus_candidate)",
        "one-SE inclusion rule",
    )
    _require(
        selector["one_se_tie_break"] == "lowest_capacity_rank_then_recipe_id",
        "one-SE capacity tie break",
    )
    _require(selector["selected_recipe_frozen_for_calendar_year"] is True, "year freeze")
    _require(selector["selection_occurs_inside_process"] is True, "selector process")
    _require(selector["state_process_inherits_matched_factor_only_recipe"] is True, "state recipe")
    _require(selector["state_process_may_not_reselect_recipe"] is True, "state reselection")

    models = program["models"]
    assert isinstance(models, dict)
    _require(models["families"] == ["ridge", "lightgbm"], "model families")
    _require(models["seed"] == 20260821, "model seed")
    _require(models["ridge_fit_intercept"] is True, "Ridge intercept")
    _require(models["ridge_loss"] == "squared_error", "Ridge loss")
    _require(models["ridge_solver"] == "cholesky", "Ridge solver")
    _require(models["ridge_tol"] == 1e-8, "Ridge tolerance")
    _require(models["ridge_max_iter"] == "none", "Ridge maximum iterations")
    _require(models["lightgbm_minimum_independent_dates_per_leaf_weekly"] == 26, "weekly leaf audit")
    _require(models["lightgbm_minimum_independent_dates_per_leaf_monthly"] == 12, "monthly leaf audit")
    _require(
        models["lightgbm_minimum_independent_calendar_years_per_leaf"] == 2,
        "LightGBM leaf calendar-year audit",
    )
    _require(models["all_recipes_invalid_process_policy"] == "fail_closed", "invalid recipes")
    _require(
        models["outer_selected_recipe_fit_or_leaf_support_failure_policy"]
        == "entire_process_frequency_invalid_no_recipe_fallback_or_carry",
        "outer recipe/leaf failure policy",
    )
    _require(
        models["outer_failure_registered_in_fixed_families_with_p1"] is True,
        "outer failure fixed-family policy",
    )

    dependencies = program["dependencies"]
    assert isinstance(dependencies, dict)
    expected_versions = {
        "python": "3.12.13",
        "numpy": "2.3.5",
        "pandas": "3.0.1",
        "scipy": "1.18.0",
        "scikit_learn": "1.9.0",
        "lightgbm": "4.6.0",
        "pyarrow": "25.0.1",
    }
    for name, version in expected_versions.items():
        _require(dependencies[name] == version, f"dependency version: {name}")
    _require(
        dependencies["pyproject_sha256"] == _sha(root / "pyproject.toml"),
        "pyproject hash mismatch",
    )
    round7_acceptance = _json(root / "config/experiments/round7/PARENT_ACCEPTANCE.json")
    round7_dependency = round7_acceptance["dependency"]
    assert isinstance(round7_dependency, dict)
    dependency_anchors = {
        "lightgbm_metadata_sha256": "metadata_sha256",
        "lightgbm_dll_sha256": "dll_sha256",
        "lightgbm_reference_deterministic_prediction_sha256": (
            "deterministic_prediction_sha256"
        ),
    }
    for field, accepted_field in dependency_anchors.items():
        _require(
            dependencies[field] == round7_dependency[accepted_field],
            f"LightGBM dependency anchor: {field}",
        )
    _require(
        dependencies["xa03_prediction_repeatability_must_be_recomputed"] is True,
        "XA03 prediction repeatability",
    )

    paths = program["paths"]
    assert isinstance(paths, dict)
    _require(paths["top_k"] == [5, 10, 20, 50], "TopK grid")
    _require(paths["cost_scenarios_bps"] == [0, 5, 10, 20], "cost grid")
    _require(paths["score_process_paths"] == 114, "score paths")
    _require(paths["topk_signal_paths"] == 456, "TopK paths")
    _require(paths["cost_paths"] == 1824, "cost paths")
    _require(paths["common_ew_cost_control_paths"] == 8, "common-EW cost controls")
    _require(
        paths["common_ew_cost_control_paths"]
        == len(sample["frequencies"]) * len(paths["cost_scenarios_bps"]),
        "common-EW control derivation",
    )

    inference = program["inference"]
    assert isinstance(inference, dict)
    _require(inference["weekly_moving_block_periods"] == 13, "weekly outer block")
    _require(inference["monthly_moving_block_periods"] == 3, "monthly outer block")
    _require(inference["bootstrap_draws"] == 5000, "outer bootstrap draws")
    _require(inference["bootstrap_seed"] == 20260821, "outer bootstrap seed")
    _require(inference["bootstrap_rng"] == "numpy_default_rng_pcg64", "outer RNG")
    _require(
        inference["bootstrap_method"]
        == "circular_moving_block_on_complete_scheduled_calendar",
        "outer bootstrap calendar",
    )
    _require(
        inference["bootstrap_seed_derivation"]
        == "uint32_from_first_8_hex_sha256(global_seed|outer_inference|frequency|outcome|family)",
        "outer seed derivation",
    )
    _require(
        inference["bootstrap_family_draws_shared_across_all_registered_members"] is True,
        "shared outer family draws",
    )
    _require(
        inference["bootstrap_resample"]
        == "sample_circular_contiguous_blocks_with_replacement_concatenate_and_truncate_to_calendar_length",
        "outer resampling",
    )
    _require(inference["bootstrap_missing_slot_weight"] == 0.0, "missing-slot weight")
    _require(
        inference["bootstrap_all_zero_weight_draw_statistic"] == 0.0,
        "all-zero bootstrap statistic",
    )
    _require(
        inference["one_sided_p_value"]
        == "(1+count(bootstrap_weighted_mean<=0))/(draws+1)",
        "outer one-sided p-value",
    )
    _require(inference["bh_method"] == "benjamini_hochberg_step_up", "BH method")
    _require(
        inference["bh_tie_order"] == "comparison_or_process_id_ascending",
        "BH tie order",
    )
    _require(
        inference["bh_q_formula"] == "min_over_j_ge_i(min(1,m*p_sorted_j/j))",
        "BH q-value formula",
    )
    _require(inference["absolute_candidate_tests_per_frequency"] == 53, "absolute BH m")
    _require(inference["predictive_diagnostic_tests_per_frequency"] == 53, "RankIC BH m")
    _require(inference["paired_promotion_tests_per_frequency"] == 39, "paired BH m")
    _require(inference["rsp_ablation_tests_per_frequency"] == 4, "RSP BH m")
    _require(
        inference["bh_families"]
        == [
            "absolute_economic_by_frequency",
            "absolute_rank_ic_diagnostic_by_frequency",
            "paired_economic_by_frequency",
            "paired_rank_ic_diagnostic_by_frequency",
            "rsp_economic_by_frequency",
            "rsp_rank_ic_diagnostic_by_frequency",
        ],
        "six fixed outcome/frequency BH families",
    )
    _require(inference["economic_bh_controls_advancement"] is True, "economic BH authority")
    _require(
        inference["rank_ic_bh_is_diagnostic_not_alternative_advancement"] is True,
        "RankIC BH authority",
    )
    _require(inference["insufficient_sample_p_for_fixed_bh_family"] == 1.0, "fixed m")
    _require(inference["causality_or_data_failure_policy"] == "fail_entire_batch", "causal fail")
    _require(inference["calendar_year_key"] == "execution_open_calendar_year", "calendar-year key")
    _require(
        inference["leave_one_year_out_year_set"]
        == "all_evaluation_execution_years_with_at_least_one_complete_period",
        "LOYO year set",
    )
    _require(
        inference["year_contribution_fraction"]
        == "abs(year_relative_log_sum)/sum_over_years(abs(year_relative_log_sum))",
        "year-contribution formula",
    )
    _require(
        inference["zero_year_contribution_denominator_policy"] == "gate_fail",
        "zero year-contribution policy",
    )
    _require(inference["subperiod_boundary_key"] == "execution_open", "subperiod key")
    _require(inference["max_drawdown_is_hard_gate"] is False, "MDD diagnostic")
    _require(inference["turnover_is_hard_gate"] is False, "turnover diagnostic")

    absolute = program["absolute_qualification"]
    assert isinstance(absolute, dict)
    _require(
        absolute["top20_primary_cost_annualized_relative_log_increment_vs_common_ew_minimum"]
        == 0.02,
        "absolute economic effect gate",
    )
    _require(absolute["economic_bh_q_maximum"] == 0.10, "absolute BH gate")
    _require(
        absolute["terminal_wealth_ratio_candidate_to_common_ew_must_exceed"] == 1.0,
        "absolute wealth gate",
    )
    _require(absolute["active_ir_vs_common_ew_must_be_positive"] is True, "absolute IR gate")
    _require(absolute["mean_rank_ic_must_be_positive"] is True, "absolute RankIC gate")
    _require(
        absolute["rank_ic_bh_q_is_not_required_for_economic_qualification"] is True,
        "absolute RankIC diagnostic authority",
    )
    _require(absolute["twenty_bps_direction_must_match"] is True, "absolute 20-bps gate")
    _require(absolute["topk_widths_for_direction"] == [10, 20, 50], "absolute TopK widths")
    _require(
        absolute["topk_direction_cost"] == "frequency_primary_cost",
        "absolute TopK cost",
    )
    _require(absolute["minimum_topk_widths_same_direction"] == 2, "absolute TopK gate")
    _require(absolute["top20_must_have_same_direction"] is True, "absolute Top20 gate")
    _require(absolute["twenty_bps_direction_path"] == "top20_20bps", "absolute 20-bps path")
    _require(
        absolute["leave_one_year_out_path"] == "top20_frequency_primary_cost",
        "absolute LOYO path",
    )
    _require(absolute["leave_one_year_out_direction_fraction_minimum"] == 0.75, "absolute LOYO gate")
    _require(absolute["maximum_single_year_absolute_contribution_fraction"] == 0.50, "absolute concentration gate")

    promotion = program["paired_promotion"]
    assert isinstance(promotion, dict)
    _require(promotion["candidate_count_cap"] == 0, "candidate cap")
    _require(promotion["topk_widths_for_direction"] == [10, 20, 50], "TopK direction widths")
    _require(promotion["minimum_topk_widths_same_direction"] == 2, "TopK direction gate")
    _require(promotion["top20_must_have_same_direction"] is True, "Top20 direction gate")
    _require(promotion["twenty_bps_increment_direction_must_match"] is True, "paired 20-bps gate")
    _require(
        promotion["topk_direction_cost"] == "frequency_primary_cost",
        "paired TopK cost",
    )
    _require(
        promotion["twenty_bps_increment_path"] == "top20_20bps_child_minus_parent",
        "paired 20-bps path",
    )
    _require(
        promotion["leave_one_year_out_path"]
        == "top20_frequency_primary_cost_child_minus_parent",
        "paired LOYO path",
    )
    _require(promotion["leave_one_year_out_direction_fraction_minimum"] == 0.75, "paired LOYO gate")
    _require(
        promotion["maximum_single_year_absolute_increment_contribution_fraction"]
        == 0.50,
        "paired concentration gate",
    )

    rsp_evidence = program["rsp_evidence"]
    assert isinstance(rsp_evidence, dict)
    _require(rsp_evidence["economic_increment_must_be_positive"] is True, "RSP effect gate")
    _require(rsp_evidence["economic_bh_q_maximum"] == 0.10, "RSP BH gate")
    _require(rsp_evidence["mean_rank_ic_increment_must_be_positive"] is True, "RSP RankIC gate")
    _require(rsp_evidence["rank_ic_bh_q_is_diagnostic_not_required"] is True, "RSP RankIC authority")
    _require(rsp_evidence["twenty_bps_direction_must_match"] is True, "RSP 20-bps gate")
    _require(rsp_evidence["topk_widths_for_direction"] == [10, 20, 50], "RSP TopK widths")
    _require(
        rsp_evidence["topk_direction_cost"] == "frequency_primary_cost",
        "RSP TopK cost",
    )
    _require(rsp_evidence["minimum_topk_widths_same_direction"] == 2, "RSP TopK gate")
    _require(rsp_evidence["top20_must_have_same_direction"] is True, "RSP Top20 gate")
    _require(
        rsp_evidence["twenty_bps_increment_path"]
        == "top20_20bps_with_rsp_minus_no_rsp",
        "RSP 20-bps path",
    )

    roles = program["roles"]
    assert isinstance(roles, dict)
    statuses = ["invalid", "qualified_incremental", "qualified_absolute_only", "not_qualified"]
    _require(roles["primary_statuses"] == statuses, "primary status universe")
    _require(roles["primary_status_precedence"] == statuses, "primary status precedence")
    _require(
        roles["nonexclusive_tags"]
        == [
            "absolute_qualified",
            "incremental_qualified",
            "state_incremental_qualified",
            "rsp_incremental_supported",
            "broadly_robust",
            "conditional_specialist",
            "predictive_only",
            "exploratory_unstable",
            "rsp_harm_point_estimate",
        ],
        "nonexclusive role tags",
    )
    _require(roles["qualified_incremental_requires_absolute_and_paired_gates"] is True, "incremental role")
    _require(roles["qualified_absolute_only_requires_absolute_gate_and_no_paired_gate"] is True, "absolute-only role")
    _require(roles["no_rsp_diagnostic_cannot_receive_qualified_status"] is True, "no-RSP role")
    _require(roles["predictive_only_requires_rank_ic_bh_q_maximum"] == 0.10, "predictive role")
    _require(roles["predictive_only_cannot_rescue_economic_failure"] is True, "predictive authority")
    _require(
        roles["broadly_robust_rule"]
        == "absolute_qualified_and_both_fixed_subperiod_relative_log_increments_gt0_and_year_contribution_gate_passes",
        "broadly robust role",
    )
    _require(
        roles["conditional_specialist_rule"]
        == "absolute_qualified_and_fixed_subperiod_relative_log_increment_product_lt0",
        "conditional specialist role",
    )
    _require(
        roles["state_concentration_is_descriptive_not_automatic_role"] is True,
        "state concentration role authority",
    )
    _require(
        roles["exploratory_unstable_rule"]
        == "raw_one_sided_economic_or_rank_ic_p<=0.05_but_relevant_q_or_registered_stability_gate_fails",
        "exploratory unstable role",
    )
    _require(
        roles["rsp_harm_point_estimate_rule"]
        == "with_rsp_minus_no_rsp_top20_primary_cost_annualized_relative_log_increment<0",
        "RSP harm point-estimate tag",
    )
    _require(
        roles["rsp_harm_point_estimate_is_descriptive_not_significance"] is True,
        "RSP harm authority",
    )
    _require(roles["automatic_winner_selection"] is False, "automatic winner selection")

    authorization = program["authorization"]
    assert isinstance(authorization, dict)
    for field in (
        "supplemental_training_targets",
        "raw_common_universe_controls",
        "single_factor_models",
        "factor_only_aggregation",
        "factor_state_aggregation",
        "rsp_no_rsp_ablation",
        "portfolio_backtests",
        "paired_model_comparisons",
    ):
        _require(authorization[field] is True, f"authorization must be true: {field}")
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
        _require(authorization[field] is False, f"authorization must be false: {field}")

    provenance = program["execution_provenance"]
    assert isinstance(provenance, dict)
    _require(provenance["prereg_lock_required_before_execution"] is True, "prereg required")
    _require(provenance["git_clean_commit_required"] is True, "clean Git required")
    _require(provenance["future_feature_perturbation_invariance_required"] is True, "feature causality")
    _require(provenance["future_label_perturbation_invariance_required"] is True, "label causality")
    _require(provenance["prediction_repeatability_required"] is True, "repeatability")

    hard_stop = program["hard_stop"]
    assert isinstance(hard_stop, dict)
    _require(hard_stop["after_batch"] == "XA03E", "XA03 hard stop")
    for field in (
        "automatic_xa04",
        "automatic_p00",
        "automatic_stacking",
        "automatic_bagging",
        "automatic_lockbox",
    ):
        _require(hard_stop[field] is False, f"hard-stop flag: {field}")
    _require(hard_stop["user_review_required"] is True, "user review hard stop")

    _validate_factor_and_feature_registries(root, program)
    _validate_model_and_process_registries(root, program)
    _validate_comparison_registry(root, program)
    _validate_parent_evidence(root, program)
    _validate_docs(root)


def build(project_root: Path) -> dict[str, object]:
    program_path = project_root / "config/experiments/xa03/program.toml"
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
        "schema_version": "xa03.prereg_lock.v1",
        "program_id": "xa03_cross_sectional_walkforward_aggregation_v1",
        "status": "locked_authorized",
        "formal_eligible": False,
        "models_authorized": True,
        "factor_aggregation_authorized": True,
        "factor_state_aggregation_authorized": True,
        "p00_authorized": False,
        "lockbox_authorized": False,
        "hard_stop_after": "XA03E",
        "processes_per_frequency": 57,
        "process_frequency_cells": 114,
        "topk_signal_paths": 456,
        "cost_paths": 1824,
        "common_ew_cost_control_paths": 8,
        "absolute_candidates_per_frequency": 53,
        "no_rsp_diagnostics_per_frequency": 4,
        "paired_promotion_comparisons_per_frequency": 39,
        "rsp_ablation_comparisons_per_frequency": 4,
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
        raise SystemExit("XA03 preregistration lock mismatch")
    print(_sha(lock_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
