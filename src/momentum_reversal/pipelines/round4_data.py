"""Immutable R4A free-factor data gate.

The builder is intentionally incapable of materializing targets, evaluating
signals, replaying strategies, or classifying drawdown events.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata as importlib_metadata
import json
from pathlib import Path
import shutil
import subprocess
import tomllib
from typing import Any

import numpy as np
import pandas as pd

from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.round2_market import canonical_arrow_sha256, sha256_file
from momentum_reversal.data.round4_factors import (
    OLD_ARM_COLUMNS,
    build_round4_core_scores,
    build_rsp_spy_score,
    build_spy_volume_scores,
    eligibility_from_weekly,
    lagged_fred_at_signals,
    normalize_rsp_tiingo,
    parse_fred_csv,
    weekly_long_table,
)


PROGRAM_ID = "defense_factor_audit_round4_v1"
BATCH_ID = "R4A_FREE_FACTOR_DATA"
FORBIDDEN_TOKENS = (
    "cash_wins_1w",
    "fwd_excess_logret_1w",
    "fwd_worst_excess",
    "strategy_nav",
    "event_peak",
    "event_trough",
)


@dataclass(frozen=True, slots=True)
class R4AResult:
    output_dir: Path
    manifest_path: Path
    status: str
    reference_eligible_arms: int
    invalid_arms: int
    common_weeks: int


def build_r4a_factor_data(
    *,
    config_path: str | Path,
    project_root: str | Path,
    runtime_root: str | Path,
    source_cache_dir: str | Path,
    run_id: str,
) -> R4AResult:
    root = Path(project_root).resolve()
    runtime = Path(runtime_root).resolve()
    cache = Path(source_cache_dir).resolve()
    config_file = Path(config_path).resolve()
    config = _load_config_and_plan_lock(config_file, root)
    if not run_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-." for character in run_id):
        raise ValueError(f"unsafe run_id: {run_id!r}")

    output = runtime / "data" / "round4" / "staging" / BATCH_ID / run_id
    output.mkdir(parents=True, exist_ok=False)
    raw_dir = output / "raw"
    raw_dir.mkdir()

    parent = (
        runtime
        / "data"
        / "round2"
        / "staging"
        / "R2A_DATA"
        / str(config["parents"]["r2a_snapshot_id"])
    )
    parent_manifest_path = parent / "manifest.json"
    _verify_sha(parent_manifest_path, str(config["parents"]["r2a_manifest_sha256"]))
    parent_manifest = json.loads(parent_manifest_path.read_text(encoding="utf-8"))
    _verify_manifest_files(parent, parent_manifest)

    market = pd.read_parquet(parent / "curated" / "market_daily.parquet")
    vix = pd.read_parquet(parent / "curated" / "vix_daily.parquet")
    decision = pd.read_parquet(parent / "curated" / "decision_calendar.parquet")
    for column in ("signal_session", "execution_session"):
        decision[column] = pd.to_datetime(decision[column]).dt.normalize()
    cutoff = pd.Timestamp(config["dates"]["maximum_prediction_signal"])
    decision = decision.loc[decision["signal_session"] <= cutoff].reset_index(drop=True)

    cache_names = {
        "fred_treasury": "r4a_treasury.csv",
        "fred_hy_oas": "r4a_hyoas_current.csv",
        "tiingo_rsp": "r4a_tiingo_rsp_2003_2021.json",
        "fred_nfci_current": "r4a_nfci_current_probe.csv",
        "alfred_nfci_form": "r4a_alfred_nfci_form.html",
    }
    raw_paths: dict[str, Path] = {}
    for source_id, filename in cache_names.items():
        source = cache / filename
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = raw_dir / filename
        shutil.copyfile(source, destination)
        raw_paths[source_id] = destination

    core = build_round4_core_scores(market, decision)
    _verify_legacy_anchor(core, runtime)
    calendar_index = core.index
    arm_values: dict[str, pd.Series] = {
        arm_id: pd.Series(core[column].to_numpy(float), index=calendar_index)
        for arm_id, column in OLD_ARM_COLUMNS.items()
    }
    source_status: dict[str, tuple[str, str]] = {
        arm_id: ("available", "R2A frozen market core; exact R2B formula")
        for arm_id in OLD_ARM_COLUMNS
    }

    vix_frame = vix.copy()
    vix_frame["session_date"] = pd.to_datetime(vix_frame["session_date"]).dt.normalize()
    vix_weekly = decision[["signal_session"]].merge(
        vix_frame[["session_date", "vix_close_percent"]],
        left_on="signal_session",
        right_on="session_date",
        how="left",
        validate="one_to_one",
    )
    vix_decimal = pd.to_numeric(vix_weekly["vix_close_percent"], errors="coerce") / 100.0
    arm_values["R4B__VIX_LEVEL"] = pd.Series(np.log(vix_decimal), index=calendar_index)
    arm_values["R4B__VIX_RV_GAP"] = pd.Series(
        vix_decimal.pow(2).to_numpy(float) - core["spy_rv21"].pow(2).to_numpy(float),
        index=calendar_index,
    )
    source_status["R4B__VIX_LEVEL"] = ("available", "Cboe parent snapshot; exact-date no-fill")
    source_status["R4B__VIX_RV_GAP"] = ("available", "Cboe VIX plus R2A RV21")

    volume_daily = build_spy_volume_scores(market)
    volume_weekly = decision[["signal_session"]].merge(
        volume_daily,
        left_on="signal_session",
        right_on="session_date",
        how="left",
        validate="one_to_one",
    )
    arm_values["R4B__DOWN_VOLUME21"] = pd.Series(
        volume_weekly["down_move_dv_share21"].to_numpy(float), index=calendar_index
    )
    arm_values["R4B__VOLUME_SHOCK"] = pd.Series(
        volume_weekly["volume_shock21_252"].to_numpy(float), index=calendar_index
    )
    source_status["R4B__DOWN_VOLUME21"] = ("available", "R2A Tiingo SPY raw close and raw volume")
    source_status["R4B__VOLUME_SHOCK"] = ("available", "R2A Tiingo SPY raw close and raw volume")

    sessions = pd.DatetimeIndex(pd.to_datetime(market["session_date"])).normalize()
    treasury = parse_fred_csv(
        raw_paths["fred_treasury"].read_bytes(), ["DGS10", "DGS3MO", "DGS2"]
    )
    treasury = treasury.loc[treasury["observation_date"] <= cutoff]
    treasury_weekly = lagged_fred_at_signals(
        treasury,
        decision,
        sessions,
        value_columns=["DGS10", "DGS3MO", "DGS2"],
        lag_sessions=int(config["availability"]["fred_daily_lag_xnys_sessions"]),
        max_staleness_sessions=int(config["availability"]["fred_daily_max_staleness_sessions"]),
    )
    arm_values["R4B__YC_10Y3M"] = pd.Series(
        -(treasury_weekly["DGS10"] - treasury_weekly["DGS3MO"]).to_numpy(float),
        index=calendar_index,
    )
    arm_values["R4B__YC_10Y2Y"] = pd.Series(
        -(treasury_weekly["DGS10"] - treasury_weekly["DGS2"]).to_numpy(float),
        index=calendar_index,
    )
    source_status["R4B__YC_10Y3M"] = ("available", "FRED daily; lagged one XNYS session")
    source_status["R4B__YC_10Y2Y"] = ("available", "FRED daily; lagged one XNYS session")

    hy = parse_fred_csv(raw_paths["fred_hy_oas"].read_bytes(), ["BAMLH0A0HYM2"])
    hy_development = hy.loc[hy["observation_date"] <= cutoff].dropna(subset=["BAMLH0A0HYM2"])
    hy_status = "available" if len(hy_development) >= 252 else "invalid_source_history_truncated"
    if hy_status == "available":
        hy_weekly = lagged_fred_at_signals(
            hy_development,
            decision,
            sessions,
            value_columns=["BAMLH0A0HYM2"],
            lag_sessions=int(config["availability"]["fred_daily_lag_xnys_sessions"]),
            max_staleness_sessions=int(config["availability"]["fred_daily_max_staleness_sessions"]),
        )
        level = pd.Series(hy_weekly["BAMLH0A0HYM2"].to_numpy(float), index=calendar_index)
        arm_values["R4B__HY_OAS_LEVEL"] = np.log(level)
        arm_values["R4B__HY_OAS_CHANGE21"] = level - level.shift(3)
    else:
        arm_values["R4B__HY_OAS_LEVEL"] = pd.Series(np.nan, index=calendar_index)
        arm_values["R4B__HY_OAS_CHANGE21"] = pd.Series(np.nan, index=calendar_index)
    hy_note = "Public FRED payload starts after the development firewall; no substitution"
    source_status["R4B__HY_OAS_LEVEL"] = (hy_status, hy_note)
    source_status["R4B__HY_OAS_CHANGE21"] = (hy_status, hy_note)

    rsp = normalize_rsp_tiingo(raw_paths["tiingo_rsp"].read_bytes(), run_id)
    rsp_score = build_rsp_spy_score(rsp, market)
    rsp_weekly = decision[["signal_session"]].merge(
        rsp_score,
        left_on="signal_session",
        right_on="session_date",
        how="left",
        validate="one_to_one",
    )
    arm_values["R4B__RSP_SPY63"] = pd.Series(
        rsp_weekly["rsp_spy_score63"].to_numpy(float), index=calendar_index
    )
    source_status["R4B__RSP_SPY63"] = ("available", "Tiingo RSP adjusted close; investable proxy")

    # The downloaded FRED graph NFCI series is revised history.  It is retained
    # as evidence but never accepted as the required release/vintage-as-of arm.
    arm_values["R4B__NFCI"] = pd.Series(np.nan, index=calendar_index)
    source_status["R4B__NFCI"] = (
        "invalid_no_vintage_asof",
        "Official current-history payload is not ALFRED initial-release/vintage-as-of data",
    )

    feature_inputs, availability = weekly_long_table(
        calendar=decision,
        arm_values=arm_values,
        available_at=core["signal_timestamp_et"],
        source_status=source_status,
        cutoff=cutoff,
    )
    expected_arms = set(pd.read_csv(root / config["factor_catalog_path"])["arm_id"])
    if set(arm_values) != expected_arms:
        raise DataQualityError("constructed arm set differs from frozen catalog")
    eligibility = eligibility_from_weekly(
        feature_inputs,
        minimum_weeks=400,
        minimum_years=8,
        max_missing_fraction=float(config["availability"]["maximum_missing_fraction"]),
        max_consecutive_missing=int(config["availability"]["maximum_consecutive_missing_weeks"]),
    )
    qualified = eligibility.loc[eligibility["data_gate_pass"], "arm_id"].tolist()
    pivot = feature_inputs.pivot(index="signal_session", columns="arm_id", values="value_available")
    common = pd.DataFrame({"signal_session": pivot.index})
    common["qualified_arm_count"] = len(qualified)
    common["all_qualified_available"] = (
        pivot[qualified].all(axis=1).to_numpy(bool) if qualified else False
    )
    common_weeks = int(common["all_qualified_available"].sum())

    source_inventory = _source_inventory(raw_paths, parent, parent_manifest_path)
    qa_summary = _qa_summary(
        feature_inputs=feature_inputs,
        eligibility=eligibility,
        common_weeks=common_weeks,
        legacy_anchor_pass=True,
    )
    config_resolved = _resolved_toml(config, run_id, parent, output, raw_paths)

    artifact_frames = {
        "availability_weekly.parquet": availability,
        "feature_inputs_weekly.parquet": feature_inputs,
    }
    for filename, frame in artifact_frames.items():
        frame.to_parquet(output / filename, index=False, compression="zstd")
    csv_frames = {
        "source_inventory.csv": source_inventory,
        "factor_eligibility.csv": eligibility,
        "common_mask_candidate.csv": common,
        "qa_summary.csv": qa_summary,
    }
    for filename, frame in csv_frames.items():
        frame.to_csv(output / filename, index=False, lineterminator="\n")
    (output / "config_resolved.toml").write_text(config_resolved, encoding="utf-8", newline="\n")

    _assert_forbidden_outputs_absent(output)
    # Hash persisted representations, not pre-serialization pandas dtypes.
    # Arrow timestamp units and CSV inference can otherwise make a correct
    # round trip appear different from the immutable artifact.
    canonical = rebuild_r4a_canonical_hashes(output)
    recorded = sorted(
        [path for path in output.rglob("*") if path.is_file()],
        key=lambda path: path.relative_to(output).as_posix(),
    )
    status = "completed_candidate"
    manifest = {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "batch_id": BATCH_ID,
        "run_id": run_id,
        "dataset_version": config["dataset_version"],
        "status": status,
        "formal_eligible": False,
        "development_firewall": {
            "maximum_prediction_signal": str(cutoff.date()),
            "targets_materialized": False,
            "signal_evaluation_run": False,
            "strategy_nav_run": False,
            "event_outcomes_run": False,
            "lockbox_read": False,
        },
        "parents": {
            "r2a_snapshot_id": config["parents"]["r2a_snapshot_id"],
            "r2a_manifest_sha256": sha256_file(parent_manifest_path),
            "r2_folds_sha256": config["parents"]["r2_folds_sha256"],
            "round4_plan_lock_sha256": sha256_file(root / "config/experiments/round4/PLAN_LOCK.json"),
        },
        "counts": {
            "registered_arms": len(eligibility),
            "reference_eligible_arms": int(eligibility["reference_gate_pass"].sum()),
            "invalid_arms": int(eligibility["eligibility_status"].eq("invalid_data").sum()),
            "weekly_feature_rows": len(feature_inputs),
            "common_weeks": common_weeks,
        },
        "canonical_content_sha256": canonical,
        "config_sha256": sha256_file(config_file),
        "design_sha256": sha256_file(root / config["design_path"]),
        "factor_catalog_sha256": sha256_file(root / config["factor_catalog_path"]),
        "build_provenance": _build_provenance(root),
        "files": [_file_record(path, output) for path in recorded],
    }
    manifest_path = output / "manifest.json"
    _write_json(manifest_path, manifest)
    _write_json(
        output / "FROZEN.json",
        {
            "schema_version": 1,
            "program_id": PROGRAM_ID,
            "batch_id": BATCH_ID,
            "run_id": run_id,
            "status": status,
            "manifest_sha256": sha256_file(manifest_path),
            "formal_eligible": False,
            "targets_materialized": False,
            "signal_evaluation_run": False,
            "strategy_nav_run": False,
            "event_outcomes_run": False,
        },
    )
    return R4AResult(
        output_dir=output,
        manifest_path=manifest_path,
        status=status,
        reference_eligible_arms=int(eligibility["reference_gate_pass"].sum()),
        invalid_arms=int(eligibility["eligibility_status"].eq("invalid_data").sum()),
        common_weeks=common_weeks,
    )


def rebuild_r4a_canonical_hashes(bundle_dir: str | Path) -> dict[str, str]:
    directory = Path(bundle_dir)
    return {
        "availability_weekly": canonical_arrow_sha256(
            pd.read_parquet(directory / "availability_weekly.parquet"),
            primary_key=["signal_session", "arm_id"],
        ),
        "feature_inputs_weekly": canonical_arrow_sha256(
            pd.read_parquet(directory / "feature_inputs_weekly.parquet"),
            primary_key=["signal_session", "arm_id"],
        ),
        "factor_eligibility": sha256_file(directory / "factor_eligibility.csv"),
        "common_mask_candidate": sha256_file(directory / "common_mask_candidate.csv"),
    }


def _load_config_and_plan_lock(path: Path, root: Path) -> dict[str, Any]:
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    if config.get("program_id") != PROGRAM_ID or config.get("batch_id") != BATCH_ID:
        raise DataQualityError("R4A config identity mismatch")
    authorization = config.get("authorization", {})
    expected_true = {"network_acquisition", "normalization", "feature_input_construction"}
    expected_false = {
        "target_materialization",
        "signal_evaluation",
        "strategy_nav",
        "event_outcomes",
        "lockbox",
        "mom255_transfer",
        "models",
        "position_search",
    }
    if any(authorization.get(key) is not True for key in expected_true) or any(
        authorization.get(key) is not False for key in expected_false
    ):
        raise DataQualityError("R4A authorization is not fail-closed")
    lock_path = root / "config/experiments/round4/PLAN_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    for relative, expected in lock["files"].items():
        _verify_sha(root / relative, expected)
    if (root / "config/experiments/round4/PREREG_LOCK.json").exists():
        raise DataQualityError("R4A must precede the Round4 PREREG_LOCK")
    return config


def _verify_legacy_anchor(core: pd.DataFrame, runtime: Path) -> None:
    anchor = (
        runtime
        / "results/experiments/round2/R2B_SIGNAL_DIAGNOSTICS/runs/r2b-free-core-v1/features_weekly.parquet"
    )
    if not anchor.is_file():
        raise FileNotFoundError(anchor)
    prior = pd.read_parquet(anchor)
    columns = [
        "signal_session",
        "spy_total_return_21d",
        "spy_total_return_126d",
        "sma50_over_sma200_minus_1",
        "drawdown_from_252d_high",
        "spy_rv21",
        "spy_rv126",
        "log_spy_rv126",
        "log_rv21_over_rv126",
        "downside_variance_share_63d",
        "return_skew_63d",
        "return_excess_kurtosis_126d",
    ]
    joined = core[columns].merge(
        prior[columns], on="signal_session", how="inner", suffixes=("_r4", "_r2"), validate="one_to_one"
    )
    if joined.empty:
        raise DataQualityError("R2B legacy anchor has no overlap")
    for column in columns[1:]:
        left = pd.to_numeric(joined[f"{column}_r4"], errors="coerce").to_numpy(float)
        right = pd.to_numeric(joined[f"{column}_r2"], errors="coerce").to_numpy(float)
        if not np.allclose(left, right, rtol=0.0, atol=0.0, equal_nan=True):
            raise DataQualityError(f"R4 legacy feature differs from R2B anchor: {column}")


def _source_inventory(
    raw_paths: dict[str, Path], parent: Path, parent_manifest_path: Path
) -> pd.DataFrame:
    rows = [
        {
            "source_id": "r2a_parent_core",
            "provider": "frozen R2A bundle",
            "url_or_parent": str(parent),
            "local_path": "parent_manifest_reference",
            "bytes": parent_manifest_path.stat().st_size,
            "sha256": sha256_file(parent_manifest_path),
            "license_or_access": "approved_for_local_research",
            "status": "verified",
        }
    ]
    metadata = {
        "fred_treasury": ("Federal Reserve Bank of St. Louis / FRED", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10,DGS3MO,DGS2", "public research download"),
        "fred_hy_oas": ("Federal Reserve Bank of St. Louis / FRED", "https://fred.stlouisfed.org/series/BAMLH0A0HYM2", "public research download; historical truncation disclosed"),
        "tiingo_rsp": ("Tiingo", "https://www.tiingo.com/documentation/end-of-day", "existing user credential; local research"),
        "fred_nfci_current": ("Federal Reserve Bank of St. Louis / FRED", "https://fred.stlouisfed.org/series/NFCI", "public revised series; evidence only"),
        "alfred_nfci_form": ("Federal Reserve Bank of St. Louis / ALFRED", "https://alfred.stlouisfed.org/series/downloaddata?seid=NFCI", "public vintage form; evidence only"),
    }
    for source_id, path in raw_paths.items():
        provider, url, license_note = metadata[source_id]
        rows.append(
            {
                "source_id": source_id,
                "provider": provider,
                "url_or_parent": url,
                "local_path": f"raw/{path.name}",
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "license_or_access": license_note,
                "status": "frozen_raw",
            }
        )
    return pd.DataFrame(rows)


def _qa_summary(
    *, feature_inputs: pd.DataFrame, eligibility: pd.DataFrame, common_weeks: int, legacy_anchor_pass: bool
) -> pd.DataFrame:
    values: list[tuple[str, object]] = [
        ("registered_arms", eligibility.shape[0]),
        ("feature_rows", feature_inputs.shape[0]),
        ("reference_eligible_arms", int(eligibility["reference_gate_pass"].sum())),
        ("invalid_arms", int(eligibility["eligibility_status"].eq("invalid_data").sum())),
        ("common_weeks", common_weeks),
        ("legacy_anchor_exact_match", legacy_anchor_pass),
        ("target_columns_present", any(token in column.casefold() for column in feature_inputs for token in FORBIDDEN_TOKENS)),
        ("targets_materialized", False),
        ("signal_evaluation_run", False),
        ("strategy_nav_run", False),
        ("event_outcomes_run", False),
        ("lockbox_read", False),
    ]
    return pd.DataFrame(values, columns=["qa_key", "qa_value"])


def _resolved_toml(
    config: dict[str, Any], run_id: str, parent: Path, output: Path, raw_paths: dict[str, Path]
) -> str:
    lines = [
        "schema_version = 1",
        f'program_id = "{PROGRAM_ID}"',
        f'batch_id = "{BATCH_ID}"',
        f'run_id = "{run_id}"',
        f'dataset_version = "{config["dataset_version"]}"',
        "formal_eligible = false",
        "targets_materialized = false",
        "signal_evaluation_run = false",
        "strategy_nav_run = false",
        "event_outcomes_run = false",
        "lockbox_read = false",
        "",
        "[paths]",
        f'parent_r2a = "{str(parent).replace(chr(92), "/")}"',
        f'output = "{str(output).replace(chr(92), "/")}"',
        "",
        "[raw_sha256]",
    ]
    for key, path in sorted(raw_paths.items()):
        lines.append(f'{key} = "{sha256_file(path)}"')
    return "\n".join(lines) + "\n"


def _build_provenance(root: Path) -> dict[str, Any]:
    paths = (
        "scripts/build_round4_r4a.py",
        "src/momentum_reversal/data/round4_factors.py",
        "src/momentum_reversal/pipelines/round4_data.py",
        "src/momentum_reversal/pipelines/round2_signals.py",
    )
    git_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    git_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    return {
        "git_commit": git_commit,
        "workspace_dirty": bool(git_status.strip()),
        "code_file_sha256": {path: sha256_file(root / path) for path in paths},
        "dependency_versions": {
            package: importlib_metadata.version(package)
            for package in ("numpy", "pandas", "pyarrow")
        },
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _verify_manifest_files(root: Path, manifest: dict[str, Any]) -> None:
    for record in manifest.get("files", []):
        path = root / str(record["path"])
        if not path.is_file() or path.stat().st_size != int(record["size_bytes"]):
            raise DataQualityError(f"parent manifest file mismatch: {record['path']}")
        if sha256_file(path) != str(record["sha256"]):
            raise DataQualityError(f"parent manifest SHA mismatch: {record['path']}")


def _verify_sha(path: Path, expected: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    actual = sha256_file(path)
    if actual != expected.lower():
        raise DataQualityError(f"SHA256 mismatch: {path} expected={expected} actual={actual}")


def _file_record(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _assert_forbidden_outputs_absent(output: Path) -> None:
    names = [path.name.casefold() for path in output.rglob("*") if path.is_file()]
    for token in FORBIDDEN_TOKENS:
        if any(token in name for name in names):
            raise DataQualityError(f"forbidden R4A output present: {token}")
