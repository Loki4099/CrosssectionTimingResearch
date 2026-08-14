"""Frozen V3 market-volatility regime rules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import product

import numpy as np
import pandas as pd

from .volatility import spy_realized_volatility


class HighVolatilityAction(StrEnum):
    T_BILL = "t_bill"
    REVERSAL_5 = "reversal_5"
    REVERSAL_20 = "reversal_20"

    @property
    def reversal_lookback(self) -> int | None:
        if self is HighVolatilityAction.REVERSAL_5:
            return 5
        if self is HighVolatilityAction.REVERSAL_20:
            return 20
        return None


@dataclass(frozen=True, order=True, slots=True)
class RegimeSwitchSpec:
    volatility_window: int
    high_volatility_action: HighVolatilityAction

    def __post_init__(self) -> None:
        if self.volatility_window not in {20, 60}:
            raise ValueError("V3 volatility window must be 20 or 60")
        object.__setattr__(
            self,
            "high_volatility_action",
            HighVolatilityAction(self.high_volatility_action),
        )

    @property
    def experiment_suffix(self) -> str:
        return f"rv{self.volatility_window}__{self.high_volatility_action.value}"


def v3_regime_specs() -> tuple[RegimeSwitchSpec, ...]:
    return tuple(
        RegimeSwitchSpec(window, action)
        for window, action in product((20, 60), tuple(HighVolatilityAction))
    )


def rolling_empirical_percentile(
    values: pd.Series,
    *,
    lookback: int = 756,
    min_history: int = 252,
) -> pd.Series:
    """Empirical CDF of the current value within its causal rolling history."""

    if lookback < 2 or min_history < 2 or min_history > lookback:
        raise ValueError("require 2 <= min_history <= lookback")
    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    if not isinstance(numeric.index, pd.DatetimeIndex):
        raise ValueError("values must use a DatetimeIndex")
    if (
        numeric.index.tz is not None
        or numeric.index.has_duplicates
        or not numeric.index.is_monotonic_increasing
    ):
        raise ValueError("values index must be unique, increasing, and timezone-naive")

    def last_percentile(window_values: np.ndarray) -> float:
        current = window_values[-1]
        valid = window_values[np.isfinite(window_values)]
        if not np.isfinite(current) or len(valid) < min_history:
            return np.nan
        return float(np.mean(valid <= current))

    percentile = numeric.rolling(
        window=lookback, min_periods=min_history
    ).apply(last_percentile, raw=True)
    percentile.name = "volatility_percentile"
    return percentile


def hysteresis_high_volatility_state(
    percentile: pd.Series,
    *,
    enter_threshold: float = 0.80,
    exit_threshold: float = 0.60,
) -> pd.Series:
    """Enter strictly above 80%; exit strictly below 60%; otherwise hold."""

    if not 0.0 <= exit_threshold < enter_threshold <= 1.0:
        raise ValueError("require 0 <= exit_threshold < enter_threshold <= 1")
    numeric = pd.to_numeric(percentile, errors="coerce").astype(float)
    if not isinstance(numeric.index, pd.DatetimeIndex):
        raise ValueError("percentile must use a DatetimeIndex")
    if numeric.index.has_duplicates or not numeric.index.is_monotonic_increasing:
        raise ValueError("percentile index must be unique and increasing")
    finite = numeric[np.isfinite(numeric)]
    if ((finite < 0.0) | (finite > 1.0)).any():
        raise ValueError("finite percentiles must lie in [0, 1]")

    active = False
    states: list[bool] = []
    for value in numeric.to_numpy(dtype=float):
        if np.isfinite(value):
            if not active and value > enter_threshold:
                active = True
            elif active and value < exit_threshold:
                active = False
        states.append(active)
    return pd.Series(states, index=numeric.index, name="high_volatility", dtype=bool)


def spy_volatility_regime(
    benchmark: pd.DataFrame,
    *,
    volatility_window: int,
    percentile_lookback: int = 756,
    min_history: int = 252,
    enter_threshold: float = 0.80,
    exit_threshold: float = 0.60,
) -> pd.DataFrame:
    realized = spy_realized_volatility(benchmark, window=volatility_window)
    percentile = rolling_empirical_percentile(
        realized,
        lookback=percentile_lookback,
        min_history=min_history,
    )
    state = hysteresis_high_volatility_state(
        percentile,
        enter_threshold=enter_threshold,
        exit_threshold=exit_threshold,
    )
    return pd.DataFrame(
        {
            "spy_realized_volatility": realized,
            "volatility_percentile": percentile,
            "high_volatility": state,
        }
    )


def regime_risk_allocation(
    high_volatility: pd.Series, signal_dates: pd.Index
) -> pd.Series:
    dates = pd.DatetimeIndex(pd.to_datetime(signal_dates)).normalize()
    state = high_volatility.reindex(dates)
    if state.isna().any():
        raise ValueError(f"regime is unavailable on {dates[state.isna()][:5].tolist()}")
    allocation = (~state.astype(bool)).astype(float)
    allocation.index = dates
    allocation.name = "target_risk_allocation"
    return allocation


def switch_cross_sectional_scores(
    momentum_scores: pd.Series,
    reversal_scores: pd.Series,
    high_volatility: pd.Series,
) -> pd.Series:
    """Use reversal only on high-volatility signal dates."""

    if not momentum_scores.index.equals(reversal_scores.index):
        raise ValueError("momentum and reversal score indexes must match exactly")
    if not isinstance(momentum_scores.index, pd.MultiIndex) or list(
        momentum_scores.index.names
    ) != ["signal_date", "sid"]:
        raise ValueError("scores must use index ['signal_date', 'sid']")
    dates = pd.DatetimeIndex(
        momentum_scores.index.get_level_values("signal_date")
    )
    state = high_volatility.reindex(dates)
    if state.isna().any():
        raise ValueError("regime is unavailable for one or more score dates")
    momentum = pd.to_numeric(momentum_scores, errors="raise").astype(float)
    reversal = pd.to_numeric(reversal_scores, errors="raise").astype(float)
    output = momentum.where(~state.to_numpy(dtype=bool), reversal)
    output.name = "score"
    return output
