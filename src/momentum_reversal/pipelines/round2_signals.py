"""Causal R2B target/feature builder with a hard lockbox outcome firewall."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata as importlib_metadata
import json
from pathlib import Path
import subprocess
import tomllib
from typing import Any

import numpy as np
import pandas as pd

from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.pipelines.round2_protocol import sha256_file, verify_r2a_candidate


CORE_FEATURES = (
    "spy_total_return_21d",
    "spy_total_return_126d",
    "sma50_over_sma200_minus_1",
    "drawdown_from_252d_high",
    "log_spy_rv126",
    "log_rv21_over_rv126",
    "downside_variance_share_63d",
    "return_skew_63d",
    "return_excess_kurtosis_126d",
)
TARGET_COLUMNS = (
    "fwd_excess_logret_1w",
    "cash_wins_1w",
    "fwd_worst_excess_4w",
)


@dataclass(frozen=True, slots=True)
class R2BResult:
    bundle_dir: Path
    manifest_path: Path
    feature_rows: int
    development_target_rows: int
    withheld_lockbox_rows: int


def build_r2b_bundle(
    *,
    project_root: str | Path,
    r2a_candidate_dir: str | Path,
    output_root: str | Path,
    run_id: str,
) -> R2BResult:
    root = Path(project_root).resolve()
    r2a = Path(r2a_candidate_dir).resolve()
    output = (
        Path(output_root).resolve()
        / "experiments"
        / "round2"
        / "R2B_SIGNAL_DIAGNOSTICS"
        / "runs"
        / run_id
    )
    output.mkdir(parents=True, exist_ok=False)
    prereg = _verify_preregistration(root)
    r2a_manifest = verify_r2a_candidate(r2a)
    if sha256_file(r2a / "manifest.json") != prereg["r2a_manifest_sha256"]:
        raise DataQualityError("R2A manifest does not match Round 2 preregistration")

    market = pd.read_parquet(r2a / "curated" / "market_daily.parquet")
    risk_free = pd.read_parquet(r2a / "curated" / "risk_free_daily.parquet")
    calendar = pd.read_parquet(r2a / "curated" / "decision_calendar.parquet")
    features = build_weekly_features(market, calendar)
    targets = build_weekly_development_targets(
        market,
        risk_free,
        calendar,
        lockbox_start_signal=pd.Timestamp("2021-12-31"),
    )
    _validate_feature_target_contract(features, targets)
    diagnostics = build_signal_diagnostics(
        features,
        targets,
        bootstrap_repetitions=2000,
        bootstrap_block_weeks=13,
        seed=20260816,
    )
    coverage = _feature_coverage(features, targets)

    files: list[Path] = []
    for name, frame in (
        ("features_weekly.parquet", features),
        ("targets_weekly.parquet", targets),
        ("sentinel_diagnostics.parquet", diagnostics),
        ("feature_coverage.parquet", coverage),
    ):
        path = output / name
        frame.to_parquet(path, index=False, compression="zstd")
        files.append(path)
    config_source = root / "config" / "experiments" / "round2" / "program.toml"
    config_path = output / "config_resolved.toml"
    config_path.write_bytes(config_source.read_bytes())
    files.append(config_path)

    provenance = _build_provenance(root)
    manifest = {
        "schema_version": 1,
        "program_id": "defense_timing_round2_v1",
        "batch_id": "R2B_SIGNAL_DIAGNOSTICS",
        "run_id": run_id,
        "status": "completed",
        "formal_eligible": False,
        "r2a": {
            "dataset_version": r2a_manifest["dataset_version"],
            "snapshot_id": r2a_manifest["snapshot_id"],
            "manifest_sha256": sha256_file(r2a / "manifest.json"),
        },
        "preregistration": {
            key: value["sha256"]
            for key, value in prereg.items()
            if isinstance(value, dict) and "sha256" in value
        },
        "counts": {
            "feature_rows": len(features),
            "feature_complete_rows": int(features["feature_complete"].sum()),
            "development_target_rows": int(
                targets["target_available"].sum()
            ),
            "withheld_lockbox_rows": int(targets["withheld_lockbox"].sum()),
            "diagnostic_rows": len(diagnostics),
        },
        "lockbox_firewall": {
            "start_signal": "2021-12-31",
            "target_values_present": False,
            "candidate_predictions_present": False,
        },
        "forbidden_outputs_present": False,
        "build_provenance": provenance,
        "files": [_file_record(path, output) for path in sorted(files)],
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return R2BResult(
        bundle_dir=output,
        manifest_path=manifest_path,
        feature_rows=len(features),
        development_target_rows=int(
            targets["target_available"].sum()
        ),
        withheld_lockbox_rows=int(targets["withheld_lockbox"].sum()),
    )


def build_weekly_features(
    market_daily: pd.DataFrame, decision_calendar: pd.DataFrame
) -> pd.DataFrame:
    market = market_daily.copy()
    market["session_date"] = pd.to_datetime(market["session_date"]).dt.normalize()
    market = market.sort_values("session_date", kind="mergesort").set_index(
        "session_date"
    )
    if not market.index.is_unique:
        raise DataQualityError("R2A market sessions are not unique")
    price = pd.to_numeric(market["tr_close"], errors="coerce").astype(float)
    simple_return = price.pct_change(fill_method=None)
    rv21 = simple_return.rolling(21, min_periods=21).std(ddof=1) * np.sqrt(252.0)
    rv126 = simple_return.rolling(126, min_periods=126).std(ddof=1) * np.sqrt(252.0)

    denominator = simple_return.pow(2).rolling(63, min_periods=63).sum()
    downside = simple_return.clip(upper=0).pow(2).rolling(63, min_periods=63).sum()
    daily = pd.DataFrame(index=market.index)
    daily["spy_total_return_21d"] = price.div(price.shift(21)).sub(1.0)
    daily["spy_total_return_126d"] = price.div(price.shift(126)).sub(1.0)
    daily["sma50_over_sma200_minus_1"] = (
        price.rolling(50, min_periods=50).mean()
        / price.rolling(200, min_periods=200).mean()
        - 1.0
    )
    daily["drawdown_from_252d_high"] = (
        price / price.rolling(252, min_periods=252).max() - 1.0
    )
    daily["spy_rv21"] = rv21
    daily["spy_rv126"] = rv126
    daily["log_spy_rv126"] = np.log(rv126)
    daily["log_rv21_over_rv126"] = np.log(rv21 / rv126)
    daily["downside_variance_share_63d"] = downside / denominator
    daily["return_skew_63d"] = simple_return.rolling(63, min_periods=63).skew()
    daily["return_excess_kurtosis_126d"] = simple_return.rolling(
        126, min_periods=126
    ).kurt()

    calendar = decision_calendar.copy()
    calendar["signal_session"] = pd.to_datetime(calendar["signal_session"]).dt.normalize()
    calendar = calendar.sort_values("signal_session", kind="mergesort")
    weekly = calendar[
        [
            "week_id",
            "signal_session",
            "signal_timestamp_et",
            "execution_session",
            "execution_timestamp_et",
        ]
    ].merge(
        daily.reset_index(),
        left_on="signal_session",
        right_on="session_date",
        how="left",
        validate="one_to_one",
    )
    weekly.drop(columns=["session_date"], inplace=True)
    prior_signal_close = weekly["signal_session"].map(price).shift(1)
    current_signal_close = weekly["signal_session"].map(price)
    weekly["hmm_spy_logret_1w"] = np.log(current_signal_close / prior_signal_close)
    finite = np.isfinite(weekly[list(CORE_FEATURES) + ["spy_rv21"]]).all(axis=1)
    weekly["feature_complete"] = finite
    weekly["feature_spec_version"] = "round2-core-v1"
    weekly["available_at"] = weekly["signal_timestamp_et"]
    return weekly.reset_index(drop=True)


def build_weekly_development_targets(
    market_daily: pd.DataFrame,
    risk_free_daily: pd.DataFrame,
    decision_calendar: pd.DataFrame,
    *,
    lockbox_start_signal: pd.Timestamp,
) -> pd.DataFrame:
    market = market_daily.copy()
    market["session_date"] = pd.to_datetime(market["session_date"]).dt.normalize()
    market = market.sort_values("session_date", kind="mergesort").set_index(
        "session_date"
    )
    if not market.index.is_unique:
        raise DataQualityError("R2A market sessions are not unique")
    rf = risk_free_daily.copy()
    rf["session_date"] = pd.to_datetime(rf["session_date"]).dt.normalize()
    rf = rf.sort_values("session_date", kind="mergesort").set_index("session_date")
    if not rf.index.is_unique:
        raise DataQualityError("R2A RF sessions are not unique")
    if not market.index.equals(rf.index):
        raise DataQualityError("R2A market and RF sessions are not identical")
    sessions = market.index
    session_position = {value: index for index, value in enumerate(sessions)}
    open_price = pd.to_numeric(market["tr_open"], errors="coerce").to_numpy(float)
    close_price = pd.to_numeric(market["tr_close"], errors="coerce").to_numpy(float)
    rf_log = pd.to_numeric(rf["rf_log"], errors="coerce").to_numpy(float)
    rf_cumsum = np.concatenate([[0.0], np.cumsum(rf_log)])

    calendar = decision_calendar.copy()
    for column in (
        "signal_session",
        "execution_session",
        "next_1w_execution",
        "next_4w_execution",
    ):
        calendar[column] = pd.to_datetime(calendar[column]).dt.normalize()
    calendar = calendar.sort_values("signal_session", kind="mergesort").reset_index(
        drop=True
    )
    execution_timestamp = {
        pd.Timestamp(row.execution_session): row.execution_timestamp_et
        for row in calendar.itertuples(index=False)
    }
    records: list[dict[str, Any]] = []
    for row in calendar.itertuples(index=False):
        signal = pd.Timestamp(row.signal_session)
        e0 = pd.Timestamp(row.execution_session)
        e1 = pd.Timestamp(row.next_1w_execution)
        e4 = pd.Timestamp(row.next_4w_execution)
        withheld = signal >= lockbox_start_signal
        endpoints_available = all(value in session_position for value in (e0, e1, e4))
        t1_mature_before_lockbox = e1 <= lockbox_start_signal
        t3_mature_before_lockbox = e4 <= lockbox_start_signal
        record: dict[str, Any] = {
            "week_id": row.week_id,
            "signal_session": signal,
            "execution_session": e0,
            "next_1w_execution": e1,
            "next_4w_execution": e4,
            "t1_available_at": execution_timestamp.get(e1, pd.NaT),
            "t3_available_at": execution_timestamp.get(e4, pd.NaT),
            "target_available_at": execution_timestamp.get(e4, pd.NaT),
            "withheld_lockbox": withheld,
            "censored_t1": e0 not in session_position or e1 not in session_position,
            "censored_t3": not endpoints_available,
            "target_available": False,
            "t3_available": False,
            "fwd_excess_logret_1w": np.nan,
            "cash_wins_1w": np.nan,
            "fwd_worst_excess_4w": np.nan,
        }
        if endpoints_available and not withheld:
            i0, i1, i4 = (
                session_position[e0],
                session_position[e1],
                session_position[e4],
            )
            if not (i0 < i1 < i4):
                raise DataQualityError("target execution endpoints are not increasing")
            t1 = np.log(open_price[i1] / open_price[i0]) - (
                rf_cumsum[i1] - rf_cumsum[i0]
            )
            path_close = np.log(close_price[i0:i4] / open_price[i0]) - (
                rf_cumsum[i0 + 1 : i4 + 1] - rf_cumsum[i0]
            )
            terminal = np.log(open_price[i4] / open_price[i0]) - (
                rf_cumsum[i4] - rf_cumsum[i0]
            )
            t3 = float(min(0.0, float(np.min(path_close)), float(terminal)))
            if t1_mature_before_lockbox:
                record.update(
                    {
                        "target_available": True,
                        "fwd_excess_logret_1w": float(t1),
                        "cash_wins_1w": float(t1 < 0.0),
                    }
                )
            if t3_mature_before_lockbox:
                record.update(
                    {
                        "t3_available": True,
                        "fwd_worst_excess_4w": t3,
                    }
                )
        records.append(record)
    return pd.DataFrame(records)


def build_signal_diagnostics(
    features: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    bootstrap_repetitions: int,
    bootstrap_block_weeks: int,
    seed: int,
) -> pd.DataFrame:
    joined = features.merge(
        targets[
            [
                "signal_session",
                *TARGET_COLUMNS,
                "target_available",
                "t3_available",
                "withheld_lockbox",
            ]
        ],
        on="signal_session",
        how="inner",
        validate="one_to_one",
    )
    sample = joined.loc[
        joined["feature_complete"]
        & joined["target_available"]
        & joined["t3_available"]
        & ~joined["withheld_lockbox"]
    ].reset_index(drop=True)
    score_specs = {
        "F_SPY_RET21": -sample["spy_total_return_21d"],
        "F_SPY_RET126": -sample["spy_total_return_126d"],
        "F_SMA_GAP": -sample["sma50_over_sma200_minus_1"],
        "F_DRAWDOWN252": -sample["drawdown_from_252d_high"],
        "F_LOG_RV126": sample["log_spy_rv126"],
        "F_LOG_RV_RATIO": sample["log_rv21_over_rv126"],
        "F_DOWNSIDE_SHARE": sample["downside_variance_share_63d"],
        "F_NEG_SKEW63": -sample["return_skew_63d"],
        "F_KURT126": sample["return_excess_kurtosis_126d"],
        "SENT_RV21": np.log(sample["spy_rv21"]),
        "SENT_SMA_GAP": -sample["sma50_over_sma200_minus_1"],
        "SENT_DRAWDOWN252": -sample["drawdown_from_252d_high"],
        "SENT_RET21": -sample["spy_total_return_21d"],
    }
    rows: list[dict[str, Any]] = []
    t1 = sample["fwd_excess_logret_1w"].to_numpy(float)
    t2 = sample["cash_wins_1w"].to_numpy(float)
    t3 = sample["fwd_worst_excess_4w"].to_numpy(float)
    for offset, (signal_id, score_series) in enumerate(score_specs.items()):
        score = pd.to_numeric(score_series, errors="coerce").to_numpy(float)
        valid = np.isfinite(score) & np.isfinite(t1) & np.isfinite(t2) & np.isfinite(t3)
        x, y1, y2, y3 = score[valid], t1[valid], t2[valid], t3[valid]
        if len(x) < 100 or len(np.unique(y2)) != 2:
            raise DataQualityError(f"insufficient development diagnostics: {signal_id}")
        rank = pd.Series(x).rank(method="average", pct=True).to_numpy(float)
        quintile = np.minimum(5, np.floor(rank * 5).astype(int) + 1)
        q1, q5 = quintile == 1, quintile == 5
        spearman_t1 = _corr(rank, pd.Series(y1).rank(method="average").to_numpy(float))
        spearman_t3 = _corr(rank, pd.Series(y3).rank(method="average").to_numpy(float))
        ci_t1 = _fixed_rank_block_bootstrap_ci(
            rank,
            pd.Series(y1).rank(method="average").to_numpy(float),
            block=bootstrap_block_weeks,
            repetitions=bootstrap_repetitions,
            seed=seed + offset * 2,
        )
        ci_t3 = _fixed_rank_block_bootstrap_ci(
            rank,
            pd.Series(y3).rank(method="average").to_numpy(float),
            block=bootstrap_block_weeks,
            repetitions=bootstrap_repetitions,
            seed=seed + offset * 2 + 1,
        )
        rows.append(
            {
                "signal_id": signal_id,
                "sample": "pre_lockbox_feature_complete",
                "n": len(x),
                "spearman_t1": spearman_t1,
                "spearman_t1_ci05": ci_t1[0],
                "spearman_t1_ci95": ci_t1[1],
                "spearman_t3": spearman_t3,
                "spearman_t3_ci05": ci_t3[0],
                "spearman_t3_ci95": ci_t3[1],
                "pearson_t1": _corr(x, y1),
                "pearson_t3": _corr(x, y3),
                "roc_auc_t2": _roc_auc(y2, x),
                "average_precision_t2": _average_precision(y2, x),
                "cash_wins_q1": float(np.mean(y2[q1])),
                "cash_wins_q5": float(np.mean(y2[q5])),
                "mean_t1_q1": float(np.mean(y1[q1])),
                "mean_t1_q5": float(np.mean(y1[q5])),
                "mean_t3_q1": float(np.mean(y3[q1])),
                "mean_t3_q5": float(np.mean(y3[q5])),
                "bootstrap_method": "fixed_full_sample_rank_13w_moving_block",
                "bootstrap_repetitions": bootstrap_repetitions,
            }
        )
    return pd.DataFrame(rows).sort_values("signal_id", kind="mergesort").reset_index(
        drop=True
    )


def _validate_feature_target_contract(
    features: pd.DataFrame, targets: pd.DataFrame
) -> None:
    if len(features) != len(targets):
        raise DataQualityError("feature/target row counts differ")
    if not features["signal_session"].equals(targets["signal_session"]):
        raise DataQualityError("feature/target signal keys differ")
    lockbox = targets["withheld_lockbox"]
    if not lockbox.any():
        raise DataQualityError("lockbox firewall has no rows")
    if targets.loc[lockbox, list(TARGET_COLUMNS)].notna().any().any():
        raise DataQualityError("lockbox target value leaked into R2B")
    available = targets["target_available"]
    if targets.loc[
        available, ["fwd_excess_logret_1w", "cash_wins_1w"]
    ].isna().any().any():
        raise DataQualityError("available development T1/T2 is missing")
    available_t3 = targets["t3_available"]
    if targets.loc[available_t3, "fwd_worst_excess_4w"].isna().any():
        raise DataQualityError("available development T3 is missing")
    if (targets.loc[available_t3, "fwd_worst_excess_4w"] > 1e-15).any():
        raise DataQualityError("T3 must be non-positive")
    first = features.loc[features["feature_complete"], "signal_session"].iloc[0]
    if pd.Timestamp(first) != pd.Timestamp("1994-01-28"):
        raise DataQualityError("feature-complete boundary differs from folds manifest")


def _feature_coverage(features: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for column in (*CORE_FEATURES, "spy_rv21", "hmm_spy_logret_1w"):
        values = pd.to_numeric(features[column], errors="coerce")
        finite = np.isfinite(values)
        rows.append(
            {
                "field": column,
                "rows": len(values),
                "finite_rows": int(finite.sum()),
                "first_finite_signal": features.loc[finite, "signal_session"].min(),
                "last_finite_signal": features.loc[finite, "signal_session"].max(),
            }
        )
    rows.extend(
        [
            {
                "field": "development_targets",
                "rows": len(targets),
                "finite_rows": int(targets["target_available"].sum()),
                "first_finite_signal": targets.loc[
                    targets["target_available"], "signal_session"
                ].min(),
                "last_finite_signal": targets.loc[
                    targets["target_available"], "signal_session"
                ].max(),
            },
            {
                "field": "withheld_lockbox_targets",
                "rows": len(targets),
                "finite_rows": int(targets["withheld_lockbox"].sum()),
                "first_finite_signal": targets.loc[
                    targets["withheld_lockbox"], "signal_session"
                ].min(),
                "last_finite_signal": targets.loc[
                    targets["withheld_lockbox"], "signal_session"
                ].max(),
            },
        ]
    )
    return pd.DataFrame(rows)


def _fixed_rank_block_bootstrap_ci(
    x_rank: np.ndarray,
    y_rank: np.ndarray,
    *,
    block: int,
    repetitions: int,
    seed: int,
) -> tuple[float, float]:
    n = len(x_rank)
    starts_max = n - block + 1
    if starts_max <= 0:
        raise ValueError("bootstrap block exceeds sample")
    rng = np.random.default_rng(seed)
    statistics = np.empty(repetitions, dtype=float)
    blocks_needed = int(np.ceil(n / block))
    offsets = np.arange(block)
    for index in range(repetitions):
        starts = rng.integers(0, starts_max, size=blocks_needed)
        sample = (starts[:, None] + offsets).reshape(-1)[:n]
        statistics[index] = _corr(x_rank[sample], y_rank[sample])
    low, high = np.quantile(statistics[np.isfinite(statistics)], [0.05, 0.95])
    return float(low), float(high)


def _corr(left: np.ndarray, right: np.ndarray) -> float:
    if len(left) != len(right) or len(left) < 2:
        return float("nan")
    if np.std(left) == 0 or np.std(right) == 0:
        return float("nan")
    return float(np.corrcoef(left, right)[0, 1])


def _roc_auc(labels: np.ndarray, scores: np.ndarray) -> float:
    ranks = pd.Series(scores).rank(method="average").to_numpy(float)
    positive = labels == 1
    n_pos = int(positive.sum())
    n_neg = len(labels) - n_pos
    return float((ranks[positive].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def _average_precision(labels: np.ndarray, scores: np.ndarray) -> float:
    order = np.argsort(-scores, kind="mergesort")
    ordered = labels[order]
    positives = int(ordered.sum())
    if positives == 0:
        return float("nan")
    precision = np.cumsum(ordered) / np.arange(1, len(ordered) + 1)
    return float(np.sum(precision * ordered) / positives)


def _verify_preregistration(project_root: Path) -> dict[str, Any]:
    lock_path = project_root / "config" / "experiments" / "round2" / "PREREG_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    for key in (
        "program",
        "amendment_1",
        "r2b_design",
        "r2c_design",
        "machine_config",
        "fold_manifest",
    ):
        record = lock[key]
        path = project_root / record["path"]
        if sha256_file(path) != record["sha256"]:
            raise DataQualityError(f"Round 2 preregistration hash mismatch: {key}")
    config = tomllib.loads(
        (project_root / lock["machine_config"]["path"]).read_text(encoding="utf-8")
    )
    if not config["authorization"]["r2b_targets_and_features"]:
        raise DataQualityError("R2B target/feature construction is not authorized")
    if config["authorization"]["r2c_development_models"]:
        raise DataQualityError("R2C models must remain unauthorized during R2B")
    return lock


def _build_provenance(project_root: Path) -> dict[str, Any]:
    paths = (
        "src/momentum_reversal/pipelines/round2_signals.py",
        "src/momentum_reversal/pipelines/round2_protocol.py",
        "scripts/build_round2_r2b.py",
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "git_commit": commit,
        "workspace_dirty": bool(dirty),
        "code_file_sha256": {path: sha256_file(project_root / path) for path in paths},
        "dependency_versions": {
            package: importlib_metadata.version(package)
            for package in ("numpy", "pandas", "pyarrow")
        },
    }


def _file_record(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
