"""Frozen 18-path first-batch experiment registry and runner."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path

import pandas as pd

from momentum_reversal.analytics import relative_performance_summary
from momentum_reversal.backtest import BacktestResult, BaselineBacktester, run_cost_scenarios
from momentum_reversal.data.corporate_actions import CorporateActionLedger
from momentum_reversal.data.membership import PITUniverse
from momentum_reversal.factors import MomentumDefinition


@dataclass(frozen=True, order=True)
class BaselineSpec:
    signal: MomentumDefinition
    top_n: int
    frequency: str

    @property
    def experiment_id(self) -> str:
        return f"baseline__{self.signal.value}__top{self.top_n}__{self.frequency}"


def baseline_specs() -> tuple[BaselineSpec, ...]:
    """Return the pre-registered 3 x 3 x 2 strategy paths."""

    signals = tuple(MomentumDefinition)
    widths = (10, 20, 50)
    frequencies = ("weekly", "monthly")
    return tuple(BaselineSpec(*values) for values in product(signals, widths, frequencies))


def run_baseline_grid(
    prices: pd.DataFrame,
    membership: PITUniverse,
    *,
    sessions: object,
    evaluation_start: object,
    signal_end: object,
    costs_bps: tuple[float, ...] = (0.0, 5.0, 10.0, 20.0),
    corporate_actions: CorporateActionLedger | pd.DataFrame | None = None,
    missing_valuation_policy: str = "strict",
    missing_execution_policy: str = "strict",
) -> dict[str, dict[float, BacktestResult]]:
    """Run all 18 paths on an explicit exchange calendar and research range.

    The calendar and evaluation bounds are intentionally mandatory.  Letting this
    public helper infer sessions from observed prices would compress factor
    lags when an entire exchange session is absent, while omitting the bounds
    could start the experiment in formation-history data.
    """

    engine = BaselineBacktester(
        prices,
        membership,
        sessions=sessions,
        evaluation_start=evaluation_start,
        signal_end=signal_end,
        corporate_actions=corporate_actions,
        missing_valuation_policy=missing_valuation_policy,
        missing_execution_policy=missing_execution_policy,
    )
    output: dict[str, dict[float, BacktestResult]] = {}
    for spec in baseline_specs():
        output[spec.experiment_id] = run_cost_scenarios(
            engine,
            signal=spec.signal,
            top_n=spec.top_n,
            frequency=spec.frequency,  # type: ignore[arg-type]
            cost_bps=costs_bps,
        )
    return output


def export_baseline_result(
    result: BacktestResult,
    output_dir: str | Path,
    *,
    risk_free_daily: pd.Series | None = None,
    benchmark_returns: pd.Series | None = None,
    full_audit: bool = True,
) -> Path:
    """Write one auditable result bundle using dependency-free CSV files.

    Optional benchmark metrics require a return series whose index exactly
    matches ``result.nav``.  Existing callers that do not provide a benchmark
    retain the original files and summary fields, plus the newly added absolute
    Sortino and drawdown-duration statistics.
    """

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    result.nav.to_csv(destination / "nav.csv")
    result.rebalances.to_csv(destination / "rebalances.csv", index=False)
    if full_audit:
        result.trades.to_csv(destination / "trades.csv", index=False)
        result.target_weights.to_csv(destination / "target_weights.csv", index=False)
        result.rankings.to_csv(destination / "rankings.csv", index=False)
        result.corporate_action_events.to_csv(
            destination / "corporate_action_events.csv", index=False
        )
        result.valuation_fallbacks.to_csv(
            destination / "valuation_fallbacks.csv", index=False
        )
    summary = result.summary(risk_free_daily=risk_free_daily)
    if benchmark_returns is not None:
        relative = relative_performance_summary(
            result.nav["daily_return"],
            benchmark_returns,
            risk_free_daily=risk_free_daily,
        )
        summary = pd.concat([summary, relative])
        benchmark_returns.rename("benchmark_return").to_csv(
            destination / "benchmark_returns.csv"
        )
    if risk_free_daily is not None:
        risk_free_daily.rename("rf_return").to_csv(
            destination / "risk_free_returns.csv"
        )
    summary.rename("value").to_csv(destination / "summary.csv")
    metadata = pd.Series(
        {
            "signal": result.signal.value,
            "top_n": result.top_n,
            "frequency": result.frequency,
            "cost_bps": result.cost_bps,
            "full_audit_export": full_audit,
            "missing_valuation_policy": (
                "carry_last_close"
                if not result.valuation_fallbacks.empty
                else "strict_or_unused_fallback"
            ),
            "risk_free_source": (
                "explicit_daily_series" if risk_free_daily is not None else "not_provided"
            ),
            "primary_sharpe_field": (
                "sharpe_excess_rf" if risk_free_daily is not None else "sharpe_zero_rf"
            ),
        },
        name="value",
    )
    metadata.to_csv(destination / "metadata.csv")
    return destination
