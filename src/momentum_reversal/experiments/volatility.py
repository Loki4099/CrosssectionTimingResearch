"""Frozen historical-volatility rules for V1 and V2."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np
import pandas as pd


@dataclass(frozen=True, order=True, slots=True)
class VolatilityTargetSpec:
    window: int
    target_volatility: float
    max_exposure: float

    def __post_init__(self) -> None:
        if self.window < 2:
            raise ValueError("volatility window must be at least two sessions")
        if self.target_volatility <= 0:
            raise ValueError("target volatility must be positive")
        if self.max_exposure <= 0:
            raise ValueError("max exposure must be positive")

    @property
    def experiment_suffix(self) -> str:
        target = round(self.target_volatility * 100)
        cap = round(self.max_exposure * 100)
        return f"rv{self.window}__target{target}__cap{cap}"


def v1_volatility_specs() -> tuple[VolatilityTargetSpec, ...]:
    """Return the frozen six unlevered and two 1.5x comparison rules."""

    unlevered = tuple(
        VolatilityTargetSpec(window, target, 1.0)
        for window, target in product((20, 60), (0.10, 0.15, 0.20))
    )
    leveraged = tuple(
        VolatilityTargetSpec(window, 0.15, 1.5) for window in (20, 60)
    )
    return unlevered + leveraged


def spy_realized_volatility(
    benchmark: pd.DataFrame, *, window: int
) -> pd.Series:
    """Compute causal annualized close-to-close SPY realized volatility."""

    if window < 2:
        raise ValueError("window must be at least two sessions")
    missing = {"date", "benchmark_tr_close"}.difference(benchmark.columns)
    if missing:
        raise ValueError(f"benchmark is missing columns: {sorted(missing)}")
    dates = pd.DatetimeIndex(pd.to_datetime(benchmark["date"], errors="coerce"))
    if dates.hasnans or dates.has_duplicates:
        raise ValueError("benchmark dates must be valid and unique")
    if dates.tz is not None:
        raise ValueError("benchmark dates must be timezone-naive")
    close = pd.Series(
        pd.to_numeric(benchmark["benchmark_tr_close"], errors="coerce").to_numpy(),
        index=dates.normalize(),
        name="benchmark_tr_close",
    ).sort_index()
    invalid = close.isna() | ~np.isfinite(close) | close.le(0.0)
    if invalid.any():
        raise ValueError(f"benchmark close is invalid on {close.index[invalid][:5].tolist()}")
    returns = close.pct_change(fill_method=None)
    realized = returns.rolling(window=window, min_periods=window).std(ddof=1)
    realized *= np.sqrt(252.0)
    realized.name = f"spy_rv_{window}"
    return realized


def volatility_target_allocation(
    realized_volatility: pd.Series,
    signal_dates: pd.Index,
    spec: VolatilityTargetSpec,
) -> pd.Series:
    """Map signal-close volatility to the next-open risky allocation."""

    dates = pd.DatetimeIndex(pd.to_datetime(signal_dates)).normalize()
    if dates.tz is not None or dates.has_duplicates:
        raise ValueError("signal dates must be timezone-naive and unique")
    values = pd.to_numeric(realized_volatility, errors="coerce").reindex(dates)
    invalid = values.isna() | ~np.isfinite(values) | values.le(0.0)
    if invalid.any():
        raise ValueError(
            f"realized volatility is unavailable on {dates[invalid][:5].tolist()}"
        )
    allocation = (spec.target_volatility / values).clip(
        lower=0.0, upper=spec.max_exposure
    )
    allocation.index = dates
    allocation.name = "target_risk_allocation"
    return allocation.astype(float)


def individual_realized_volatility(
    prices: pd.DataFrame,
    signal_dates: pd.Index,
    *,
    window: int,
    sessions: pd.Index | None = None,
) -> pd.Series:
    """Return causal annualized RV for every ``(signal_date, sid)``.

    The estimate uses exactly ``window`` close-to-close simple returns ending
    at the signal close. Missing closes are never filled and therefore make the
    affected rolling window unavailable.
    """

    if window < 2:
        raise ValueError("window must be at least two sessions")
    if not isinstance(prices.index, pd.MultiIndex) or list(prices.index.names) != [
        "date",
        "sid",
    ]:
        raise ValueError("prices must use a MultiIndex named ['date', 'sid']")
    if "tr_close" not in prices or not prices.index.is_unique:
        raise ValueError("prices must contain unique tr_close observations")

    frame = prices[["tr_close"]].reset_index()
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any() or getattr(dates.dt, "tz", None) is not None:
        raise ValueError("price dates must be valid and timezone-naive")
    frame["date"] = dates.dt.normalize()
    frame["tr_close"] = pd.to_numeric(frame["tr_close"], errors="raise")
    close = frame.pivot(index="date", columns="sid", values="tr_close").sort_index()
    close = close.where(np.isfinite(close) & close.gt(0.0))

    if sessions is not None:
        calendar = pd.DatetimeIndex(pd.to_datetime(list(sessions)))
        if calendar.tz is not None:
            raise ValueError("sessions must be timezone-naive")
        calendar = calendar.normalize()
        if (
            calendar.empty
            or calendar.hasnans
            or calendar.has_duplicates
            or not calendar.is_monotonic_increasing
        ):
            raise ValueError("sessions must be unique and strictly increasing")
        non_sessions = close.index.difference(calendar)
        if len(non_sessions):
            raise ValueError(
                "price dates are absent from the authoritative calendar: "
                f"{non_sessions[:5].tolist()}"
            )
        close = close.reindex(calendar)

    requested = pd.DatetimeIndex(pd.to_datetime(signal_dates)).normalize()
    if requested.tz is not None or requested.has_duplicates:
        raise ValueError("signal dates must be timezone-naive and unique")
    missing_dates = requested.difference(close.index)
    if len(missing_dates):
        raise ValueError(f"signal dates are not exchange sessions: {missing_dates.tolist()}")

    returns = close.pct_change(fill_method=None)
    realized = returns.rolling(window=window, min_periods=window).std(ddof=1)
    realized = (realized * np.sqrt(252.0)).reindex(requested)
    realized.index.name = "signal_date"
    realized.columns.name = "sid"
    output = (
        realized.reset_index()
        .melt(id_vars=["signal_date"], var_name="sid", value_name="realized_volatility")
        .set_index(["signal_date", "sid"])["realized_volatility"]
        .sort_index()
    )
    output.name = f"individual_rv_{window}"
    return output


def risk_adjusted_momentum_scores(
    momentum_scores: pd.Series, realized_volatility: pd.Series
) -> pd.Series:
    """Divide momentum by positive individual RV without clipping or filling."""

    expected_names = ["signal_date", "sid"]
    for label, values in (
        ("momentum_scores", momentum_scores),
        ("realized_volatility", realized_volatility),
    ):
        if not isinstance(values, pd.Series):
            raise TypeError(f"{label} must be a pandas Series")
        if not isinstance(values.index, pd.MultiIndex) or list(values.index.names) != expected_names:
            raise ValueError(f"{label} must use index {expected_names}")
        if not values.index.is_unique:
            raise ValueError(f"{label} index must be unique")
    if not momentum_scores.index.equals(realized_volatility.index):
        raise ValueError("momentum and realized-volatility indexes must match exactly")

    momentum = pd.to_numeric(momentum_scores, errors="raise").astype(float)
    volatility = pd.to_numeric(realized_volatility, errors="raise").astype(float)
    valid = (
        np.isfinite(momentum)
        & np.isfinite(volatility)
        & volatility.gt(0.0)
    )
    adjusted = pd.Series(np.nan, index=momentum.index, dtype=float, name="score")
    adjusted.loc[valid] = momentum.loc[valid] / volatility.loc[valid]
    return adjusted


def apply_linear_cost(
    gross_nav: pd.DataFrame,
    rebalances: pd.DataFrame,
    *,
    cost_bps: float,
) -> pd.DataFrame:
    """Apply proportional turnover costs exactly to a homogeneous gross path.

    Portfolio weights and L1 turnover do not depend on NAV scale.  Therefore a
    cost scenario is the gross daily wealth factor multiplied by
    ``1 - cost_rate * L1`` on each execution date.
    """

    if cost_bps < 0:
        raise ValueError("cost_bps cannot be negative")
    if "daily_return" not in gross_nav:
        raise ValueError("gross_nav must contain daily_return")
    required = {"execution_date", "l1_turnover"}
    missing = required.difference(rebalances.columns)
    if missing:
        raise ValueError(f"rebalances are missing columns: {sorted(missing)}")
    returns = pd.to_numeric(gross_nav["daily_return"], errors="coerce")
    if returns.isna().any() or not np.isfinite(returns).all():
        raise ValueError("gross daily returns must be finite")
    dates = pd.DatetimeIndex(pd.to_datetime(rebalances["execution_date"])).normalize()
    if dates.has_duplicates:
        raise ValueError("rebalance execution dates must be unique")
    turnover = pd.Series(
        pd.to_numeric(rebalances["l1_turnover"], errors="coerce").to_numpy(),
        index=dates,
    )
    if turnover.isna().any() or not np.isfinite(turnover).all() or turnover.lt(0).any():
        raise ValueError("L1 turnover must be finite and non-negative")
    multiplier = pd.Series(1.0, index=returns.index, dtype=float)
    missing_dates = turnover.index.difference(multiplier.index)
    if len(missing_dates):
        raise ValueError(f"rebalance dates are absent from NAV: {missing_dates.tolist()}")
    multiplier.loc[turnover.index] = 1.0 - cost_bps / 10_000.0 * turnover
    if multiplier.le(0.0).any():
        raise ValueError("transaction cost multiplier must remain positive")
    net_factor = (1.0 + returns) * multiplier
    result = pd.DataFrame(
        {
            "daily_return": net_factor - 1.0,
            "nav": net_factor.cumprod(),
            "cost_multiplier": multiplier,
        },
        index=returns.index,
    )
    result.index.name = gross_nav.index.name or "date"
    return result
