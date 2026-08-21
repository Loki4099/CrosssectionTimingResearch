"""Causal scheduled materialisation of the registered market-data factors.

The public function in this module deliberately consumes the frozen daily
tables rather than a provider-specific object.  Every lag is an exact position
on the supplied exchange calendar, every rolling window is backward looking,
and an unavailable value remains unavailable (it is never replaced with zero).

``raw_value`` preserves the economically natural measurement.  ``score`` is
oriented so that a larger value is the preferred side of the cross section.
In particular, short-term return, maximum daily return, and the Frazzini-
Pedersen beta estimate are negated (with the documented beta shrinkage) when
forming ``score``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from momentum_reversal.data.membership import PITMembership


FACTOR_IDS: tuple[str, ...] = (
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
)

VOLUME_FACTOR_IDS = frozenset(
    {"XS018_AMIHUD_252", "XS020_VOLUME_SHOCK_50D"}
)

OUTPUT_COLUMNS: tuple[str, ...] = (
    "signal_date",
    "sid",
    "factor_id",
    "raw_value",
    "score",
    "eligible",
    "missing_reason",
)

_GENERIC_MISSING_REASON = "insufficient_history_or_missing_input"
_VOLUME_QA_REASON = "volume_qa_not_passed"


def materialize_cross_sectional_market_factors(
    prices: pd.DataFrame,
    benchmark_daily: pd.DataFrame,
    calendar: pd.DataFrame,
    membership: PITMembership | pd.DataFrame | Any,
    signal_dates: Iterable[object],
    *,
    factor_ids: Sequence[str] | None = None,
    volume_qa_passed: bool = False,
    allowed_signal_frequencies: Sequence[str] = ("monthly",),
) -> pd.DataFrame:
    """Return a long point-in-time factor table for registered signal dates.

    Parameters
    ----------
    prices:
        Frozen long table with ``date, sid, tr_close, raw_close, volume,
        stock_splits``.  A ``(date, sid)`` MultiIndex is also accepted.
    benchmark_daily:
        Daily benchmark table with ``date`` and ``benchmark_tr_close`` (or the
        unambiguous alias ``tr_close``).
    calendar:
        Authoritative session table containing ``session_date``,
        ``month_last_session`` and ``week_last_session``.
    membership:
        :class:`PITMembership`, another object exposing ``members_on(date)``,
        or a membership interval/snapshot DataFrame.
    signal_dates:
        Monthly exchange sessions at which values are observable after close.
    factor_ids:
        Optional registered subset.  Output ordering is canonical regardless
        of caller ordering.
    volume_qa_passed:
        The two volume-dependent factors are materialised only after upstream
        volume QA has explicitly passed.  Otherwise their rows are retained as
        ineligible with ``volume_qa_not_passed``.

    Notes
    -----
    ``XS004_HIGH_52W`` uses ``raw_close * cumulative_split_factor``.  This is
    a causal, split-adjusted, *ex-dividend* price series: a split known at time
    ``t`` joins the pre/post-split price scales without using future splits or
    total-return dividend adjustments.

    ``XS008_SAME_MONTH_5Y`` predicts the coming holding month.  Thus a signal
    formed at month ``m`` uses completed excess returns from ``m-11``,
    ``m-23``, ..., ``m-59``: those are the same calendar month as ``m+1`` at
    target-return lags 12/24/36/48/60.  At least three observations are
    required.
    ``XS013_LOW_BETA_FP`` follows the Frazzini-Pedersen construction using a
    five-year (1,260-session) rolling correlation of overlapping three-session
    log returns (minimum 750 pairs), a 252-session daily-log-return volatility
    ratio, and score ``-(0.6 * beta + 0.4)``.
    """

    if not isinstance(volume_qa_passed, (bool, np.bool_)):
        raise TypeError("volume_qa_passed must be boolean")
    requested = _factor_selection(factor_ids)
    signals = _normalise_signal_dates(signal_dates)
    calendar_frame = _normalise_calendar(calendar)
    calendar_sessions = pd.DatetimeIndex(calendar_frame["session_date"])

    missing_signals = signals.difference(calendar_sessions)
    if len(missing_signals):
        raise ValueError(
            "signal dates are not exchange sessions: "
            f"{missing_signals[:5].tolist()}"
        )
    allowed = tuple(dict.fromkeys(str(value) for value in allowed_signal_frequencies))
    if not allowed or any(value not in {"weekly", "monthly"} for value in allowed):
        raise ValueError("allowed_signal_frequencies must contain weekly/monthly")
    indexed_calendar = calendar_frame.set_index("session_date")
    permitted = pd.Series(False, index=signals)
    if "monthly" in allowed:
        permitted |= indexed_calendar["month_last_session"].reindex(signals).astype(bool)
    if "weekly" in allowed:
        permitted |= indexed_calendar["week_last_session"].reindex(signals).astype(bool)
    invalid_signals = signals[~permitted.to_numpy(dtype=bool)]
    if len(invalid_signals):
        raise ValueError(
            "signal_dates are outside the authorized weekly/monthly schedule: "
            f"{invalid_signals[:5].tolist()}"
        )

    universe = _normalise_membership(membership)
    members_by_date = _members_for_signals(universe, signals)
    member_sids = tuple(
        sorted({sid for members in members_by_date.values() for sid in members})
    )

    price_frame = _normalise_prices(prices, calendar_sessions)
    benchmark = _normalise_benchmark(benchmark_daily, calendar_sessions)

    # Truncation is stronger than merely relying on trailing operations: no
    # value dated after the final requested signal enters any intermediate.
    sessions = calendar_sessions[calendar_sessions <= signals.max()]
    price_frame = price_frame.loc[price_frame["date"] <= signals.max()]
    benchmark = benchmark.loc[benchmark.index <= signals.max()]

    price_sids = tuple(sorted(price_frame["sid"].unique().tolist()))
    all_sids = tuple(sorted(set(price_sids).union(member_sids)))
    matrices: dict[str, pd.DataFrame] = {}

    def field(name: str) -> pd.DataFrame:
        if name not in matrices:
            matrices[name] = _wide(price_frame, name, sessions, all_sids)
        return matrices[name]

    tr_close = field("tr_close")
    benchmark_close = benchmark.reindex(sessions)

    panels: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}

    if "XS001_MOM_255_0" in requested:
        raw = _simple_return(tr_close, tr_close.shift(255))
        complete = tr_close.gt(0).rolling(256, min_periods=256).sum().eq(256)
        raw = raw.where(complete)
        selected = raw.reindex(signals)
        panels["XS001_MOM_255_0"] = (selected, selected)

    if "XS002_MOM_12_1" in requested:
        raw = _simple_return(tr_close.shift(21), tr_close.shift(252))
        selected = raw.reindex(signals)
        panels["XS002_MOM_12_1"] = (selected, selected)

    if "XS003_MOM_12_7" in requested:
        raw = _simple_return(tr_close.shift(126), tr_close.shift(252))
        selected = raw.reindex(signals)
        panels["XS003_MOM_12_7"] = (selected, selected)

    if "XS004_HIGH_52W" in requested:
        raw_close = field("raw_close")
        split_events = field("stock_splits")
        # Zero is the provider's explicit "no split" event.  A missing event
        # alongside a valid close is unknown data, not zero, and invalidates
        # the subsequent split basis.  Skeleton rows with no close may safely
        # contribute the neutral multiplier because no price is observed.
        split_known = split_events.notna() | raw_close.isna()
        event_multiplier = split_events.where(split_events.gt(0), 1.0).where(
            split_known
        )
        causal_split_close = raw_close * event_multiplier.cumprod(skipna=False)
        causal_split_close = causal_split_close.where(causal_split_close.gt(0))
        high = causal_split_close.rolling(252, min_periods=252).max()
        raw = causal_split_close.div(high).where(high.gt(0))
        selected = raw.reindex(signals)
        panels["XS004_HIGH_52W"] = (selected, selected)
        del causal_split_close, event_multiplier, high, raw, split_events
        matrices.pop("stock_splits", None)

    if "XS007_ST_REV_21" in requested:
        raw = _simple_return(tr_close, tr_close.shift(21))
        selected = raw.reindex(signals)
        panels["XS007_ST_REV_21"] = (selected, -selected)

    if "XS008_SAME_MONTH_5Y" in requested:
        month_dates = pd.DatetimeIndex(
            calendar_frame.loc[
                calendar_frame["month_last_session"]
                & (calendar_frame["session_date"] <= signals.max()),
                "session_date",
            ]
        )
        stock_monthly = tr_close.reindex(month_dates)
        market_monthly = benchmark_close.reindex(month_dates)
        stock_monthly_return = _simple_return(
            stock_monthly, stock_monthly.shift(1)
        )
        market_monthly_return = _series_simple_return(
            market_monthly, market_monthly.shift(1)
        )
        excess = stock_monthly_return.sub(market_monthly_return, axis=0)
        # The return row is labelled by the month in which the return was
        # earned.  At an end-of-month signal m, the target is m+1, so target
        # lags 12/24/... map to completed rows m-11/m-23/....
        observations = [excess.shift(lag) for lag in (11, 23, 35, 47, 59)]
        count = sum(value.notna().astype(np.int8) for value in observations)
        total = sum(value.fillna(0.0) for value in observations)
        raw = total.div(count.where(count.gt(0))).where(count.ge(3))
        if allowed == ("monthly",):
            selected = raw.reindex(signals)
        else:
            # Same-month seasonality is a target-calendar-month characteristic,
            # not a generic rolling weekly feature.  Every decision whose next
            # execution occurs in target month M uses the score formed at the
            # final exchange session of M-1.  This exactly preserves the legacy
            # month-end values while carrying one target-month score across its
            # weekly decisions.
            next_session_by_date = pd.Series(
                calendar_sessions[1:], index=calendar_sessions[:-1]
            )
            next_sessions = pd.to_datetime(
                next_session_by_date.reindex(signals), errors="coerce"
            ).dt.normalize()
            month_end_by_period = pd.Series(
                month_dates,
                index=month_dates.to_period("M"),
            )
            target_periods = []
            for signal, target in zip(signals, next_sessions, strict=True):
                if pd.isna(target):
                    if not bool(indexed_calendar.loc[signal, "month_last_session"]):
                        raise ValueError("non-month-end signal date lacks a next exchange session")
                    target_periods.append(signal.to_period("M") + 1)
                else:
                    target_periods.append(pd.Timestamp(target).to_period("M"))
            formation_dates = pd.DatetimeIndex(
                [
                    month_end_by_period.get(target_period - 1, pd.NaT)
                    for target_period in target_periods
                ]
            )
            selected = raw.reindex(formation_dates)
            selected.index = signals
        panels["XS008_SAME_MONTH_5Y"] = (selected, selected)
        del excess, observations, count, total, raw

    if "XS013_LOW_BETA_FP" in requested:
        positive_close = tr_close.where(tr_close.gt(0))
        positive_market = benchmark_close.where(benchmark_close.gt(0))
        stock_log = np.log(positive_close)
        market_log = np.log(positive_market)
        stock_three_day = stock_log - stock_log.shift(3)
        market_three_day = market_log - market_log.shift(3)
        correlation = stock_three_day.rolling(
            1260, min_periods=750
        ).corr(market_three_day)
        stock_volatility = stock_log.diff().rolling(
            252, min_periods=252
        ).std(ddof=1)
        market_volatility = market_log.diff().rolling(
            252, min_periods=252
        ).std(ddof=1)
        raw = correlation.mul(stock_volatility).div(
            market_volatility.where(market_volatility.gt(0)), axis=0
        )
        score = -(0.6 * raw + 0.4)
        panels["XS013_LOW_BETA_FP"] = (
            raw.reindex(signals), score.reindex(signals)
        )
        del (
            positive_close,
            stock_log,
            stock_three_day,
            correlation,
            stock_volatility,
            raw,
            score,
        )

    if "XS015_MAX_21" in requested or "XS018_AMIHUD_252" in requested:
        daily_return = _simple_return(tr_close, tr_close.shift(1))
    else:
        daily_return = pd.DataFrame(index=sessions, columns=all_sids, dtype=float)

    if "XS015_MAX_21" in requested:
        raw = daily_return.rolling(21, min_periods=21).max()
        selected = raw.reindex(signals)
        panels["XS015_MAX_21"] = (selected, -selected)

    if "XS018_AMIHUD_252" in requested and volume_qa_passed:
        raw_close = field("raw_close")
        volume = field("volume")
        dollar_volume = raw_close.mul(volume).where(
            raw_close.gt(0) & volume.gt(0)
        )
        daily_illiquidity = daily_return.abs().div(dollar_volume)
        raw = daily_illiquidity.rolling(252, min_periods=252).mean()
        selected = raw.reindex(signals)
        panels["XS018_AMIHUD_252"] = (selected, selected)
        del daily_illiquidity, dollar_volume, raw

    if "XS019_PRICE_DELAY_52W" in requested:
        week_dates = pd.DatetimeIndex(
            calendar_frame.loc[
                calendar_frame["week_last_session"]
                & (calendar_frame["session_date"] <= signals.max()),
                "session_date",
            ]
        )
        raw = _price_delay_panel(
            tr_close=tr_close,
            benchmark_close=benchmark_close,
            week_dates=week_dates,
            signal_dates=signals,
        )
        panels["XS019_PRICE_DELAY_52W"] = (raw, raw)

    if "XS020_VOLUME_SHOCK_50D" in requested and volume_qa_passed:
        volume = field("volume").where(lambda value: value.ge(0))
        split_events = field("stock_splits")
        raw_close = field("raw_close")
        split_known = split_events.notna() | raw_close.isna()
        event_multiplier = split_events.where(split_events.gt(0), 1.0).where(
            split_known
        )
        # A 2-for-1 split roughly doubles raw share volume.  Dividing by the
        # causal cumulative split basis keeps the pre/post observations on one
        # share scale without using a future split.
        causal_volume = volume.div(event_multiplier.cumprod(skipna=False))
        raw = causal_volume.rolling(50, min_periods=50).rank(
            method="average", pct=True
        )
        selected = raw.reindex(signals)
        panels["XS020_VOLUME_SHOCK_50D"] = (selected, selected)

    return _long_output(
        panels=panels,
        requested=requested,
        signals=signals,
        members_by_date=members_by_date,
        volume_qa_passed=volume_qa_passed,
    )


# Descriptive aliases make the API easy to discover without changing the
# package-level __init__ in this self-contained module addition.
compute_cross_sectional_market_factors = materialize_cross_sectional_market_factors
compute_cross_sectional_market_factor_panel = (
    materialize_cross_sectional_market_factors
)
compute_market_factor_panel = materialize_cross_sectional_market_factors
materialize_market_factors = materialize_cross_sectional_market_factors


def _factor_selection(factor_ids: Sequence[str] | None) -> tuple[str, ...]:
    if factor_ids is None:
        return FACTOR_IDS
    requested = (
        (factor_ids,)
        if isinstance(factor_ids, str)
        else tuple(str(value) for value in factor_ids)
    )
    if not requested:
        raise ValueError("factor_ids cannot be empty")
    if len(set(requested)) != len(requested):
        raise ValueError("factor_ids cannot contain duplicates")
    unknown = sorted(set(requested).difference(FACTOR_IDS))
    if unknown:
        raise ValueError(f"unknown factor_ids: {unknown}")
    requested_set = set(requested)
    return tuple(value for value in FACTOR_IDS if value in requested_set)


def _normalise_signal_dates(values: Iterable[object]) -> pd.DatetimeIndex:
    raw = list(values)
    parsed = pd.to_datetime(raw, errors="coerce")
    dates = pd.DatetimeIndex(parsed)
    if dates.tz is not None:
        raise ValueError("signal_dates must be timezone-naive")
    dates = dates.normalize()
    if dates.empty:
        raise ValueError("signal_dates cannot be empty")
    if dates.hasnans:
        raise ValueError("signal_dates cannot contain invalid dates")
    if dates.has_duplicates:
        raise ValueError("signal_dates cannot contain duplicates")
    return dates.sort_values()


def _normalise_calendar(calendar: pd.DataFrame) -> pd.DataFrame:
    required = {"session_date", "month_last_session", "week_last_session"}
    missing = required.difference(calendar.columns)
    if missing:
        raise ValueError(f"calendar missing columns: {sorted(missing)}")
    frame = calendar.loc[:, sorted(required)].copy()
    frame["session_date"] = _date_column(
        frame["session_date"], "calendar session_date"
    )
    if frame["session_date"].duplicated().any():
        raise ValueError("calendar session_date must be unique")
    if not frame["session_date"].is_monotonic_increasing:
        raise ValueError("calendar sessions must be strictly increasing")
    for column in ("month_last_session", "week_last_session"):
        if frame[column].isna().any():
            raise ValueError(f"calendar {column} cannot be missing")
        try:
            frame[column] = frame[column].astype("boolean").astype(bool)
        except (TypeError, ValueError) as error:
            raise ValueError(f"calendar {column} must be boolean") from error
    if frame.empty:
        raise ValueError("calendar cannot be empty")
    return frame.loc[
        :, ["session_date", "month_last_session", "week_last_session"]
    ]


def _normalise_prices(
    prices: pd.DataFrame, calendar_sessions: pd.DatetimeIndex
) -> pd.DataFrame:
    required = {
        "date",
        "sid",
        "tr_close",
        "raw_close",
        "volume",
        "stock_splits",
    }
    missing_key_columns = {"date", "sid"}.difference(prices.columns)
    keys_are_index = (
        isinstance(prices.index, pd.MultiIndex)
        and {"date", "sid"}.issubset(prices.index.names)
    )
    if missing_key_columns == {"date", "sid"} and keys_are_index:
        frame = prices.reset_index()
    else:
        frame = prices.copy()
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"prices missing columns: {sorted(missing)}")
    frame = frame.loc[:, [
        "date", "sid", "tr_close", "raw_close", "volume", "stock_splits"
    ]].copy()
    frame["date"] = _date_column(frame["date"], "price date")
    frame["sid"] = _sid_column(frame["sid"], "price sid")
    if frame.duplicated(["date", "sid"]).any():
        raise ValueError("prices must be unique on (date, sid)")
    non_sessions = pd.DatetimeIndex(frame["date"].unique()).difference(
        calendar_sessions
    )
    if len(non_sessions):
        raise ValueError(
            "price dates are not present in calendar: "
            f"{non_sessions[:5].tolist()}"
        )
    for column in ("tr_close", "raw_close", "volume", "stock_splits"):
        frame[column] = _numeric_column(frame[column], f"prices {column}")
    negative_splits = frame["stock_splits"].dropna().lt(0)
    if negative_splits.any():
        raise ValueError("stock_splits cannot be negative")
    return frame.sort_values(["date", "sid"]).reset_index(drop=True)


def _normalise_benchmark(
    benchmark_daily: pd.DataFrame, calendar_sessions: pd.DatetimeIndex
) -> pd.Series:
    frame = benchmark_daily.copy()
    if "date" not in frame.columns and frame.index.name == "date":
        frame = frame.reset_index()
    if "date" not in frame.columns:
        raise ValueError("benchmark_daily missing column: date")
    close_candidates = [
        column
        for column in ("benchmark_tr_close", "tr_close")
        if column in frame.columns
    ]
    if len(close_candidates) != 1:
        raise ValueError(
            "benchmark_daily must contain exactly one of benchmark_tr_close "
            "or tr_close"
        )
    close_column = close_candidates[0]
    frame = frame.loc[:, ["date", close_column]].copy()
    frame["date"] = _date_column(frame["date"], "benchmark date")
    if frame["date"].duplicated().any():
        raise ValueError("benchmark_daily dates must be unique")
    non_sessions = pd.DatetimeIndex(frame["date"].unique()).difference(
        calendar_sessions
    )
    if len(non_sessions):
        raise ValueError(
            "benchmark dates are not present in calendar: "
            f"{non_sessions[:5].tolist()}"
        )
    frame[close_column] = _numeric_column(
        frame[close_column], "benchmark close"
    )
    result = frame.set_index("date")[close_column].sort_index()
    result.name = "benchmark_tr_close"
    return result


def _normalise_membership(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        if {"sid", "effective_from", "effective_to"}.issubset(value.columns):
            return PITMembership.from_intervals(value)
        if {"date", "sid"}.issubset(value.columns):
            return PITMembership.from_snapshots(value)
        raise ValueError(
            "membership DataFrame must contain interval or snapshot columns"
        )
    if not hasattr(value, "members_on") or not callable(value.members_on):
        raise TypeError("membership must expose members_on(date)")
    return value


def _members_for_signals(
    membership: Any, signals: pd.DatetimeIndex
) -> Mapping[pd.Timestamp, tuple[str, ...]]:
    result: dict[pd.Timestamp, tuple[str, ...]] = {}
    for date in signals:
        raw_members = list(membership.members_on(date))
        members = tuple(sorted({_normalise_sid(value) for value in raw_members}))
        if not members:
            raise ValueError(f"membership is empty on signal date {date.date()}")
        result[pd.Timestamp(date)] = members
    return result


def _wide(
    frame: pd.DataFrame,
    value_column: str,
    sessions: pd.DatetimeIndex,
    sids: Sequence[str],
) -> pd.DataFrame:
    panel = frame.pivot(index="date", columns="sid", values=value_column)
    panel = panel.reindex(index=sessions, columns=sids)
    panel.index.name = "date"
    panel.columns.name = "sid"
    return panel.astype(float)


def _simple_return(
    numerator: pd.DataFrame, denominator: pd.DataFrame
) -> pd.DataFrame:
    valid = numerator.gt(0) & denominator.gt(0)
    return numerator.div(denominator).sub(1.0).where(valid)


def _series_simple_return(
    numerator: pd.Series, denominator: pd.Series
) -> pd.Series:
    valid = numerator.gt(0) & denominator.gt(0)
    return numerator.div(denominator).sub(1.0).where(valid)


def _price_delay_panel(
    *,
    tr_close: pd.DataFrame,
    benchmark_close: pd.Series,
    week_dates: pd.DatetimeIndex,
    signal_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Compute Hou-Moskowitz D1 using completed exchange weeks only."""

    stock_weekly = _simple_return(
        tr_close.reindex(week_dates), tr_close.reindex(week_dates).shift(1)
    )
    market_weekly = _series_simple_return(
        benchmark_close.reindex(week_dates),
        benchmark_close.reindex(week_dates).shift(1),
    )
    design = pd.concat(
        [market_weekly.shift(lag).rename(f"market_lag_{lag}") for lag in range(5)],
        axis=1,
    ).dropna()

    output = pd.DataFrame(
        np.nan,
        index=signal_dates,
        columns=tr_close.columns,
        dtype=float,
    )
    for signal_date in signal_dates:
        x_window = design.loc[design.index <= signal_date].tail(52)
        if len(x_window) < 40:
            continue
        y_window = stock_weekly.reindex(x_window.index)
        output.loc[signal_date] = _delay_for_window(
            x_window.to_numpy(dtype=float), y_window.to_numpy(dtype=float)
        )
    output.index.name = "signal_date"
    output.columns.name = "sid"
    return output


def _delay_for_window(market_lags: np.ndarray, stock_returns: np.ndarray) -> np.ndarray:
    result = np.full(stock_returns.shape[1], np.nan, dtype=float)
    complete = np.isfinite(stock_returns)
    full_columns = np.flatnonzero(complete.all(axis=0))
    if len(full_columns):
        result[full_columns] = _delay_ols(
            market_lags, stock_returns[:, full_columns]
        )

    partial_columns = np.flatnonzero(~complete.all(axis=0))
    for column in partial_columns:
        valid = complete[:, column]
        if valid.sum() < 40:
            continue
        result[column] = _delay_ols(
            market_lags[valid], stock_returns[valid, column : column + 1]
        )[0]
    return result


def _delay_ols(market_lags: np.ndarray, stock_returns: np.ndarray) -> np.ndarray:
    unrestricted = np.column_stack(
        [np.ones(len(market_lags), dtype=float), market_lags]
    )
    restricted = unrestricted[:, :2]
    if (
        len(market_lags) < 40
        or np.linalg.matrix_rank(unrestricted) < unrestricted.shape[1]
        or np.linalg.matrix_rank(restricted) < restricted.shape[1]
    ):
        return np.full(stock_returns.shape[1], np.nan, dtype=float)

    centered = stock_returns - stock_returns.mean(axis=0, keepdims=True)
    total_sum_squares = np.square(centered).sum(axis=0)
    valid_variance = total_sum_squares > np.finfo(float).eps

    beta_restricted = np.linalg.lstsq(
        restricted, stock_returns, rcond=None
    )[0]
    beta_unrestricted = np.linalg.lstsq(
        unrestricted, stock_returns, rcond=None
    )[0]
    residual_restricted = stock_returns - restricted @ beta_restricted
    residual_unrestricted = stock_returns - unrestricted @ beta_unrestricted
    r2_restricted = 1.0 - np.square(residual_restricted).sum(axis=0) / np.where(
        valid_variance, total_sum_squares, np.nan
    )
    r2_unrestricted = 1.0 - np.square(residual_unrestricted).sum(axis=0) / np.where(
        valid_variance, total_sum_squares, np.nan
    )
    # The unrestricted model nests the restricted model.  Clipping only removes
    # floating-point excursions outside the theoretical R-squared bounds.
    r2_restricted = np.clip(r2_restricted, 0.0, 1.0)
    r2_unrestricted = np.clip(r2_unrestricted, 0.0, 1.0)
    delay = 1.0 - r2_restricted / np.where(
        r2_unrestricted > np.finfo(float).eps, r2_unrestricted, np.nan
    )
    delay = np.clip(delay, 0.0, 1.0)
    delay[~valid_variance] = np.nan
    return delay


def _long_output(
    *,
    panels: Mapping[str, tuple[pd.DataFrame, pd.DataFrame]],
    requested: Sequence[str],
    signals: pd.DatetimeIndex,
    members_by_date: Mapping[pd.Timestamp, tuple[str, ...]],
    volume_qa_passed: bool,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for factor_id in requested:
        dates: list[pd.Timestamp] = []
        sids: list[str] = []
        raw_values: list[float] = []
        scores: list[float] = []
        blocked = factor_id in VOLUME_FACTOR_IDS and not volume_qa_passed
        raw_panel, score_panel = panels.get(
            factor_id,
            (
                pd.DataFrame(dtype=float),
                pd.DataFrame(dtype=float),
            ),
        )
        for signal_date in signals:
            members = members_by_date[pd.Timestamp(signal_date)]
            dates.extend([pd.Timestamp(signal_date)] * len(members))
            sids.extend(members)
            if blocked:
                raw_values.extend([np.nan] * len(members))
                scores.extend([np.nan] * len(members))
                continue
            raw_row = raw_panel.reindex(
                index=[signal_date], columns=members
            ).iloc[0]
            score_row = score_panel.reindex(
                index=[signal_date], columns=members
            ).iloc[0]
            raw_values.extend(raw_row.to_numpy(dtype=float).tolist())
            scores.extend(score_row.to_numpy(dtype=float).tolist())

        raw_array = np.asarray(raw_values, dtype=float)
        score_array = np.asarray(scores, dtype=float)
        finite = np.isfinite(raw_array) & np.isfinite(score_array)
        raw_array[~np.isfinite(raw_array)] = np.nan
        score_array[~np.isfinite(score_array)] = np.nan
        missing_reason = np.full(len(dates), pd.NA, dtype=object)
        missing_reason[~finite] = (
            _VOLUME_QA_REASON if blocked else _GENERIC_MISSING_REASON
        )
        frames.append(
            pd.DataFrame(
                {
                    "signal_date": dates,
                    "sid": sids,
                    "factor_id": factor_id,
                    "raw_value": raw_array,
                    "score": score_array,
                    "eligible": finite,
                    "missing_reason": pd.array(missing_reason, dtype="string"),
                }
            )
        )

    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values(
        ["signal_date", "sid", "factor_id"], kind="mergesort"
    ).reset_index(drop=True)
    if result.duplicated(["signal_date", "sid", "factor_id"]).any():
        raise AssertionError("factor output key is not unique")
    return result.loc[:, OUTPUT_COLUMNS]


def _date_column(values: pd.Series, label: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    if parsed.isna().any():
        raise ValueError(f"{label} cannot contain blank or invalid dates")
    if getattr(parsed.dt, "tz", None) is not None:
        raise ValueError(f"{label} must be timezone-naive")
    return parsed.dt.normalize()


def _sid_column(values: pd.Series, label: str) -> pd.Series:
    if values.isna().any():
        raise ValueError(f"{label} cannot be missing")
    result = values.astype(str).str.strip()
    if result.eq("").any():
        raise ValueError(f"{label} cannot be blank")
    return result


def _normalise_sid(value: object) -> str:
    if pd.isna(value):
        raise ValueError("membership sid cannot be missing")
    sid = str(value).strip()
    if not sid:
        raise ValueError("membership sid cannot be blank")
    return sid


def _numeric_column(values: pd.Series, label: str) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    invalid = values.notna() & numeric.isna()
    if invalid.any():
        sample = values.loc[invalid].astype(str).tolist()[:5]
        raise ValueError(f"invalid {label} values: {sample}")
    numeric = numeric.astype(float)
    if np.isinf(numeric.to_numpy()).any():
        raise ValueError(f"{label} cannot contain infinite values")
    return numeric


__all__ = [
    "FACTOR_IDS",
    "OUTPUT_COLUMNS",
    "VOLUME_FACTOR_IDS",
    "compute_cross_sectional_market_factor_panel",
    "compute_cross_sectional_market_factors",
    "compute_market_factor_panel",
    "materialize_cross_sectional_market_factors",
    "materialize_market_factors",
]
