"""Strictly aligned benchmark return construction and relative analytics."""

from __future__ import annotations

import numpy as np
import pandas as pd


def benchmark_returns_from_total_return_prices(
    benchmark_prices: pd.DataFrame,
    strategy_returns: pd.Series | pd.Index,
    *,
    date_column: str = "date",
    open_column: str = "tr_open",
    close_column: str = "tr_close",
) -> pd.Series:
    """Build benchmark returns on exactly the strategy return index.

    ``benchmark_prices`` may use a date index or a ``date`` column.  The second
    argument may be the strategy return Series or its date Index.  Those dates
    must begin on the strategy's first next-open execution session.  On
    that first session the benchmark return is total-return close divided by
    total-return open minus one; every later observation is close-to-close.
    Thus the benchmark does not receive the pre-deployment overnight return that
    the strategy never held.  Extra benchmark history is allowed, but every
    strategy session must have a benchmark close and the first session must have
    an open.
    """

    if not isinstance(benchmark_prices, pd.DataFrame):
        raise TypeError("benchmark_prices must be a pandas DataFrame")
    if not isinstance(strategy_returns, (pd.Series, pd.Index)):
        raise TypeError("strategy_returns must be a pandas Series or Index")
    missing_columns = {
        column for column in (open_column, close_column) if column not in benchmark_prices
    }
    if missing_columns:
        raise ValueError(f"benchmark_prices missing columns: {sorted(missing_columns)}")
    frame = benchmark_prices.copy()
    if date_column in frame.columns:
        dates = pd.to_datetime(frame[date_column], errors="coerce")
        if dates.isna().any():
            raise ValueError("benchmark_prices date column contains invalid dates")
        frame.index = pd.DatetimeIndex(dates, name=date_column)
    strategy_index = (
        strategy_returns.index if isinstance(strategy_returns, pd.Series) else strategy_returns
    )
    _require_unique_index(frame.index, "benchmark_prices")
    _require_unique_index(strategy_index, "strategy_returns")
    if len(strategy_index) == 0:
        raise ValueError("strategy_returns cannot be empty")
    if not strategy_index.is_monotonic_increasing:
        raise ValueError("strategy_returns index must be monotonic increasing")

    missing_dates = strategy_index.difference(frame.index)
    if len(missing_dates):
        raise ValueError(
            "benchmark_prices do not cover every strategy session: "
            f"{missing_dates.tolist()}"
        )
    aligned = frame.reindex(strategy_index)
    closes = pd.to_numeric(aligned[close_column], errors="coerce").astype(float)
    first_open = pd.to_numeric(
        pd.Series([aligned[open_column].iloc[0]]), errors="coerce"
    ).iloc[0]
    invalid_close = closes.isna() | ~np.isfinite(closes) | (closes <= 0.0)
    if invalid_close.any():
        raise ValueError(
            "benchmark close is missing or non-positive on strategy sessions: "
            f"{closes.index[invalid_close].tolist()}"
        )
    if pd.isna(first_open) or not np.isfinite(first_open) or first_open <= 0.0:
        raise ValueError("benchmark first-session open must be finite and positive")

    result = closes.pct_change(fill_method=None)
    result.iloc[0] = closes.iloc[0] / float(first_open) - 1.0
    result.index = strategy_index
    result.name = "benchmark_return"
    return result.astype(float)


def relative_performance_summary(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
    *,
    risk_free_daily: pd.Series | None = None,
    periods_per_year: int = 252,
) -> pd.Series:
    """Return relative metrics after exact index validation.

    Strategy and benchmark labels and order must be identical; the function
    never intersects or silently reorders indexes.  Missing/non-finite values
    are then removed pairwise.  ``annualized_excess_return`` is the annualized
    arithmetic mean active return.  ``geometric_excess_return`` is the
    annualized strategy-to-benchmark wealth ratio.

    Short or degenerate samples return ``NaN`` for statistics that are not
    identifiable: beta/alpha need two observations and positive benchmark
    variance, tracking error needs two observations, and information ratio
    needs positive tracking error.
    """

    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    if not isinstance(strategy_returns, pd.Series):
        raise TypeError("strategy_returns must be a pandas Series")
    if not isinstance(benchmark_returns, pd.Series):
        raise TypeError("benchmark_returns must be a pandas Series")
    _require_exact_index(strategy_returns, benchmark_returns)

    columns: dict[str, pd.Series] = {
        "strategy": pd.to_numeric(strategy_returns, errors="coerce"),
        "benchmark": pd.to_numeric(benchmark_returns, errors="coerce"),
    }
    if risk_free_daily is None:
        columns["risk_free"] = pd.Series(0.0, index=strategy_returns.index)
    else:
        if not isinstance(risk_free_daily, pd.Series):
            raise TypeError("risk_free_daily must be a pandas Series")
        _require_exact_index(strategy_returns, risk_free_daily, right_name="risk_free_daily")
        columns["risk_free"] = pd.to_numeric(risk_free_daily, errors="coerce")

    paired = pd.DataFrame(columns).replace([np.inf, -np.inf], np.nan).dropna()
    observations = len(paired)
    empty_result = {
        "relative_observations": observations,
        "annualized_excess_return": np.nan,
        "geometric_excess_return": np.nan,
        "beta": np.nan,
        "annualized_alpha_zero_rf": np.nan,
        "annualized_alpha_excess_rf": np.nan,
        "tracking_error": np.nan,
        "information_ratio": np.nan,
    }
    if paired.empty:
        return pd.Series(empty_result, dtype=float)

    strategy = paired["strategy"].astype(float)
    benchmark = paired["benchmark"].astype(float)
    risk_free = paired["risk_free"].astype(float)
    active = strategy - benchmark
    annualized_excess = float(active.mean() * periods_per_year)
    geometric_excess = _annualized_relative_growth(
        strategy, benchmark, periods_per_year=periods_per_year
    )

    beta = np.nan
    alpha_zero_rf = np.nan
    alpha_excess_rf = np.nan
    tracking_error = np.nan
    information_ratio = np.nan
    if observations >= 2:
        benchmark_variance = float(benchmark.var(ddof=1))
        if np.isfinite(benchmark_variance) and benchmark_variance > 0.0:
            covariance = float(strategy.cov(benchmark))
            beta = float(covariance / benchmark_variance)
            alpha_zero_rf = float(
                (strategy.mean() - beta * benchmark.mean()) * periods_per_year
            )
        if risk_free_daily is not None:
            benchmark_excess = benchmark - risk_free
            strategy_excess = strategy - risk_free
            excess_variance = float(benchmark_excess.var(ddof=1))
            if np.isfinite(excess_variance) and excess_variance > 0.0:
                excess_beta = float(
                    strategy_excess.cov(benchmark_excess) / excess_variance
                )
                alpha_excess_rf = float(
                    (
                        strategy_excess.mean()
                        - excess_beta * benchmark_excess.mean()
                    )
                    * periods_per_year
                )

        active_std = float(active.std(ddof=1))
        if np.isfinite(active_std):
            tracking_error = float(active_std * np.sqrt(periods_per_year))
            if active_std > 0.0:
                information_ratio = float(
                    active.mean() / active_std * np.sqrt(periods_per_year)
                )

    return pd.Series(
        {
            "relative_observations": observations,
            "annualized_excess_return": annualized_excess,
            "geometric_excess_return": geometric_excess,
            "beta": beta,
            "annualized_alpha_zero_rf": alpha_zero_rf,
            "annualized_alpha_excess_rf": alpha_excess_rf,
            "tracking_error": tracking_error,
            "information_ratio": information_ratio,
        },
        dtype=float,
    )


def _annualized_relative_growth(
    strategy: pd.Series,
    benchmark: pd.Series,
    *,
    periods_per_year: int,
) -> float:
    strategy_factors = 1.0 + strategy.to_numpy(dtype=float)
    benchmark_factors = 1.0 + benchmark.to_numpy(dtype=float)
    if (strategy_factors < 0.0).any() or (benchmark_factors <= 0.0).any():
        return np.nan
    if (strategy_factors == 0.0).any():
        return -1.0
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        relative_log_growth = float(
            np.log(strategy_factors).sum() - np.log(benchmark_factors).sum()
        )
        value = np.expm1(relative_log_growth * periods_per_year / len(strategy))
    return float(value) if np.isfinite(value) else np.nan


def _require_unique_index(index: pd.Index, label: str) -> None:
    if not index.is_unique:
        raise ValueError(f"{label} index must be unique")


def _require_exact_index(
    left: pd.Series,
    right: pd.Series,
    *,
    right_name: str = "benchmark_returns",
) -> None:
    _require_unique_index(left.index, "strategy_returns")
    _require_unique_index(right.index, right_name)
    if not left.index.equals(right.index):
        raise ValueError(
            f"strategy_returns and {right_name} must have exactly the same index and order"
        )
