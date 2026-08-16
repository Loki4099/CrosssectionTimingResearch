"""G31 strict-Q4 derisking driven by lagged SPY realized volatility."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from momentum_reversal.analytics import benchmark_returns_from_total_return_prices
from momentum_reversal.backtest import BaselineBacktester, replay_linear_cost
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
    strict_lagged_spy_quartiles,
)
from .run_context import ExperimentRunContext, LoadedExperimentData, load_experiment_data


@dataclass(frozen=True, slots=True)
class G31RunConfig:
    context: ExperimentRunContext
    reference_g00_root: Path
    allow_review_dataset: bool = False
    workers: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_g00_root", Path(self.reference_g00_root))
        if self.context.group_id != "G31":
            raise ValueError("G31 runner requires the registered G31 spec")
        if isinstance(self.workers, bool) or not isinstance(self.workers, int):
            raise ValueError("G31 workers must be an integer")
        if self.workers <= 0 or self.workers > 8:
            raise ValueError("G31 workers must be between 1 and 8")


@dataclass(frozen=True, slots=True)
class G31RunResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    strategy_count: int
    scenario_count: int
    comparison_count: int
    q4_rebalance_count: int
    formal_run_eligible: bool


@dataclass(frozen=True, slots=True)
class _G31CoreBatch:
    strategy_count: int
    summary: pd.DataFrame
    nav: pd.DataFrame
    rebalances: pd.DataFrame
    holdings: pd.DataFrame
    trades: pd.DataFrame
    diagnostics: pd.DataFrame


def strict_q4_derisk_allocation(
    regime: pd.DataFrame, signal_dates: pd.Index | None = None
) -> pd.Series:
    """Return 1x outside Q4 and ``min(1, lagged_q75 / RV21)`` in Q4."""

    if not isinstance(regime, pd.DataFrame):
        raise TypeError("regime must be a pandas DataFrame")
    required = {"spy_realized_volatility", "lagged_q75", "volatility_quartile"}
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

    rv = pd.to_numeric(regime["spy_realized_volatility"], errors="coerce")
    q75 = pd.to_numeric(regime["lagged_q75"], errors="coerce")
    quartile = pd.to_numeric(regime["volatility_quartile"], errors="coerce")
    available = quartile.notna()
    invalid_quartile = available & ~quartile.isin([1, 2, 3, 4])
    invalid_values = available & (
        rv.isna() | q75.isna() | ~np.isfinite(rv) | ~np.isfinite(q75)
        | rv.le(0.0) | q75.le(0.0)
    )
    if invalid_quartile.any() or invalid_values.any():
        raise ValueError("available regime rows must contain valid quartiles and volatility")
    high = quartile.eq(4)
    if (high & ~rv.gt(q75)).any():
        raise ValueError("Q4 rows must be strictly above the lagged q75 threshold")
    if (available & ~high & rv.gt(q75)).any():
        raise ValueError("non-Q4 rows cannot exceed the lagged q75 threshold")
    if "high_volatility" in regime:
        stated = regime["high_volatility"].astype("boolean")
        mismatch = available & stated.notna() & stated.ne(high)
        if mismatch.any():
            raise ValueError("high_volatility conflicts with volatility_quartile")

    allocation = pd.Series(np.nan, index=regime.index, dtype=float)
    allocation.loc[available] = 1.0
    allocation.loc[high] = (q75.loc[high] / rv.loc[high]).clip(upper=1.0)
    allocation.name = "target_risk_allocation"
    if signal_dates is None:
        return allocation
    dates = pd.DatetimeIndex(pd.to_datetime(signal_dates)).normalize()
    if dates.tz is not None or dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("signal dates must be timezone-naive, unique, and increasing")
    sampled = allocation.reindex(dates)
    if sampled.isna().any():
        raise DataQualityError(
            "G31 allocation is unavailable on signal dates: "
            f"{dates[sampled.isna()][:5].tolist()}"
        )
    sampled.index = dates
    return sampled.astype(float)


def run_g31(config: G31RunConfig) -> G31RunResult:
    if config.context.bundle_dir.exists():
        raise FileExistsError(
            f"immutable experiment bundle already exists: {config.context.bundle_dir}"
        )
    data = load_experiment_data(
        config.context, allow_review_dataset=config.allow_review_dataset
    )
    reference = _load_g00_reference(config.reference_g00_root, data)
    regime, allocation = _prepare_g31_allocation(config.context, data)
    strategies = config.context.strategies
    if config.workers == 1:
        batches = [
            _run_g31_core_batch(
                context=config.context,
                data=data,
                allocation=allocation,
                strategies=strategies,
            )
        ]
    else:
        partitions = _partition_strategies(strategies, config.workers)
        batches = []
        with ProcessPoolExecutor(max_workers=len(partitions)) as executor:
            futures = [
                executor.submit(
                    _run_g31_core_batch_worker,
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
            reference=reference,
            summary=summary,
            comparison=comparison,
            regime=regime,
            rebalances=artifacts["rebalances"],
        ),
    )
    return G31RunResult(
        run_id=config.context.run_id,
        output_dir=bundle.output_dir,
        manifest_path=bundle.manifest_path,
        strategy_count=len(strategies),
        scenario_count=len(summary),
        comparison_count=len(comparison),
        q4_rebalance_count=q4_count,
        formal_run_eligible=False,
    )


def _prepare_g31_allocation(
    context: ExperimentRunContext, data: LoadedExperimentData
) -> tuple[pd.DataFrame, pd.Series]:
    parameters = _frozen_parameters(context)
    regime = strict_lagged_spy_quartiles(
        data.benchmark.reset_index(drop=True),
        realized_vol_window=parameters["realized_vol_window"],
        history_sessions=parameters["state_history_sessions"],
    )
    dates_by_frequency = _signal_dates_by_frequency(data)
    signal_dates = dates_by_frequency["weekly"].union(dates_by_frequency["monthly"])
    allocation = strict_q4_derisk_allocation(regime, signal_dates)
    regime = regime.copy()
    regime["target_risk_allocation"] = strict_q4_derisk_allocation(regime)
    return regime, allocation


def _run_g31_core_batch_worker(
    context: ExperimentRunContext,
    allow_review_dataset: bool,
    strategy_ids: tuple[str, ...],
) -> _G31CoreBatch:
    data = load_experiment_data(context, allow_review_dataset=allow_review_dataset)
    _, allocation = _prepare_g31_allocation(context, data)
    lookup = {strategy.strategy_id: strategy for strategy in context.strategies}
    try:
        strategies = tuple(lookup[strategy_id] for strategy_id in strategy_ids)
    except KeyError as error:
        raise ValueError(f"unknown G31 worker strategy: {error.args[0]}") from error
    return _run_g31_core_batch(
        context=context, data=data, allocation=allocation, strategies=strategies
    )


def _run_g31_core_batch(
    *,
    context: ExperimentRunContext,
    data: LoadedExperimentData,
    allocation: pd.Series,
    strategies: tuple[StrategySpec, ...],
) -> _G31CoreBatch:
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
    summary_rows: list[dict[str, object]] = []
    nav_frames: list[pd.DataFrame] = []
    rebalance_frames: list[pd.DataFrame] = []
    holding_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    diagnostic_rows: list[dict[str, object]] = []

    for strategy in strategies:
        primary_cost = _primary_cost(context, strategy.frequency)
        if strategy.portfolio_mode is PortfolioMode.LONG_ONLY:
            zero_cost = long_only_engine.run(
                signal=strategy.signal,
                top_n=strategy.top_n,
                frequency=strategy.frequency,  # type: ignore[arg-type]
                cost_bps=0.0,
                risk_allocation=allocation,
                risk_free_daily=data.risk_free_daily,
                full_audit=True,
            )
            for cost_bps in costs:
                result = replay_linear_cost(zero_cost, cost_bps=float(cost_bps))
                _append_g31_scenario(
                    strategy, result, cost_bps, 0.0, primary_cost, data, benchmark,
                    summary_rows, nav_frames, rebalance_frames, holding_frames,
                    trade_frames, diagnostic_rows,
                )
        else:
            generator = _winner_loser_generator(strategy.top_n)
            for annual_borrow_fee in borrow_fees:
                zero_cost = long_short_engine.run(
                    signal=strategy.signal,
                    top_n=strategy.top_n,
                    frequency=strategy.frequency,  # type: ignore[arg-type]
                    cost_bps=0.0,
                    target_weight_generator=generator,
                    target_weight_cache_key=strategy.strategy_id,
                    risk_allocation=allocation,
                    risk_free_daily=data.risk_free_daily,
                    short_borrow_fee_daily=annual_borrow_fee_to_daily(annual_borrow_fee),
                    signed_missing_execution_policy="terminal_last_close",
                    terminal_last_close_max_sessions=data.terminal_last_close_max_sessions,
                    full_audit=np.isclose(annual_borrow_fee, 0.01),
                )
                for cost_bps in costs:
                    result = replay_linear_cost(zero_cost, cost_bps=float(cost_bps))
                    _append_g31_scenario(
                        strategy, result, cost_bps, annual_borrow_fee, primary_cost,
                        data, benchmark, summary_rows, nav_frames, rebalance_frames,
                        holding_frames, trade_frames, diagnostic_rows,
                    )
    summary = pd.DataFrame(summary_rows).sort_values(
        ["strategy_id", "cost_bps", "borrow_fee_annual"], ignore_index=True
    )
    return _G31CoreBatch(
        strategy_count=len(strategies),
        summary=summary,
        nav=pd.concat(nav_frames, ignore_index=True),
        rebalances=_concat_or_empty(rebalance_frames, "rebalances"),
        holdings=_concat_or_empty(holding_frames, "holdings"),
        trades=_concat_or_empty(trade_frames, "trades"),
        diagnostics=pd.DataFrame(diagnostic_rows),
    )


def _append_g31_scenario(
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
    primary_borrow = 0.0 if strategy.portfolio_mode is PortfolioMode.LONG_ONLY else 0.01
    _append_scenario(
        strategy=strategy,
        result=result,  # type: ignore[arg-type]
        cost_bps=cost_bps,
        borrow_fee_annual=borrow_fee,
        primary=np.isclose(cost_bps, primary_cost) and np.isclose(borrow_fee, primary_borrow),
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


def _frozen_parameters(context: ExperimentRunContext) -> dict[str, int]:
    raw = context.group.raw.get("parameters")
    if not isinstance(raw, dict):
        raise ValueError("G31 parameters table is missing")
    expected: dict[str, object] = {
        "realized_vol_window": 21,
        "state_history_sessions": 756,
        "high_vol_quantile": 0.75,
        "state_rule": "strict_q4_no_hysteresis",
        "q4_scale_rule": "min_1_q75_over_sigma",
    }
    for key, value in expected.items():
        if raw.get(key) != value:
            raise ValueError(f"G31 frozen parameter changed: {key}={raw.get(key)!r}")
    return {"realized_vol_window": 21, "state_history_sessions": 756}


def _validate_main_counts(summary: pd.DataFrame, context: ExperimentRunContext) -> None:
    if len(context.strategies) != 36 or len(summary) != 288:
        raise RuntimeError(
            "G31 requires 36 core paths and 288 scenarios, got "
            f"{len(context.strategies)} and {len(summary)}"
        )
    key = ["strategy_id", "variant_id", "cost_bps", "borrow_fee_annual"]
    if summary.duplicated(key).any():
        raise RuntimeError("G31 summary contains duplicate scenario identities")
    if int(summary["is_primary_scenario"].sum()) != 36:
        raise RuntimeError("G31 must contain exactly 36 primary scenarios")


def _attach_regime_audit(
    summary: pd.DataFrame, rebalances: pd.DataFrame, regime: pd.DataFrame
) -> None:
    dates = pd.DatetimeIndex(pd.to_datetime(rebalances["signal_date"])).normalize()
    sampled = regime.reindex(dates)
    required = [
        "spy_realized_volatility", "lagged_q75", "volatility_quartile",
        "high_volatility", "target_risk_allocation",
    ]
    if sampled[required].isna().any().any():
        raise DataQualityError("G31 regime audit is unavailable for a rebalance")
    actual = pd.to_numeric(rebalances["target_risk_allocation"], errors="coerce")
    expected = sampled["target_risk_allocation"].to_numpy(dtype=float)
    if actual.isna().any() or not np.allclose(actual, expected, rtol=0.0, atol=1e-12):
        raise DataQualityError("G31 engine allocation differs from the frozen Q4 rule")
    for column in required[:-1]:
        rebalances[column] = sampled[column].to_numpy()
    rebalances["derisk_scale"] = expected
    grouped = rebalances.groupby("strategy_id", observed=True)
    stats = grouped["high_volatility"].agg(["sum", "mean"])
    q4 = rebalances.loc[rebalances["high_volatility"].astype(bool)]
    q4_stats = q4.groupby("strategy_id", observed=True)["target_risk_allocation"].agg(
        ["mean", "min"]
    )
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


def _attach_g00_comparisons(
    summary: pd.DataFrame, reference: pd.DataFrame
) -> pd.DataFrame:
    lookup = reference.set_index(
        ["strategy_id", "cost_bps", "borrow_fee_annual"]
    )
    if not lookup.index.is_unique:
        raise DataQualityError("G00 reference contains duplicate scenario identities")
    rows: list[dict[str, object]] = []
    for index, row in summary.iterrows():
        strategy_id = str(row["strategy_id"])
        parent = strategy_id.replace("G31__", "G00__", 1)
        if parent == strategy_id:
            raise DataQualityError(f"invalid G31 strategy identity: {strategy_id}")
        key = (parent, float(row["cost_bps"]), float(row["borrow_fee_annual"]))
        if key not in lookup.index:
            raise DataQualityError(f"G31 lacks matching G00 scenario: {key}")
        baseline = lookup.loc[key]
        for metric in _COMPARISON_METRICS:
            delta = float(row[metric] - baseline[metric])
            summary.at[index, f"delta_vs_g00_{metric}"] = delta
            rows.append(
                {
                    "group_id": "G31",
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
    if len(comparison) != 1440:
        raise RuntimeError(f"G31 must record 1440 G00 comparison rows, got {len(comparison)}")
    return comparison


def _manifest_metadata(
    *,
    config: G31RunConfig,
    data: LoadedExperimentData,
    reference: _G00Reference,
    summary: pd.DataFrame,
    comparison: pd.DataFrame,
    regime: pd.DataFrame,
    rebalances: pd.DataFrame,
) -> dict[str, object]:
    evaluation = regime.loc[data.evaluation_start : data.evaluation_end]
    signal_rows = rebalances.drop_duplicates("signal_date")
    blockers = {str(value) for value in data.dataset_manifest.get("formal_blockers", [])}
    blockers.add("systematic_G31_bundle_is_prototype")
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
            "run_id": reference.manifest.get("run_id"),
            "manifest_sha256": reference.manifest_sha256,
            "comparison_rows": len(comparison),
        },
        "regime": {
            "risk_source": "SPY RV21",
            "threshold": "strictly greater than lagged rolling 756-session q75",
            "hysteresis": False,
            "normal_state_allocation": 1.0,
            "q4_scale_rule": "min(1, lagged_q75 / current_RV21)",
            "evaluation_daily_q4_fraction": float(evaluation["high_volatility"].mean()),
            "signal_q4_fraction": float(signal_rows["high_volatility"].mean()),
            "minimum_signal_allocation": float(signal_rows["target_risk_allocation"].min()),
        },
        "counts": {
            "core_strategies": len(config.context.strategies),
            "main_scenarios": len(summary),
            "primary_scenarios": int(summary["is_primary_scenario"].sum()),
            "primary_q4_rebalances": int(rebalances["high_volatility"].sum()),
        },
        "accounting": {
            "long_only_cash": "unallocated cash compounds at daily T-bill RF",
            "long_short_collateral": "cash compounds at daily T-bill RF",
            "risk_allocation_timing": "signal close determines next-open target and holds until next rebalance",
        },
        "execution": {
            "worker_processes": config.workers,
            "cost_scenarios": "exact homogeneous-NAV replay from zero-cost event paths",
            "event_paths_simulated": 72,
            "reported_scenarios": len(summary),
        },
        "runtime_code": {
            "g31_sha256": sha256_file(Path(__file__)),
            "g21_regime_helper_sha256": sha256_file(Path(__file__).with_name("g21.py")),
            "engine_sha256": sha256_file(
                Path(__file__).resolve().parents[1] / "backtest" / "engine.py"
            ),
        },
        "limitations": [
            "free-research dataset and SPY total-return proxy",
            "strict Q4 derisking is a frozen mechanism test, not a deployment claim",
        ],
    }
