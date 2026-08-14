"""Shared, engine-neutral context for one registered experiment run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import pandas as pd

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
from momentum_reversal.experiments.catalog import ExperimentCatalog
from momentum_reversal.experiments.spec import GroupSpec, StrategySpec


_SAFE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True, slots=True)
class ExperimentRunContext:
    """Resolved metadata and paths; intentionally contains no strategy engine."""

    group: GroupSpec
    run_id: str
    dataset_version: str
    data_root: Path
    output_root: Path

    @property
    def group_id(self) -> str:
        return self.group.group_id

    @property
    def spec_id(self) -> str:
        return self.group.spec_id

    @property
    def strategies(self) -> tuple[StrategySpec, ...]:
        return self.group.strategies()

    @property
    def dataset_manifest_path(self) -> Path:
        return self.data_root / "manifests" / f"{self.dataset_version}.json"

    @property
    def bundle_dir(self) -> Path:
        return (
            self.output_root
            / "experiments"
            / self.group_id
            / "runs"
            / self.run_id
        )

    def manifest_identity(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "spec_id": self.spec_id,
            "run_id": self.run_id,
            "dataset_version": self.dataset_version,
            "spec_sha256": self.group.resolved_sha256,
            "portfolio_modes": [mode.value for mode in self.group.program.portfolio_modes],
            "strategy_count": self.group.strategy_count,
        }


@dataclass(frozen=True, slots=True)
class LoadedExperimentData:
    """Verified curated inputs and authoritative common evaluation boundary."""

    context: ExperimentRunContext
    dataset_manifest: dict[str, object]
    dataset_manifest_path: Path
    dataset_manifest_sha256: str
    prices: pd.DataFrame
    membership: PITMembership
    corporate_actions: CorporateActionLedger
    benchmark: pd.DataFrame
    risk_free_daily: pd.Series
    sessions: pd.DatetimeIndex
    evaluation_start: pd.Timestamp
    evaluation_end: pd.Timestamp
    missing_valuation_policy: str
    legacy_missing_execution_policy: str
    terminal_last_close_max_sessions: int
    dataset_status: str
    calendar_source: str
    benchmark_kind: str
    dataset_declares_formal_eligible: bool
    dataset_research_tier: str
    excluded_sids: tuple[str, ...]
    exclusion_reason: str | None
    excluded_membership_rows: int

    @property
    def evaluation_sessions(self) -> pd.DatetimeIndex:
        return self.sessions[
            (self.sessions >= self.evaluation_start)
            & (self.sessions <= self.evaluation_end)
        ]


def prepare_experiment_run(
    spec_path: str | Path,
    *,
    run_id: str,
    dataset_version: str,
    data_root: str | Path = "data",
    output_root: str | Path = "results",
    require_dataset_manifest: bool = False,
) -> ExperimentRunContext:
    """Validate a registered TOML group and resolve its standard bundle path."""

    _require_safe_token(run_id, "run_id")
    _require_safe_token(dataset_version, "dataset_version")
    source = Path(spec_path)
    catalog = ExperimentCatalog.load(source.parent)
    group = catalog.group(source.stem)
    if group.path.resolve() != source.resolve():
        raise ValueError(
            f"spec path must be the canonical registered file for {group.group_id}: "
            f"{group.path}"
        )
    context = ExperimentRunContext(
        group=group,
        run_id=run_id,
        dataset_version=dataset_version,
        data_root=Path(data_root),
        output_root=Path(output_root),
    )
    if require_dataset_manifest and not context.dataset_manifest_path.is_file():
        raise FileNotFoundError(context.dataset_manifest_path)
    return context


def load_experiment_data(
    context: ExperimentRunContext, *, allow_review_dataset: bool = False
) -> LoadedExperimentData:
    """Load and verify the frozen dataset without silently admitting review data."""

    layout = DatasetLayout(context.data_root)
    manifest_store = ManifestStore(layout)
    manifest = manifest_store.read(context.dataset_version)
    manifest_path = layout.manifest_path(context.dataset_version)
    _verify_dataset_files(layout, manifest)
    status = str(manifest.get("status", "unknown"))
    if status == "review" and not allow_review_dataset:
        raise DataQualityError(
            "dataset manifest is review; inspect QA and explicitly pass "
            "allow_review_dataset for a non-formal experiment"
        )
    if status == "invalid_data":
        raise DataQualityError("systematic experiments cannot run invalid_data datasets")
    if status not in {"valid", "review"}:
        raise DataQualityError(f"unknown dataset status: {status!r}")
    _validate_review_dataset_gates(manifest, status=status)
    terminal_max_sessions = _terminal_last_close_max_sessions(
        manifest, status=status
    )
    calendar_source = str(manifest.get("calendar_source", "unknown"))
    if calendar_source != "XNYS":
        raise DataQualityError("systematic experiments require an XNYS calendar")

    store = ParquetStore(layout)
    prices = store.read_curated_prices(dataset_version=context.dataset_version)
    membership_frame = store.read_curated_table(
        dataset_version=context.dataset_version, table_name="membership"
    )
    exclusion = context.group.program.raw.get("data_quality_exclusions", {})
    if not isinstance(exclusion, dict):
        raise DataQualityError("program data_quality_exclusions must be a TOML table")
    excluded_values = exclusion.get("sids", [])
    if not isinstance(excluded_values, list) or not all(
        isinstance(value, str) and value for value in excluded_values
    ):
        raise DataQualityError("data_quality_exclusions.sids must be a string array")
    excluded_sids = tuple(dict.fromkeys(excluded_values))
    exclusion_reason_value = exclusion.get("reason")
    exclusion_reason = (
        str(exclusion_reason_value).strip()
        if exclusion_reason_value is not None
        else ""
    )
    if excluded_sids and not exclusion_reason:
        raise DataQualityError(
            "data_quality_exclusions.reason is required when SIDs are excluded"
        )
    membership_sids = membership_frame["sid"].astype(str)
    missing_exclusions = [
        sid for sid in excluded_sids if not membership_sids.eq(sid).any()
    ]
    if missing_exclusions:
        raise DataQualityError(
            "configured data-quality exclusions do not match membership rows: "
            f"{missing_exclusions}"
        )
    excluded_membership_rows = int(
        membership_sids.isin(excluded_sids).sum()
    )
    if excluded_sids:
        membership_frame = membership_frame.loc[
            ~membership_frame["sid"].astype(str).isin(excluded_sids)
        ].copy()
    membership = _membership_from_frame(membership_frame)
    calendar = store.read_curated_table(
        dataset_version=context.dataset_version, table_name="calendar"
    )
    if "session_date" not in calendar:
        raise DataQualityError("curated calendar is missing session_date")
    sessions = pd.DatetimeIndex(
        pd.to_datetime(calendar["session_date"], errors="raise")
    ).normalize()
    if sessions.tz is not None or sessions.has_duplicates or not sessions.is_monotonic_increasing:
        raise DataQualityError("curated sessions must be unique, ordered, and timezone-naive")

    request = manifest.get("request")
    dates = context.group.program.raw.get("dates")
    if not isinstance(request, dict) or not isinstance(dates, dict):
        raise DataQualityError("dataset request or frozen program dates are missing")
    try:
        manifest_start = pd.Timestamp(request["research_start"]).normalize()
        manifest_end = pd.Timestamp(request["end"]).normalize()
        frozen_start = pd.Timestamp(dates["evaluation_start_open"]).normalize()
        frozen_end = pd.Timestamp(dates["evaluation_end_close"]).normalize()
    except (KeyError, TypeError, ValueError) as error:
        raise DataQualityError("invalid evaluation bounds in manifest/program") from error
    if (manifest_start, manifest_end) != (frozen_start, frozen_end):
        raise DataQualityError(
            "dataset evaluation bounds do not match frozen program: "
            f"manifest=({manifest_start.date()}, {manifest_end.date()}), "
            f"program=({frozen_start.date()}, {frozen_end.date()})"
        )
    if frozen_start not in sessions or frozen_end not in sessions:
        raise DataQualityError("frozen evaluation bounds must both be XNYS sessions")
    sessions = sessions[sessions <= frozen_end]
    for frequency in ("weekly", "monthly"):
        executions = pd.DatetimeIndex(
            rebalance_schedule(sessions, frequency)["execution_date"]
        )
        if frozen_start not in executions:
            raise DataQualityError(
                f"evaluation start is not a common {frequency} execution boundary"
            )

    benchmark = store.read_curated_table(
        dataset_version=context.dataset_version, table_name="benchmark_daily"
    )
    risk_free_metadata = manifest.get("risk_free")
    if not isinstance(risk_free_metadata, dict) or risk_free_metadata.get("provided") is not True:
        raise DataQualityError("G00 requires a curated daily T-bill return series")
    risk_free_table_name = str(risk_free_metadata.get("curated_table", "risk_free_daily"))
    risk_free_table = store.read_curated_table(
        dataset_version=context.dataset_version, table_name=risk_free_table_name
    )
    evaluation_sessions = sessions[
        (sessions >= frozen_start) & (sessions <= frozen_end)
    ]
    risk_free_daily = align_daily_risk_free(risk_free_table, evaluation_sessions)

    corporate_action_metadata = manifest.get("corporate_actions")
    corporate_actions_provided = bool(
        isinstance(corporate_action_metadata, dict)
        and corporate_action_metadata.get("provided") is True
    )
    corporate_action_table = (
        str(corporate_action_metadata.get("curated_table", "corporate_actions"))
        if isinstance(corporate_action_metadata, dict)
        else "corporate_actions"
    )
    corporate_actions = (
        CorporateActionLedger(
            store.read_curated_table(
                dataset_version=context.dataset_version,
                table_name=corporate_action_table,
            )
        )
        if corporate_actions_provided
        else CorporateActionLedger.empty()
    )
    benchmark_metadata = manifest.get("benchmark")
    benchmark_kind = (
        str(benchmark_metadata.get("kind", "unknown"))
        if isinstance(benchmark_metadata, dict)
        else "unknown"
    )
    return LoadedExperimentData(
        context=context,
        dataset_manifest=manifest,
        dataset_manifest_path=manifest_path,
        dataset_manifest_sha256=sha256_file(manifest_path),
        prices=prices,
        membership=membership,
        corporate_actions=corporate_actions,
        benchmark=benchmark,
        risk_free_daily=risk_free_daily,
        sessions=sessions,
        evaluation_start=frozen_start,
        evaluation_end=frozen_end,
        missing_valuation_policy=str(
            manifest.get("prototype_valuation_policy", "strict")
        ),
        legacy_missing_execution_policy=str(
            manifest.get("prototype_execution_policy", "strict")
        ),
        terminal_last_close_max_sessions=terminal_max_sessions,
        dataset_status=status,
        calendar_source=calendar_source,
        benchmark_kind=benchmark_kind,
        dataset_declares_formal_eligible=manifest.get("formal_eligible") is True,
        dataset_research_tier=str(manifest.get("research_tier", "unspecified")),
        excluded_sids=excluded_sids,
        exclusion_reason=exclusion_reason or None,
        excluded_membership_rows=excluded_membership_rows,
    )


def _require_safe_token(value: str, label: str) -> None:
    if not _SAFE_TOKEN.fullmatch(value):
        raise ValueError(f"unsafe {label}: {value!r}")


def _validate_review_dataset_gates(
    manifest: dict[str, object], *, status: str
) -> None:
    if status != "review":
        return
    terminal_gate = manifest.get("terminal_gate")
    corporate_action_gate = manifest.get("corporate_actions")
    if not isinstance(terminal_gate, dict) or terminal_gate.get("passed") is not True:
        raise DataQualityError("review dataset terminal_gate.passed must be true")
    if (
        not isinstance(corporate_action_gate, dict)
        or corporate_action_gate.get("valuation_gate_passed") is not True
    ):
        raise DataQualityError(
            "review dataset corporate_actions.valuation_gate_passed must be true"
        )


def _terminal_last_close_max_sessions(
    manifest: dict[str, object], *, status: str
) -> int:
    key = "prototype_terminal_last_close_max_sessions"
    if status == "valid" and key not in manifest:
        return 25
    value = manifest.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DataQualityError(
            "prototype_terminal_last_close_max_sessions must be a positive integer"
        )
    return value


def _membership_from_frame(frame: pd.DataFrame) -> PITMembership:
    if {"sid", "effective_from", "effective_to"}.issubset(frame.columns):
        return PITMembership.from_intervals(frame)
    if {"date", "sid"}.issubset(frame.columns):
        return PITMembership.from_snapshots(frame)
    raise DataQualityError("curated membership table has an unknown schema")


def _verify_dataset_files(layout: DatasetLayout, manifest: dict[str, object]) -> None:
    """Verify immutable dataset artifacts while treating code as provenance.

    Some historical manifests recorded the build/runtime source files beside
    the actual data artifacts.  Those hashes document which code produced the
    frozen dataset, but cannot be an execution-time immutability gate: research
    code must be able to evolve without pretending that prices or membership
    changed.  Data, ledgers, raw inputs, QA outputs, and parent manifests remain
    hard hash gates.
    """

    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise DataQualityError("dataset manifest contains no referenced files")
    for record in records:
        if not isinstance(record, dict) or not {"path", "sha256"}.issubset(record):
            raise DataQualityError("malformed file record in dataset manifest")
        path = Path(str(record["path"]))
        if not path.is_absolute():
            path = layout.root / path
        if not path.is_file():
            raise DataQualityError(f"manifest-referenced dataset file is missing: {path}")
        if sha256_file(path) != str(record["sha256"]) and not _is_code_provenance(
            path, layout
        ):
            raise DataQualityError(f"dataset file hash mismatch: {path}")


def _is_code_provenance(path: Path, layout: DatasetLayout) -> bool:
    project_root = layout.root.parent.resolve()
    try:
        relative = path.resolve().relative_to(project_root)
    except ValueError:
        return False
    if not relative.parts:
        return False
    return relative.parts[0] in {"src", "scripts", "tests"} or relative.as_posix() in {
        "pyproject.toml",
    }
