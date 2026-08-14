"""G21 strict-Q4 reversal driven by lagged SPY realized volatility."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd

from momentum_reversal.analytics import benchmark_returns_from_total_return_prices
from momentum_reversal.backtest import (
    BaselineBacktester,
    rebalance_schedule,
    replay_linear_cost,
)
from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.storage import sha256_file
from momentum_reversal.experiments import (
    PortfolioMode,
    StrategySpec,
    spy_realized_volatility,
    switch_cross_sectional_scores,
)
from momentum_reversal.factors import compute_momentum_scores, compute_reversal_scores

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
from .run_context import ExperimentRunContext, LoadedExperimentData, load_experiment_data


_COMPARISON_METRICS = (
    "cagr",
    "sharpe_excess_rf",
    "max_drawdown",
    "annualized_volatility",
    "annualized_l1_turnover",
)


@dataclass(frozen=True, slots=True)
class G21RunConfig:
    context: ExperimentRunContext
    reference_g00_root: Path
    allow_review_dataset: bool = False
    workers: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_g00_root", Path(self.reference_g00_root))
        if self.context.group_id != "G21":
            raise ValueError("G21 runner requires the registered G21 spec")
        if isinstance(self.workers, bool) or not isinstance(self.workers, int):
            raise ValueError("G21 workers must be an integer")
        if self.workers <= 0 or self.workers > 8:
            raise ValueError("G21 workers must be between 1 and 8")


@dataclass(frozen=True, slots=True)
class G21RunResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    strategy_count: int
    scenario_count: int
    comparison_count: int
    conditional_diagnostic_count: int
    formal_run_eligible: bool


@dataclass(frozen=True, slots=True)
class _G00Reference:
    root: Path
    manifest: dict[str, object]
    manifest_sha256: str
    summary: pd.DataFrame
    rebalances: pd.DataFrame


@dataclass(frozen=True, slots=True)
class _G21CoreBatch:
    strategy_count: int
    summary: pd.DataFrame
    nav: pd.DataFrame
    rebalances: pd.DataFrame
    holdings: pd.DataFrame
    trades: pd.DataFrame
    diagnostics: pd.DataFrame


def strict_lagged_spy_quartiles(
    benchmark: pd.DataFrame,
    *,
    realized_vol_window: int = 21,
    history_sessions: int = 756,
) -> pd.DataFrame:
    """Classify current SPY RV against quartiles from strictly prior sessions."""

    if history_sessions < 4:
        raise ValueError("history_sessions must be at least four")
    realized = spy_realized_volatility(benchmark, window=realized_vol_window)
    lagged = realized.shift(1)
    rolling = lagged.rolling(history_sessions, min_periods=history_sessions)
    q25 = rolling.quantile(0.25)
    q50 = rolling.quantile(0.50)
    q75 = rolling.quantile(0.75)
    available = realized.notna() & q25.notna() & q50.notna() & q75.notna()
    quartile = pd.Series(pd.NA, index=realized.index, dtype="Int64")
    quartile.loc[available & realized.le(q25)] = 1
    quartile.loc[available & realized.gt(q25) & realized.le(q50)] = 2
    quartile.loc[available & realized.gt(q50) & realized.le(q75)] = 3
    quartile.loc[available & realized.gt(q75)] = 4
    return pd.DataFrame(
        {
            "spy_realized_volatility": realized,
            "lagged_q25": q25,
            "lagged_q50": q50,
            "lagged_q75": q75,
            "volatility_quartile": quartile,
            "high_volatility": quartile.eq(4).fillna(False),
        }
    )


def run_g21(config: G21RunConfig) -> G21RunResult:
    data = load_experiment_data(
        config.context, allow_review_dataset=config.allow_review_dataset
    )
    reference = _load_g00_reference(config.reference_g00_root, data)
    regime, reversal, switched = _prepare_g21_signals(config.context, data)
    strategies = config.context.strategies
    if config.workers == 1:
        batches = [
            _run_g21_core_batch(
                context=config.context,
                data=data,
                switched=switched,
                strategies=strategies,
            )
        ]
        print(
            f"completed_G21_core_paths={len(strategies)}/{len(strategies)}",
            flush=True,
        )
    else:
        partitions = _partition_strategies(strategies, config.workers)
        batches = []
        completed = 0
        with ProcessPoolExecutor(max_workers=len(partitions)) as executor:
            futures = [
                executor.submit(
                    _run_g21_core_batch_worker,
                    config.context,
                    config.allow_review_dataset,
                    tuple(strategy.strategy_id for strategy in partition),
                )
                for partition in partitions
            ]
            for future in as_completed(futures):
                batch = future.result()
                batches.append(batch)
                completed += batch.strategy_count
                print(
                    f"completed_G21_core_paths={completed}/{len(strategies)}",
                    flush=True,
                )

    summary = pd.concat([batch.summary for batch in batches], ignore_index=True).sort_values(
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
    _validate_main_counts(summary, config.context)
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
    _attach_regime_audit(summary, artifacts["rebalances"], regime)

    comparison = _attach_g00_comparisons(summary, reference.summary)
    conditional_rows = _conditional_diagnostics(
        context=config.context,
        data=data,
        regime=regime,
        reversal_scores=reversal,
        reference_rebalances=reference.rebalances,
    )
    artifacts["diagnostics"] = pd.concat(
        [artifacts["diagnostics"], pd.DataFrame(conditional_rows)],
        ignore_index=True,
    )
    conditional_count = len(conditional_rows)
    bundle: BundleWriteResult = write_experiment_bundle(
        config.context,
        summary=summary,
        comparison=comparison,
        artifacts=artifacts,
        status="completed",
        extra_manifest=_manifest_metadata(
            config=config,
            data=data,
            reference=reference,
            summary=summary,
            comparison=comparison,
            conditional_count=conditional_count,
            regime=regime,
        ),
    )
    return G21RunResult(
        run_id=config.context.run_id,
        output_dir=bundle.output_dir,
        manifest_path=bundle.manifest_path,
        strategy_count=len(config.context.strategies),
        scenario_count=len(summary),
        comparison_count=len(comparison),
        conditional_diagnostic_count=conditional_count,
        formal_run_eligible=False,
    )


def _prepare_g21_signals(
    context: ExperimentRunContext, data: LoadedExperimentData
) -> tuple[pd.DataFrame, dict[int, pd.Series], dict[tuple[object, int], pd.Series]]:
    parameters = _frozen_parameters(context)
    regime = strict_lagged_spy_quartiles(
        data.benchmark.reset_index(drop=True),
        realized_vol_window=parameters["realized_vol_window"],
        history_sessions=parameters["state_history_sessions"],
    )
    signal_dates_by_frequency = _signal_dates_by_frequency(data)
    all_signal_dates = signal_dates_by_frequency["weekly"].union(
        signal_dates_by_frequency["monthly"]
    )
    sampled_regime = regime.reindex(all_signal_dates)
    if sampled_regime["volatility_quartile"].isna().any():
        missing = sampled_regime.index[
            sampled_regime["volatility_quartile"].isna()
        ]
        raise DataQualityError(
            "G21 lacks the frozen 756-session lagged state history on "
            f"{missing[:5].tolist()}"
        )
    momentum = {
        definition: compute_momentum_scores(
            data.prices,
            all_signal_dates,
            definition,
            sessions=data.sessions,
        )
        for definition in context.group.program.signals
    }
    reversal = {
        lookback: compute_reversal_scores(
            data.prices,
            all_signal_dates,
            lookback=lookback,
            sessions=data.sessions,
        )
        for lookback in context.group.reversal_lookbacks
    }
    switched = {
        (definition, lookback): switch_cross_sectional_scores(
            momentum[definition],
            reversal[lookback],
            regime["high_volatility"],
        )
        for definition in momentum
        for lookback in reversal
    }
    return regime, reversal, switched


def _run_g21_core_batch_worker(
    context: ExperimentRunContext,
    allow_review_dataset: bool,
    strategy_ids: tuple[str, ...],
) -> _G21CoreBatch:
    data = load_experiment_data(
        context, allow_review_dataset=allow_review_dataset
    )
    _, _, switched = _prepare_g21_signals(context, data)
    lookup = {strategy.strategy_id: strategy for strategy in context.strategies}
    try:
        strategies = tuple(lookup[strategy_id] for strategy_id in strategy_ids)
    except KeyError as error:
        raise ValueError(f"unknown G21 worker strategy: {error.args[0]}") from error
    return _run_g21_core_batch(
        context=context,
        data=data,
        switched=switched,
        strategies=strategies,
    )


def _partition_strategies(
    strategies: tuple[StrategySpec, ...], workers: int
) -> tuple[tuple[StrategySpec, ...], ...]:
    def work_units(strategy: StrategySpec) -> int:
        borrow_paths = (
            3 if strategy.portfolio_mode is PortfolioMode.LONG_SHORT else 1
        )
        # Weekly paths have over four times as many ranking/audit events as
        # monthly paths.  Daily valuation is shared, so a conservative 3:2
        # scheduler weight balances observed runtime without overfitting the
        # partitioner to one machine.
        frequency_weight = 3 if strategy.frequency == "weekly" else 2
        return borrow_paths * frequency_weight

    count = min(workers, len(strategies))
    buckets: list[list[StrategySpec]] = [[] for _ in range(count)]
    loads = [0] * count
    weighted = sorted(
        strategies,
        key=lambda strategy: (
            -work_units(strategy),
            strategy.strategy_id,
        ),
    )
    for strategy in weighted:
        index = min(range(count), key=lambda item: (loads[item], item))
        buckets[index].append(strategy)
        loads[index] += work_units(strategy)
    return tuple(tuple(bucket) for bucket in buckets if bucket)


def _run_g21_core_batch(
    *,
    context: ExperimentRunContext,
    data: LoadedExperimentData,
    switched: dict[tuple[object, int], pd.Series],
    strategies: tuple[StrategySpec, ...],
) -> _G21CoreBatch:
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
    benchmark_prices = data.benchmark.rename(
        columns={"benchmark_tr_open": "tr_open", "benchmark_tr_close": "tr_close"}
    )
    costs = _frozen_costs(context)
    borrow_fees = _frozen_borrow_fees(context)

    summary_rows: list[dict[str, object]] = []
    nav_frames: list[pd.DataFrame] = []
    rebalance_frames: list[pd.DataFrame] = []
    holding_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    diagnostic_rows: list[dict[str, object]] = []
    for strategy in strategies:
        lookback = _variant_lookback(strategy)
        selection_scores = switched[(strategy.signal, lookback)]
        selection_label = f"{strategy.signal.value}_q1q3__reversal_{lookback}_q4"
        primary_cost = _primary_cost(context, strategy.frequency)
        if strategy.portfolio_mode is PortfolioMode.LONG_ONLY:
            zero_cost = long_only_engine.run(
                signal=strategy.signal,
                top_n=strategy.top_n,
                frequency=strategy.frequency,  # type: ignore[arg-type]
                cost_bps=0.0,
                selection_scores=selection_scores,
                selection_label=selection_label,
                selection_score_cache_key=(
                    f"G21_{strategy.signal.value}_rev{lookback}"
                ),
                risk_free_daily=data.risk_free_daily,
                full_audit=True,
            )
            for cost_bps in costs:
                primary = np.isclose(cost_bps, primary_cost)
                result = replay_linear_cost(
                    zero_cost, cost_bps=float(cost_bps)
                )
                _validate_result_bounds(result, data)
                benchmark_returns = benchmark_returns_from_total_return_prices(
                    benchmark_prices, result.nav["daily_return"]
                )
                _append_scenario(
                    strategy=strategy,
                    result=result,
                    cost_bps=cost_bps,
                    borrow_fee_annual=0.0,
                    primary=primary,
                    risk_free_daily=data.risk_free_daily,
                    benchmark_returns=benchmark_returns,
                    summary_rows=summary_rows,
                    nav_frames=nav_frames,
                    rebalance_frames=rebalance_frames,
                    holding_frames=holding_frames,
                    trade_frames=trade_frames,
                    diagnostic_rows=diagnostic_rows,
                )
        else:
            generator = _winner_loser_generator(strategy.top_n)
            for annual_borrow_fee in borrow_fees:
                zero_cost = long_short_engine.run(
                    signal=strategy.signal,
                    top_n=strategy.top_n,
                    frequency=strategy.frequency,  # type: ignore[arg-type]
                    cost_bps=0.0,
                    selection_scores=selection_scores,
                    selection_label=selection_label,
                    selection_score_cache_key=(
                        f"G21_{strategy.signal.value}_rev{lookback}"
                    ),
                    target_weight_generator=generator,
                    target_weight_cache_key=strategy.strategy_id,
                    risk_free_daily=data.risk_free_daily,
                    short_borrow_fee_daily=annual_borrow_fee_to_daily(
                        annual_borrow_fee
                    ),
                    signed_missing_execution_policy="terminal_last_close",
                    terminal_last_close_max_sessions=(
                        data.terminal_last_close_max_sessions
                    ),
                    full_audit=np.isclose(annual_borrow_fee, 0.01),
                )
                for cost_bps in costs:
                    primary = np.isclose(cost_bps, primary_cost) and np.isclose(
                        annual_borrow_fee, 0.01
                    )
                    result = replay_linear_cost(
                        zero_cost, cost_bps=float(cost_bps)
                    )
                    _validate_result_bounds(result, data)
                    benchmark_returns = benchmark_returns_from_total_return_prices(
                        benchmark_prices, result.nav["daily_return"]
                    )
                    _append_scenario(
                        strategy=strategy,
                        result=result,
                        cost_bps=cost_bps,
                        borrow_fee_annual=annual_borrow_fee,
                        primary=primary,
                        risk_free_daily=data.risk_free_daily,
                        benchmark_returns=benchmark_returns,
                        summary_rows=summary_rows,
                        nav_frames=nav_frames,
                        rebalance_frames=rebalance_frames,
                        holding_frames=holding_frames,
                        trade_frames=trade_frames,
                        diagnostic_rows=diagnostic_rows,
                    )
    summary = pd.DataFrame(summary_rows).sort_values(
        ["strategy_id", "cost_bps", "borrow_fee_annual"], ignore_index=True
    )
    return _G21CoreBatch(
        strategy_count=len(strategies),
        summary=summary,
        nav=pd.concat(nav_frames, ignore_index=True),
        rebalances=_concat_or_empty(rebalance_frames, "rebalances"),
        holdings=_concat_or_empty(holding_frames, "holdings"),
        trades=_concat_or_empty(trade_frames, "trades"),
        diagnostics=pd.DataFrame(diagnostic_rows),
    )


def _frozen_parameters(context: ExperimentRunContext) -> dict[str, int | float]:
    raw = context.group.raw.get("parameters")
    if not isinstance(raw, dict):
        raise ValueError("G21 parameters table is missing")
    expected = {
        "realized_vol_window": 21,
        "state_history_sessions": 756,
        "high_vol_quantile": 0.75,
        "state_rule": "strict_q4_no_hysteresis",
    }
    for key, value in expected.items():
        if raw.get(key) != value:
            raise ValueError(f"G21 frozen parameter changed: {key}={raw.get(key)!r}")
    return {
        "realized_vol_window": 21,
        "state_history_sessions": 756,
        "high_vol_quantile": 0.75,
    }


def _signal_dates_by_frequency(data: LoadedExperimentData) -> dict[str, pd.DatetimeIndex]:
    result: dict[str, pd.DatetimeIndex] = {}
    for frequency in ("weekly", "monthly"):
        schedule = rebalance_schedule(data.sessions, frequency)  # type: ignore[arg-type]
        selected = schedule.loc[
            schedule["execution_date"].ge(data.evaluation_start)
            & schedule["signal_date"].le(data.evaluation_end),
            "signal_date",
        ]
        result[frequency] = pd.DatetimeIndex(selected)
    return result


def _variant_lookback(strategy: StrategySpec) -> int:
    if not strategy.variant_id.startswith("rev"):
        raise ValueError(f"G21 strategy lacks reversal variant: {strategy.strategy_id}")
    value = int(strategy.variant_id.removeprefix("rev"))
    if value not in {5, 20}:
        raise ValueError(f"unsupported G21 reversal lookback: {value}")
    return value


def _load_g00_reference(root: Path, data: LoadedExperimentData) -> _G00Reference:
    source = root.resolve()
    manifest_path = source / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "completed" or manifest.get("group_id") != "G00":
        raise DataQualityError("G21 reference must be a completed G00 bundle")
    if manifest.get("dataset_version") != data.context.dataset_version:
        raise DataQualityError("G21 and G00 must use the same frozen dataset version")
    dataset = manifest.get("dataset")
    if not isinstance(dataset, dict) or dataset.get("manifest_sha256") != data.dataset_manifest_sha256:
        raise DataQualityError("G21 reference G00 uses a different dataset manifest")
    for record in manifest.get("files", []):
        path = source / str(record["path"])
        if not path.is_file() or sha256_file(path) != str(record["sha256"]):
            raise DataQualityError(f"G00 reference artifact hash mismatch: {path}")
    summary = pd.read_csv(source / "summary.csv")
    rebalances = pd.read_parquet(source / "artifacts" / "rebalances.parquet")
    if len(summary) != 288 or not summary["valid_scenario"].astype(bool).all():
        raise DataQualityError("G00 reference summary is incomplete or invalid")
    return _G00Reference(
        root=source,
        manifest=manifest,
        manifest_sha256=sha256_file(manifest_path),
        summary=summary,
        rebalances=rebalances,
    )


def _validate_main_counts(summary: pd.DataFrame, context: ExperimentRunContext) -> None:
    if len(context.strategies) != 72 or len(summary) != 576:
        raise RuntimeError(
            f"G21 requires 72 core paths and 576 scenarios, got "
            f"{len(context.strategies)} and {len(summary)}"
        )
    key = ["strategy_id", "variant_id", "cost_bps", "borrow_fee_annual"]
    if summary.duplicated(key).any():
        raise RuntimeError("G21 summary contains duplicate scenario identities")
    if int(summary["is_primary_scenario"].sum()) != 72:
        raise RuntimeError("G21 must contain exactly 72 primary scenarios")


def _attach_regime_audit(
    summary: pd.DataFrame, rebalances: pd.DataFrame, regime: pd.DataFrame
) -> None:
    signal_dates = pd.DatetimeIndex(pd.to_datetime(rebalances["signal_date"]))
    sampled = regime.reindex(signal_dates)
    rebalances["spy_realized_volatility"] = sampled[
        "spy_realized_volatility"
    ].to_numpy(dtype=float)
    rebalances["lagged_q75"] = sampled["lagged_q75"].to_numpy(dtype=float)
    rebalances["volatility_quartile"] = sampled[
        "volatility_quartile"
    ].to_numpy(dtype=int)
    rebalances["high_volatility"] = sampled["high_volatility"].to_numpy(dtype=bool)
    stats = (
        rebalances.groupby("strategy_id", observed=True)["high_volatility"]
        .agg(["sum", "mean"])
        .rename(columns={"sum": "high_vol_rebalance_count", "mean": "high_vol_rebalance_fraction"})
    )
    summary["high_vol_rebalance_count"] = summary["strategy_id"].map(
        stats["high_vol_rebalance_count"]
    )
    summary["high_vol_rebalance_fraction"] = summary["strategy_id"].map(
        stats["high_vol_rebalance_fraction"]
    )


def _attach_g00_comparisons(
    summary: pd.DataFrame, reference: pd.DataFrame
) -> pd.DataFrame:
    lookup = reference.set_index(
        ["strategy_id", "cost_bps", "borrow_fee_annual"], verify_integrity=True
    )
    comparison_rows: list[dict[str, object]] = []
    deltas = {metric: [] for metric in _COMPARISON_METRICS}
    for row in summary.itertuples(index=False):
        parent = str(row.strategy_id).replace("G21__", "G00__", 1).rsplit("__", 1)[0]
        key = (parent, float(row.cost_bps), float(row.borrow_fee_annual))
        if key not in lookup.index:
            raise DataQualityError(f"G21 lacks matching G00 scenario: {key}")
        baseline = lookup.loc[key]
        for metric in _COMPARISON_METRICS:
            delta = float(getattr(row, metric) - baseline[metric])
            deltas[metric].append(delta)
            comparison_rows.append(
                {
                    "group_id": "G21",
                    "strategy_id": row.strategy_id,
                    "portfolio_mode": row.portfolio_mode,
                    "variant_id": row.variant_id,
                    "cost_bps": float(row.cost_bps),
                    "borrow_fee_annual": float(row.borrow_fee_annual),
                    "reference_strategy_id": parent,
                    "comparison_type": "delta_vs_same_scenario_G00",
                    "metric": metric,
                    "estimate": delta,
                }
            )
    for metric, values in deltas.items():
        summary[f"delta_vs_g00_{metric}"] = values
    return pd.DataFrame(comparison_rows)


def _conditional_diagnostics(
    *,
    context: ExperimentRunContext,
    data: LoadedExperimentData,
    regime: pd.DataFrame,
    reversal_scores: dict[int, pd.Series],
    reference_rebalances: pd.DataFrame,
) -> list[dict[str, object]]:
    """Compare naked momentum and pure reversal next-holding-period returns by Q."""

    reversal_controls: dict[tuple[int, int, str, str], pd.DataFrame] = {}
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
    unique_controls = sorted(
        {
            (_variant_lookback(strategy), strategy.top_n, strategy.frequency, strategy.portfolio_mode.value)
            for strategy in context.strategies
        }
    )
    signed_frames: list[pd.DataFrame] = []
    for lookback, top_n, frequency, mode in unique_controls:
        cost = _primary_cost(context, frequency)
        if mode == PortfolioMode.LONG_ONLY.value:
            result = long_only_engine.run(
                signal="mom_255_0",
                top_n=top_n,
                frequency=frequency,  # type: ignore[arg-type]
                cost_bps=cost,
                selection_scores=reversal_scores[lookback],
                selection_label=f"pure_reversal_{lookback}",
                risk_free_daily=data.risk_free_daily,
                full_audit=True,
            )
        else:
            result = long_short_engine.run(
                signal="mom_255_0",
                top_n=top_n,
                frequency=frequency,  # type: ignore[arg-type]
                cost_bps=cost,
                selection_scores=reversal_scores[lookback],
                selection_label=f"pure_reversal_{lookback}",
                target_weight_generator=_winner_loser_generator(top_n),
                target_weight_cache_key=f"conditional_rev{lookback}_top{top_n}_{frequency}",
                risk_free_daily=data.risk_free_daily,
                short_borrow_fee_daily=annual_borrow_fee_to_daily(0.01),
                signed_missing_execution_policy="terminal_last_close",
                terminal_last_close_max_sessions=data.terminal_last_close_max_sessions,
                full_audit=True,
            )
            signed = result.rebalances.copy()
            signed["strategy_id"] = f"conditional_rev{lookback}_top{top_n}_{frequency}"
            signed_frames.append(signed)
        reversal_controls[(lookback, top_n, frequency, mode)] = result.rebalances
    if signed_frames:
        _validate_signed_execution_audit(
            pd.concat(signed_frames, ignore_index=True),
            corporate_actions=data.corporate_actions,
            sessions=data.sessions,
        )

    output: list[dict[str, object]] = []
    for strategy in context.strategies:
        primary_cost = _primary_cost(context, strategy.frequency)
        primary_borrow = 0.0 if strategy.portfolio_mode is PortfolioMode.LONG_ONLY else 0.01
        parent = strategy.parent_id
        if parent is None:
            raise RuntimeError("G21 strategy lacks G00 parent")
        naked = reference_rebalances.loc[
            reference_rebalances["strategy_id"].eq(parent)
            & np.isclose(reference_rebalances["cost_bps"], primary_cost)
            & np.isclose(reference_rebalances["borrow_fee_annual"], primary_borrow)
        ]
        pure_reversal = reversal_controls[
            (
                _variant_lookback(strategy),
                strategy.top_n,
                strategy.frequency,
                strategy.portfolio_mode.value,
            )
        ]
        for source, frame in (("momentum", naked), ("reversal", pure_reversal)):
            aggregated = _conditional_period_summary(frame, regime)
            for row in aggregated.itertuples(index=False):
                identity = {
                    "group_id": "G21",
                    "strategy_id": strategy.strategy_id,
                    "portfolio_mode": strategy.portfolio_mode.value,
                    "variant_id": strategy.variant_id,
                    "cost_bps": float(primary_cost),
                    "borrow_fee_annual": float(primary_borrow),
                }
                for metric in ("event_count", "mean", "median", "win_rate", "es10", "worst"):
                    output.append(
                        {
                            **identity,
                            "scope": f"conditional_{source}_q{int(row.volatility_quartile)}",
                            "diagnostic": f"holding_period_{metric}",
                            "value": float(getattr(row, metric)),
                        }
                    )
    return output


def _conditional_period_summary(
    rebalances: pd.DataFrame, regime: pd.DataFrame
) -> pd.DataFrame:
    required = {"signal_date", "execution_status", "pretrade_nav"}
    missing = required.difference(rebalances.columns)
    if missing:
        raise DataQualityError(
            f"conditional-return rebalances lack columns: {sorted(missing)}"
        )
    frame = (
        rebalances.reset_index(drop=True)
        .sort_values("execution_date")
        .reset_index(drop=True)
        .copy()
    )
    nav = pd.to_numeric(frame["pretrade_nav"], errors="coerce")
    frame["holding_period_return"] = nav.shift(-1) / nav - 1.0
    frame["volatility_quartile"] = regime["volatility_quartile"].reindex(
        pd.DatetimeIndex(pd.to_datetime(frame["signal_date"]))
    ).to_numpy()
    valid = (
        frame["execution_status"].astype(str).str.startswith("executed")
        & frame["holding_period_return"].notna()
        & frame["volatility_quartile"].notna()
    )
    frame = frame.loc[valid]
    rows: list[dict[str, float]] = []
    for quartile in (1, 2, 3, 4):
        values = frame.loc[
            frame["volatility_quartile"].eq(quartile), "holding_period_return"
        ].astype(float)
        if values.empty:
            raise DataQualityError(f"conditional return table has no Q{quartile} events")
        tail_count = max(1, int(np.ceil(len(values) * 0.10)))
        rows.append(
            {
                "volatility_quartile": float(quartile),
                "event_count": float(len(values)),
                "mean": float(values.mean()),
                "median": float(values.median()),
                "win_rate": float(values.gt(0).mean()),
                "es10": float(values.nsmallest(tail_count).mean()),
                "worst": float(values.min()),
            }
        )
    return pd.DataFrame(rows)


def _manifest_metadata(
    *,
    config: G21RunConfig,
    data: LoadedExperimentData,
    reference: _G00Reference,
    summary: pd.DataFrame,
    comparison: pd.DataFrame,
    conditional_count: int,
    regime: pd.DataFrame,
) -> dict[str, object]:
    q4 = regime.loc[data.evaluation_start : data.evaluation_end, "high_volatility"]
    blockers = set(str(value) for value in data.dataset_manifest.get("formal_blockers", []))
    blockers.update({"dataset_status_review", "systematic_G21_bundle_is_prototype"})
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
            "run_id": reference.manifest.get("run_id"),
            "manifest_sha256": reference.manifest_sha256,
            "comparison_rows": len(comparison),
        },
        "regime": {
            "risk_source": "SPY RV21",
            "threshold": "strictly greater than lagged rolling 756-session q75",
            "hysteresis": False,
            "evaluation_daily_q4_fraction": float(q4.mean()),
            "reversal_lookbacks": [5, 20],
        },
        "counts": {
            "core_strategies": len(config.context.strategies),
            "main_scenarios": len(summary),
            "primary_scenarios": int(summary["is_primary_scenario"].sum()),
            "conditional_diagnostic_rows": conditional_count,
        },
        "execution": {
            "worker_processes": config.workers,
            "cost_scenarios": (
                "exact homogeneous-NAV replay from one zero-cost event path "
                "per borrow-fee scenario"
            ),
            "event_paths_simulated": 36 + 36 * 3,
            "reported_scenarios": len(summary),
        },
        "runtime_code": {
            "g21_sha256": sha256_file(Path(__file__)),
            "engine_sha256": sha256_file(
                Path(__file__).resolve().parents[1] / "backtest" / "engine.py"
            ),
        },
        "limitations": [
            "free-research dataset and SPY total-return proxy",
            "strict Q4 direct reversal is a frozen mechanism test, not a deployment claim",
            "conditional returns use execution-to-next-execution pretrade NAV and exclude skipped signals",
        ],
    }
