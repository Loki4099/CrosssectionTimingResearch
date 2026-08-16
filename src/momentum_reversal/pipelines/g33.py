"""G33 strict-Q4 derisking driven by causal naked-book EWMA forecasts."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
import gc
from pathlib import Path

import numpy as np
import pandas as pd

from momentum_reversal.analytics import benchmark_returns_from_total_return_prices
from momentum_reversal.backtest import BaselineBacktester, rebalance_schedule, replay_linear_cost
from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.storage import sha256_file
from momentum_reversal.experiments import PortfolioMode, StrategySpec

from .bundle import BundleWriteResult, write_experiment_bundle
from .g00 import (
    _append_scenario,
    _concat_or_empty,
    _frozen_borrow_fees,
    _frozen_costs,
    _primary_cost,
    _validate_result_bounds,
    _validate_signed_execution_audit,
    _winner_loser_generator,
    annual_borrow_fee_to_daily,
)
from .g21 import (
    _COMPARISON_METRICS,
    _G00Reference,
    _load_g00_reference,
    _partition_strategies,
    _signal_dates_by_frequency,
)
from .run_context import ExperimentRunContext, LoadedExperimentData, load_experiment_data


_FROZEN_DESIGN_SHA256 = "440e9fd0ed97e282cf8cbb19cb8031bb16da0d4c6ed5d928b386f90195ef7a7d"
_FROZEN_DATASET_VERSION = (
    "sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate"
)
_FROZEN_DATASET_MANIFEST_SHA256 = (
    "65b628d604f7e2f456e8d1d43a3c3e88b6bd3e86cc1c9455cdcfe28b856a3ec7"
)
_FROZEN_RECORD_SHA256 = (
    "a3ef9ee72cd3d535c2e5bf06b3d1f520c54667a8552891543ee0f9ca50488296"
)
_FROZEN_PROGRAM_SHA256 = (
    "11394af02fa028abe4a11434874be31e33e692f55feb73e9236da9bf8d07d413"
)
_FROZEN_GROUP_CONFIG_SHA256 = (
    "03247cbc7c3d7924268307f5583cfd6b55d47be084b91d7484a208019582cbc0"
)
_FROZEN_G00_MANIFEST_SHA256 = (
    "8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66"
)
_FROZEN_IMPLEMENTATION_NOTE_SHA256 = (
    "61104f8376c4845d35ef52cab8cbb6fe72ffc1af984d54bf3796aa923c833079"
)


@dataclass(frozen=True, slots=True)
class G33RunConfig:
    context: ExperimentRunContext
    reference_g00_root: Path
    allow_review_dataset: bool = False
    workers: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_g00_root", Path(self.reference_g00_root))
        if self.context.group_id != "G33":
            raise ValueError("G33 runner requires the registered G33 spec")
        if isinstance(self.workers, bool) or not isinstance(self.workers, int):
            raise ValueError("G33 workers must be an integer")
        if self.workers <= 0 or self.workers > 8:
            raise ValueError("G33 workers must be between 1 and 8")


@dataclass(frozen=True, slots=True)
class G33RunResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    strategy_count: int
    scenario_count: int
    comparison_count: int
    q4_rebalance_count: int
    formal_run_eligible: bool


@dataclass(frozen=True, slots=True)
class _G33CoreBatch:
    strategy_count: int
    summary: pd.DataFrame
    nav: pd.DataFrame
    rebalances: pd.DataFrame
    holdings: pd.DataFrame
    trades: pd.DataFrame
    diagnostics: pd.DataFrame


@dataclass(frozen=True, slots=True)
class _G33RegimePath:
    regime: pd.DataFrame
    allocation: pd.Series
    naked_observations: int
    seed_signal_date: pd.Timestamp | None
    engine_start_date: pd.Timestamp


def forecast_engine_start(
    sessions: pd.DatetimeIndex,
    frequency: str,
    history_start: object,
) -> tuple[pd.Timestamp | None, pd.Timestamp]:
    """Return the frozen engine seed signal and start for one frequency."""

    authoritative = pd.DatetimeIndex(pd.to_datetime(sessions)).normalize()
    if (
        authoritative.tz is not None
        or authoritative.hasnans
        or authoritative.has_duplicates
        or not authoritative.is_monotonic_increasing
    ):
        raise ValueError("sessions must be timezone-naive, unique, and increasing")
    start = pd.Timestamp(history_start).normalize()
    if start.tzinfo is not None:
        start = start.tz_localize(None)
    if frequency == "weekly":
        if start not in authoritative:
            raise DataQualityError("G33 weekly t0 is not an authoritative session")
        return None, start
    if frequency != "monthly":
        raise ValueError("G33 frequency must be weekly or monthly")
    schedule = rebalance_schedule(authoritative, frequency)  # type: ignore[arg-type]
    eligible = schedule.loc[schedule["execution_date"].lt(start)]
    if eligible.empty:
        raise DataQualityError(
            f"G33 has no monthly seed execution before {start.date()}"
        )
    row = eligible.sort_values("execution_date").iloc[-1]
    signal_date = pd.Timestamp(row["signal_date"]).normalize()
    execution_date = pd.Timestamp(row["execution_date"]).normalize()
    if not signal_date < execution_date < start:
        raise DataQualityError("G33 forecast seed event is not strictly pre-start")
    return signal_date, execution_date


def forecast_input_returns(
    naked_nav: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    history_start: object,
    evaluation_end: object,
) -> pd.Series:
    """Slice the complete post-t0 naked-book return history used by EWMA."""

    if not isinstance(naked_nav, pd.DataFrame) or "daily_return" not in naked_nav:
        raise ValueError("naked_nav must contain daily_return")
    if not isinstance(naked_nav.index, pd.DatetimeIndex):
        raise ValueError("naked_nav must use a DatetimeIndex")
    source = naked_nav.copy()
    source.index = pd.DatetimeIndex(source.index).normalize()
    if (
        source.index.tz is not None
        or source.index.has_duplicates
        or not source.index.is_monotonic_increasing
    ):
        raise ValueError("naked_nav index must be timezone-naive, unique, and increasing")
    authoritative = pd.DatetimeIndex(pd.to_datetime(sessions)).normalize()
    if (
        authoritative.tz is not None
        or authoritative.hasnans
        or authoritative.has_duplicates
        or not authoritative.is_monotonic_increasing
    ):
        raise ValueError("sessions must be timezone-naive, unique, and increasing")
    non_sessions = source.index.difference(authoritative)
    if len(non_sessions):
        raise DataQualityError(
            f"G33 naked book contains non-authoritative sessions: "
            f"{non_sessions[:5].tolist()}"
        )
    start = pd.Timestamp(history_start).normalize()
    end = pd.Timestamp(evaluation_end).normalize()
    if start.tzinfo is not None:
        start = start.tz_localize(None)
    if end.tzinfo is not None:
        end = end.tz_localize(None)
    expected = authoritative[(authoritative >= start) & (authoritative <= end)]
    if expected.empty or expected[0] != start or expected[-1] != end:
        raise DataQualityError("G33 forecast interval is not an authoritative session span")
    values = pd.to_numeric(source["daily_return"], errors="coerce").reindex(expected)
    invalid = values.isna() | ~np.isfinite(values) | values.le(-1.0)
    if invalid.any():
        raise DataQualityError(
            "G33 naked-book forecast history is incomplete or invalid: "
            f"{values.index[invalid][:5].tolist()}"
        )
    if np.isclose(float(values.iloc[0]), 0.0, rtol=0.0, atol=0.0):
        raise DataQualityError(
            "G33 initial t0 naked-book return is zero; EWMA variance must be positive"
        )
    values.name = "book_return"
    return values.astype(float)


def strict_lagged_book_forecast_quartiles(
    book_returns: pd.Series,
    *,
    ewma_decay: float = 0.94,
    forecast_horizon_sessions: int = 21,
    history_sessions: int = 756,
) -> pd.DataFrame:
    """Build causal EWMA(0.94) forecast volatility and strict prior quartiles."""

    if not isinstance(book_returns, pd.Series):
        raise TypeError("book_returns must be a pandas Series")
    if not isinstance(book_returns.index, pd.DatetimeIndex):
        raise ValueError("book_returns must use a DatetimeIndex")
    if (
        book_returns.index.tz is not None
        or book_returns.index.has_duplicates
        or not book_returns.index.is_monotonic_increasing
    ):
        raise ValueError(
            "book_returns index must be timezone-naive, unique, and increasing"
        )
    if not np.isclose(float(ewma_decay), 0.94, rtol=0.0, atol=1e-15):
        raise ValueError("G33 requires ewma_decay=0.94")
    if (
        isinstance(forecast_horizon_sessions, bool)
        or forecast_horizon_sessions != 21
    ):
        raise ValueError("G33 requires forecast_horizon_sessions=21")
    if isinstance(history_sessions, bool) or not isinstance(history_sessions, int):
        raise ValueError("history_sessions must be an integer")
    if history_sessions < 4:
        raise ValueError("history_sessions must be at least four")
    values = pd.to_numeric(book_returns, errors="coerce").astype(float)
    if values.empty:
        raise ValueError("book_returns cannot be empty")
    invalid = values.isna() | ~np.isfinite(values) | values.le(-1.0)
    if invalid.any():
        raise DataQualityError(
            "naked-book returns must be finite and greater than -100%: "
            f"{values.index[invalid][:5].tolist()}"
        )

    variance = values.pow(2.0).ewm(alpha=0.06, adjust=False).mean()
    invalid_variance = variance.isna() | ~np.isfinite(variance) | variance.le(0.0)
    if invalid_variance.any():
        raise DataQualityError(
            "G33 EWMA variance must remain finite and strictly positive: "
            f"{variance.index[invalid_variance][:5].tolist()}"
        )
    _validate_ewma_recurrence(values, variance, decay=float(ewma_decay))
    forecast_variance = variance * float(forecast_horizon_sessions)
    forecast_volatility = np.sqrt(
        (252.0 / float(forecast_horizon_sessions)) * forecast_variance
    )
    lagged = forecast_volatility.shift(1)
    rolling = lagged.rolling(history_sessions, min_periods=history_sessions)
    q25 = rolling.quantile(0.25)
    q50 = rolling.quantile(0.50)
    q75 = rolling.quantile(0.75)
    positive_history = (
        lagged.gt(0.0)
        .rolling(history_sessions, min_periods=history_sessions)
        .sum()
        .eq(float(history_sessions))
    )
    available = (
        forecast_volatility.notna()
        & np.isfinite(forecast_volatility)
        & forecast_volatility.gt(0.0)
        & q25.notna()
        & np.isfinite(q25)
        & q25.gt(0.0)
        & q50.notna()
        & np.isfinite(q50)
        & q50.gt(0.0)
        & q75.notna()
        & np.isfinite(q75)
        & q75.gt(0.0)
        & positive_history
    )
    quartile = pd.Series(pd.NA, index=values.index, dtype="Int64")
    quartile.loc[available & forecast_volatility.le(q25)] = 1
    quartile.loc[
        available & forecast_volatility.gt(q25) & forecast_volatility.le(q50)
    ] = 2
    quartile.loc[
        available & forecast_volatility.gt(q50) & forecast_volatility.le(q75)
    ] = 3
    quartile.loc[available & forecast_volatility.gt(q75)] = 4
    return pd.DataFrame(
        {
            "book_forecast_volatility": forecast_volatility,
            "ewma_variance": variance,
            "forecast_variance_21": forecast_variance,
            "lagged_q25": q25,
            "lagged_q50": q50,
            "lagged_q75": q75,
            "volatility_quartile": quartile,
            "high_volatility": quartile.eq(4).fillna(False),
        }
    )


def _validate_ewma_recurrence(
    book_returns: pd.Series,
    variance: pd.Series,
    *,
    decay: float,
) -> None:
    returns = book_returns.to_numpy(dtype=float)
    actual = variance.to_numpy(dtype=float)
    expected = np.empty_like(returns)
    expected[0] = returns[0] ** 2.0
    for index in range(1, len(returns)):
        expected[index] = decay * expected[index - 1] + (1.0 - decay) * (
            returns[index] ** 2.0
        )
    if not np.allclose(actual, expected, rtol=1e-13, atol=1e-18):
        maximum = float(np.max(np.abs(actual - expected)))
        raise DataQualityError(
            f"G33 pandas EWMA differs from direct recursion: {maximum:.3e}"
        )


def strict_q4_forecast_allocation(
    regime: pd.DataFrame, signal_dates: pd.Index | None = None
) -> pd.Series:
    """Return the frozen next-open scale from causal book forecast states."""

    if not isinstance(regime, pd.DataFrame):
        raise TypeError("regime must be a pandas DataFrame")
    required = {"book_forecast_volatility", "lagged_q75", "volatility_quartile"}
    missing = required.difference(regime.columns)
    if missing:
        raise ValueError(f"regime is missing columns: {sorted(missing)}")
    if not isinstance(regime.index, pd.DatetimeIndex):
        raise ValueError("regime must use a DatetimeIndex")
    if (
        regime.index.tz is not None
        or regime.index.has_duplicates
        or not regime.index.is_monotonic_increasing
    ):
        raise ValueError("regime index must be timezone-naive, unique, and increasing")

    sigma = pd.to_numeric(regime["book_forecast_volatility"], errors="coerce")
    q75 = pd.to_numeric(regime["lagged_q75"], errors="coerce")
    quartile = pd.to_numeric(regime["volatility_quartile"], errors="coerce")
    available = quartile.notna()
    invalid_quartile = available & ~quartile.isin([1, 2, 3, 4])
    invalid_values = available & (
        sigma.isna()
        | q75.isna()
        | ~np.isfinite(sigma)
        | ~np.isfinite(q75)
        | sigma.le(0.0)
        | q75.le(0.0)
    )
    if invalid_quartile.any() or invalid_values.any():
        raise ValueError("available regime rows must contain valid quartiles and volatility")
    high = quartile.eq(4)
    if (high & ~sigma.gt(q75)).any():
        raise ValueError("Q4 rows must be strictly above the lagged q75 threshold")
    if (available & ~high & sigma.gt(q75)).any():
        raise ValueError("non-Q4 rows cannot exceed the lagged q75 threshold")
    if "high_volatility" in regime:
        stated = regime["high_volatility"].astype("boolean")
        mismatch = available & stated.notna() & stated.ne(high)
        if mismatch.any():
            raise ValueError("high_volatility conflicts with volatility_quartile")

    allocation = pd.Series(np.nan, index=regime.index, dtype=float)
    allocation.loc[available] = 1.0
    allocation.loc[high] = (q75.loc[high] / sigma.loc[high]).clip(upper=1.0)
    if (allocation.loc[high].le(0.0) | allocation.loc[high].ge(1.0)).any():
        raise DataQualityError("G33 Q4 allocation must be strictly between zero and one")
    allocation.name = "target_risk_allocation"
    if signal_dates is None:
        return allocation
    dates = pd.DatetimeIndex(pd.to_datetime(signal_dates)).normalize()
    if dates.tz is not None or dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("signal dates must be timezone-naive, unique, and increasing")
    sampled = allocation.reindex(dates)
    if sampled.isna().any():
        raise DataQualityError(
            "G33 allocation is unavailable on signal dates: "
            f"{dates[sampled.isna()][:5].tolist()}"
        )
    sampled.index = dates
    return sampled.astype(float)


def run_g33(config: G33RunConfig) -> G33RunResult:
    """Run and freeze the 36-path G33 naked-book EWMA forecast grid."""

    if config.context.bundle_dir.exists():
        raise FileExistsError(
            f"immutable experiment bundle already exists: {config.context.bundle_dir}"
        )
    data = load_experiment_data(
        config.context, allow_review_dataset=config.allow_review_dataset
    )
    reference_g00 = _load_g00_reference(config.reference_g00_root, data)
    _validate_frozen_inputs(config.context, data)
    _validate_reference_anchor(reference_g00)
    _validate_runtime_roots(config)
    strategies = config.context.strategies
    if config.workers == 1:
        batches = [
            _run_g33_core_batch(
                context=config.context,
                data=data,
                strategies=strategies,
            )
        ]
    else:
        partitions = _partition_strategies(strategies, config.workers)
        batches = []
        with ProcessPoolExecutor(max_workers=len(partitions)) as executor:
            futures = [
                executor.submit(
                    _run_g33_core_batch_worker,
                    config.context,
                    config.allow_review_dataset,
                    tuple(strategy.strategy_id for strategy in partition),
                )
                for partition in partitions
            ]
            for future in as_completed(futures):
                batches.append(future.result())

    summary = pd.concat([batch.summary for batch in batches], ignore_index=True)
    summary = summary.sort_values(
        ["strategy_id", "cost_bps", "borrow_fee_annual"], ignore_index=True
    )
    artifacts = {
        "nav": pd.concat([batch.nav for batch in batches], ignore_index=True),
        "rebalances": pd.concat(
            [batch.rebalances for batch in batches], ignore_index=True
        ),
        "holdings": pd.concat([batch.holdings for batch in batches], ignore_index=True),
        "trades": pd.concat([batch.trades for batch in batches], ignore_index=True),
        "diagnostics": pd.concat(
            [batch.diagnostics for batch in batches], ignore_index=True
        ),
    }
    artifacts = {
        name: _stable_artifact_sort(name, frame) for name, frame in artifacts.items()
    }
    _validate_g00_path_identity(
        artifacts["rebalances"], artifacts["holdings"], reference_g00
    )
    _validate_main_counts(summary, config.context)
    _validate_artifact_counts(artifacts, data)
    signed_primary = artifacts["rebalances"].loc[
        artifacts["rebalances"]["portfolio_mode"].eq("long_short")
    ]
    _validate_signed_execution_audit(
        signed_primary,
        corporate_actions=data.corporate_actions,
        sessions=data.sessions,
    )
    summary.loc[summary["portfolio_mode"].eq("long_short"), "valid_scenario"] = True
    summary.loc[summary["portfolio_mode"].eq("long_short"), "invalid_reason"] = ""
    comparison = _attach_g00_comparisons(summary, reference_g00.summary)
    q4_count = int(artifacts["rebalances"]["high_volatility"].sum())
    bundle: BundleWriteResult = write_experiment_bundle(
        config.context,
        summary=summary,
        comparison=comparison,
        artifacts=artifacts,
        status="completed",
        extra_manifest=_manifest_metadata(
            config=config,
            data=data,
            reference_g00=reference_g00,
            summary=summary,
            comparison=comparison,
            rebalances=artifacts["rebalances"],
        ),
    )
    return G33RunResult(
        run_id=config.context.run_id,
        output_dir=bundle.output_dir,
        manifest_path=bundle.manifest_path,
        strategy_count=len(strategies),
        scenario_count=len(summary),
        comparison_count=len(comparison),
        q4_rebalance_count=q4_count,
        formal_run_eligible=False,
    )


def _run_g33_core_batch_worker(
    context: ExperimentRunContext,
    allow_review_dataset: bool,
    strategy_ids: tuple[str, ...],
) -> _G33CoreBatch:
    data = load_experiment_data(context, allow_review_dataset=allow_review_dataset)
    lookup = {strategy.strategy_id: strategy for strategy in context.strategies}
    try:
        strategies = tuple(lookup[strategy_id] for strategy_id in strategy_ids)
    except KeyError as error:
        raise ValueError(f"unknown G33 worker strategy: {error.args[0]}") from error
    return _run_g33_core_batch(context=context, data=data, strategies=strategies)


def _run_g33_core_batch(
    *,
    context: ExperimentRunContext,
    data: LoadedExperimentData,
    strategies: tuple[StrategySpec, ...],
) -> _G33CoreBatch:
    regimes = _build_strategy_regimes(context=context, data=data, strategies=strategies)
    long_only_engine = BaselineBacktester(
        data.prices,
        data.membership,
        sessions=data.sessions,
        evaluation_start=data.evaluation_start,
        signal_end=data.evaluation_end,
        corporate_actions=data.corporate_actions,
        missing_valuation_policy=data.missing_valuation_policy,
        missing_execution_policy=data.legacy_missing_execution_policy,
    )
    long_short_engine = BaselineBacktester(
        data.prices,
        data.membership,
        sessions=data.sessions,
        evaluation_start=data.evaluation_start,
        signal_end=data.evaluation_end,
        corporate_actions=data.corporate_actions,
        missing_valuation_policy=data.missing_valuation_policy,
        missing_execution_policy="strict",
    )
    benchmark = data.benchmark.rename(
        columns={"benchmark_tr_open": "tr_open", "benchmark_tr_close": "tr_close"}
    )
    costs = _frozen_costs(context)
    borrow_fees = _frozen_borrow_fees(context)
    summary_frames: list[pd.DataFrame] = []
    nav_frames: list[pd.DataFrame] = []
    rebalance_frames: list[pd.DataFrame] = []
    holding_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    diagnostic_frames: list[pd.DataFrame] = []

    for strategy in strategies:
        path = regimes[strategy.strategy_id]
        summary_rows: list[dict[str, object]] = []
        strategy_nav: list[pd.DataFrame] = []
        strategy_rebalances: list[pd.DataFrame] = []
        strategy_holdings: list[pd.DataFrame] = []
        strategy_trades: list[pd.DataFrame] = []
        diagnostic_rows: list[dict[str, object]] = []
        primary_cost = _primary_cost(context, strategy.frequency)
        if strategy.portfolio_mode is PortfolioMode.LONG_ONLY:
            zero_cost = long_only_engine.run(
                signal=strategy.signal,
                top_n=strategy.top_n,
                frequency=strategy.frequency,  # type: ignore[arg-type]
                cost_bps=0.0,
                risk_allocation=path.allocation,
                risk_free_daily=data.risk_free_daily,
                full_audit=True,
            )
            for cost_bps in costs:
                result = replay_linear_cost(zero_cost, cost_bps=float(cost_bps))
                _validate_core_path_state_identity(zero_cost, result)
                _append_g33_scenario(
                    strategy, result, cost_bps, 0.0, primary_cost, data, benchmark,
                    summary_rows, strategy_nav, strategy_rebalances,
                    strategy_holdings, strategy_trades, diagnostic_rows,
                )
        else:
            generator = _winner_loser_generator(strategy.top_n)
            state_reference: object | None = None
            for annual_borrow_fee in borrow_fees:
                zero_cost = long_short_engine.run(
                    signal=strategy.signal,
                    top_n=strategy.top_n,
                    frequency=strategy.frequency,  # type: ignore[arg-type]
                    cost_bps=0.0,
                    target_weight_generator=generator,
                    target_weight_cache_key=strategy.strategy_id,
                    risk_allocation=path.allocation,
                    risk_free_daily=data.risk_free_daily,
                    short_borrow_fee_daily=annual_borrow_fee_to_daily(
                        annual_borrow_fee
                    ),
                    signed_missing_execution_policy="terminal_last_close",
                    terminal_last_close_max_sessions=data.terminal_last_close_max_sessions,
                    full_audit=np.isclose(annual_borrow_fee, 0.01),
                )
                if state_reference is None:
                    state_reference = zero_cost
                else:
                    _validate_core_path_state_identity(state_reference, zero_cost)
                for cost_bps in costs:
                    result = replay_linear_cost(zero_cost, cost_bps=float(cost_bps))
                    _validate_core_path_state_identity(zero_cost, result)
                    _append_g33_scenario(
                        strategy, result, cost_bps, annual_borrow_fee, primary_cost,
                        data, benchmark, summary_rows, strategy_nav,
                        strategy_rebalances, strategy_holdings, strategy_trades,
                        diagnostic_rows,
                    )

        strategy_summary = pd.DataFrame(summary_rows).sort_values(
            ["strategy_id", "cost_bps", "borrow_fee_annual"], ignore_index=True
        )
        rebalances = _concat_or_empty(strategy_rebalances, "rebalances")
        _attach_forecast_regime_audit(strategy_summary, rebalances, path.regime)
        diagnostic_rows.extend(
            _naked_book_diagnostics(
                strategy=strategy,
                primary_cost=primary_cost,
                path=path,
                rebalances=rebalances,
            )
        )
        diagnostic_rows.extend(
            _daily_naked_regime_diagnostics(
                strategy=strategy,
                primary_cost=primary_cost,
                regime=path.regime,
            )
        )
        summary_frames.append(strategy_summary)
        nav_frames.append(pd.concat(strategy_nav, ignore_index=True))
        rebalance_frames.append(rebalances)
        holding_frames.append(_concat_or_empty(strategy_holdings, "holdings"))
        trade_frames.append(_concat_or_empty(strategy_trades, "trades"))
        diagnostic_frames.append(pd.DataFrame(diagnostic_rows))

    return _G33CoreBatch(
        strategy_count=len(strategies),
        summary=pd.concat(summary_frames, ignore_index=True),
        nav=pd.concat(nav_frames, ignore_index=True),
        rebalances=pd.concat(rebalance_frames, ignore_index=True),
        holdings=pd.concat(holding_frames, ignore_index=True),
        trades=pd.concat(trade_frames, ignore_index=True),
        diagnostics=pd.concat(diagnostic_frames, ignore_index=True),
    )


def _build_strategy_regimes(
    *,
    context: ExperimentRunContext,
    data: LoadedExperimentData,
    strategies: tuple[StrategySpec, ...],
) -> dict[str, _G33RegimePath]:
    parameters = _frozen_parameters(context)
    history_start = pd.Timestamp(parameters["history_start"])
    seed_events = {
        frequency: forecast_engine_start(data.sessions, frequency, history_start)
        for frequency in ("weekly", "monthly")
    }
    long_only_engine = BaselineBacktester(
        data.prices,
        data.membership,
        sessions=data.sessions,
        evaluation_start=history_start,
        signal_end=data.evaluation_end,
        corporate_actions=data.corporate_actions,
        missing_valuation_policy=data.missing_valuation_policy,
        missing_execution_policy=data.legacy_missing_execution_policy,
    )
    long_short_engine = BaselineBacktester(
        data.prices,
        data.membership,
        sessions=data.sessions,
        evaluation_start=history_start,
        signal_end=data.evaluation_end,
        corporate_actions=data.corporate_actions,
        missing_valuation_policy=data.missing_valuation_policy,
        missing_execution_policy="strict",
    )
    signal_dates = _signal_dates_by_frequency(data)
    output: dict[str, _G33RegimePath] = {}
    for strategy in strategies:
        seed_signal, engine_start = seed_events[strategy.frequency]
        engine = (
            long_only_engine
            if strategy.portfolio_mode is PortfolioMode.LONG_ONLY
            else long_short_engine
        )
        # The engine's schedule is frequency-specific and each frequency always
        # uses the same frozen seed execution.  Pre-t0 NAV establishes a real
        # book only; forecast_input_returns excludes all of its returns.
        engine.evaluation_start = engine_start
        if strategy.portfolio_mode is PortfolioMode.LONG_ONLY:
            naked = engine.run(
                signal=strategy.signal,
                top_n=strategy.top_n,
                frequency=strategy.frequency,  # type: ignore[arg-type]
                cost_bps=0.0,
                risk_free_daily=None,
                short_borrow_fee_daily=0.0,
                full_audit=False,
            )
        else:
            naked = engine.run(
                signal=strategy.signal,
                top_n=strategy.top_n,
                frequency=strategy.frequency,  # type: ignore[arg-type]
                cost_bps=0.0,
                target_weight_generator=_winner_loser_generator(strategy.top_n),
                target_weight_cache_key=strategy.strategy_id,
                risk_free_daily=None,
                short_borrow_fee_daily=0.0,
                signed_missing_execution_policy="terminal_last_close",
                terminal_last_close_max_sessions=data.terminal_last_close_max_sessions,
                full_audit=False,
            )
        if naked.nav.index[0] != engine_start:
            raise DataQualityError(
                f"G33 naked book did not start on its seed execution for "
                f"{strategy.strategy_id}"
            )
        naked_returns = forecast_input_returns(
            naked.nav,
            data.sessions,
            history_start,
            data.evaluation_end,
        )
        if len(naked_returns) != 3_018:
            raise DataQualityError(
                f"G33 requires exactly 3018 forecast observations for "
                f"{strategy.strategy_id}, got {len(naked_returns)}"
            )
        regime = strict_lagged_book_forecast_quartiles(
            naked_returns,
            ewma_decay=float(parameters["ewma_decay"]),
            forecast_horizon_sessions=int(parameters["forecast_horizon_sessions"]),
            history_sessions=int(parameters["history_sessions"]),
        )
        regime = regime.copy()
        regime["book_return"] = naked_returns
        regime["target_risk_allocation"] = strict_q4_forecast_allocation(regime)
        allocation = strict_q4_forecast_allocation(
            regime, signal_dates[strategy.frequency]
        )
        output[strategy.strategy_id] = _G33RegimePath(
            regime=regime,
            allocation=allocation,
            naked_observations=len(naked_returns),
            seed_signal_date=seed_signal,
            engine_start_date=engine_start,
        )
    del long_only_engine
    del long_short_engine
    gc.collect()
    return output


def _validate_core_path_state_identity(reference: object, candidate: object) -> None:
    """Reject any cost/borrow path that changes signals, state, or targets."""

    identity_columns = [
        "signal_date",
        "execution_date",
        "execution_status",
        "target_risk_allocation",
        "requested_selected_sids",
        "missing_target_sids",
    ]
    left = reference.rebalances.reset_index(drop=True)  # type: ignore[attr-defined]
    right = candidate.rebalances.reset_index(drop=True)  # type: ignore[attr-defined]
    requested_exposure_columns = [
        "requested_long_exposure",
        "requested_short_exposure",
        "requested_gross_exposure",
        "requested_net_exposure",
    ]
    target_exposure_columns = [
        "target_long_exposure",
        "target_short_exposure",
        "target_gross_exposure",
        "target_net_exposure",
    ]
    if all(column in left and column in right for column in requested_exposure_columns):
        exposure_columns = requested_exposure_columns
    elif all(column in left and column in right for column in target_exposure_columns):
        exposure_columns = target_exposure_columns
    else:
        raise DataQualityError("G33 core-path audit lacks state or targets")
    required = identity_columns + exposure_columns
    for label, frame in (("reference", left), ("candidate", right)):
        missing = set(required).difference(frame.columns)
        if missing:
            raise DataQualityError(
                f"G33 {label} core-path audit lacks columns: {sorted(missing)}"
            )
    if len(left) != len(right):
        raise DataQualityError("G33 cost/borrow path changed rebalance count")
    for column in (
        "signal_date",
        "execution_date",
        "execution_status",
        "requested_selected_sids",
        "missing_target_sids",
    ):
        left_values = left[column].fillna("").astype(str)
        right_values = right[column].fillna("").astype(str)
        if not left_values.equals(right_values):
            raise DataQualityError(
                f"G33 cost/borrow path changed frozen {column}"
            )
    numeric = ["target_risk_allocation", *exposure_columns]
    left_numeric = left[numeric].apply(pd.to_numeric, errors="coerce")
    right_numeric = right[numeric].apply(pd.to_numeric, errors="coerce")
    if (
        left_numeric.isna().any().any()
        or right_numeric.isna().any().any()
        or not np.isfinite(left_numeric.to_numpy(dtype=float)).all()
        or not np.isfinite(right_numeric.to_numpy(dtype=float)).all()
        or not np.allclose(
            left_numeric.to_numpy(dtype=float),
            right_numeric.to_numpy(dtype=float),
            rtol=0.0,
            atol=1e-12,
        )
    ):
        raise DataQualityError("G33 cost/borrow path changed frozen state or targets")


def _append_g33_scenario(
    strategy: StrategySpec,
    result: object,
    cost_bps: float,
    borrow_fee: float,
    primary_cost: float,
    data: LoadedExperimentData,
    benchmark: pd.DataFrame,
    summary_rows: list[dict[str, object]],
    nav_frames: list[pd.DataFrame],
    rebalance_frames: list[pd.DataFrame],
    holding_frames: list[pd.DataFrame],
    trade_frames: list[pd.DataFrame],
    diagnostic_rows: list[dict[str, object]],
) -> None:
    _validate_result_bounds(result, data)  # type: ignore[arg-type]
    benchmark_returns = benchmark_returns_from_total_return_prices(
        benchmark, result.nav["daily_return"]  # type: ignore[attr-defined]
    )
    primary_borrow = (
        0.0 if strategy.portfolio_mode is PortfolioMode.LONG_ONLY else 0.01
    )
    _append_pnl_attribution_diagnostics(
        strategy=strategy,
        result=result,
        cost_bps=cost_bps,
        borrow_fee=borrow_fee,
        diagnostic_rows=diagnostic_rows,
    )
    _append_scenario(
        strategy=strategy,
        result=result,  # type: ignore[arg-type]
        cost_bps=cost_bps,
        borrow_fee_annual=borrow_fee,
        primary=np.isclose(cost_bps, primary_cost)
        and np.isclose(borrow_fee, primary_borrow),
        risk_free_daily=data.risk_free_daily,
        benchmark_returns=benchmark_returns,
        summary_rows=summary_rows,
        nav_frames=nav_frames,
        rebalance_frames=rebalance_frames,
        holding_frames=holding_frames,
        trade_frames=trade_frames,
        diagnostic_rows=diagnostic_rows,
    )
    row = summary_rows[-1]
    row["base_target_gross_exposure"] = row.pop("target_gross_exposure")
    row["base_target_net_exposure"] = row.pop("target_net_exposure")


def _attach_forecast_regime_audit(
    summary: pd.DataFrame,
    rebalances: pd.DataFrame,
    regime: pd.DataFrame,
) -> None:
    dates = pd.DatetimeIndex(pd.to_datetime(rebalances["signal_date"])).normalize()
    sampled = regime.reindex(dates)
    required = [
        "book_forecast_volatility",
        "ewma_variance",
        "forecast_variance_21",
        "lagged_q25",
        "lagged_q50",
        "lagged_q75",
        "volatility_quartile",
        "high_volatility",
        "target_risk_allocation",
    ]
    if sampled[required].isna().any().any():
        raise DataQualityError("G33 forecast regime is unavailable for a rebalance")
    actual = pd.to_numeric(rebalances["target_risk_allocation"], errors="coerce")
    expected = sampled["target_risk_allocation"].to_numpy(dtype=float)
    if actual.isna().any() or not np.allclose(
        actual, expected, rtol=0.0, atol=1e-12
    ):
        raise DataQualityError("G33 engine allocation differs from frozen Q4 rule")
    for column in required[:-1]:
        rebalances[column] = sampled[column].to_numpy()
    rebalances["derisk_scale"] = expected
    grouped = rebalances.groupby("strategy_id", observed=True)
    stats = grouped["high_volatility"].agg(["sum", "mean"])
    q4 = rebalances.loc[rebalances["high_volatility"].astype(bool)]
    q4_stats = q4.groupby("strategy_id", observed=True)[
        "target_risk_allocation"
    ].agg(["mean", "min"])
    summary["high_vol_rebalance_count"] = summary["strategy_id"].map(stats["sum"])
    summary["high_vol_rebalance_fraction"] = summary["strategy_id"].map(stats["mean"])
    summary["below_full_investment_fraction"] = summary[
        "high_vol_rebalance_fraction"
    ]
    summary["average_q4_target_risk_allocation"] = summary["strategy_id"].map(
        q4_stats["mean"]
    )
    summary["minimum_q4_target_risk_allocation"] = summary["strategy_id"].map(
        q4_stats["min"]
    )


def _naked_book_diagnostics(
    *,
    strategy: StrategySpec,
    primary_cost: float,
    path: _G33RegimePath,
    rebalances: pd.DataFrame,
) -> list[dict[str, object]]:
    primary_borrow = (
        0.0 if strategy.portfolio_mode is PortfolioMode.LONG_ONLY else 0.01
    )
    first = path.regime.iloc[0]
    values: dict[str, object] = {
        "naked_book_observations": float(path.naked_observations),
        "formal_signal_rows": float(len(rebalances)),
        "q4_signal_rows": float(rebalances["high_volatility"].sum()),
        "minimum_signal_allocation": float(
            rebalances["target_risk_allocation"].min()
        ),
        "t0_book_return": float(first["book_return"]),
        "t0_ewma_variance": float(first["ewma_variance"]),
    }
    return [
        {
            "group_id": "G33",
            "strategy_id": strategy.strategy_id,
            "portfolio_mode": strategy.portfolio_mode.value,
            "variant_id": "base",
            "cost_bps": float(primary_cost),
            "borrow_fee_annual": primary_borrow,
            "scope": "naked_book_audit",
            "diagnostic": label,
            "value": value,
        }
        for label, value in values.items()
    ]


def _daily_naked_regime_diagnostics(
    *,
    strategy: StrategySpec,
    primary_cost: float,
    regime: pd.DataFrame,
) -> list[dict[str, object]]:
    primary_borrow = (
        0.0 if strategy.portfolio_mode is PortfolioMode.LONG_ONLY else 0.01
    )
    frame = regime.reset_index()
    frame.rename(columns={frame.columns[0]: "date"}, inplace=True)
    frame["group_id"] = "G33"
    frame["strategy_id"] = strategy.strategy_id
    frame["portfolio_mode"] = strategy.portfolio_mode.value
    frame["variant_id"] = "base"
    frame["cost_bps"] = float(primary_cost)
    frame["borrow_fee_annual"] = primary_borrow
    frame["scope"] = "daily_naked_regime"
    frame["diagnostic"] = "causal_book_forecast_state"
    frame["value"] = frame["target_risk_allocation"]
    return frame.to_dict(orient="records")


def _append_pnl_attribution_diagnostics(
    *,
    strategy: StrategySpec,
    result: object,
    cost_bps: float,
    borrow_fee: float,
    diagnostic_rows: list[dict[str, object]],
) -> None:
    """Record a daily-closing P&L bridge and fail if it does not close."""

    nav = result.nav.sort_index().copy()  # type: ignore[attr-defined]
    rebalances = result.rebalances.copy()  # type: ignore[attr-defined]
    index = pd.DatetimeIndex(nav.index).normalize()
    if not index.is_unique or not index.is_monotonic_increasing:
        raise DataQualityError("G33 attribution requires a unique ordered NAV")
    flow_long = pd.Series(0.0, index=index)
    flow_short = pd.Series(0.0, index=index)
    transaction_cost = pd.Series(0.0, index=index)
    if not rebalances.empty:
        execution_dates = pd.DatetimeIndex(
            pd.to_datetime(rebalances["execution_date"], errors="raise")
        ).normalize()
        if execution_dates.has_duplicates or len(execution_dates.difference(index)):
            raise DataQualityError("G33 attribution rebalance dates are invalid")
        pretrade_nav = pd.to_numeric(
            rebalances["pretrade_nav"], errors="raise"
        ).to_numpy(dtype=float)
        postcost_nav = pd.to_numeric(
            rebalances["postcost_nav"], errors="raise"
        ).to_numpy(dtype=float)
        flow_long.loc[execution_dates] = (
            postcost_nav
            * pd.to_numeric(
                rebalances["target_long_exposure"], errors="raise"
            ).to_numpy(dtype=float)
            - pretrade_nav
            * pd.to_numeric(
                rebalances["pretrade_long_exposure"], errors="raise"
            ).to_numpy(dtype=float)
        )
        flow_short.loc[execution_dates] = (
            postcost_nav
            * pd.to_numeric(
                rebalances["target_short_exposure"], errors="raise"
            ).to_numpy(dtype=float)
            - pretrade_nav
            * pd.to_numeric(
                rebalances["pretrade_short_exposure"], errors="raise"
            ).to_numpy(dtype=float)
        )
        transaction_cost.loc[execution_dates] = pd.to_numeric(
            rebalances["cost_amount"], errors="raise"
        ).to_numpy(dtype=float)

    long_value = pd.to_numeric(nav["long_value"], errors="raise").astype(float)
    short_value = pd.to_numeric(nav["short_value"], errors="raise").astype(float)
    long_pnl = long_value - long_value.shift(fill_value=0.0) - flow_long
    short_pnl = -(short_value - short_value.shift(fill_value=0.0) - flow_short)
    borrow_amount = pd.to_numeric(
        nav["short_borrow_fee_amount"], errors="raise"
    ).astype(float)
    rf_return = pd.to_numeric(nav["rf_return"], errors="raise").astype(float)
    cash_value = pd.to_numeric(nav["cash_value"], errors="raise").astype(float)
    cash_before_interest = (cash_value + borrow_amount) / (1.0 + rf_return)
    t_bill_pnl = cash_before_interest * rf_return
    nav_value = pd.to_numeric(nav["nav"], errors="raise").astype(float)
    total_pnl = nav_value - nav_value.shift(fill_value=1.0)
    base_attribution = (
        long_pnl + short_pnl + t_bill_pnl - transaction_cost - borrow_amount
    )
    raw_bridge = total_pnl - base_attribution
    evidenced_dates = pd.DatetimeIndex([])
    corporate_events = result.corporate_action_events  # type: ignore[attr-defined]
    if not corporate_events.empty and "apply_session" in corporate_events:
        applied = corporate_events
        if "status" in applied:
            applied = applied.loc[applied["status"].astype(str).eq("applied")]
        evidenced_dates = evidenced_dates.union(
            pd.DatetimeIndex(
                pd.to_datetime(applied["apply_session"], errors="raise")
            ).normalize()
        )
    valuation_fallbacks = result.valuation_fallbacks  # type: ignore[attr-defined]
    if not valuation_fallbacks.empty and "date" in valuation_fallbacks:
        evidenced_dates = evidenced_dates.union(
            pd.DatetimeIndex(
                pd.to_datetime(valuation_fallbacks["date"], errors="raise")
            ).normalize()
        )
    if not rebalances.empty:
        terminal = pd.to_numeric(
            rebalances.get(
                "terminal_liquidation_count", pd.Series(0, index=rebalances.index)
            ),
            errors="coerce",
        ).fillna(0.0)
        terminal_dates = pd.DatetimeIndex(
            pd.to_datetime(
                rebalances.loc[terminal.gt(0.0), "execution_date"], errors="raise"
            )
        ).normalize()
        evidenced_dates = evidenced_dates.union(terminal_dates)
    evidenced = pd.Series(index.isin(evidenced_dates), index=index, dtype=bool)
    action_and_execution_bridge = raw_bridge.where(evidenced, 0.0)
    unexplained_bridge = raw_bridge.where(~evidenced, 0.0)
    maximum_unexplained = float(unexplained_bridge.abs().max())
    if maximum_unexplained > 1e-10:
        offending = unexplained_bridge.abs().nlargest(5)
        raise DataQualityError(
            "G33 P&L attribution has an unsupported daily bridge: "
            f"{offending.to_dict()}"
        )
    attributed = base_attribution + action_and_execution_bridge + unexplained_bridge
    closure = total_pnl - attributed
    numeric = pd.concat(
        [
            total_pnl,
            long_pnl,
            short_pnl,
            t_bill_pnl,
            transaction_cost,
            borrow_amount,
            action_and_execution_bridge,
            unexplained_bridge,
            closure,
        ],
        axis=1,
    )
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise DataQualityError("G33 P&L attribution contains non-finite values")
    maximum_error = float(closure.abs().max())
    if maximum_error > 1e-12:
        raise DataQualityError(
            f"G33 daily P&L attribution does not close: {maximum_error:.3e}"
        )
    result.nav["pnl_total"] = total_pnl  # type: ignore[attr-defined]
    result.nav["pnl_long_risk"] = long_pnl  # type: ignore[attr-defined]
    result.nav["pnl_short_risk"] = short_pnl  # type: ignore[attr-defined]
    result.nav["pnl_t_bill"] = t_bill_pnl  # type: ignore[attr-defined]
    result.nav["pnl_transaction_cost"] = -transaction_cost  # type: ignore[attr-defined]
    result.nav["pnl_short_borrow_fee"] = -borrow_amount  # type: ignore[attr-defined]
    result.nav["pnl_action_execution_bridge"] = (  # type: ignore[attr-defined]
        action_and_execution_bridge
    )
    result.nav["pnl_unexplained_bridge"] = unexplained_bridge  # type: ignore[attr-defined]
    result.nav["pnl_bridge_has_frozen_evidence"] = evidenced.astype(int)  # type: ignore[attr-defined]
    result.nav["pnl_attributed"] = attributed  # type: ignore[attr-defined]
    result.nav["pnl_closure_error"] = closure  # type: ignore[attr-defined]
    values = {
        "total_pnl": float(total_pnl.sum()),
        "long_risk_pnl": float(long_pnl.sum()),
        "short_risk_pnl": float(short_pnl.sum()),
        "action_and_execution_bridge_pnl": float(action_and_execution_bridge.sum()),
        "unexplained_bridge_pnl": float(unexplained_bridge.sum()),
        "total_risk_asset_pnl": float(
            (
                long_pnl
                + short_pnl
                + action_and_execution_bridge
                + unexplained_bridge
            ).sum()
        ),
        "t_bill_pnl": float(t_bill_pnl.sum()),
        "transaction_cost_pnl": -float(transaction_cost.sum()),
        "short_borrow_fee_pnl": -float(borrow_amount.sum()),
        "attributed_pnl": float(attributed.sum()),
        "maximum_abs_daily_closure_error": maximum_error,
        "maximum_abs_daily_action_execution_bridge": float(
            action_and_execution_bridge.abs().max()
        ),
        "maximum_abs_daily_unexplained_bridge": maximum_unexplained,
    }
    identity = {
        "group_id": "G33",
        "strategy_id": strategy.strategy_id,
        "portfolio_mode": strategy.portfolio_mode.value,
        "variant_id": "base",
        "cost_bps": float(cost_bps),
        "borrow_fee_annual": float(borrow_fee),
    }
    diagnostic_rows.extend(
        {
            **identity,
            "scope": "pnl_attribution",
            "diagnostic": label,
            "value": value,
        }
        for label, value in values.items()
    )


def _frozen_parameters(context: ExperimentRunContext) -> dict[str, object]:
    raw = context.group.raw.get("parameters")
    dates = context.group.program.raw.get("dates")
    if not isinstance(raw, dict) or not isinstance(dates, dict):
        raise ValueError("G33 parameters or program dates are missing")
    expected: dict[str, object] = {
        "forecast_model": "ewma",
        "ewma_decay": 0.94,
        "forecast_horizon_sessions": 21,
        "state_history_sessions": 756,
        "high_vol_quantile": 0.75,
        "state_rule": "strict_q4_no_hysteresis",
        "q4_scale_rule": "min_1_q75_over_sigma",
        "book_source": "matching_G00_portfolio_mode",
    }
    for key, value in expected.items():
        if raw.get(key) != value:
            raise ValueError(f"G33 frozen parameter changed: {key}={raw.get(key)!r}")
    history_start = str(dates.get("strategy_forecast_history_start", ""))
    if history_start != "2014-06-30":
        raise ValueError(f"G33 frozen history start changed: {history_start!r}")
    return {
        "ewma_decay": 0.94,
        "forecast_horizon_sessions": 21,
        "history_sessions": 756,
        "history_start": history_start,
    }


def _validate_frozen_inputs(
    context: ExperimentRunContext, data: LoadedExperimentData
) -> None:
    _frozen_parameters(context)
    if context.dataset_version != _FROZEN_DATASET_VERSION:
        raise DataQualityError("G33 requires the frozen v3 dataset version")
    if data.dataset_manifest_sha256 != _FROZEN_DATASET_MANIFEST_SHA256:
        raise DataQualityError("G33 frozen dataset manifest hash changed")
    freeze_record = context.data_root / "curated" / context.dataset_version / "FROZEN.json"
    if (
        not freeze_record.is_file()
        or sha256_file(freeze_record) != _FROZEN_RECORD_SHA256
    ):
        raise DataQualityError("G33 frozen dataset record hash changed")
    design = (
        context.project_root
        / "docs"
        / "20_experiments"
        / "G33_book_forecast_derisk"
        / "design.md"
    )
    if not design.is_file():
        raise FileNotFoundError(design)
    if sha256_file(design) != _FROZEN_DESIGN_SHA256:
        raise DataQualityError("G33 preregistered design hash changed")
    implementation_note = design.with_name("implementation_note.md")
    if not implementation_note.is_file():
        raise FileNotFoundError(implementation_note)
    if sha256_file(implementation_note) != _FROZEN_IMPLEMENTATION_NOTE_SHA256:
        raise DataQualityError("G33 frozen implementation note hash changed")
    program = context.project_root / "config" / "experiments" / "program.toml"
    if not program.is_file() or sha256_file(program) != _FROZEN_PROGRAM_SHA256:
        raise DataQualityError("G33 frozen experiment program hash changed")
    if sha256_file(context.group.path) != _FROZEN_GROUP_CONFIG_SHA256:
        raise DataQualityError("G33 frozen group config hash changed")
    costs = context.group.program.raw.get("costs")
    long_short = context.group.program.raw.get("long_short")
    if not isinstance(costs, dict) or not isinstance(long_short, dict):
        raise DataQualityError("G33 frozen cost tables are missing")
    if (
        costs.get("scenarios_bps") != [0, 5, 10, 20]
        or costs.get("weekly_primary_bps") != 10
        or costs.get("monthly_primary_bps") != 5
        or long_short.get("borrow_fee_scenarios_annual") != [0.0, 0.01, 0.03]
        or long_short.get("primary_borrow_fee_annual") != 0.01
    ):
        raise DataQualityError("G33 frozen cost or borrow-fee contract changed")


def _validate_reference_anchor(reference_g00: _G00Reference) -> None:
    if (
        reference_g00.manifest.get("run_id") != "g00-frozen-v3-v1"
        or reference_g00.manifest_sha256 != _FROZEN_G00_MANIFEST_SHA256
    ):
        raise DataQualityError("G33 G00 reference is not the frozen v3-v1 bundle")


def _validate_runtime_roots(config: G33RunConfig) -> None:
    data_root = config.context.data_root.resolve()
    output_root = config.context.output_root.resolve()
    project_root = config.context.project_root.resolve()
    if (
        data_root.name.lower() != "data"
        or output_root.name.lower() != "results"
        or data_root.parent != output_root.parent
    ):
        raise DataQualityError("G33 requires sibling data/results local runtime roots")
    try:
        output_root.relative_to(project_root)
    except ValueError:
        pass
    else:
        raise DataQualityError("G33 full bundle cannot be written inside the repository")
    reference_root = config.reference_g00_root.resolve()
    try:
        reference_root.relative_to(output_root)
    except ValueError as error:
        raise DataQualityError(
            "G33 G00 reference must come from the local results root"
        ) from error


def _validate_g00_path_identity(
    rebalances: pd.DataFrame,
    holdings: pd.DataFrame,
    reference: _G00Reference,
) -> None:
    """Hard-gate G33 as G00 targets multiplied only by causal allocation."""

    candidate_rebalances = rebalances.copy()
    baseline_rebalances = reference.rebalances.copy()
    for frame in (candidate_rebalances, baseline_rebalances):
        frame["signal_date"] = pd.to_datetime(frame["signal_date"]).dt.normalize()
        frame["execution_date"] = pd.to_datetime(
            frame["execution_date"]
        ).dt.normalize()
    baseline_rebalances["strategy_id"] = baseline_rebalances[
        "strategy_id"
    ].astype(str).str.replace(r"^G00__", "G33__", regex=True)
    rebalance_key = ["strategy_id", "signal_date"]
    if (
        candidate_rebalances.duplicated(rebalance_key).any()
        or baseline_rebalances.duplicated(rebalance_key).any()
    ):
        raise DataQualityError("G33/G00 reference rebalances are not unique")
    candidate_indexed = candidate_rebalances.set_index(rebalance_key).sort_index()
    baseline_indexed = baseline_rebalances.set_index(rebalance_key).sort_index()
    if not candidate_indexed.index.equals(baseline_indexed.index):
        raise DataQualityError("G33 and G00 rebalance identities differ")
    for column in ("execution_date", "execution_status"):
        if not candidate_indexed[column].equals(baseline_indexed[column]):
            raise DataQualityError(f"G33 and G00 {column} paths differ")
    for column in (
        "requested_selected_count",
        "requested_selected_sids",
        "missing_target_count",
        "missing_target_sids",
    ):
        if column in candidate_indexed and column in baseline_indexed:
            left = candidate_indexed[column].fillna("").astype(str)
            right = baseline_indexed[column].fillna("").astype(str)
            if not left.equals(right):
                raise DataQualityError(f"G33 and G00 {column} paths differ")

    baseline_holdings = pd.read_parquet(
        reference.root / "artifacts" / "holdings.parquet"
    )
    candidate_holdings = holdings.copy()
    baseline_holdings["strategy_id"] = baseline_holdings["strategy_id"].astype(
        str
    ).str.replace(r"^G00__", "G33__", regex=True)
    holding_key = ["strategy_id", "signal_date", "execution_date", "sid"]
    for frame in (candidate_holdings, baseline_holdings):
        frame["signal_date"] = pd.to_datetime(frame["signal_date"]).dt.normalize()
        frame["execution_date"] = pd.to_datetime(
            frame["execution_date"]
        ).dt.normalize()
        frame["sid"] = frame["sid"].astype(str)
    if (
        candidate_holdings.duplicated(holding_key).any()
        or baseline_holdings.duplicated(holding_key).any()
    ):
        raise DataQualityError("G33/G00 target holdings are not unique")
    candidate_targets = candidate_holdings.set_index(holding_key).sort_index()
    baseline_targets = baseline_holdings.set_index(holding_key).sort_index()
    if not candidate_targets.index.equals(baseline_targets.index):
        raise DataQualityError("G33 and G00 selected holding identities differ")
    allocation = candidate_indexed["target_risk_allocation"]
    holding_rebalance_index = pd.MultiIndex.from_arrays(
        [
            candidate_targets.index.get_level_values("strategy_id"),
            candidate_targets.index.get_level_values("signal_date"),
        ],
        names=rebalance_key,
    )
    holding_allocation = allocation.reindex(holding_rebalance_index)
    if holding_allocation.isna().any():
        raise DataQualityError("G33 holding allocation lacks a rebalance state")
    expected = (
        pd.to_numeric(
            baseline_targets["target_weight"], errors="coerce"
        ).to_numpy(dtype=float)
        * holding_allocation.to_numpy(dtype=float)
    )
    actual = pd.to_numeric(
        candidate_targets["target_weight"], errors="coerce"
    ).to_numpy(dtype=float)
    if (
        not np.isfinite(expected).all()
        or not np.isfinite(actual).all()
        or not np.allclose(actual, expected, rtol=0.0, atol=1e-12)
    ):
        raise DataQualityError("G33 target weights are not scalar multiples of G00")


def _validate_main_counts(summary: pd.DataFrame, context: ExperimentRunContext) -> None:
    if len(context.strategies) != 36 or len(summary) != 288:
        raise RuntimeError(
            "G33 requires 36 core paths and 288 scenarios, got "
            f"{len(context.strategies)} and {len(summary)}"
        )
    key = ["strategy_id", "variant_id", "cost_bps", "borrow_fee_annual"]
    if summary.duplicated(key).any():
        raise RuntimeError("G33 summary contains duplicate scenario identities")
    if int(summary["is_primary_scenario"].sum()) != 36:
        raise RuntimeError("G33 must contain exactly 36 primary scenarios")
    mode_counts = summary.groupby("portfolio_mode", observed=True).size().to_dict()
    if mode_counts != {"long_only": 72, "long_short": 216}:
        raise RuntimeError(f"G33 scenario mode counts changed: {mode_counts}")


def _validate_artifact_counts(
    artifacts: dict[str, pd.DataFrame], data: LoadedExperimentData
) -> None:
    nav = artifacts["nav"]
    if len(data.evaluation_sessions) != 2_134 or len(nav) != 614_592:
        raise RuntimeError(
            "G33 requires 2134 sessions and 614592 NAV rows, got "
            f"{len(data.evaluation_sessions)} and {len(nav)}"
        )
    key = ["strategy_id", "cost_bps", "borrow_fee_annual"]
    counts = nav.groupby(key, observed=True).size()
    if len(counts) != 288 or not counts.eq(2_134).all():
        raise RuntimeError("G33 every scenario must contain exactly 2134 NAV rows")
    daily = artifacts["diagnostics"].loc[
        artifacts["diagnostics"]["scope"].eq("daily_naked_regime")
    ]
    if len(daily) != 108_648:
        raise RuntimeError(
            f"G33 requires 108648 persisted daily forecast rows, got {len(daily)}"
        )
    dates = pd.DatetimeIndex(pd.to_datetime(daily["date"], errors="raise"))
    if dates.min() != pd.Timestamp("2014-06-30") or dates.max() != data.evaluation_end:
        raise DataQualityError("G33 persisted forecast rows escaped the frozen interval")


def _attach_g00_comparisons(
    summary: pd.DataFrame, reference: pd.DataFrame
) -> pd.DataFrame:
    lookup = reference.set_index(["strategy_id", "cost_bps", "borrow_fee_annual"])
    if not lookup.index.is_unique:
        raise DataQualityError("G00 reference contains duplicate scenario identities")
    rows: list[dict[str, object]] = []
    for index, row in summary.iterrows():
        strategy_id = str(row["strategy_id"])
        parent = strategy_id.replace("G33__", "G00__", 1)
        if parent == strategy_id:
            raise DataQualityError(f"invalid G33 strategy identity: {strategy_id}")
        key = (parent, float(row["cost_bps"]), float(row["borrow_fee_annual"]))
        if key not in lookup.index:
            raise DataQualityError(f"G33 lacks matching G00 scenario: {key}")
        baseline = lookup.loc[key]
        for metric in _COMPARISON_METRICS:
            delta = float(row[metric] - baseline[metric])
            summary.at[index, f"delta_vs_g00_{metric}"] = delta
            rows.append(
                {
                    "group_id": "G33",
                    "strategy_id": strategy_id,
                    "portfolio_mode": row["portfolio_mode"],
                    "variant_id": row["variant_id"],
                    "cost_bps": float(row["cost_bps"]),
                    "borrow_fee_annual": float(row["borrow_fee_annual"]),
                    "reference_strategy_id": parent,
                    "comparison_type": "delta_vs_same_scenario_G00",
                    "metric": metric,
                    "estimate": delta,
                }
            )
    comparison = pd.DataFrame(rows)
    if len(comparison) != 1_440:
        raise RuntimeError(
            f"G33 must record 1440 G00 comparison rows, got {len(comparison)}"
        )
    return comparison


def _stable_artifact_sort(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    preferred = {
        "nav": ["strategy_id", "cost_bps", "borrow_fee_annual", "date"],
        "rebalances": ["strategy_id", "borrow_fee_annual", "signal_date"],
        "holdings": [
            "strategy_id",
            "cost_bps",
            "borrow_fee_annual",
            "signal_date",
            "sid",
        ],
        "trades": [
            "strategy_id",
            "cost_bps",
            "borrow_fee_annual",
            "execution_date",
            "sid",
        ],
        "diagnostics": [
            "strategy_id",
            "cost_bps",
            "borrow_fee_annual",
            "scope",
            "diagnostic",
            "date",
        ],
    }[name]
    keys = [column for column in preferred if column in frame.columns]
    return frame.sort_values(keys, kind="mergesort", ignore_index=True)


def _manifest_metadata(
    *,
    config: G33RunConfig,
    data: LoadedExperimentData,
    reference_g00: _G00Reference,
    summary: pd.DataFrame,
    comparison: pd.DataFrame,
    rebalances: pd.DataFrame,
) -> dict[str, object]:
    project = config.context.project_root
    design = (
        project
        / "docs"
        / "20_experiments"
        / "G33_book_forecast_derisk"
        / "design.md"
    )
    implementation_note = design.with_name("implementation_note.md")
    program = project / "config" / "experiments" / "program.toml"
    signal_rows = rebalances.drop_duplicates(["strategy_id", "signal_date"])
    naked_history_sessions = int(
        (
            (data.sessions >= pd.Timestamp("2014-06-30"))
            & (data.sessions <= data.evaluation_end)
        ).sum()
    )
    weekly_signal, weekly_start = forecast_engine_start(
        data.sessions, "weekly", "2014-06-30"
    )
    monthly_signal, monthly_start = forecast_engine_start(
        data.sessions, "monthly", "2014-06-30"
    )
    blockers = {str(value) for value in data.dataset_manifest.get("formal_blockers", [])}
    blockers.add("systematic_G33_bundle_is_prototype")
    if data.dataset_status != "valid":
        blockers.add(f"dataset_status_{data.dataset_status}")
    return {
        "research_tier": "prototype",
        "formal_run_eligible": False,
        "formal_blockers": sorted(blockers),
        "dataset": {
            "status": data.dataset_status,
            "research_tier": data.dataset_research_tier,
            "manifest_sha256": data.dataset_manifest_sha256,
            "review_override_explicit": config.allow_review_dataset,
        },
        "evaluation": {
            "start_open": str(data.evaluation_start.date()),
            "end_close": str(data.evaluation_end.date()),
            "sessions": len(data.evaluation_sessions),
        },
        "reference_g00": {
            "run_id": reference_g00.manifest.get("run_id"),
            "manifest_sha256": reference_g00.manifest_sha256,
            "comparison_rows": len(comparison),
            "role": "only formal reference and only prior experiment runtime input",
        },
        "preregistration": {
            "design_path": str(design.relative_to(project).as_posix()),
            "design_sha256": sha256_file(design),
            "implementation_note_path": str(
                implementation_note.relative_to(project).as_posix()
            ),
            "implementation_note_sha256": sha256_file(implementation_note),
            "program_sha256": sha256_file(program),
            "group_config_sha256": sha256_file(config.context.group.path),
            "freeze_record_sha256": _FROZEN_RECORD_SHA256,
        },
        "regime": {
            "risk_source": "matching naked-book causal EWMA forecast volatility",
            "forecast_history_start": "2014-06-30",
            "forecast_history_sessions": naked_history_sessions,
            "seed_by_frequency": {
                "weekly": {
                    "signal_date": (
                        None if weekly_signal is None else str(weekly_signal.date())
                    ),
                    "engine_start": str(weekly_start.date()),
                },
                "monthly": {
                    "signal_date": str(monthly_signal.date()),  # type: ignore[union-attr]
                    "engine_start": str(monthly_start.date()),
                },
            },
            "pre_forecast_returns_used_by_ewma": 0,
            "naked_book_cost_bps": 0.0,
            "naked_book_borrow_fee_annual": 0.0,
            "naked_book_cash_return": 0.0,
            "forecast_model": "ewma",
            "ewma_decay": 0.94,
            "ewma_adjust": False,
            "forecast_horizon_sessions": 21,
            "forecast_horizon_variance": "21 * current EWMA daily variance",
            "forecast_annualization": "sqrt(252 * current EWMA daily variance)",
            "threshold": "strictly greater than lagged rolling 756-session q75",
            "hysteresis": False,
            "normal_state_allocation": 1.0,
            "q4_scale_rule": "min(1, lagged_q75 / current_forecast_sigma)",
            "signal_q4_fraction": float(signal_rows["high_volatility"].mean()),
            "minimum_signal_allocation": float(
                signal_rows["target_risk_allocation"].min()
            ),
        },
        "counts": {
            "core_strategies": len(config.context.strategies),
            "main_scenarios": len(summary),
            "primary_scenarios": int(summary["is_primary_scenario"].sum()),
            "primary_q4_rebalances": int(rebalances["high_volatility"].sum()),
            "naked_book_paths": len(config.context.strategies),
            "daily_naked_regime_rows": (
                naked_history_sessions * len(config.context.strategies)
            ),
        },
        "accounting": {
            "long_only_cash": "unallocated formal-run cash compounds at daily T-bill RF",
            "long_short_collateral": "formal-run cash compounds at daily T-bill RF",
            "risk_allocation_timing": "signal close determines next-open target and holds until next rebalance",
            "pnl_attribution": (
                "daily long/short risk, T-bill, transaction cost, borrow fee, "
                "and action/execution bridge close exactly to NAV P&L"
            ),
        },
        "execution": {
            "worker_processes": config.workers,
            "cost_scenarios": "exact homogeneous-NAV replay from zero-cost event paths",
            "naked_event_paths_simulated": 36,
            "formal_event_paths_simulated": 72,
            "reported_scenarios": len(summary),
        },
        "runtime_code": {
            "g33_sha256": sha256_file(Path(__file__)),
            "g21_reference_helper_sha256": sha256_file(Path(__file__).with_name("g21.py")),
            "g00_accounting_helper_sha256": sha256_file(Path(__file__).with_name("g00.py")),
            "bundle_sha256": sha256_file(Path(__file__).with_name("bundle.py")),
            "run_context_sha256": sha256_file(Path(__file__).with_name("run_context.py")),
            "engine_sha256": sha256_file(
                Path(__file__).resolve().parents[1] / "backtest" / "engine.py"
            ),
            "calendar_sha256": sha256_file(
                Path(__file__).resolve().parents[1] / "backtest" / "calendar.py"
            ),
        },
        "limitations": [
            "free-research dataset and SPY total-return proxy",
            "EWMA forecast is a frozen rule-based conditional variance estimate",
            "strict Q4 naked-book forecast derisking is a mechanism test, not a deployment claim",
        ],
    }
