"""Event-driven next-session-open baseline backtester."""

from .calendar import rebalance_schedule
from .engine import (
    BacktestResult,
    BaselineBacktester,
    MissingCorporateActionPriceError,
    MissingExecutionPriceError,
    MissingValuationPriceError,
    TargetWeightGenerator,
    replay_linear_cost,
    run_cost_scenarios,
)

__all__ = [
    "BacktestResult",
    "BaselineBacktester",
    "MissingCorporateActionPriceError",
    "MissingExecutionPriceError",
    "MissingValuationPriceError",
    "TargetWeightGenerator",
    "rebalance_schedule",
    "replay_linear_cost",
    "run_cost_scenarios",
]
