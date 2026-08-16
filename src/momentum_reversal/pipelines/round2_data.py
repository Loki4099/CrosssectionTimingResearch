"""R2A long-market-line staging builder.

The builder is intentionally unable to compute targets, features, model
scores, or strategy returns.  It downloads and audits source data only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata as importlib_metadata
import json
from pathlib import Path
import re
import subprocess
from typing import Any

import numpy as np
import pandas as pd

from momentum_reversal.data.provider import AssetRef
from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.round2_market import (
    CBOE_VIX_HISTORY_URL,
    CBOE_VIX_LEGACY_URL,
    KEN_FRENCH_DAILY_FACTORS_URL,
    build_round2_decision_calendar,
    canonical_arrow_sha256,
    download_public_bytes,
    download_tiingo_eod_json,
    load_and_validate_r2a_config,
    normalize_cboe_vix_csv,
    normalize_cboe_vix_legacy_xls,
    normalize_french_daily_rf_zip,
    sha256_file,
)
from momentum_reversal.data.tiingo_provider import normalize_tiingo_response


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True, slots=True)
class R2ALongStagingResult:
    output_dir: Path
    manifest_path: Path
    status: str
    spy_rows: int
    risk_free_rows: int
    vix_rows: int
    vix_missing_sessions: int
    vix_missing_signal_sessions: int


def rebuild_r2a_long_canonical_hashes(
    staging_dir: str | Path,
) -> dict[str, str]:
    """Rebuild all curated L-line tables from frozen raw bytes in memory.

    This is the second clean-build gate.  It never reads the persisted curated
    Parquet tables and therefore catches non-deterministic normalization.
    """

    directory = Path(staging_dir).resolve()
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot_id = str(manifest["snapshot_id"])
    start = pd.Timestamp(manifest["range"]["start"])
    end = pd.Timestamp(manifest["range"]["end"])
    raw = directory / "raw"
    tiingo_bytes = (raw / "tiingo_spy_1993_2026.json").read_bytes()
    cboe_bytes = (raw / "cboe_vix_history.csv").read_bytes()
    cboe_legacy_bytes = (raw / "cboe_vix_1990_2003.xls").read_bytes()
    french_bytes = (raw / "ken_french_daily_factors.zip").read_bytes()
    calendar = _full_calendar(start, end)
    decision = build_round2_decision_calendar(start=start, end=end)
    spy = normalize_tiingo_response(
        json.loads(tiingo_bytes.decode("utf-8")), AssetRef("SPY", "SPY")
    ).reset_index()
    market = _market_daily(spy, calendar, snapshot_id)
    risk_free = _risk_free_daily(
        normalize_french_daily_rf_zip(french_bytes, start=start, end=end),
        calendar,
        snapshot_id,
    )
    vix, _ = _vix_daily(
        current=normalize_cboe_vix_csv(cboe_bytes, start=start, end=end),
        legacy=normalize_cboe_vix_legacy_xls(
            cboe_legacy_bytes, start=start, end=min(end, pd.Timestamp("2003-12-31"))
        ),
        calendar=calendar,
        snapshot_id=snapshot_id,
    )
    return {
        "market_daily": canonical_arrow_sha256(
            market, primary_key=["session_date", "asset_id"]
        ),
        "vix_daily": canonical_arrow_sha256(vix, primary_key=["session_date"]),
        "risk_free_daily": canonical_arrow_sha256(
            risk_free, primary_key=["session_date"]
        ),
        "decision_calendar": canonical_arrow_sha256(
            decision, primary_key=["week_id"]
        ),
    }


def build_r2a_long_staging(
    *,
    config_path: str | Path,
    project_root: str | Path,
    data_root: str | Path,
    snapshot_id: str,
    license_review_status: str = "pending_human_confirmation",
    reuse_raw_from: str | Path | None = None,
) -> R2ALongStagingResult:
    """Build one immutable R2A L-line staging snapshot and QA manifest."""

    if not _SAFE_ID.fullmatch(snapshot_id):
        raise ValueError(f"unsafe snapshot_id: {snapshot_id!r}")
    if license_review_status not in {
        "pending_human_confirmation",
        "approved_for_local_research",
    }:
        raise ValueError("unsupported license_review_status")
    root = Path(project_root).resolve()
    runtime_data = Path(data_root).resolve()
    try:
        runtime_data.relative_to(root)
    except ValueError:
        pass
    else:
        raise DataQualityError("R2A raw/staging data must remain outside the Git project")

    config = load_and_validate_r2a_config(config_path, project_root=root)
    line = config["long_line"]
    start = pd.Timestamp(line["start"])
    end = pd.Timestamp(line["end"])
    output_dir = runtime_data / "round2" / "staging" / "R2A_DATA" / snapshot_id
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = output_dir / "raw"
    curated_dir = output_dir / "curated"
    qa_dir = output_dir / "qa"
    for directory in (raw_dir, curated_dir, qa_dir):
        directory.mkdir()

    parent_snapshot_id: str | None = None
    if reuse_raw_from is None:
        retrieved_at = datetime.now(timezone.utc).isoformat()
        tiingo_bytes = download_tiingo_eod_json(
            symbol="SPY", start=start, end=end, project_root=root
        )
        cboe_bytes = download_public_bytes(CBOE_VIX_HISTORY_URL)
        cboe_legacy_bytes = download_public_bytes(CBOE_VIX_LEGACY_URL)
        french_bytes = download_public_bytes(KEN_FRENCH_DAILY_FACTORS_URL)
    else:
        source = Path(reuse_raw_from).resolve()
        parent_manifest_path = source / "manifest.json"
        if not parent_manifest_path.is_file():
            raise FileNotFoundError(parent_manifest_path)
        parent_manifest = json.loads(parent_manifest_path.read_text(encoding="utf-8"))
        parent_snapshot_id = str(parent_manifest["snapshot_id"])
        if parent_manifest.get("dataset_version") != line["dataset_version"]:
            raise DataQualityError("reused raw snapshot has a different dataset_version")
        parent_range = parent_manifest.get("range", {})
        if parent_range != {"start": str(start.date()), "end": str(end.date())}:
            raise DataQualityError("reused raw snapshot has a different date range")
        source_ledger = json.loads(
            (source / "raw" / "source_ledger.json").read_text(encoding="utf-8")
        )
        retrieved_at = str(source_ledger["retrieved_at_utc"])
        tiingo_bytes, cboe_bytes, cboe_legacy_bytes, french_bytes = (
            _read_verified_parent_raw(source, parent_manifest, relative_path)
            for relative_path in (
                "raw/tiingo_spy_1993_2026.json",
                "raw/cboe_vix_history.csv",
                "raw/cboe_vix_1990_2003.xls",
                "raw/ken_french_daily_factors.zip",
            )
        )
    raw_paths = {
        "tiingo_spy": raw_dir / "tiingo_spy_1993_2026.json",
        "cboe_vix": raw_dir / "cboe_vix_history.csv",
        "cboe_vix_legacy": raw_dir / "cboe_vix_1990_2003.xls",
        "ken_french_rf": raw_dir / "ken_french_daily_factors.zip",
    }
    for key, payload in (
        ("tiingo_spy", tiingo_bytes),
        ("cboe_vix", cboe_bytes),
        ("cboe_vix_legacy", cboe_legacy_bytes),
        ("ken_french_rf", french_bytes),
    ):
        raw_paths[key].write_bytes(payload)

    calendar = _full_calendar(start, end)
    session_set = set(calendar["session_date"])
    decision = build_round2_decision_calendar(start=start, end=end)

    spy_payload = json.loads(tiingo_bytes.decode("utf-8"))
    spy = normalize_tiingo_response(spy_payload, AssetRef("SPY", "SPY")).reset_index()
    market = _market_daily(spy, calendar, snapshot_id)
    rf = _risk_free_daily(
        normalize_french_daily_rf_zip(french_bytes, start=start, end=end),
        calendar,
        snapshot_id,
    )
    vix, vix_reconciliation = _vix_daily(
        current=normalize_cboe_vix_csv(cboe_bytes, start=start, end=end),
        legacy=normalize_cboe_vix_legacy_xls(
            cboe_legacy_bytes, start=start, end=min(end, pd.Timestamp("2003-12-31"))
        ),
        calendar=calendar,
        snapshot_id=snapshot_id,
    )

    spy_dates = set(market["session_date"])
    rf_dates = set(rf["session_date"])
    vix_dates = set(vix["session_date"])
    missing_spy = sorted(session_set.difference(spy_dates))
    missing_rf = sorted(session_set.difference(rf_dates))
    missing_vix = sorted(session_set.difference(vix_dates))
    missing_vix_signals = sorted(
        set(decision["signal_session"]).difference(vix_dates)
    )
    if missing_spy:
        raise DataQualityError(f"R2A SPY missing XNYS sessions: {missing_spy[:10]}")
    if missing_rf:
        raise DataQualityError(f"R2A RF missing XNYS sessions: {missing_rf[:10]}")

    v3_overlap = _build_v3_overlap(runtime_data, market, rf)
    adjustment_events = market.loc[
        market["dividend_cash"].ne(0) | market["split_factor"].ne(1),
        [
            "session_date",
            "dividend_cash",
            "split_factor",
            "raw_close",
            "tr_close",
        ],
    ].reset_index(drop=True)

    curated_tables = {
        "market_daily": market,
        "vix_daily": vix,
        "risk_free_daily": rf,
        "decision_calendar": decision,
    }
    curated_paths: dict[str, Path] = {}
    for name, frame in curated_tables.items():
        path = curated_dir / f"{name}.parquet"
        frame.to_parquet(path, index=False, compression="zstd")
        curated_paths[name] = path
    qa_tables = {
        "overlap_reconciliation": v3_overlap,
        "adjustment_events": adjustment_events,
        "vix_source_reconciliation": vix_reconciliation,
    }
    qa_paths: dict[str, Path] = {}
    for name, frame in qa_tables.items():
        path = qa_dir / f"{name}.parquet"
        frame.to_parquet(path, index=False, compression="zstd")
        qa_paths[name] = path

    license_approved = license_review_status == "approved_for_local_research"
    f3_status = "available" if not missing_vix_signals else "invalid_data/not_available"
    status = "completed_candidate" if license_approved else "staging_review"
    gates = {
        "schema_version": 1,
        "status": status,
        "required_gates_pass": bool(not missing_spy and not missing_rf),
        "license_gate_pass": license_approved,
        "spy_xnys_sessions": len(session_set),
        "spy_missing_sessions": [str(value.date()) for value in missing_spy],
        "risk_free_missing_sessions": [str(value.date()) for value in missing_rf],
        "vix_missing_sessions": [str(value.date()) for value in missing_vix],
        "vix_missing_signal_sessions": [
            str(value.date()) for value in missing_vix_signals
        ],
        "vix_F3_status": f3_status,
        "targets_computed": False,
        "features_computed": False,
        "models_run": False,
        "backtests_run": False,
    }
    gates_path = qa_dir / "gates.json"
    _write_json(gates_path, gates)

    source_ledger = {
        "schema_version": 1,
        "retrieved_at_utc": retrieved_at,
        "license_review_status": license_review_status,
        "sources": [
            _source_record(
                "tiingo_eod",
                "Tiingo EOD / SPY",
                "https://www.tiingo.com/documentation/end-of-day",
                raw_paths["tiingo_spy"],
                credential_env_var="TIINGO_API_TOKEN",
            ),
            _source_record(
                "cboe_vix_current",
                "Cboe VIX Historical Price Data",
                CBOE_VIX_HISTORY_URL,
                raw_paths["cboe_vix"],
            ),
            _source_record(
                "cboe_vix_legacy",
                "Cboe VIX 1990-2003 archive",
                CBOE_VIX_LEGACY_URL,
                raw_paths["cboe_vix_legacy"],
            ),
            _source_record(
                "ken_french_daily_rf",
                "F-F Research Data Factors daily",
                KEN_FRENCH_DAILY_FACTORS_URL,
                raw_paths["ken_french_rf"],
            ),
        ],
    }
    source_ledger_path = raw_dir / "source_ledger.json"
    _write_json(source_ledger_path, source_ledger)
    download_manifest_path = raw_dir / "download_manifest.json"
    _write_json(
        download_manifest_path,
        {
            "snapshot_id": snapshot_id,
            "parent_snapshot_id": parent_snapshot_id,
            "raw_reuse_without_network": reuse_raw_from is not None,
            "retrieved_at_utc": retrieved_at,
            "requests": {
                "start": str(start.date()),
                "end": str(end.date()),
                "tiingo_symbol": "SPY",
            },
            "source_ledger_sha256": sha256_file(source_ledger_path),
        },
    )

    recorded_paths = [
        *raw_paths.values(),
        source_ledger_path,
        download_manifest_path,
        *curated_paths.values(),
        *qa_paths.values(),
        gates_path,
    ]
    provenance = _build_provenance(root)
    manifest = {
        "schema_version": 1,
        "program_id": "defense_timing_round2_v1",
        "batch_id": "R2A_DATA",
        "dataset_version": line["dataset_version"],
        "snapshot_id": snapshot_id,
        "parent_snapshot_id": parent_snapshot_id,
        "status": status,
        "formal_eligible": False,
        "license_review_status": license_review_status,
        "range": {"start": str(start.date()), "end": str(end.date())},
        "counts": {
            "xnys_sessions": len(session_set),
            "market_daily": len(market),
            "vix_daily": len(vix),
            "risk_free_daily": len(rf),
            "decision_calendar": len(decision),
        },
        "optional_blocks": {"F3_vix": f3_status},
        "forbidden_outputs_present": False,
        "config_sha256": sha256_file(config_path),
        "design_sha256": config["design_sha256"],
        "program_sha256": config["program_sha256"],
        "build_provenance": provenance,
        "canonical_content_sha256": {
            "market_daily": canonical_arrow_sha256(
                market, primary_key=["session_date", "asset_id"]
            ),
            "vix_daily": canonical_arrow_sha256(vix, primary_key=["session_date"]),
            "risk_free_daily": canonical_arrow_sha256(
                rf, primary_key=["session_date"]
            ),
            "decision_calendar": canonical_arrow_sha256(
                decision, primary_key=["week_id"]
            ),
        },
        "files": [_file_record(path, output_dir) for path in sorted(recorded_paths)],
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    # FROZEN.json is deliberately withheld until license review is explicit.
    if license_approved:
        _write_json(
            output_dir / "FROZEN.json",
            {
                "schema_version": 1,
                "dataset_version": line["dataset_version"],
                "snapshot_id": snapshot_id,
                "status": "completed_candidate",
                "manifest_sha256": sha256_file(manifest_path),
                "formal_eligible": False,
                "config_sha256": sha256_file(config_path),
                "design_sha256": config["design_sha256"],
                "program_sha256": config["program_sha256"],
                "code_commit": provenance["git_commit"],
                "workspace_dirty": provenance["workspace_dirty"],
                "code_file_sha256": provenance["code_file_sha256"],
                "dependency_versions": provenance["dependency_versions"],
            },
        )
    return R2ALongStagingResult(
        output_dir=output_dir,
        manifest_path=manifest_path,
        status=status,
        spy_rows=len(market),
        risk_free_rows=len(rf),
        vix_rows=len(vix),
        vix_missing_sessions=len(missing_vix),
        vix_missing_signal_sessions=len(missing_vix_signals),
    )


def _read_verified_parent_raw(
    source: Path, parent_manifest: dict[str, Any], relative_path: str
) -> bytes:
    records = {
        str(record["path"]): record for record in parent_manifest.get("files", [])
    }
    if relative_path not in records:
        raise DataQualityError(f"parent manifest omits {relative_path}")
    path = source / Path(relative_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    record = records[relative_path]
    if path.stat().st_size != int(record["size_bytes"]):
        raise DataQualityError(f"parent raw byte count mismatch: {relative_path}")
    if sha256_file(path) != str(record["sha256"]):
        raise DataQualityError(f"parent raw SHA256 mismatch: {relative_path}")
    return path.read_bytes()


def _build_provenance(project_root: Path) -> dict[str, Any]:
    relative_code_paths = (
        "scripts/build_round2_r2a.py",
        "src/momentum_reversal/data/round2_market.py",
        "src/momentum_reversal/data/tiingo_provider.py",
        "src/momentum_reversal/pipelines/round2_data.py",
    )
    code_hashes = {
        relative_path: sha256_file(project_root / relative_path)
        for relative_path in relative_code_paths
    }
    git_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    git_status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    dependencies = {
        package: importlib_metadata.version(package)
        for package in (
            "exchange-calendars",
            "numpy",
            "pandas",
            "pyarrow",
            "xlrd",
        )
    }
    return {
        "git_commit": git_commit,
        "workspace_dirty": bool(git_status.strip()),
        "code_file_sha256": code_hashes,
        "dependency_versions": dependencies,
    }


def _full_calendar(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    import exchange_calendars as xcals
    import importlib.metadata as metadata

    extended_end = end + pd.Timedelta(days=14)
    calendar = xcals.get_calendar("XNYS", start=start, end=extended_end)
    sessions = pd.DatetimeIndex(calendar.sessions_in_range(start, end)).tz_localize(None)
    opens = [calendar.session_open(value).tz_convert("America/New_York") for value in sessions]
    closes = [calendar.session_close(value).tz_convert("America/New_York") for value in sessions]
    next_opens = []
    for value in sessions:
        next_session = calendar.next_session(value)
        next_opens.append(calendar.session_open(next_session).tz_convert("America/New_York"))
    return pd.DataFrame(
        {
            "session_date": sessions.normalize(),
            "market_open_et": opens,
            "market_close_et": closes,
            "next_market_open_et": next_opens,
            "calendar_package_version": metadata.version("exchange-calendars"),
        }
    )


def _market_daily(
    spy: pd.DataFrame, calendar: pd.DataFrame, snapshot_id: str
) -> pd.DataFrame:
    renamed = spy.rename(
        columns={
            "date": "session_date",
            "volume": "volume_raw",
            "adjusted_volume": "volume_adjusted",
            "dividends": "dividend_cash",
            "stock_splits": "split_factor_event",
        }
    ).copy()
    renamed["asset_id"] = "SPY"
    renamed["split_factor"] = np.where(
        renamed["split_factor_event"].eq(0), 1.0, renamed["split_factor_event"]
    )
    renamed["provider"] = "Tiingo"
    renamed["provider_symbol"] = renamed["source_symbol"]
    renamed["source_snapshot_id"] = snapshot_id
    renamed = renamed.merge(
        calendar[["session_date", "market_close_et"]],
        on="session_date",
        how="left",
        validate="one_to_one",
    ).rename(columns={"market_close_et": "available_at"})
    if renamed["available_at"].isna().any():
        raise DataQualityError("Tiingo SPY contains dates outside XNYS calendar")
    columns = [
        "session_date",
        "asset_id",
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "tr_open",
        "tr_high",
        "tr_low",
        "tr_close",
        "volume_raw",
        "volume_adjusted",
        "dividend_cash",
        "split_factor",
        "provider",
        "provider_symbol",
        "source_snapshot_id",
        "available_at",
    ]
    result = renamed.loc[:, columns].sort_values("session_date", kind="mergesort")
    result["source_record_hash"] = _row_hashes(result, exclude=["source_record_hash"])
    return result.reset_index(drop=True)


def _risk_free_daily(
    rf: pd.DataFrame, calendar: pd.DataFrame, snapshot_id: str
) -> pd.DataFrame:
    result = rf.merge(
        calendar[["session_date", "next_market_open_et"]],
        on="session_date",
        how="inner",
        validate="one_to_one",
    ).rename(columns={"next_market_open_et": "realized_available_at"})
    result["source_snapshot_id"] = snapshot_id
    result["source_record_hash"] = _row_hashes(result, exclude=["source_record_hash"])
    return result.sort_values("session_date", kind="mergesort").reset_index(drop=True)


def _vix_daily(
    *,
    current: pd.DataFrame,
    legacy: pd.DataFrame,
    calendar: pd.DataFrame,
    snapshot_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    overlap = current.merge(
        legacy,
        on="session_date",
        how="inner",
        suffixes=("_current", "_legacy"),
        validate="one_to_one",
    )
    overlap["absolute_difference"] = (
        overlap["vix_close_percent_current"] - overlap["vix_close_percent_legacy"]
    ).abs()
    current_dates = set(current["session_date"])
    recovery = legacy.loc[~legacy["session_date"].isin(current_dates)].copy()
    current_copy = current.copy()
    current_copy["source_variant"] = "current_csv"
    recovery["source_variant"] = "legacy_xls_recovery"
    combined = pd.concat([current_copy, recovery], ignore_index=True)
    combined = combined.loc[combined["session_date"].isin(set(calendar["session_date"]))]
    combined = combined.sort_values("session_date", kind="mergesort").drop_duplicates(
        "session_date", keep="first"
    )
    combined["source_snapshot_id"] = snapshot_id
    combined = combined.merge(
        calendar[["session_date", "market_close_et"]],
        on="session_date",
        how="left",
        validate="one_to_one",
    ).rename(columns={"market_close_et": "available_at"})
    combined["source_record_hash"] = _row_hashes(
        combined, exclude=["source_record_hash"]
    )
    return combined.reset_index(drop=True), overlap.reset_index(drop=True)


def _build_v3_overlap(
    data_root: Path, market: pd.DataFrame, rf: pd.DataFrame
) -> pd.DataFrame:
    version = "sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate"
    curated = data_root / "curated" / version
    benchmark_path = curated / "benchmark_daily.parquet"
    rf_path = curated / "risk_free_daily.parquet"
    if not benchmark_path.is_file() or not rf_path.is_file():
        raise FileNotFoundError("frozen v3 benchmark/risk-free tables are unavailable")
    benchmark = pd.read_parquet(benchmark_path).rename(columns={"date": "session_date"})
    old_rf = pd.read_parquet(rf_path).rename(columns={"date": "session_date"})
    benchmark["session_date"] = pd.to_datetime(benchmark["session_date"]).dt.normalize()
    old_rf["session_date"] = pd.to_datetime(old_rf["session_date"]).dt.normalize()
    joined = market.merge(
        benchmark[
            ["session_date", "benchmark_tr_open", "benchmark_tr_close"]
        ],
        on="session_date",
        how="inner",
        validate="one_to_one",
    ).merge(
        rf[["session_date", "rf_simple_decimal"]],
        on="session_date",
        how="left",
        validate="one_to_one",
    ).merge(
        old_rf[["session_date", "rf_return"]],
        on="session_date",
        how="inner",
        validate="one_to_one",
    )
    joined["tr_open_abs_diff"] = (joined["tr_open"] - joined["benchmark_tr_open"]).abs()
    joined["tr_close_abs_diff"] = (joined["tr_close"] - joined["benchmark_tr_close"]).abs()
    joined["rf_abs_diff"] = (joined["rf_simple_decimal"] - joined["rf_return"]).abs()
    return joined[
        [
            "session_date",
            "tr_open",
            "benchmark_tr_open",
            "tr_open_abs_diff",
            "tr_close",
            "benchmark_tr_close",
            "tr_close_abs_diff",
            "rf_simple_decimal",
            "rf_return",
            "rf_abs_diff",
        ]
    ].reset_index(drop=True)


def _row_hashes(frame: pd.DataFrame, *, exclude: list[str]) -> list[str]:
    columns = [column for column in frame.columns if column not in exclude]
    hashes: list[str] = []
    for values in frame[columns].itertuples(index=False, name=None):
        encoded = json.dumps(
            [_json_scalar(value) for value in values],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        hashes.append(hashlib.sha256(encoded).hexdigest())
    return hashes


def _json_scalar(value: object) -> object:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if pd.isna(value):
        return None
    return value


def _source_record(
    source_id: str,
    dataset: str,
    official_url: str,
    path: Path,
    *,
    credential_env_var: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "source_id": source_id,
        "dataset": dataset,
        "official_url": official_url,
        "file_name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if credential_env_var:
        result["credential_env_var"] = credential_env_var
    return result


def _file_record(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
