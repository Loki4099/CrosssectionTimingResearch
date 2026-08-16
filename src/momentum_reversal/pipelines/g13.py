"""G13 continuous unlevered scaling driven by causal naked-book EWMA forecasts."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
import gc
from pathlib import Path

import numpy as np
import pandas as pd

from momentum_reversal.backtest import BaselineBacktester, replay_linear_cost
from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.storage import sha256_file
from momentum_reversal.experiments import PortfolioMode, StrategySpec
from momentum_reversal.experiments.spec import toml_dumps

from .bundle import BundleWriteResult, write_experiment_bundle
from .g00 import (
    _concat_or_empty,
    _frozen_borrow_fees,
    _frozen_costs,
    _primary_cost,
    _validate_signed_execution_audit,
    _winner_loser_generator,
    annual_borrow_fee_to_daily,
)
from .g11 import (
    _G11CoreBatch,
    _stable_artifact_sort,
    _validate_core_path_state_identity,
    _validate_g00_path_identity as _validate_g11_g00_path_identity,
    _validate_main_counts,
)
from .g21 import (
    _COMPARISON_METRICS,
    _G00Reference,
    _load_g00_reference,
    _partition_strategies,
    _signal_dates_by_frequency,
)
from .g33 import (
    _G33RegimePath,
    _append_g33_scenario,
    _attach_forecast_regime_audit,
    _daily_naked_regime_diagnostics,
    _naked_book_diagnostics,
    forecast_engine_start,
    forecast_input_returns,
    strict_lagged_book_forecast_quartiles,
)
from .run_context import ExperimentRunContext, LoadedExperimentData, load_experiment_data


_FROZEN_DESIGN_SHA256 = (
    "076204a90a40eef4b41ed843ebe0c8ddb05b56058856c603d3157465a876abd3"
)
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
    "1a66e1b2dfacfccad7d90c3780d4c7dd8bb71931e2c77380c0a39acbb8386654"
)
_FROZEN_G00_MANIFEST_SHA256 = (
    "8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66"
)
_AUDIT_START = pd.Timestamp("2014-06-30")


@dataclass(frozen=True, slots=True)
class G13RunConfig:
    context: ExperimentRunContext
    reference_g00_root: Path
    allow_review_dataset: bool = False
    workers: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_g00_root", Path(self.reference_g00_root))
        if self.context.group_id != "G13":
            raise ValueError("G13 runner requires the registered G13 spec")
        if isinstance(self.workers, bool) or not isinstance(self.workers, int):
            raise ValueError("G13 workers must be an integer")
        if self.workers <= 0 or self.workers > 8:
            raise ValueError("G13 workers must be between 1 and 8")


@dataclass(frozen=True, slots=True)
class G13RunResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    strategy_count: int
    scenario_count: int
    comparison_count: int
    scaled_rebalance_count: int
    formal_run_eligible: bool


def continuous_forecast_allocation(
    regime: pd.DataFrame,
    signal_dates: pd.Index | None = None,
    *,
    annual_target_volatility: float = 0.15,
    maximum_scale: float = 1.0,
) -> pd.Series:
    """Return ``min(1, 0.15 / current naked-book EWMA forecast sigma)``.

    Warm-up observations may be missing only when the unsampled daily series is
    requested.  Every requested signal date must be finite and positive.
    Quartile columns are deliberately ignored by the allocation rule.
    """

    if not isinstance(regime, pd.DataFrame):
        raise TypeError("regime must be a pandas DataFrame")
    if "book_forecast_volatility" not in regime:
        raise ValueError("regime is missing book_forecast_volatility")
    if not isinstance(regime.index, pd.DatetimeIndex):
        raise ValueError("regime must use a DatetimeIndex")
    if (
        regime.index.tz is not None
        or regime.index.has_duplicates
        or not regime.index.is_monotonic_increasing
    ):
        raise ValueError("regime index must be timezone-naive, unique, and increasing")
    if not np.isclose(annual_target_volatility, 0.15, rtol=0.0, atol=1e-15):
        raise ValueError("G13 requires annual_target_volatility=0.15")
    if not np.isclose(maximum_scale, 1.0, rtol=0.0, atol=1e-15):
        raise ValueError("G13 requires maximum_scale=1.0")

    forecast = pd.to_numeric(regime["book_forecast_volatility"], errors="coerce")
    observed = forecast.notna()
    invalid = observed & (~np.isfinite(forecast) | forecast.le(0.0))
    if invalid.any():
        raise DataQualityError(
            "G13 naked-book forecast volatility must be finite and positive "
            f"when available: {forecast.index[invalid][:5].tolist()}"
        )
    allocation = pd.Series(np.nan, index=regime.index, dtype=float)
    allocation.loc[observed] = (
        annual_target_volatility / forecast.loc[observed]
    ).clip(upper=maximum_scale)
    allocation.name = "target_risk_allocation"
    if signal_dates is None:
        return allocation

    dates = pd.DatetimeIndex(pd.to_datetime(signal_dates)).normalize()
    if dates.tz is not None or dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("signal dates must be timezone-naive, unique, and increasing")
    sampled = allocation.reindex(dates)
    invalid_signal = sampled.isna() | ~np.isfinite(sampled) | sampled.le(0.0)
    if invalid_signal.any():
        raise DataQualityError(
            "G13 allocation is unavailable on signal dates: "
            f"{dates[invalid_signal][:5].tolist()}"
        )
    sampled.index = dates
    return sampled.astype(float)


def run_g13(config: G13RunConfig) -> G13RunResult:
    """Run and freeze the 36-path G13 continuous naked-book forecast grid."""

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
            _run_g13_core_batch(
                context=config.context,
                data=data,
                strategies=strategies,
            )
        ]
    else:
        partitions = _partition_strategies(strategies, config.workers)
        batches: list[_G11CoreBatch] = []
        with ProcessPoolExecutor(max_workers=len(partitions)) as executor:
            futures = [
                executor.submit(
                    _run_g13_core_batch_worker,
                    config.context,
                    config.allow_review_dataset,
                    tuple(strategy.strategy_id for strategy in partition),
                )
                for partition in partitions
            ]
            for future in as_completed(futures):
                batches.append(future.result())
                print(
                    "completed_G13_core_paths="
                    f"{sum(batch.strategy_count for batch in batches)}/{len(strategies)}",
                    flush=True,
                )

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
    if not summary["valid_scenario"].eq(True).all():
        raise DataQualityError("G13 completion gate found invalid scenarios")
    comparison = _attach_g00_comparisons(summary, reference_g00.summary)
    scaled_count = int(
        pd.to_numeric(
            artifacts["rebalances"]["target_risk_allocation"], errors="raise"
        ).lt(1.0).sum()
    )
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
            diagnostics=artifacts["diagnostics"],
        ),
        resolved_config_toml=_render_g13_resolved_config_toml(
            config=config,
            data=data,
            reference_g00=reference_g00,
        ),
    )
    return G13RunResult(
        run_id=config.context.run_id,
        output_dir=bundle.output_dir,
        manifest_path=bundle.manifest_path,
        strategy_count=len(strategies),
        scenario_count=len(summary),
        comparison_count=len(comparison),
        scaled_rebalance_count=scaled_count,
        formal_run_eligible=False,
    )


def _run_g13_core_batch_worker(
    context: ExperimentRunContext,
    allow_review_dataset: bool,
    strategy_ids: tuple[str, ...],
) -> _G11CoreBatch:
    data = load_experiment_data(context, allow_review_dataset=allow_review_dataset)
    lookup = {strategy.strategy_id: strategy for strategy in context.strategies}
    try:
        strategies = tuple(lookup[strategy_id] for strategy_id in strategy_ids)
    except KeyError as error:
        raise ValueError(f"unknown G13 worker strategy: {error.args[0]}") from error
    return _run_g13_core_batch(context=context, data=data, strategies=strategies)


def _run_g13_core_batch(
    *,
    context: ExperimentRunContext,
    data: LoadedExperimentData,
    strategies: tuple[StrategySpec, ...],
) -> _G11CoreBatch:
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
                    strategy,
                    result,
                    cost_bps,
                    0.0,
                    primary_cost,
                    data,
                    benchmark,
                    summary_rows,
                    strategy_nav,
                    strategy_rebalances,
                    strategy_holdings,
                    strategy_trades,
                    diagnostic_rows,
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
                        strategy,
                        result,
                        cost_bps,
                        annual_borrow_fee,
                        primary_cost,
                        data,
                        benchmark,
                        summary_rows,
                        strategy_nav,
                        strategy_rebalances,
                        strategy_holdings,
                        strategy_trades,
                        diagnostic_rows,
                    )

        strategy_summary = pd.DataFrame(summary_rows).sort_values(
            ["strategy_id", "cost_bps", "borrow_fee_annual"], ignore_index=True
        )
        rebalances = _concat_or_empty(strategy_rebalances, "rebalances")
        _attach_forecast_regime_audit(strategy_summary, rebalances, path.regime)
        audit_dates = pd.DatetimeIndex(
            pd.to_datetime(rebalances["signal_date"], errors="raise")
        ).normalize()
        audit_state = path.regime.reindex(audit_dates)
        for column in ("target_scaled_source_volatility", "cap_is_binding"):
            if audit_state[column].isna().any():
                raise DataQualityError(
                    f"G13 {column} is unavailable for a formal rebalance"
                )
            rebalances[column] = audit_state[column].to_numpy()
        rebalances["continuous_scale"] = pd.to_numeric(
            rebalances["target_risk_allocation"], errors="raise"
        )
        rebalances.drop(columns=["derisk_scale"], inplace=True)
        allocation_grouped = rebalances.groupby("strategy_id", observed=True)[
            "target_risk_allocation"
        ]
        allocation_stats = allocation_grouped.agg(["mean", "min", "max"])
        allocation_stats["below_full"] = allocation_grouped.apply(
            lambda values: values.lt(1.0).mean()
        )
        strategy_summary["average_target_risk_allocation"] = strategy_summary[
            "strategy_id"
        ].map(allocation_stats["mean"])
        strategy_summary["minimum_target_risk_allocation"] = strategy_summary[
            "strategy_id"
        ].map(allocation_stats["min"])
        strategy_summary["maximum_target_risk_allocation"] = strategy_summary[
            "strategy_id"
        ].map(allocation_stats["max"])
        strategy_summary["below_full_investment_fraction"] = strategy_summary[
            "strategy_id"
        ].map(allocation_stats["below_full"])
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
        for row in diagnostic_rows:
            row["group_id"] = "G13"
        summary_frames.append(strategy_summary)
        nav_frames.append(pd.concat(strategy_nav, ignore_index=True))
        rebalance_frames.append(rebalances)
        holding_frames.append(_concat_or_empty(strategy_holdings, "holdings"))
        trade_frames.append(_concat_or_empty(strategy_trades, "trades"))
        diagnostic_frames.append(pd.DataFrame(diagnostic_rows))

    del long_only_engine
    del long_short_engine
    gc.collect()
    return _G11CoreBatch(
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
                f"G13 naked book did not start on its seed execution for "
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
                f"G13 requires exactly 3018 forecast observations for "
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
        regime["target_risk_allocation"] = continuous_forecast_allocation(regime)
        regime["target_scaled_source_volatility"] = (
            regime["book_forecast_volatility"]
            * regime["target_risk_allocation"]
        )
        cap = pd.Series(pd.NA, index=regime.index, dtype="boolean")
        available = regime["book_forecast_volatility"].notna()
        cap.loc[available] = regime.loc[
            available, "book_forecast_volatility"
        ].le(0.15)
        regime["cap_is_binding"] = cap
        allocation = continuous_forecast_allocation(
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


def _validate_g00_path_identity(
    rebalances: pd.DataFrame,
    holdings: pd.DataFrame,
    reference: _G00Reference,
) -> None:
    """Apply the audited G11 scalar gate after a lossless G13 ID alias."""

    candidate_rebalances = rebalances.copy()
    candidate_holdings = holdings.copy()
    for frame in (candidate_rebalances, candidate_holdings):
        identifiers = frame["strategy_id"].astype(str)
        if not identifiers.str.startswith("G13__").all():
            raise DataQualityError("G13 artifact contains a non-G13 strategy identity")
        frame["strategy_id"] = identifiers.str.replace(
            r"^G13__", "G11__", regex=True
        )
    _validate_g11_g00_path_identity(
        candidate_rebalances, candidate_holdings, reference
    )


def _validate_artifact_counts(
    artifacts: dict[str, pd.DataFrame], data: LoadedExperimentData
) -> None:
    nav = artifacts["nav"]
    if len(data.evaluation_sessions) != 2_134 or len(nav) != 614_592:
        raise RuntimeError(
            "G13 requires 2134 sessions and 614592 NAV rows, got "
            f"{len(data.evaluation_sessions)} and {len(nav)}"
        )
    key = ["strategy_id", "cost_bps", "borrow_fee_annual"]
    counts = nav.groupby(key, observed=True).size()
    if len(counts) != 288 or not counts.eq(2_134).all():
        raise RuntimeError("G13 every scenario must contain exactly 2134 NAV rows")
    required_pnl = [
        "pnl_total",
        "pnl_long_risk",
        "pnl_short_risk",
        "pnl_t_bill",
        "pnl_transaction_cost",
        "pnl_short_borrow_fee",
        "pnl_action_execution_bridge",
        "pnl_unexplained_bridge",
        "pnl_bridge_has_frozen_evidence",
        "pnl_attributed",
        "pnl_closure_error",
    ]
    missing = set(required_pnl).difference(nav.columns)
    if missing:
        raise DataQualityError(f"G13 NAV attribution lacks columns: {sorted(missing)}")
    numeric = nav[required_pnl].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric).all().all():
        raise DataQualityError("G13 NAV attribution contains non-finite values")
    if numeric["pnl_unexplained_bridge"].abs().max() > 1e-10:
        raise DataQualityError("G13 unexplained daily P&L bridge exceeds tolerance")
    if numeric["pnl_closure_error"].abs().max() > 1e-12:
        raise DataQualityError("G13 daily P&L closure exceeds tolerance")
    unsupported = (
        numeric["pnl_action_execution_bridge"].abs().gt(1e-10)
        & numeric["pnl_bridge_has_frozen_evidence"].ne(1.0)
    )
    if unsupported.any():
        raise DataQualityError("G13 material action bridge lacks frozen evidence")
    _validate_daily_naked_regime(
        artifacts["diagnostics"], artifacts["rebalances"], data
    )


def _validate_daily_naked_regime(
    diagnostics: pd.DataFrame,
    rebalances: pd.DataFrame,
    data: LoadedExperimentData,
) -> None:
    daily = diagnostics.loc[diagnostics["scope"].eq("daily_naked_regime")].copy()
    required = {
        "date",
        "strategy_id",
        "book_return",
        "book_forecast_volatility",
        "ewma_variance",
        "forecast_variance_21",
        "lagged_q25",
        "lagged_q50",
        "lagged_q75",
        "volatility_quartile",
        "high_volatility",
        "target_risk_allocation",
        "target_scaled_source_volatility",
        "cap_is_binding",
    }
    missing = required.difference(daily.columns)
    if missing:
        raise DataQualityError(
            f"G13 daily naked regime lacks columns: {sorted(missing)}"
        )
    if len(daily) != 108_648:
        raise RuntimeError(
            f"G13 requires 108648 persisted daily risk rows, got {len(daily)}"
        )
    daily["date"] = pd.to_datetime(daily["date"], errors="raise").dt.normalize()
    if daily["date"].min() != _AUDIT_START or daily["date"].max() != data.evaluation_end:
        raise DataQualityError("G13 daily naked regime escaped the frozen interval")
    if daily.duplicated(["strategy_id", "date"]).any() or daily["strategy_id"].nunique() != 36:
        raise DataQualityError("G13 daily naked regime identities are invalid")
    counts = daily.groupby("strategy_id", observed=True).size()
    if not counts.eq(3_018).all():
        raise DataQualityError("G13 each path must persist exactly 3018 risk rows")

    authoritative = pd.DatetimeIndex(pd.to_datetime(data.sessions)).normalize()
    authoritative = authoritative[
        (authoritative >= _AUDIT_START) & (authoritative <= data.evaluation_end)
    ]
    for strategy_id, frame in daily.groupby("strategy_id", observed=True, sort=False):
        frame = frame.sort_values("date")
        dates = pd.DatetimeIndex(frame["date"])
        if not dates.equals(authoritative):
            raise DataQualityError(
                f"G13 daily dates differ from the calendar: {strategy_id}"
            )
        returns = pd.Series(
            pd.to_numeric(frame["book_return"], errors="coerce").to_numpy(dtype=float),
            index=dates,
        )
        expected = strict_lagged_book_forecast_quartiles(
            returns,
            ewma_decay=0.94,
            forecast_horizon_sessions=21,
            history_sessions=756,
        )
        expected["target_risk_allocation"] = continuous_forecast_allocation(expected)
        expected["target_scaled_source_volatility"] = (
            expected["book_forecast_volatility"]
            * expected["target_risk_allocation"]
        )
        cap = pd.Series(pd.NA, index=dates, dtype="boolean")
        available = expected["book_forecast_volatility"].notna()
        cap.loc[available] = expected.loc[
            available, "book_forecast_volatility"
        ].le(0.15)
        expected["cap_is_binding"] = cap
        for column in (
            "book_forecast_volatility",
            "ewma_variance",
            "forecast_variance_21",
            "lagged_q25",
            "lagged_q50",
            "lagged_q75",
            "target_risk_allocation",
            "target_scaled_source_volatility",
        ):
            actual = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
            wanted = pd.to_numeric(expected[column], errors="coerce").to_numpy(dtype=float)
            if not np.allclose(actual, wanted, rtol=0.0, atol=0.0, equal_nan=True):
                raise DataQualityError(
                    f"G13 stored daily {column} differs from direct calculation: "
                    f"{strategy_id}"
                )
        actual_quartile = pd.to_numeric(
            frame["volatility_quartile"], errors="coerce"
        ).to_numpy(dtype=float)
        wanted_quartile = pd.to_numeric(
            expected["volatility_quartile"], errors="coerce"
        ).to_numpy(dtype=float)
        if not np.allclose(
            actual_quartile,
            wanted_quartile,
            rtol=0.0,
            atol=0.0,
            equal_nan=True,
        ):
            raise DataQualityError(
                "G13 stored daily volatility_quartile differs from direct "
                f"calculation: {strategy_id}"
            )
        for column in ("high_volatility", "cap_is_binding"):
            actual = frame[column].astype("string").fillna("<NA>").reset_index(drop=True)
            wanted = expected[column].astype("string").fillna("<NA>").reset_index(drop=True)
            if not actual.equals(wanted):
                raise DataQualityError(
                    f"G13 stored daily {column} differs from direct calculation: "
                    f"{strategy_id}"
                )

    formal_required = [
        "book_forecast_volatility",
        "ewma_variance",
        "forecast_variance_21",
        "lagged_q25",
        "lagged_q50",
        "lagged_q75",
        "volatility_quartile",
        "target_risk_allocation",
        "target_scaled_source_volatility",
        "cap_is_binding",
    ]
    if rebalances[formal_required].isna().any().any():
        raise DataQualityError("G13 formal signal risk state is incomplete")
    formal_a = pd.to_numeric(rebalances["target_risk_allocation"], errors="coerce")
    formal_forecast = pd.to_numeric(
        rebalances["book_forecast_volatility"], errors="coerce"
    )
    if (
        not np.isfinite(formal_a).all()
        or not np.isfinite(formal_forecast).all()
        or formal_a.le(0.0).any()
        or formal_a.gt(1.0).any()
        or not np.allclose(
            formal_a.to_numpy(dtype=float),
            np.minimum(1.0, 0.15 / formal_forecast.to_numpy(dtype=float)),
            rtol=0.0,
            atol=1e-15,
        )
    ):
        raise DataQualityError("G13 formal allocation violates the frozen rule")


def _attach_g00_comparisons(
    summary: pd.DataFrame, reference: pd.DataFrame
) -> pd.DataFrame:
    lookup = reference.set_index(["strategy_id", "cost_bps", "borrow_fee_annual"])
    if not lookup.index.is_unique:
        raise DataQualityError("G00 reference contains duplicate scenario identities")
    rows: list[dict[str, object]] = []
    for index, row in summary.iterrows():
        strategy_id = str(row["strategy_id"])
        parent = strategy_id.replace("G13__", "G00__", 1)
        if parent == strategy_id:
            raise DataQualityError(f"invalid G13 strategy identity: {strategy_id}")
        key = (parent, float(row["cost_bps"]), float(row["borrow_fee_annual"]))
        if key not in lookup.index:
            raise DataQualityError(f"G13 lacks matching G00 scenario: {key}")
        baseline = lookup.loc[key]
        for metric in _COMPARISON_METRICS:
            delta = float(row[metric] - baseline[metric])
            summary.at[index, f"delta_vs_g00_{metric}"] = delta
            rows.append(
                {
                    "group_id": "G13",
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
            f"G13 must record 1440 G00 comparison rows, got {len(comparison)}"
        )
    return comparison


def _frozen_parameters(context: ExperimentRunContext) -> dict[str, object]:
    raw = context.group.raw.get("parameters")
    dates = context.group.program.raw.get("dates")
    if not isinstance(raw, dict) or not isinstance(dates, dict):
        raise ValueError("G13 parameters or program dates are missing")
    expected: dict[str, object] = {
        "forecast_model": "ewma",
        "ewma_decay": 0.94,
        "forecast_horizon_sessions": 21,
        "annual_target_volatility": 0.15,
        "maximum_scale": 1.0,
        "book_source": "matching_G00_portfolio_mode",
    }
    for key, value in expected.items():
        actual = raw.get(key)
        if isinstance(value, float):
            matches = isinstance(actual, (int, float)) and not isinstance(actual, bool)
            matches = matches and np.isclose(
                float(actual), value, rtol=0.0, atol=1e-15
            )
        else:
            matches = actual == value
        if not matches:
            raise ValueError(f"G13 frozen parameter changed: {key}={actual!r}")
    history_start = str(dates.get("strategy_forecast_history_start", ""))
    if history_start != "2014-06-30":
        raise ValueError(f"G13 frozen history start changed: {history_start!r}")
    return {
        "ewma_decay": 0.94,
        "forecast_horizon_sessions": 21,
        "history_sessions": 756,
        "history_start": history_start,
        "annual_target_volatility": 0.15,
        "maximum_scale": 1.0,
    }


def _validate_frozen_inputs(
    context: ExperimentRunContext, data: LoadedExperimentData
) -> None:
    _frozen_parameters(context)
    if context.dataset_version != _FROZEN_DATASET_VERSION:
        raise DataQualityError("G13 requires the frozen v3 dataset version")
    if data.dataset_manifest_sha256 != _FROZEN_DATASET_MANIFEST_SHA256:
        raise DataQualityError("G13 frozen dataset manifest hash changed")
    freeze_record = context.data_root / "curated" / context.dataset_version / "FROZEN.json"
    if (
        not freeze_record.is_file()
        or sha256_file(freeze_record) != _FROZEN_RECORD_SHA256
    ):
        raise DataQualityError("G13 frozen dataset record hash changed")
    design = (
        context.project_root
        / "docs"
        / "20_experiments"
        / "G13_book_forecast_continuous_scale"
        / "design.md"
    )
    if not design.is_file() or sha256_file(design) != _FROZEN_DESIGN_SHA256:
        raise DataQualityError("G13 preregistered design hash changed")
    program = context.project_root / "config" / "experiments" / "program.toml"
    if not program.is_file() or sha256_file(program) != _FROZEN_PROGRAM_SHA256:
        raise DataQualityError("G13 frozen experiment program hash changed")
    if sha256_file(context.group.path) != _FROZEN_GROUP_CONFIG_SHA256:
        raise DataQualityError("G13 frozen group config hash changed")
    costs = context.group.program.raw.get("costs")
    long_short = context.group.program.raw.get("long_short")
    if not isinstance(costs, dict) or not isinstance(long_short, dict):
        raise DataQualityError("G13 frozen cost tables are missing")
    if (
        costs.get("scenarios_bps") != [0, 5, 10, 20]
        or costs.get("weekly_primary_bps") != 10
        or costs.get("monthly_primary_bps") != 5
        or long_short.get("borrow_fee_scenarios_annual") != [0.0, 0.01, 0.03]
        or long_short.get("primary_borrow_fee_annual") != 0.01
    ):
        raise DataQualityError("G13 frozen cost or borrow-fee contract changed")


def _validate_reference_anchor(reference_g00: _G00Reference) -> None:
    if (
        reference_g00.manifest.get("run_id") != "g00-frozen-v3-v1"
        or reference_g00.manifest_sha256 != _FROZEN_G00_MANIFEST_SHA256
    ):
        raise DataQualityError("G13 G00 reference is not the frozen v3-v1 bundle")


def _validate_runtime_roots(config: G13RunConfig) -> None:
    data_root = config.context.data_root.resolve()
    output_root = config.context.output_root.resolve()
    project_root = config.context.project_root.resolve()
    if (
        data_root.name.lower() != "data"
        or output_root.name.lower() != "results"
        or data_root.parent != output_root.parent
    ):
        raise DataQualityError("G13 requires sibling data/results local runtime roots")
    try:
        output_root.relative_to(project_root)
    except ValueError:
        pass
    else:
        raise DataQualityError("G13 full bundle cannot be written inside the repository")
    try:
        config.reference_g00_root.resolve().relative_to(output_root)
    except ValueError as error:
        raise DataQualityError(
            "G13 G00 reference must come from the local results root"
        ) from error


def _render_g13_resolved_config_toml(
    *,
    config: G13RunConfig,
    data: LoadedExperimentData,
    reference_g00: _G00Reference,
) -> str:
    context = config.context
    project = context.project_root
    design = (
        project
        / "docs"
        / "20_experiments"
        / "G13_book_forecast_continuous_scale"
        / "design.md"
    )
    program = project / "config" / "experiments" / "program.toml"
    freeze_record = context.data_root / "curated" / context.dataset_version / "FROZEN.json"
    resolved = context.group.resolved_config()
    resolved["run"] = {
        "run_id": context.run_id,
        "dataset_version": context.dataset_version,
        "allow_review_dataset": config.allow_review_dataset,
        "workers": config.workers,
        "project_root": str(project.resolve()),
        "data_root": str(context.data_root.resolve()),
        "output_root": str(context.output_root.resolve()),
        "bundle_dir": str(context.bundle_dir.resolve()),
        "formal_run_eligible": False,
        "resolved_spec_sha256": context.group.resolved_sha256,
        "design_sha256": sha256_file(design),
        "group_config_sha256": sha256_file(context.group.path),
        "program_sha256": sha256_file(program),
        "dataset_anchor": {
            "manifest_path": str(context.dataset_manifest_path.resolve()),
            "manifest_sha256": data.dataset_manifest_sha256,
            "freeze_record_path": str(freeze_record.resolve()),
            "freeze_record_sha256": sha256_file(freeze_record),
            "status": data.dataset_status,
            "research_tier": data.dataset_research_tier,
        },
        "reference_g00": {
            "root": str(reference_g00.root.resolve()),
            "run_id": str(reference_g00.manifest.get("run_id")),
            "manifest_sha256": reference_g00.manifest_sha256,
        },
    }
    rendered = toml_dumps(resolved)
    if not rendered.endswith("\n"):
        raise RuntimeError("G13 resolved TOML must end with newline")
    return rendered


def _manifest_metadata(
    *,
    config: G13RunConfig,
    data: LoadedExperimentData,
    reference_g00: _G00Reference,
    summary: pd.DataFrame,
    comparison: pd.DataFrame,
    rebalances: pd.DataFrame,
    diagnostics: pd.DataFrame,
) -> dict[str, object]:
    project = config.context.project_root
    design = (
        project
        / "docs"
        / "20_experiments"
        / "G13_book_forecast_continuous_scale"
        / "design.md"
    )
    program = project / "config" / "experiments" / "program.toml"
    signal_rows = rebalances.drop_duplicates(["strategy_id", "signal_date"])
    daily = diagnostics.loc[diagnostics["scope"].eq("daily_naked_regime")]
    available_a = pd.to_numeric(
        daily["target_risk_allocation"], errors="coerce"
    ).dropna()
    blockers = {str(value) for value in data.dataset_manifest.get("formal_blockers", [])}
    blockers.add("systematic_G13_bundle_is_prototype")
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
            "program_sha256": sha256_file(program),
            "group_config_sha256": sha256_file(config.context.group.path),
            "freeze_record_sha256": _FROZEN_RECORD_SHA256,
        },
        "risk_rule": {
            "risk_source": "matching naked-book causal EWMA forecast volatility",
            "naked_book_history_start": "2014-06-30",
            "naked_book_cost_bps": 0.0,
            "naked_book_borrow_fee_annual": 0.0,
            "naked_book_cash_return": 0.0,
            "forecast_model": "ewma",
            "ewma_decay": 0.94,
            "forecast_horizon_sessions": 21,
            "ewma_initialization": "v_t0 = r_t0^2",
            "annualization": "sqrt(252 * ewma_variance)",
            "annual_target_volatility": 0.15,
            "maximum_scale": 1.0,
            "scale_rule": "min(1, 0.15 / current_signal_close_book_forecast_sigma)",
            "quartile_role": "diagnostic_only",
            "quartile_history_sessions": 756,
            "quartile_current_observation_excluded": True,
            "hysteresis": False,
            "leverage": False,
            "minimum_daily_allocation": float(available_a.min()),
            "maximum_daily_allocation": float(available_a.max()),
            "daily_below_full_fraction": float(available_a.lt(1.0).mean()),
            "signal_below_full_fraction": float(
                signal_rows["target_risk_allocation"].lt(1.0).mean()
            ),
            "minimum_signal_allocation": float(
                signal_rows["target_risk_allocation"].min()
            ),
        },
        "counts": {
            "core_strategies": len(config.context.strategies),
            "naked_book_paths": len(config.context.strategies),
            "event_paths_simulated": 72,
            "main_scenarios": len(summary),
            "primary_scenarios": int(summary["is_primary_scenario"].sum()),
            "primary_scaled_rebalances": int(
                rebalances["target_risk_allocation"].lt(1.0).sum()
            ),
            "daily_naked_regime_rows": len(daily),
        },
        "accounting": {
            "long_only_cash": "unallocated formal-run cash compounds at daily T-bill RF",
            "long_short_collateral": "formal-run cash compounds at daily T-bill RF",
            "risk_allocation_timing": (
                "signal close determines next-open target and holds until next rebalance"
            ),
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
            "g13_sha256": sha256_file(Path(__file__)),
            "g33_forecast_helper_sha256": sha256_file(Path(__file__).with_name("g33.py")),
            "g11_scalar_audit_helper_sha256": sha256_file(
                Path(__file__).with_name("g11.py")
            ),
            "g21_reference_helper_sha256": sha256_file(
                Path(__file__).with_name("g21.py")
            ),
            "g00_accounting_helper_sha256": sha256_file(
                Path(__file__).with_name("g00.py")
            ),
            "bundle_sha256": sha256_file(Path(__file__).with_name("bundle.py")),
            "run_context_sha256": sha256_file(
                Path(__file__).with_name("run_context.py")
            ),
            "engine_sha256": sha256_file(
                Path(__file__).resolve().parents[1] / "backtest" / "engine.py"
            ),
        },
        "limitations": [
            "free-research dataset and SPY total-return proxy",
            "continuous naked-book forecast scaling is a mechanism test, not a deployment claim",
            "G11, G12, and G33 are post-completion report comparators, not runtime references",
        ],
    }
