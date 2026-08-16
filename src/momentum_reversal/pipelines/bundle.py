"""Atomic, portable output bundle contract for systematic experiments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from typing import Mapping

import pandas as pd

from .run_context import ExperimentRunContext


MANIFEST_SCHEMA = "momentum_reversal.experiment_manifest.v1"
SUMMARY_SCHEMA = "momentum_reversal.experiment_summary.v1"

SUMMARY_COLUMNS = (
    "group_id",
    "strategy_id",
    "portfolio_mode",
    "signal",
    "top_n",
    "frequency",
    "variant_id",
    "cost_bps",
    "borrow_fee_annual",
    "valid_scenario",
    "invalid_reason",
    "observations",
    "total_return",
    "cagr",
    "annualized_volatility",
    "sharpe_zero_rf",
    "sharpe_excess_rf",
    "sortino",
    "max_drawdown",
    "max_drawdown_duration",
    "calmar",
    "beta",
    "annualized_alpha_excess_rf",
    "tracking_error",
    "average_gross_exposure",
    "average_net_exposure",
    "average_l1_turnover",
    "annualized_l1_turnover",
)

COMPARISON_COLUMNS = (
    "group_id",
    "strategy_id",
    "portfolio_mode",
    "variant_id",
    "cost_bps",
    "borrow_fee_annual",
    "reference_strategy_id",
    "comparison_type",
    "metric",
    "estimate",
)

ARTIFACT_SCHEMAS: Mapping[str, tuple[str, ...]] = {
    "nav": (
        "group_id",
        "strategy_id",
        "portfolio_mode",
        "variant_id",
        "cost_bps",
        "borrow_fee_annual",
        "date",
        "nav",
        "daily_return",
        "factor_excess_return",
        "derived_gross2_factor_return",
        "long_value",
        "short_value",
        "cash_value",
        "long_exposure",
        "short_exposure",
        "gross_exposure",
        "net_exposure",
        "short_borrow_fee_amount",
    ),
    "rebalances": (
        "group_id",
        "strategy_id",
        "portfolio_mode",
        "variant_id",
        "cost_bps",
        "borrow_fee_annual",
        "signal_date",
        "execution_date",
        "pretrade_long_exposure",
        "pretrade_short_exposure",
        "pretrade_gross_exposure",
        "pretrade_net_exposure",
        "requested_long_exposure",
        "requested_short_exposure",
        "requested_gross_exposure",
        "requested_net_exposure",
        "target_long_exposure",
        "target_short_exposure",
        "target_gross_exposure",
        "target_net_exposure",
        "target_cash_weight",
        "l1_turnover",
        "one_way_turnover",
        "cost_amount",
        "execution_status",
        "unfilled_selected_count",
        "unfilled_selected_sids",
        "missing_target_count",
        "missing_target_sids",
        "missing_existing_count",
        "missing_existing_sids",
        "terminal_liquidation_count",
        "terminal_liquidation_sids",
        "terminal_liquidation_fallback_dates",
    ),
    "holdings": (
        "group_id",
        "strategy_id",
        "portfolio_mode",
        "variant_id",
        "cost_bps",
        "borrow_fee_annual",
        "signal_date",
        "execution_date",
        "sid",
        "target_weight",
    ),
    "trades": (
        "group_id",
        "strategy_id",
        "portfolio_mode",
        "variant_id",
        "cost_bps",
        "borrow_fee_annual",
        "execution_date",
        "sid",
        "pretrade_weight",
        "target_weight",
        "trade_weight",
    ),
    "diagnostics": (
        "group_id",
        "strategy_id",
        "portfolio_mode",
        "variant_id",
        "cost_bps",
        "borrow_fee_annual",
        "scope",
        "diagnostic",
        "value",
    ),
}


@dataclass(frozen=True, slots=True)
class BundleWriteResult:
    output_dir: Path
    manifest_path: Path
    summary_path: Path
    comparison_path: Path


def empty_summary_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=SUMMARY_COLUMNS)


def empty_comparison_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=COMPARISON_COLUMNS)


def write_experiment_bundle(
    context: ExperimentRunContext,
    *,
    summary: pd.DataFrame,
    comparison: pd.DataFrame,
    artifacts: Mapping[str, pd.DataFrame] | None = None,
    status: str = "completed",
    extra_manifest: Mapping[str, object] | None = None,
    resolved_config_toml: str | None = None,
) -> BundleWriteResult:
    """Write one immutable group run using a staging directory and atomic rename.

    ``prepared`` is reserved for schema/integration checks and may omit detailed
    artifacts.  A ``completed`` bundle must contain all five standard long-table
    artifacts and at least one summary row.
    """

    if status not in {"prepared", "completed"}:
        raise ValueError("bundle status must be prepared or completed")
    if resolved_config_toml is not None:
        if not isinstance(resolved_config_toml, str):
            raise TypeError("resolved_config_toml must be a string")
        if not resolved_config_toml or not resolved_config_toml.endswith("\n"):
            raise ValueError("resolved_config_toml must be non-empty and end with newline")
    _validate_columns(summary, SUMMARY_COLUMNS, "summary")
    _validate_columns(comparison, COMPARISON_COLUMNS, "comparison")
    _validate_identity(summary, context)
    artifact_frames = dict(artifacts or {})
    unknown = set(artifact_frames).difference(ARTIFACT_SCHEMAS)
    if unknown:
        raise ValueError(f"unknown artifact tables: {sorted(unknown)}")
    for name, frame in artifact_frames.items():
        _validate_columns(frame, ARTIFACT_SCHEMAS[name], f"artifact {name}")
    if status == "completed":
        if summary.empty:
            raise ValueError("completed bundle summary cannot be empty")
        missing = set(ARTIFACT_SCHEMAS).difference(artifact_frames)
        if missing:
            raise ValueError(
                f"completed bundle is missing artifact tables: {sorted(missing)}"
            )

    final = context.bundle_dir
    if final.exists():
        raise FileExistsError(f"immutable experiment bundle already exists: {final}")
    final.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{context.run_id}.", dir=final.parent) as temp:
        staging = Path(temp)
        artifacts_dir = staging / "artifacts"
        artifacts_dir.mkdir()
        (staging / "config_resolved.toml").write_text(
            (
                context.group.resolved_toml()
                if resolved_config_toml is None
                else resolved_config_toml
            ),
            encoding="utf-8",
            newline="\n",
        )
        summary.to_csv(staging / "summary.csv", index=False)
        comparison.to_csv(staging / "comparison.csv", index=False)
        for name, frame in artifact_frames.items():
            _write_parquet(frame, artifacts_dir / f"{name}.parquet")

        files = _file_records(staging)
        manifest: dict[str, object] = {
            "schema_version": MANIFEST_SCHEMA,
            "summary_schema": SUMMARY_SCHEMA,
            "status": status,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            **context.manifest_identity(),
            "summary_rows": len(summary),
            "comparison_rows": len(comparison),
            "artifact_contract": {
                name: list(columns) for name, columns in ARTIFACT_SCHEMAS.items()
            },
            "files": files,
        }
        if extra_manifest:
            protected = set(manifest).intersection(extra_manifest)
            if protected:
                raise ValueError(
                    f"extra_manifest cannot replace protected keys: {sorted(protected)}"
                )
            manifest.update(extra_manifest)
        validate_experiment_manifest(manifest)
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        staging.replace(final)

    return BundleWriteResult(
        output_dir=final,
        manifest_path=final / "manifest.json",
        summary_path=final / "summary.csv",
        comparison_path=final / "comparison.csv",
    )


def validate_experiment_manifest(manifest: Mapping[str, object]) -> None:
    required = {
        "schema_version",
        "summary_schema",
        "status",
        "created_at_utc",
        "group_id",
        "spec_id",
        "run_id",
        "dataset_version",
        "spec_sha256",
        "portfolio_modes",
        "strategy_count",
        "summary_rows",
        "comparison_rows",
        "artifact_contract",
        "files",
    }
    missing = required.difference(manifest)
    if missing:
        raise ValueError(f"experiment manifest missing keys: {sorted(missing)}")
    if manifest["schema_version"] != MANIFEST_SCHEMA:
        raise ValueError("unsupported experiment manifest schema")
    if manifest["summary_schema"] != SUMMARY_SCHEMA:
        raise ValueError("unsupported experiment summary schema")
    if manifest["status"] not in {"prepared", "completed"}:
        raise ValueError("invalid experiment manifest status")
    modes = manifest["portfolio_modes"]
    if not isinstance(modes, list) or set(modes) != {"long_only", "long_short"}:
        raise ValueError("manifest must declare both portfolio modes")
    files = manifest["files"]
    if not isinstance(files, list):
        raise ValueError("manifest files must be a list")
    for record in files:
        if not isinstance(record, dict) or not {"path", "sha256", "bytes"}.issubset(record):
            raise ValueError("malformed experiment manifest file record")
        if Path(str(record["path"])).is_absolute():
            raise ValueError("bundle manifest file paths must be relative")


def _validate_columns(frame: pd.DataFrame, required: tuple[str, ...], label: str) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{label} must be a pandas DataFrame")
    missing = set(required).difference(frame.columns)
    if missing:
        raise ValueError(f"{label} missing columns: {sorted(missing)}")


def _validate_identity(frame: pd.DataFrame, context: ExperimentRunContext) -> None:
    if frame.empty:
        return
    if set(frame["group_id"].astype(str)) != {context.group_id}:
        raise ValueError("summary group_id does not match run context")
    allowed_ids = {item.strategy_id for item in context.strategies}
    unknown = set(frame["strategy_id"].astype(str)).difference(allowed_ids)
    if unknown:
        raise ValueError(f"summary contains unregistered strategy IDs: {sorted(unknown)}")


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    try:
        frame.to_parquet(path, index=False)
    except ImportError as error:
        raise RuntimeError(
            "writing detailed experiment artifacts requires the optional pyarrow "
            "dependency"
        ) from error


def _file_records(root: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return records


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
