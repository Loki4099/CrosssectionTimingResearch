"""Small, dependency-light performance metric set for experiment comparisons."""

from __future__ import annotations

import numpy as np
import pandas as pd


def performance_summary(
    returns: pd.Series,
    *,
    nav: pd.Series | None = None,
    risk_free_daily: pd.Series | None = None,
    benchmark_returns: pd.Series | None = None,
    periods_per_year: int = 252,
) -> pd.Series:
    """Return conventional net performance statistics.

    ``returns`` must include the first execution day's open-to-close return and
    its transaction cost.  This avoids silently dropping initial deployment cost.
    """

    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    values = (
        pd.to_numeric(returns, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
        .astype(float)
    )
    if values.empty:
        raise ValueError("returns cannot be empty")
    if (values < -1.0).any():
        raise ValueError("returns cannot be less than -100%")
    zero_rf_excess = values
    rf_excess: pd.Series | None = None
    if risk_free_daily is not None:
        if not isinstance(risk_free_daily, pd.Series):
            raise TypeError("risk_free_daily must be a pandas Series")
        if not risk_free_daily.index.is_unique:
            raise ValueError("risk_free_daily index must be unique")
        rf = (
            pd.to_numeric(risk_free_daily, errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .reindex(values.index)
        )
        if rf.isna().any():
            missing = rf.index[rf.isna()].tolist()
            raise ValueError(
                "risk_free_daily must cover every strategy observation with a "
                f"finite value; invalid dates: {missing[:10]}"
            )
        rf_excess = values - rf.astype(float)

    # Always reconstruct normalized wealth from the supplied return stream.  By
    # prepending the initial unit of capital, deployment cost or a first-day loss
    # correctly contributes to maximum drawdown.
    wealth = (1.0 + values).cumprod()
    wealth_with_initial = pd.concat(
        [pd.Series([1.0], index=["initial"]), wealth.reset_index(drop=True)]
    )
    years = len(values) / periods_per_year
    total_return = float((1.0 + values).prod() - 1.0)
    cagr = float((1.0 + total_return) ** (1.0 / years) - 1.0) if years > 0 else np.nan
    volatility = float(values.std(ddof=1) * np.sqrt(periods_per_year))
    zero_rf_std = float(zero_rf_excess.std(ddof=1))
    sharpe_zero_rf = (
        float(zero_rf_excess.mean() / zero_rf_std * np.sqrt(periods_per_year))
        if zero_rf_std > 0
        else np.nan
    )
    sharpe_excess_rf = np.nan
    if rf_excess is not None:
        rf_excess_std = float(rf_excess.std(ddof=1))
        if rf_excess_std > 0:
            sharpe_excess_rf = float(
                rf_excess.mean() / rf_excess_std * np.sqrt(periods_per_year)
            )
    sortino_base = rf_excess if rf_excess is not None else zero_rf_excess
    downside = np.minimum(sortino_base.to_numpy(dtype=float), 0.0)
    downside_deviation = float(np.sqrt(np.mean(np.square(downside))))
    sortino = (
        float(sortino_base.mean() / downside_deviation * np.sqrt(periods_per_year))
        if downside_deviation > 0
        else np.nan
    )
    drawdown = wealth_with_initial / wealth_with_initial.cummax() - 1.0
    max_drawdown = float(drawdown.min())
    max_drawdown_duration = _longest_underwater_run(drawdown.iloc[1:])
    calmar = float(cagr / abs(max_drawdown)) if max_drawdown < 0 else np.nan
    summary = pd.Series(
        {
            "observations": len(values),
            "total_return": total_return,
            "cagr": cagr,
            "annualized_volatility": volatility,
            "sharpe_zero_rf": sharpe_zero_rf,
            "sharpe_excess_rf": sharpe_excess_rf,
            "sortino": sortino,
            "max_drawdown": max_drawdown,
            "max_drawdown_duration": max_drawdown_duration,
            "calmar": calmar,
        },
        dtype=float,
    )
    if benchmark_returns is not None:
        # Import locally so the standalone relative-metric module can remain
        # dependency-light and analytics exports do not form an import cycle.
        from .benchmark import relative_performance_summary

        summary = pd.concat(
            [
                summary,
                relative_performance_summary(
                    returns,
                    benchmark_returns,
                    risk_free_daily=risk_free_daily,
                    periods_per_year=periods_per_year,
                ),
            ]
        )
    return summary


def _longest_underwater_run(drawdown: pd.Series) -> int:
    """Count the longest consecutive observations below the prior high-water mark."""

    longest = 0
    current = 0
    for is_underwater in drawdown.lt(0.0).to_numpy(dtype=bool):
        if is_underwater:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
