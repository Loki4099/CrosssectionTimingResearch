"""Strict read-only validation of a completed G00 long-only result sleeve."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pandas as pd

from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.storage import sha256_file
from momentum_reversal.experiments import PortfolioMode

from .bundle import (
    ARTIFACT_SCHEMAS,
    COMPARISON_COLUMNS,
    SUMMARY_COLUMNS,
    validate_experiment_manifest,
)
from .run_context import ExperimentRunContext, LoadedExperimentData


_SCENARIO_KEY = (
    "strategy_id",
    "variant_id",
    "cost_bps",
    "borrow_fee_annual",
)
_EXPECTED_FILES = {
    "config_resolved.toml",
    "summary.csv",
    "comparison.csv",
    "artifacts/nav.parquet",
    "artifacts/rebalances.parquet",
    "artifacts/holdings.parquet",
    "artifacts/trades.parquet",
    "artifacts/diagnostics.parquet",
}


class LongOnlyReuseError(DataQualityError):
    """A proposed source bundle cannot be trusted as the G00 long-only sleeve."""


@dataclass(frozen=True, slots=True)
class ReusedLongOnlyBundle:
    root: Path
    manifest_sha256: str
    source_run_id: str
    summary: pd.DataFrame
    comparison: pd.DataFrame
    nav: pd.DataFrame
    rebalances: pd.DataFrame
    holdings: pd.DataFrame
    trades: pd.DataFrame
    diagnostics: pd.DataFrame

    @property
    def scenario_count(self) -> int:
        return len(self.summary)

    @property
    def nav_rows(self) -> int:
        return len(self.nav)


def load_reusable_long_only_bundle(
    source: str | Path,
    *,
    context: ExperimentRunContext,
    data: LoadedExperimentData,
    legacy_manifest_sha256: str,
) -> ReusedLongOnlyBundle:
    """Validate every source file before returning only long-only rows."""

    root = Path(source).resolve()
    if root == context.bundle_dir.resolve():
        raise LongOnlyReuseError("reuse source and new immutable output must differ")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise LongOnlyReuseError(f"reuse manifest is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_experiment_manifest(manifest)
    except Exception as error:
        raise LongOnlyReuseError("reuse manifest is malformed") from error
    expected_identity = {
        "status": "completed",
        "group_id": "G00",
        "spec_id": context.spec_id,
        "dataset_version": context.dataset_version,
        "spec_sha256": context.group.resolved_sha256,
        "strategy_count": 36,
        "summary_rows": 288,
        "comparison_rows": 432,
    }
    for key, expected in expected_identity.items():
        if manifest.get(key) != expected:
            raise LongOnlyReuseError(
                f"reuse manifest {key} mismatch: expected={expected!r}, "
                f"actual={manifest.get(key)!r}"
            )
    dataset = manifest.get("dataset")
    if not isinstance(dataset, dict) or dataset.get("manifest_sha256") != data.dataset_manifest_sha256:
        raise LongOnlyReuseError("reuse bundle dataset manifest hash does not match")
    legacy = manifest.get("legacy_reference")
    if (
        not isinstance(legacy, dict)
        or legacy.get("manifest_sha256") != legacy_manifest_sha256
        or legacy.get("hard_gate_scenarios") != 72
    ):
        raise LongOnlyReuseError("reuse bundle legacy reference does not match")
    _verify_file_inventory(root, manifest)

    summary = pd.read_csv(root / "summary.csv")
    comparison = pd.read_csv(root / "comparison.csv")
    artifacts = {
        name: pd.read_parquet(root / "artifacts" / f"{name}.parquet")
        for name in ARTIFACT_SCHEMAS
    }
    source_summary_columns = tuple(
        column
        for column in SUMMARY_COLUMNS
        if column not in {"valid_scenario", "invalid_reason"}
    )
    _require_columns(summary, source_summary_columns, "reuse summary")
    _require_columns(comparison, COMPARISON_COLUMNS, "reuse comparison")
    legacy_rebalance_defaults: dict[str, object] = {
        "missing_target_count": 0,
        "missing_target_sids": "",
        "missing_existing_count": 0,
        "missing_existing_sids": "",
        "terminal_liquidation_count": 0,
        "terminal_liquidation_sids": "",
        "terminal_liquidation_fallback_dates": "",
    }
    for column, default in legacy_rebalance_defaults.items():
        if column not in artifacts["rebalances"]:
            artifacts["rebalances"][column] = default
    for name, frame in artifacts.items():
        _require_columns(frame, ARTIFACT_SCHEMAS[name], f"reuse {name}")

    long_only_ids = {
        strategy.strategy_id
        for strategy in context.strategies
        if strategy.portfolio_mode is PortfolioMode.LONG_ONLY
    }
    lo_summary = _long_only(summary)
    lo_summary["valid_scenario"] = True
    lo_summary["invalid_reason"] = ""
    if "terminal_last_close_count" not in lo_summary:
        lo_summary["terminal_last_close_count"] = 0
    _validate_summary(lo_summary, long_only_ids)
    lo_nav = _long_only(artifacts["nav"])
    _validate_nav(lo_nav, lo_summary, data)
    primary_keys = _scenario_keys(
        lo_summary.loc[lo_summary["is_primary_scenario"].eq(True)]
    )
    if len(primary_keys) != 18:
        raise LongOnlyReuseError("reuse summary must contain 18 primary LO scenarios")
    audit_frames: dict[str, pd.DataFrame] = {}
    for name in ("rebalances", "holdings", "trades"):
        frame = _long_only(artifacts[name])
        if _scenario_keys(frame) != primary_keys:
            raise LongOnlyReuseError(
                f"reuse {name} does not contain exactly the 18 primary LO scenarios"
            )
        audit_frames[name] = frame.copy()
    lo_diagnostics = _long_only(artifacts["diagnostics"])
    _validate_diagnostics(lo_diagnostics, lo_summary)
    lo_comparison = _long_only(comparison)
    _validate_comparison(lo_comparison, lo_summary)
    return ReusedLongOnlyBundle(
        root=root,
        manifest_sha256=sha256_file(manifest_path),
        source_run_id=str(manifest["run_id"]),
        summary=lo_summary.copy(),
        comparison=lo_comparison.copy(),
        nav=lo_nav.copy(),
        rebalances=audit_frames["rebalances"],
        holdings=audit_frames["holdings"],
        trades=audit_frames["trades"],
        diagnostics=lo_diagnostics.copy(),
    )


def _verify_file_inventory(root: Path, manifest: dict[str, object]) -> None:
    records = manifest.get("files")
    if not isinstance(records, list):
        raise LongOnlyReuseError("reuse manifest files must be a list")
    by_path: dict[str, dict[str, object]] = {}
    for record in records:
        if not isinstance(record, dict) or not {"path", "sha256", "bytes"}.issubset(record):
            raise LongOnlyReuseError("reuse manifest contains a malformed file record")
        relative = Path(str(record["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise LongOnlyReuseError("reuse manifest contains an unsafe file path")
        key = relative.as_posix()
        if key in by_path:
            raise LongOnlyReuseError(f"reuse manifest duplicates file record: {key}")
        by_path[key] = record
    if set(by_path) != _EXPECTED_FILES:
        raise LongOnlyReuseError(
            "reuse file inventory mismatch: "
            f"expected={sorted(_EXPECTED_FILES)}, actual={sorted(by_path)}"
        )
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if actual_files != _EXPECTED_FILES:
        raise LongOnlyReuseError("reuse directory contains missing or unregistered files")
    for key, record in by_path.items():
        path = root / Path(key)
        if not path.is_file():
            raise LongOnlyReuseError(f"reuse file is missing: {key}")
        if path.stat().st_size != int(record["bytes"]):
            raise LongOnlyReuseError(f"reuse file byte count mismatch: {key}")
        if sha256_file(path) != str(record["sha256"]):
            raise LongOnlyReuseError(f"reuse file hash mismatch: {key}")


def _validate_summary(frame: pd.DataFrame, expected_ids: set[str]) -> None:
    if len(frame) != 72 or len(_scenario_keys(frame)) != 72:
        raise LongOnlyReuseError("reuse bundle must contain 72 unique LO summary rows")
    if set(frame["strategy_id"].astype(str)) != expected_ids:
        raise LongOnlyReuseError("reuse summary LO strategy IDs are incomplete")
    if not frame["borrow_fee_annual"].astype(float).eq(0.0).all():
        raise LongOnlyReuseError("reuse long-only rows must have zero borrow fee")
    per_strategy = frame.groupby("strategy_id", observed=True)["cost_bps"].agg(
        lambda values: set(map(float, values))
    )
    if not all(values == {0.0, 5.0, 10.0, 20.0} for values in per_strategy):
        raise LongOnlyReuseError("reuse LO summary cost grid is incomplete")


def _validate_nav(
    frame: pd.DataFrame, summary: pd.DataFrame, data: LoadedExperimentData
) -> None:
    expected_rows = 72 * len(data.evaluation_sessions)
    if len(frame) != expected_rows or _scenario_keys(frame) != _scenario_keys(summary):
        raise LongOnlyReuseError(
            f"reuse LO NAV must contain 72 x {len(data.evaluation_sessions)} rows"
        )
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any():
        raise LongOnlyReuseError("reuse LO NAV contains invalid dates")
    normalized = dates.dt.normalize()
    for _, positions in frame.assign(_date=normalized).groupby(
        list(_SCENARIO_KEY), observed=True, dropna=False
    ):
        scenario_dates = pd.DatetimeIndex(positions["_date"])
        if not scenario_dates.equals(data.evaluation_sessions):
            raise LongOnlyReuseError("reuse LO NAV has a missing/reordered date path")


def _validate_diagnostics(frame: pd.DataFrame, summary: pd.DataFrame) -> None:
    if _scenario_keys(frame) != _scenario_keys(summary):
        raise LongOnlyReuseError("reuse LO diagnostics scenario coverage is incomplete")
    required = {
        "corporate_action_events_applied",
        "valuation_fallback_count",
        "unfilled_execution_count",
        "signed_skipped_rebalance_count",
    }
    allowed = required | {"terminal_last_close_count"}
    for _, rows in frame.groupby(list(_SCENARIO_KEY), observed=True, dropna=False):
        actual = set(rows["diagnostic"].astype(str))
        if not required.issubset(actual) or not actual.issubset(allowed):
            raise LongOnlyReuseError("reuse LO diagnostics fields are incomplete")


def _validate_comparison(frame: pd.DataFrame, summary: pd.DataFrame) -> None:
    if len(frame) != 432 or _scenario_keys(frame) != _scenario_keys(summary):
        raise LongOnlyReuseError("reuse LO comparison coverage is incomplete")
    expected_pairs = {
        (comparison_type, metric)
        for comparison_type in (
            "engine_reproduction_legacy_zero_cash",
            "cash_policy_delta_tbill_vs_legacy",
        )
        for metric in (
            "date_index_equal",
            "max_abs_nav_diff",
            "max_abs_daily_return_diff",
        )
    }
    for _, rows in frame.groupby(list(_SCENARIO_KEY), observed=True, dropna=False):
        actual = set(
            zip(
                rows["comparison_type"].astype(str),
                rows["metric"].astype(str),
            )
        )
        if actual != expected_pairs:
            raise LongOnlyReuseError("reuse LO comparison metrics are incomplete")


def _long_only(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.loc[frame["portfolio_mode"].astype(str).eq("long_only")].copy()


def _scenario_keys(frame: pd.DataFrame) -> set[tuple[object, ...]]:
    return set(frame.loc[:, list(_SCENARIO_KEY)].itertuples(index=False, name=None))


def _require_columns(
    frame: pd.DataFrame, required: tuple[str, ...], label: str
) -> None:
    missing = set(required).difference(frame.columns)
    if missing:
        raise LongOnlyReuseError(f"{label} missing columns: {sorted(missing)}")
