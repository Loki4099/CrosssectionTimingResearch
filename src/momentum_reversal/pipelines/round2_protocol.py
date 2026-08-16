"""Deterministic calendar-only protocol builder for Round 2.

This module deliberately reads no returns, targets, features, or model output.
It turns the frozen R2A decision calendar into absolute outer/inner folds and
the one mechanical lockbox boundary required before R2B/R2C may start.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from momentum_reversal.data.qa import DataQualityError


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_r2a_candidate(candidate_dir: str | Path) -> dict[str, Any]:
    directory = Path(candidate_dir).resolve()
    manifest_path = directory / "manifest.json"
    frozen_path = directory / "FROZEN.json"
    if not manifest_path.is_file() or not frozen_path.is_file():
        raise FileNotFoundError("R2A candidate requires manifest.json and FROZEN.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "completed_candidate":
        raise DataQualityError("R2A manifest is not completed_candidate")
    if manifest.get("license_review_status") != "approved_for_local_research":
        raise DataQualityError("R2A local-research license gate is not approved")
    if frozen.get("manifest_sha256") != sha256_file(manifest_path):
        raise DataQualityError("R2A FROZEN manifest anchor mismatch")
    for record in manifest.get("files", []):
        path = directory / Path(record["path"])
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.stat().st_size != int(record["size_bytes"]):
            raise DataQualityError(f"R2A byte count mismatch: {record['path']}")
        if sha256_file(path) != str(record["sha256"]):
            raise DataQualityError(f"R2A SHA256 mismatch: {record['path']}")
    return manifest


def build_round2_fold_manifest(
    candidate_dir: str | Path,
    *,
    max_feature_lookback_sessions: int = 252,
    minimum_outer_training_weeks: int = 520,
    inner_validation_weeks: int = 52,
    maximum_inner_folds: int = 5,
    minimum_inner_folds: int = 3,
    minimum_inner_training_weeks: int = 260,
    boundary_excluded_signals: int = 5,
    minimum_lockbox_weeks: int = 208,
) -> dict[str, Any]:
    directory = Path(candidate_dir).resolve()
    manifest = verify_r2a_candidate(directory)
    market = pd.read_parquet(
        directory / "curated" / "market_daily.parquet",
        columns=["session_date"],
    )
    calendar = pd.read_parquet(directory / "curated" / "decision_calendar.parquet")
    market_dates = pd.to_datetime(market["session_date"]).dt.normalize()
    if market_dates.duplicated().any() or not market_dates.is_monotonic_increasing:
        raise DataQualityError("R2A market sessions must be unique and increasing")
    for column in (
        "signal_session",
        "execution_session",
        "next_4w_execution",
    ):
        calendar[column] = pd.to_datetime(calendar[column]).dt.normalize()
    calendar = calendar.sort_values("signal_session", kind="mergesort").reset_index(
        drop=True
    )
    if calendar["signal_session"].duplicated().any():
        raise DataQualityError("R2A scheduled signals are not unique")
    position = {value: index for index, value in enumerate(market_dates)}
    calendar["market_session_index"] = calendar["signal_session"].map(position)
    if calendar["market_session_index"].isna().any():
        raise DataQualityError("R2A signal session is absent from market_daily")
    calendar["feature_complete"] = calendar["market_session_index"].ge(
        max_feature_lookback_sessions - 1
    )
    feature_rows = calendar.loc[calendar["feature_complete"]].copy()
    if feature_rows.empty:
        raise DataQualityError("no feature-complete weekly signals")

    first_year = int(calendar["execution_session"].dt.year.min()) + 1
    last_full_year = int(calendar["execution_session"].dt.year.max()) - 1
    full_years = list(range(first_year, last_full_year + 1))
    first_outer_year: int | None = None
    for year in full_years:
        test = calendar.loc[calendar["execution_session"].dt.year.eq(year)]
        if test.empty:
            continue
        boundary = int(test.index[0])
        first_signal = pd.Timestamp(test.iloc[0]["signal_session"])
        eligible = calendar.loc[
            calendar["feature_complete"]
            & calendar.index.to_series().le(boundary - boundary_excluded_signals - 1)
            & calendar["next_4w_execution"].le(first_signal)
        ]
        if len(eligible) >= minimum_outer_training_weeks:
            first_outer_year = year
            break
    if first_outer_year is None:
        raise DataQualityError("no full outer year meets the 520-week training gate")

    lockbox_years = [
        year
        for year in full_years
        if len(calendar.loc[calendar["execution_session"].dt.year.ge(year)])
        >= minimum_lockbox_weeks
    ]
    if not lockbox_years:
        raise DataQualityError("no full-year lockbox boundary meets the 208-week gate")
    lockbox_year = max(lockbox_years)
    if lockbox_year <= first_outer_year:
        raise DataQualityError("lockbox leaves no development outer years")

    outer_folds: list[dict[str, Any]] = []
    for year in range(first_outer_year, lockbox_year):
        test = calendar.loc[calendar["execution_session"].dt.year.eq(year)]
        if test.empty:
            raise DataQualityError(f"outer year {year} has no scheduled signals")
        boundary = int(test.index[0])
        first_signal = pd.Timestamp(test.iloc[0]["signal_session"])
        train = calendar.loc[
            calendar["feature_complete"]
            & calendar.index.to_series().le(boundary - boundary_excluded_signals - 1)
            & calendar["next_4w_execution"].le(first_signal)
        ]
        if len(train) < minimum_outer_training_weeks:
            raise DataQualityError(f"outer year {year} violates training gate")
        inner_folds = _inner_folds(
            train,
            full_calendar=calendar,
            validation_weeks=inner_validation_weeks,
            maximum_folds=maximum_inner_folds,
            minimum_folds=minimum_inner_folds,
            minimum_training_weeks=minimum_inner_training_weeks,
            boundary_excluded_signals=boundary_excluded_signals,
        )
        outer_folds.append(
            {
                "outer_year": year,
                "train_start_signal": _date(train.iloc[0]["signal_session"]),
                "train_end_signal": _date(train.iloc[-1]["signal_session"]),
                "train_weeks": len(train),
                "test_start_signal": _date(test.iloc[0]["signal_session"]),
                "test_start_execution": _date(test.iloc[0]["execution_session"]),
                "test_end_signal": _date(test.iloc[-1]["signal_session"]),
                "test_end_execution": _date(test.iloc[-1]["execution_session"]),
                "test_weeks": len(test),
                "inner_folds": inner_folds,
            }
        )

    lockbox = calendar.loc[calendar["execution_session"].dt.year.ge(lockbox_year)]
    lockbox_start = lockbox.iloc[0]
    lockbox_end = lockbox.iloc[-1]
    result = {
        "schema_version": 1,
        "program_id": "defense_timing_round2_v1",
        "source": {
            "dataset_version": manifest["dataset_version"],
            "snapshot_id": manifest["snapshot_id"],
            "manifest_sha256": sha256_file(directory / "manifest.json"),
            "canonical_content_sha256": manifest["canonical_content_sha256"],
        },
        "rules": {
            "max_feature_lookback_sessions": max_feature_lookback_sessions,
            "minimum_outer_training_weeks": minimum_outer_training_weeks,
            "inner_validation_weeks": inner_validation_weeks,
            "maximum_inner_folds": maximum_inner_folds,
            "minimum_inner_folds": minimum_inner_folds,
            "minimum_inner_training_weeks": minimum_inner_training_weeks,
            "boundary_excluded_signals": boundary_excluded_signals,
            "minimum_lockbox_weeks": minimum_lockbox_weeks,
        },
        "first_feature_complete": {
            "signal": _date(feature_rows.iloc[0]["signal_session"]),
            "execution": _date(feature_rows.iloc[0]["execution_session"]),
            "market_session_index_zero_based": int(
                feature_rows.iloc[0]["market_session_index"]
            ),
        },
        "development": {
            "first_outer_year": first_outer_year,
            "last_outer_year": lockbox_year - 1,
            "outer_folds": outer_folds,
            "total_test_weeks": sum(fold["test_weeks"] for fold in outer_folds),
        },
        "mechanical_lockbox": {
            "start_execution_year": lockbox_year,
            "start_signal": _date(lockbox_start["signal_session"]),
            "start_execution": _date(lockbox_start["execution_session"]),
            "end_signal": _date(lockbox_end["signal_session"]),
            "end_execution": _date(lockbox_end["execution_session"]),
            "weeks": len(lockbox),
            "full_years": [
                year for year in range(lockbox_year, last_full_year + 1)
            ],
            "partial_final_year": int(calendar.iloc[-1]["execution_session"].year),
        },
        "pit01": {
            "status": "not_opened_user_deferred_paid_pit_data",
            "champion_eligibility": False,
        },
    }
    return result


def _inner_folds(
    outer_train: pd.DataFrame,
    *,
    full_calendar: pd.DataFrame,
    validation_weeks: int,
    maximum_folds: int,
    minimum_folds: int,
    minimum_training_weeks: int,
    boundary_excluded_signals: int,
) -> list[dict[str, Any]]:
    folds: list[dict[str, Any]] = []
    end = len(outer_train)
    for reverse_index in range(maximum_folds, 0, -1):
        start_position = end - reverse_index * validation_weeks
        stop_position = start_position + validation_weeks
        if start_position < 0:
            continue
        validation = outer_train.iloc[start_position:stop_position]
        if len(validation) != validation_weeks:
            continue
        validation_start_global = int(validation.index[0])
        validation_start_signal = pd.Timestamp(validation.iloc[0]["signal_session"])
        train = full_calendar.loc[
            full_calendar["feature_complete"]
            & full_calendar.index.to_series().le(
                validation_start_global - boundary_excluded_signals - 1
            )
            & full_calendar["next_4w_execution"].le(validation_start_signal)
        ]
        if len(train) < minimum_training_weeks:
            continue
        folds.append(
            {
                "inner_fold": len(folds) + 1,
                "train_start_signal": _date(train.iloc[0]["signal_session"]),
                "train_end_signal": _date(train.iloc[-1]["signal_session"]),
                "train_weeks": len(train),
                "validation_start_signal": _date(
                    validation.iloc[0]["signal_session"]
                ),
                "validation_end_signal": _date(
                    validation.iloc[-1]["signal_session"]
                ),
                "validation_weeks": len(validation),
            }
        )
    if len(folds) < minimum_folds:
        raise DataQualityError("outer fold has fewer than three valid inner folds")
    return folds


def _date(value: object) -> str:
    return str(pd.Timestamp(value).date())

