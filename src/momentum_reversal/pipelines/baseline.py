"""Run and export the frozen 18-path baseline from a curated dataset."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from momentum_reversal.analytics import (
    benchmark_returns_from_total_return_prices,
    relative_performance_summary,
)
from momentum_reversal.backtest import BaselineBacktester
from momentum_reversal.backtest import rebalance_schedule
from momentum_reversal.data import (
    CorporateActionLedger,
    DatasetLayout,
    ManifestStore,
    PITMembership,
    ParquetStore,
    align_daily_risk_free,
)
from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.storage import sha256_file
from momentum_reversal.experiments import baseline_specs, export_baseline_result


_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True, slots=True)
class BaselineRunConfig:
    data_root: Path
    dataset_version: str
    output_root: Path
    run_id: str
    costs_bps: tuple[float, ...] = (0.0, 5.0, 10.0, 20.0)
    allow_review_dataset: bool = False
    allow_invalid_dataset: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_root", Path(self.data_root).resolve())
        object.__setattr__(self, "output_root", Path(self.output_root).resolve())
        object.__setattr__(self, "costs_bps", tuple(map(float, self.costs_bps)))
        if not _SAFE_RUN_ID.fullmatch(self.run_id):
            raise ValueError(f"unsafe run_id: {self.run_id!r}")
        if not self.costs_bps or len(set(self.costs_bps)) != len(self.costs_bps):
            raise ValueError("costs_bps must be non-empty and unique")
        if any(cost < 0 for cost in self.costs_bps):
            raise ValueError("costs_bps cannot contain negative values")


@dataclass(frozen=True, slots=True)
class BaselineRunResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    path_count: int
    scenario_count: int
    formal_run_eligible: bool


def run_frozen_baselines(config: BaselineRunConfig) -> BaselineRunResult:
    """Verify one frozen dataset and stream all result bundles to disk."""

    layout = DatasetLayout(config.data_root)
    manifest_store = ManifestStore(layout)
    dataset_manifest = manifest_store.read(config.dataset_version)
    dataset_manifest_path = layout.manifest_path(config.dataset_version)
    _verify_dataset_files(layout, dataset_manifest)
    dataset_status = str(dataset_manifest.get("status", "unknown"))
    calendar_source = str(dataset_manifest.get("calendar_source", "unknown"))
    dataset_declares_formal = dataset_manifest.get("formal_eligible") is True
    research_tier = str(dataset_manifest.get("research_tier", "unspecified"))
    benchmark_metadata = dataset_manifest.get("benchmark")
    benchmark_kind = (
        str(benchmark_metadata.get("kind", "unknown"))
        if isinstance(benchmark_metadata, dict)
        else "unknown"
    )
    if dataset_status == "review" and not config.allow_review_dataset:
        raise DataQualityError(
            "dataset manifest is review; inspect QA or explicitly pass "
            "allow_review_dataset for a non-formal run"
        )
    if dataset_status == "invalid_data" and not config.allow_invalid_dataset:
        raise DataQualityError(
            "dataset manifest is invalid_data; repair the source/version or pass "
            "allow_invalid_dataset only for diagnostic runs"
        )
    if dataset_status not in {"valid", "review", "invalid_data"}:
        raise DataQualityError(f"unknown dataset status: {dataset_status!r}")
    if calendar_source != "XNYS" and not (
        config.allow_review_dataset or config.allow_invalid_dataset
    ):
        raise DataQualityError(
            "formal runs require an XNYS curated calendar; observed calendars "
            "are allowed only under an explicit non-formal override"
        )

    store = ParquetStore(layout)
    prices = store.read_curated_prices(dataset_version=config.dataset_version)
    membership_frame = store.read_curated_table(
        dataset_version=config.dataset_version, table_name="membership"
    )
    membership = _membership_from_frame(membership_frame)
    corporate_action_metadata = dataset_manifest.get("corporate_actions")
    corporate_actions_provided = bool(
        isinstance(corporate_action_metadata, dict)
        and corporate_action_metadata.get("provided") is True
    )
    corporate_action_table_name = (
        str(corporate_action_metadata.get("curated_table", "corporate_actions"))
        if isinstance(corporate_action_metadata, dict)
        else "corporate_actions"
    )
    corporate_actions = (
        CorporateActionLedger(
            store.read_curated_table(
                dataset_version=config.dataset_version,
                table_name=corporate_action_table_name,
            )
        )
        if corporate_actions_provided
        else CorporateActionLedger.empty()
    )
    missing_valuation_policy = str(
        dataset_manifest.get("prototype_valuation_policy", "strict")
    )
    missing_execution_policy = str(
        dataset_manifest.get("prototype_execution_policy", "strict")
    )
    benchmark = store.read_curated_table(
        dataset_version=config.dataset_version, table_name="benchmark_daily"
    )
    risk_free_metadata = dataset_manifest.get("risk_free")
    risk_free_provided = bool(
        isinstance(risk_free_metadata, dict)
        and risk_free_metadata.get("provided") is True
    )
    risk_free_table = (
        store.read_curated_table(
            dataset_version=config.dataset_version, table_name="risk_free_daily"
        )
        if risk_free_provided
        else None
    )
    calendar_frame = store.read_curated_table(
        dataset_version=config.dataset_version, table_name="calendar"
    )
    if "session_date" not in calendar_frame:
        raise DataQualityError("curated calendar is missing session_date")
    sessions = pd.DatetimeIndex(
        pd.to_datetime(calendar_frame["session_date"], errors="raise")
    ).normalize()
    request_metadata = dataset_manifest.get("request")
    if not isinstance(request_metadata, dict):
        raise DataQualityError("dataset manifest is missing request metadata")
    try:
        evaluation_start = pd.Timestamp(
            request_metadata["research_start"]
        ).normalize()
        signal_end = pd.Timestamp(request_metadata["end"]).normalize()
    except (KeyError, TypeError, ValueError) as error:
        raise DataQualityError(
            "dataset manifest lacks valid research_start/end bounds"
        ) from error
    if evaluation_start not in sessions:
        raise DataQualityError(
            "research_start must be an XNYS session so every strategy and "
            "benchmark can share one exact opening boundary"
        )
    missing_frequency_boundaries = [
        frequency
        for frequency in ("weekly", "monthly")
        if evaluation_start
        not in pd.DatetimeIndex(
            rebalance_schedule(sessions, frequency)["execution_date"]
        )
    ]
    if missing_frequency_boundaries:
        raise DataQualityError(
            "research_start must be a scheduled next-open execution for every "
            "tested frequency; missing: "
            f"{missing_frequency_boundaries}. Choose a common boundary such as "
            "2018-01-02, following the 2017-12-29 weekly/monthly close."
        )

    config.output_root.mkdir(parents=True, exist_ok=True)
    final_dir = config.output_root / config.run_id
    if final_dir.exists():
        raise FileExistsError(f"run output already exists: {final_dir}")
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{config.run_id}.", dir=config.output_root)
    )

    engine = BaselineBacktester(
        prices,
        membership,
        sessions=sessions,
        evaluation_start=evaluation_start,
        signal_end=signal_end,
        corporate_actions=corporate_actions,
        missing_valuation_policy=missing_valuation_policy,
        missing_execution_policy=missing_execution_policy,
    )
    summary_rows: list[dict[str, object]] = []
    specs = baseline_specs()
    try:
        for spec in specs:
            for cost in config.costs_bps:
                result = engine.run(
                    signal=spec.signal,
                    top_n=spec.top_n,
                    frequency=spec.frequency,  # type: ignore[arg-type]
                    cost_bps=cost,
                )
                if pd.Timestamp(result.nav.index[0]) != evaluation_start:
                    raise DataQualityError(
                        f"{spec.experiment_id} starts on "
                        f"{pd.Timestamp(result.nav.index[0]).date()}, not the "
                        f"common evaluation date {evaluation_start.date()}"
                    )
                scenario_dir = (
                    staging_dir / spec.experiment_id / _cost_directory_name(cost)
                )
                benchmark_for_analytics = benchmark.rename(
                    columns={
                        "benchmark_tr_open": "tr_open",
                        "benchmark_tr_close": "tr_close",
                    }
                )
                benchmark_returns = benchmark_returns_from_total_return_prices(
                    benchmark_for_analytics, result.nav["daily_return"]
                )
                risk_free_daily = (
                    align_daily_risk_free(risk_free_table, result.nav.index)
                    if risk_free_table is not None
                    else None
                )
                export_baseline_result(
                    result,
                    scenario_dir,
                    benchmark_returns=benchmark_returns,
                    risk_free_daily=risk_free_daily,
                    full_audit=(
                        cost == (10.0 if spec.frequency == "weekly" else 5.0)
                    ),
                )
                metrics = result.summary(risk_free_daily=risk_free_daily).to_dict()
                relative = relative_performance_summary(
                    result.nav["daily_return"],
                    benchmark_returns,
                    risk_free_daily=risk_free_daily,
                ).to_dict()
                summary_rows.append(
                    {
                        "experiment_id": spec.experiment_id,
                        "signal": spec.signal.value,
                        "top_n": spec.top_n,
                        "frequency": spec.frequency,
                        "cost_bps": cost,
                        "is_primary_cost": (
                            cost == (10.0 if spec.frequency == "weekly" else 5.0)
                        ),
                        **metrics,
                        **relative,
                    }
                )

        summary = pd.DataFrame(summary_rows).sort_values(
            ["experiment_id", "cost_bps"], ignore_index=True
        )
        summary.to_csv(staging_dir / "all_results_summary.csv", index=False)
        registry = pd.DataFrame(
            [
                {
                    "experiment_id": spec.experiment_id,
                    "signal": spec.signal.value,
                    "top_n": spec.top_n,
                    "frequency": spec.frequency,
                }
                for spec in specs
            ]
        )
        registry.to_csv(staging_dir / "experiment_registry.csv", index=False)

        result_files = [
            path for path in sorted(staging_dir.rglob("*")) if path.is_file()
        ]
        dataset_formal_blockers = list(dataset_manifest.get("formal_blockers", []))
        formal_run_eligible = (
            dataset_declares_formal
            and dataset_status == "valid"
            and calendar_source == "XNYS"
            and benchmark_kind == "total_return_index"
            and risk_free_provided
            and not dataset_formal_blockers
        )
        formal_blockers = dataset_formal_blockers
        if not dataset_declares_formal and not formal_blockers:
            formal_blockers.append("dataset_does_not_declare_formal_eligibility")
        if dataset_status != "valid":
            formal_blockers.append(f"dataset_status_{dataset_status}")
        if calendar_source != "XNYS":
            formal_blockers.append("calendar_is_not_XNYS")
        if benchmark_kind != "total_return_index":
            formal_blockers.append("benchmark_is_not_total_return_index")
        if not risk_free_provided:
            formal_blockers.append("risk_free_daily_not_provided")
        run_manifest = {
            "run_id": config.run_id,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "dataset_version": config.dataset_version,
            "dataset_status": dataset_status,
            "research_tier": research_tier,
            "dataset_declares_formal_eligible": dataset_declares_formal,
            "formal_run_eligible": formal_run_eligible,
            "formal_blockers": sorted(set(map(str, formal_blockers))),
            "dataset_status_override": (
                "review"
                if dataset_status == "review"
                else "invalid_data"
                if dataset_status == "invalid_data"
                else None
            ),
            "calendar_override": calendar_source != "XNYS",
            "dataset_manifest": str(dataset_manifest_path),
            "dataset_manifest_sha256": sha256_file(dataset_manifest_path),
            "evaluation_start": str(evaluation_start.date()),
            "signal_end": str(signal_end.date()),
            "calendar_source": calendar_source,
            "benchmark_kind": benchmark_kind,
            "risk_free": {
                "source": (
                    risk_free_metadata.get("source")
                    if isinstance(risk_free_metadata, dict)
                    else "zero_assumption"
                ),
                "t_bill_series_loaded": risk_free_provided,
                "units": (
                    risk_free_metadata.get("units")
                    if isinstance(risk_free_metadata, dict)
                    else "zero_daily_return_assumption"
                ),
                "reported_sharpe_without_rf": "sharpe_zero_rf",
                "reported_sharpe_with_rf": (
                    "sharpe_excess_rf" if risk_free_provided else None
                ),
                "relative_alpha_with_rf": (
                    "annualized_alpha_excess_rf" if risk_free_provided else None
                ),
                "relative_alpha_zero_rf": "annualized_alpha_zero_rf",
            },
            "corporate_actions": {
                "provided": corporate_actions_provided,
                "curated_table": (
                    corporate_action_table_name if corporate_actions_provided else None
                ),
                "source": (
                    corporate_action_metadata.get("source")
                    if isinstance(corporate_action_metadata, dict)
                    else None
                ),
                "ledger_record_count": len(corporate_actions.to_frame()),
                "applied_event_rows_across_scenarios": int(
                    sum(
                        int(row.get("corporate_action_events_applied", 0))
                        for row in summary_rows
                    )
                ),
                "event_accounting": "pre_open_before_rebalance_no_forced_turnover_cost",
            },
            "prototype_valuation_policy": missing_valuation_policy,
            "prototype_execution_policy": missing_execution_policy,
            "code_commit": _git_commit(Path(__file__).resolve().parents[3]),
            "strategy_path_count": len(specs),
            "cost_scenarios_bps": list(config.costs_bps),
            "scenario_result_count": len(summary_rows),
            "audit_export_policy": (
                "full holdings/rankings only for weekly 10bps and monthly 5bps; "
                "all cost scenarios retain NAV, rebalance, benchmark, RF, and summary"
            ),
            "rules": {
                "long_only": True,
                "equal_weight_each_rebalance": True,
                "execution": "next_exchange_session_open",
                "evaluation_boundary": (
                    "initialize from cash at research_start open; the first "
                    "eligible signal may be the preceding exchange-session close"
                ),
                "missing_held_price": missing_valuation_policy,
                "missing_selected_open": missing_execution_policy,
                "signals": ["mom_255_0", "mom_255_21", "mom_12_1"],
                "top_n": [10, 20, 50],
                "frequency": ["weekly", "monthly"],
            },
            "files": [
                {
                    "path": str(path.relative_to(staging_dir)),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for path in result_files
            ],
        }
        manifest_path = staging_dir / "run_manifest.json"
        manifest_path.write_text(
            json.dumps(run_manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        staging_dir.rename(final_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    return BaselineRunResult(
        run_id=config.run_id,
        output_dir=final_dir,
        manifest_path=final_dir / "run_manifest.json",
        path_count=len(specs),
        scenario_count=len(summary_rows),
        formal_run_eligible=formal_run_eligible,
    )


def _membership_from_frame(frame: pd.DataFrame) -> PITMembership:
    if {"sid", "effective_from", "effective_to"}.issubset(frame.columns):
        return PITMembership.from_intervals(frame)
    if {"date", "sid"}.issubset(frame.columns):
        return PITMembership.from_snapshots(frame)
    raise DataQualityError("curated membership table has an unknown schema")


def _verify_dataset_files(layout: DatasetLayout, manifest: dict[str, object]) -> None:
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise DataQualityError("dataset manifest contains no referenced files")
    for record in records:
        if not isinstance(record, dict) or "path" not in record or "sha256" not in record:
            raise DataQualityError("malformed file record in dataset manifest")
        path = Path(str(record["path"]))
        if not path.is_absolute():
            path = layout.root / path
        if not path.is_file():
            raise DataQualityError(f"manifest-referenced dataset file is missing: {path}")
        if sha256_file(path) != str(record["sha256"]):
            raise DataQualityError(f"dataset file hash mismatch: {path}")


def _cost_directory_name(cost: float) -> str:
    text = f"{cost:g}".replace("-", "m").replace(".", "p")
    return f"cost_{text}bps"


def _git_commit(repository: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return completed.stdout.strip()
    except Exception:
        return "unavailable"
