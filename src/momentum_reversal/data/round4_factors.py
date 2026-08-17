"""Round 4 factor-input normalization and causal weekly construction.

This module is deliberately outcome blind.  It may construct only values that
were observable at a weekly signal close; targets, model scores, NAVs, and
drawdown-event labels do not belong here.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.pipelines.round2_signals import build_weekly_features


OLD_ARM_COLUMNS: dict[str, str] = {
    "R4B__RV21": "r4_score_rv21",
    "R4B__RV126": "r4_score_rv126",
    "R4B__RV_RATIO": "r4_score_rv_ratio",
    "R4B__RET21": "r4_score_ret21",
    "R4B__RET126": "r4_score_ret126",
    "R4B__SMA_GAP": "r4_score_sma_gap",
    "R4B__DRAWDOWN252": "r4_score_drawdown252",
    "R4B__DOWNSIDE_VAR63": "r4_score_downside_var63",
    "R4B__SKEW63": "r4_score_skew63",
    "R4B__KURT126": "r4_score_kurt126",
}


@dataclass(frozen=True, slots=True)
class SourceBranch:
    frame: pd.DataFrame
    source_status: str
    source_note: str


def build_round4_core_scores(
    market_daily: pd.DataFrame, decision_calendar: pd.DataFrame
) -> pd.DataFrame:
    """Rebuild the ten legacy scores through the frozen R2B formulas."""

    weekly = build_weekly_features(market_daily, decision_calendar)
    weekly["r4_score_rv21"] = np.log(weekly["spy_rv21"])
    weekly["r4_score_rv126"] = weekly["log_spy_rv126"]
    weekly["r4_score_rv_ratio"] = weekly["log_rv21_over_rv126"]
    weekly["r4_score_ret21"] = -weekly["spy_total_return_21d"]
    weekly["r4_score_ret126"] = -weekly["spy_total_return_126d"]
    weekly["r4_score_sma_gap"] = -weekly["sma50_over_sma200_minus_1"]
    weekly["r4_score_drawdown252"] = -weekly["drawdown_from_252d_high"]
    weekly["r4_score_downside_var63"] = weekly[
        "downside_variance_share_63d"
    ]
    weekly["r4_score_skew63"] = -weekly["return_skew_63d"]
    weekly["r4_score_kurt126"] = weekly[
        "return_excess_kurtosis_126d"
    ]
    return weekly


def build_spy_volume_scores(market_daily: pd.DataFrame) -> pd.DataFrame:
    """Build the two frozen SPY dollar-volume inputs on XNYS sessions."""

    frame = _market_index(market_daily)
    raw_close = _finite_numeric(frame, "raw_close", positive=True)
    raw_volume = _finite_numeric(frame, "volume_raw", positive=True)
    tr_close = _finite_numeric(frame, "tr_close", positive=True)
    dollar_volume = raw_close * raw_volume
    log_return = np.log(tr_close / tr_close.shift(1))
    weighted_move = dollar_volume * log_return.abs()
    down_weighted_move = weighted_move.where(log_return < 0.0, 0.0)
    denominator = weighted_move.rolling(21, min_periods=21).sum()
    down_share = (
        down_weighted_move.rolling(21, min_periods=21).sum() / denominator
    )
    shock = np.log(
        dollar_volume.rolling(21, min_periods=21).mean()
        / dollar_volume.rolling(252, min_periods=252).median()
    )
    result = pd.DataFrame(
        {
            "session_date": frame.index,
            "down_move_dv_share21": down_share,
            "volume_shock21_252": shock,
        }
    )
    return result.reset_index(drop=True)


def parse_fred_csv(payload: bytes | str, expected: list[str]) -> pd.DataFrame:
    """Parse an official fredgraph CSV without filling missing observations."""

    text = payload.decode("utf-8-sig", errors="strict") if isinstance(payload, bytes) else payload
    source = pd.read_csv(io.StringIO(text), na_values=[".", ""])
    date_column = "observation_date"
    if date_column not in source:
        raise DataQualityError("FRED CSV omits observation_date")
    missing = [column for column in expected if column not in source]
    if missing:
        raise DataQualityError(f"FRED CSV omits expected series: {missing}")
    result = pd.DataFrame(
        {"observation_date": pd.to_datetime(source[date_column], errors="coerce")}
    )
    if result["observation_date"].isna().any():
        raise DataQualityError("FRED CSV contains invalid dates")
    result["observation_date"] = result["observation_date"].dt.normalize()
    for column in expected:
        result[column] = pd.to_numeric(source[column], errors="coerce")
        finite = result[column].dropna()
        if not np.isfinite(finite).all():
            raise DataQualityError(f"FRED series contains non-finite values: {column}")
    if result["observation_date"].duplicated().any():
        raise DataQualityError("FRED CSV contains duplicate dates")
    return result.sort_values("observation_date", kind="mergesort").reset_index(drop=True)


def lagged_fred_at_signals(
    source: pd.DataFrame,
    decision_calendar: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    *,
    value_columns: list[str],
    lag_sessions: int = 1,
    max_staleness_sessions: int = 5,
) -> pd.DataFrame:
    """As-of FRED observations after a conservative XNYS-session lag."""

    if lag_sessions < 1 or max_staleness_sessions < 0:
        raise ValueError("invalid lag/staleness")
    observation = source.copy()
    observation["observation_date"] = pd.to_datetime(
        observation["observation_date"]
    ).dt.normalize()
    calendar = decision_calendar[["week_id", "signal_session"]].copy()
    calendar["signal_session"] = pd.to_datetime(calendar["signal_session"]).dt.normalize()
    positions = {date: index for index, date in enumerate(sessions)}
    rows: list[dict[str, Any]] = []
    for record in calendar.itertuples(index=False):
        signal = pd.Timestamp(record.signal_session)
        row: dict[str, Any] = {
            "week_id": record.week_id,
            "signal_session": signal,
            "source_observation_date": pd.NaT,
            "staleness_sessions": np.nan,
        }
        for column in value_columns:
            row[column] = np.nan
        if signal not in positions or positions[signal] < lag_sessions:
            rows.append(row)
            continue
        cutoff = sessions[positions[signal] - lag_sessions]
        eligible = observation.loc[observation["observation_date"] <= cutoff]
        eligible = eligible.dropna(subset=value_columns, how="any")
        if not eligible.empty:
            latest = eligible.iloc[-1]
            date = pd.Timestamp(latest["observation_date"])
            insertion = int(sessions.searchsorted(date, side="left"))
            stale = positions[signal] - lag_sessions - insertion
            if date not in positions:
                stale = positions[signal] - lag_sessions - int(
                    sessions.searchsorted(date, side="right") - 1
                )
            if 0 <= stale <= max_staleness_sessions:
                row["source_observation_date"] = date
                row["staleness_sessions"] = stale
                for column in value_columns:
                    row[column] = float(latest[column])
        rows.append(row)
    return pd.DataFrame(rows)


def normalize_rsp_tiingo(payload: bytes, snapshot_id: str) -> pd.DataFrame:
    """Normalize the frozen Tiingo RSP JSON to its adjusted close."""

    import json

    from momentum_reversal.data.provider import AssetRef
    from momentum_reversal.data.tiingo_provider import normalize_tiingo_response

    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DataQualityError("RSP Tiingo payload is invalid JSON") from error
    normalized = normalize_tiingo_response(parsed, AssetRef("RSP", "RSP")).reset_index()
    result = normalized[["date", "tr_close"]].rename(columns={"date": "session_date"})
    result["session_date"] = pd.to_datetime(result["session_date"]).dt.normalize()
    result["source_snapshot_id"] = snapshot_id
    if result["session_date"].duplicated().any():
        raise DataQualityError("RSP Tiingo payload contains duplicate dates")
    return result.sort_values("session_date", kind="mergesort").reset_index(drop=True)


def build_rsp_spy_score(
    rsp_daily: pd.DataFrame, market_daily: pd.DataFrame
) -> pd.DataFrame:
    spy = market_daily[["session_date", "tr_close"]].copy()
    spy["session_date"] = pd.to_datetime(spy["session_date"]).dt.normalize()
    joined = spy.merge(
        rsp_daily[["session_date", "tr_close"]],
        on="session_date",
        how="left",
        suffixes=("_spy", "_rsp"),
        validate="one_to_one",
    ).sort_values("session_date", kind="mergesort")
    ratio = np.log(
        pd.to_numeric(joined["tr_close_rsp"], errors="coerce")
        / pd.to_numeric(joined["tr_close_spy"], errors="coerce")
    )
    joined["rsp_spy_score63"] = -(ratio - ratio.shift(63))
    return joined[["session_date", "rsp_spy_score63"]].reset_index(drop=True)


def weekly_long_table(
    *,
    calendar: pd.DataFrame,
    arm_values: dict[str, pd.Series],
    available_at: pd.Series,
    source_status: dict[str, tuple[str, str]],
    cutoff: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create the feature-input and availability long tables."""

    cal = calendar.copy()
    cal["signal_session"] = pd.to_datetime(cal["signal_session"]).dt.normalize()
    cal = cal.loc[cal["signal_session"] <= cutoff].reset_index(drop=True)
    records: list[pd.DataFrame] = []
    for arm_id, values in arm_values.items():
        status, note = source_status[arm_id]
        value = pd.to_numeric(values.reindex(cal.index), errors="coerce")
        part = pd.DataFrame(
            {
                "week_id": cal["week_id"],
                "signal_session": cal["signal_session"],
                "signal_timestamp_et": cal["signal_timestamp_et"],
                "arm_id": arm_id,
                "defense_score": value.to_numpy(float),
                "available_at": available_at.reindex(cal.index).to_numpy(),
                "source_status": status,
                "source_note": note,
            }
        )
        part["value_available"] = np.isfinite(part["defense_score"])
        part["missing_reason"] = np.where(
            part["value_available"],
            "",
            np.where(status == "available", "warmup_or_source_missing", status),
        )
        records.append(part)
    features = pd.concat(records, ignore_index=True)
    availability = features[
        [
            "week_id",
            "signal_session",
            "arm_id",
            "value_available",
            "available_at",
            "source_status",
            "missing_reason",
        ]
    ].copy()
    return features, availability


def eligibility_from_weekly(
    features: pd.DataFrame,
    *,
    minimum_weeks: int,
    minimum_years: int,
    max_missing_fraction: float,
    max_consecutive_missing: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for arm_id, group in features.groupby("arm_id", sort=True):
        group = group.sort_values("signal_session", kind="mergesort").reset_index(drop=True)
        source_status = str(group["source_status"].iloc[0])
        valid_positions = np.flatnonzero(group["value_available"].to_numpy(bool))
        if len(valid_positions):
            start, end = int(valid_positions[0]), int(valid_positions[-1])
            span = group.iloc[start : end + 1]
            missing = ~span["value_available"].to_numpy(bool)
            missing_fraction = float(missing.mean())
            max_gap = _max_true_run(missing)
            valid = span.loc[span["value_available"]]
            valid_years = int(valid["signal_session"].dt.year.nunique())
            valid_weeks = int(len(valid))
            first = valid["signal_session"].min()
            last = valid["signal_session"].max()
        else:
            missing_fraction = 1.0
            max_gap = len(group)
            valid_years = 0
            valid_weeks = 0
            first = pd.NaT
            last = pd.NaT
        data_gate = (
            source_status == "available"
            and missing_fraction <= max_missing_fraction
            and max_gap <= max_consecutive_missing
        )
        reference_gate = data_gate and valid_weeks >= minimum_weeks and valid_years >= minimum_years
        status = (
            "reference_eligible"
            if reference_gate
            else "descriptive_only"
            if data_gate
            else "invalid_data"
        )
        rows.append(
            {
                "arm_id": arm_id,
                "eligibility_status": status,
                "source_status": source_status,
                "valid_weeks": valid_weeks,
                "valid_years": valid_years,
                "first_valid_signal": first,
                "last_valid_signal": last,
                "missing_fraction_eligible_span": missing_fraction,
                "max_consecutive_missing_weeks": max_gap,
                "data_gate_pass": data_gate,
                "reference_gate_pass": reference_gate,
            }
        )
    return pd.DataFrame(rows)


def _market_index(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["session_date"] = pd.to_datetime(result["session_date"]).dt.normalize()
    result = result.sort_values("session_date", kind="mergesort").set_index("session_date")
    if not result.index.is_unique:
        raise DataQualityError("market_daily session_date is not unique")
    return result


def _finite_numeric(frame: pd.DataFrame, column: str, *, positive: bool) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce").astype(float)
    invalid = ~np.isfinite(values)
    if positive:
        invalid |= values <= 0.0
    if invalid.any():
        raise DataQualityError(f"invalid market field: {column}")
    return values


def _max_true_run(values: np.ndarray) -> int:
    best = current = 0
    for value in values:
        current = current + 1 if bool(value) else 0
        best = max(best, current)
    return best
