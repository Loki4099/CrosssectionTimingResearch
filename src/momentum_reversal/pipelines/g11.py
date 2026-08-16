"""G11 continuous unlevered scaling driven by causal SPY RV21."""

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
from momentum_reversal.experiments.spec import toml_dumps
from momentum_reversal.experiments.volatility import spy_realized_volatility

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


_FROZEN_DESIGN_SHA256 = (
    "c0e41c31fc5d8dc1fd53b466c7440fd5d02dc1cf77c48bd83f3a63bb452594c8"
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
    "3464d030eac592f3419f1bfcc00743770a0a82ed1c24f9354ff387ba92b381a5"
)
_FROZEN_G00_MANIFEST_SHA256 = (
    "8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66"
)
_AUDIT_START = pd.Timestamp("2014-06-30")


@dataclass(frozen=True, slots=True)
class G11RunConfig:
    context: ExperimentRunContext
    reference_g00_root: Path
    allow_review_dataset: bool = False
    workers: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_g00_root", Path(self.reference_g00_root))
        if self.context.group_id != "G11":
            raise ValueError("G11 runner requires the registered G11 spec")
        if isinstance(self.workers, bool) or not isinstance(self.workers, int):
            raise ValueError("G11 workers must be an integer")
        if self.workers <= 0 or self.workers > 8:
            raise ValueError("G11 workers must be between 1 and 8")


@dataclass(frozen=True, slots=True)
class G11RunResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    strategy_count: int
    scenario_count: int
    comparison_count: int
    scaled_rebalance_count: int
    formal_run_eligible: bool


@dataclass(frozen=True, slots=True)
class _G11CoreBatch:
    strategy_count: int
    summary: pd.DataFrame
    nav: pd.DataFrame
    rebalances: pd.DataFrame
    holdings: pd.DataFrame
    trades: pd.DataFrame
    diagnostics: pd.DataFrame


def continuous_spy_allocation(
    regime: pd.DataFrame,
    signal_dates: pd.Index | None = None,
    *,
    annual_target_volatility: float = 0.15,
    maximum_scale: float = 1.0,
) -> pd.Series:
    """Return ``min(1, 0.15 / current SPY RV21)`` on available dates.

    Missing warm-up observations are retained only when a full daily series is
    requested.  Any requested signal date must have a finite positive RV.
    Quartile columns, when present, are deliberately ignored by this rule.
    """

    if not isinstance(regime, pd.DataFrame):
        raise TypeError("regime must be a pandas DataFrame")
    if "spy_realized_volatility" not in regime:
        raise ValueError("regime is missing spy_realized_volatility")
    if not isinstance(regime.index, pd.DatetimeIndex):
        raise ValueError("regime must use a DatetimeIndex")
    if (
        regime.index.tz is not None
        or regime.index.has_duplicates
        or not regime.index.is_monotonic_increasing
    ):
        raise ValueError("regime index must be timezone-naive, unique, and increasing")
    if not np.isclose(annual_target_volatility, 0.15, rtol=0.0, atol=1e-15):
        raise ValueError("G11 requires annual_target_volatility=0.15")
    if not np.isclose(maximum_scale, 1.0, rtol=0.0, atol=1e-15):
        raise ValueError("G11 requires maximum_scale=1.0")

    rv = pd.to_numeric(regime["spy_realized_volatility"], errors="coerce")
    observed = rv.notna()
    invalid = observed & (~np.isfinite(rv) | rv.le(0.0))
    if invalid.any():
        raise DataQualityError(
            "G11 SPY RV21 must be finite and positive when available: "
            f"{rv.index[invalid][:5].tolist()}"
        )
    allocation = pd.Series(np.nan, index=regime.index, dtype=float)
    allocation.loc[observed] = (
        annual_target_volatility / rv.loc[observed]
    ).clip(upper=maximum_scale)
    allocation.name = "target_risk_allocation"
    if signal_dates is None:
        return allocation

    dates = pd.DatetimeIndex(pd.to_datetime(signal_dates)).normalize()
    if dates.tz is not None or dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("signal dates must be timezone-naive, unique, and increasing")
    sampled = allocation.reindex(dates)
    if sampled.isna().any() or not np.isfinite(sampled).all() or sampled.le(0.0).any():
        invalid_dates = dates[
            sampled.isna() | ~np.isfinite(sampled) | sampled.le(0.0)
        ]
        raise DataQualityError(
            "G11 allocation is unavailable on signal dates: "
            f"{invalid_dates[:5].tolist()}"
        )
    sampled.index = dates
    return sampled.astype(float)


def run_g11(config: G11RunConfig) -> G11RunResult:
    """Run and freeze the 36-path G11 continuous SPY RV21 grid."""

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
    regime, allocation = _prepare_g11_regime(config.context, data)
    strategies = config.context.strategies
    if config.workers == 1:
        batches = [
            _run_g11_core_batch(
                context=config.context,
                data=data,
                regime=regime,
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
                    _run_g11_core_batch_worker,
                    config.context,
                    config.allow_review_dataset,
                    tuple(strategy.strategy_id for strategy in partition),
                )
                for partition in partitions
            ]
            for future in as_completed(futures):
                batch = future.result()
                batches.append(batch)
                print(
                    "completed_G11_core_paths="
                    f"{sum(item.strategy_count for item in batches)}/{len(strategies)}",
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
        raise DataQualityError("G11 completion gate found invalid scenarios")
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
            regime=regime,
            rebalances=artifacts["rebalances"],
        ),
        resolved_config_toml=_render_g11_resolved_config_toml(
            config=config,
            data=data,
            reference_g00=reference_g00,
        ),
    )
    return G11RunResult(
        run_id=config.context.run_id,
        output_dir=bundle.output_dir,
        manifest_path=bundle.manifest_path,
        strategy_count=len(strategies),
        scenario_count=len(summary),
        comparison_count=len(comparison),
        scaled_rebalance_count=scaled_count,
        formal_run_eligible=False,
    )


def _prepare_g11_regime(
    context: ExperimentRunContext, data: LoadedExperimentData
) -> tuple[pd.DataFrame, pd.Series]:
    parameters = _frozen_parameters(context)
    benchmark = data.benchmark.reset_index(drop=True)
    regime = strict_lagged_spy_quartiles(
        benchmark,
        realized_vol_window=int(parameters["realized_vol_window"]),
        history_sessions=756,
    )
    direct = spy_realized_volatility(
        benchmark, window=int(parameters["realized_vol_window"])
    )
    if not regime.index.equals(direct.index) or not np.allclose(
        regime["spy_realized_volatility"].to_numpy(dtype=float),
        direct.to_numpy(dtype=float),
        rtol=0.0,
        atol=0.0,
        equal_nan=True,
    ):
        raise DataQualityError("G11 SPY RV21 differs from the direct frozen calculation")
    regime = regime.copy()
    quartile_available = regime["volatility_quartile"].notna()
    regime["high_volatility"] = regime["volatility_quartile"].eq(4).astype(
        "boolean"
    )
    regime.loc[~quartile_available, "high_volatility"] = pd.NA
    daily_allocation = continuous_spy_allocation(
        regime,
        annual_target_volatility=float(parameters["annual_target_volatility"]),
        maximum_scale=float(parameters["maximum_scale"]),
    )
    regime["target_risk_allocation"] = daily_allocation
    regime["target_scaled_source_volatility"] = (
        regime["spy_realized_volatility"] * daily_allocation
    )
    regime["cap_is_binding"] = (
        pd.to_numeric(regime["spy_realized_volatility"], errors="coerce")
        .le(float(parameters["annual_target_volatility"]))
        .astype("boolean")
    )
    dates_by_frequency = _signal_dates_by_frequency(data)
    signal_dates = dates_by_frequency["weekly"].union(dates_by_frequency["monthly"])
    allocation = continuous_spy_allocation(
        regime,
        signal_dates,
        annual_target_volatility=float(parameters["annual_target_volatility"]),
        maximum_scale=float(parameters["maximum_scale"]),
    )
    _validate_formal_signal_regime(regime, signal_dates)
    return regime, allocation


def _validate_formal_signal_regime(
    regime: pd.DataFrame, signal_dates: pd.DatetimeIndex
) -> None:
    required = [
        "spy_realized_volatility",
        "lagged_q25",
        "lagged_q50",
        "lagged_q75",
        "volatility_quartile",
        "high_volatility",
        "target_risk_allocation",
        "target_scaled_source_volatility",
        "cap_is_binding",
    ]
    sampled = regime.reindex(signal_dates)
    if sampled[required].isna().any().any():
        raise DataQualityError("G11 formal signal risk state is incomplete")
    _validate_available_quartile_contract(
        sampled, require_all=True, label="formal signal"
    )
    rv = pd.to_numeric(sampled["spy_realized_volatility"], errors="raise")
    allocation = pd.to_numeric(sampled["target_risk_allocation"], errors="raise")
    expected = np.minimum(1.0, 0.15 / rv.to_numpy(dtype=float))
    if not np.allclose(allocation, expected, rtol=0.0, atol=1e-15):
        raise DataQualityError("G11 formal signal allocation violates the frozen rule")
    scaled = pd.to_numeric(
        sampled["target_scaled_source_volatility"], errors="raise"
    )
    if not np.allclose(scaled, rv * allocation, rtol=0.0, atol=1e-15):
        raise DataQualityError("G11 scaled source volatility is inconsistent")
    if scaled.gt(0.15 + 1e-15).any():
        raise DataQualityError("G11 scaled source volatility exceeded 15%")
    cap = sampled["cap_is_binding"].astype("boolean")
    if cap.isna().any() or not cap.equals(rv.le(0.15).astype("boolean")):
        raise DataQualityError("G11 formal signal cap-binding state is inconsistent")


def _validate_available_quartile_contract(
    frame: pd.DataFrame, *, require_all: bool, label: str
) -> None:
    columns = [
        "spy_realized_volatility",
        "lagged_q25",
        "lagged_q50",
        "lagged_q75",
        "volatility_quartile",
    ]
    numeric = frame[columns].apply(pd.to_numeric, errors="coerce")
    threshold_available = numeric[
        ["lagged_q25", "lagged_q50", "lagged_q75", "volatility_quartile"]
    ].notna().all(axis=1)
    if require_all and not threshold_available.all():
        raise DataQualityError(f"G11 {label} quartile state is incomplete")
    available = numeric.loc[threshold_available]
    if available.empty:
        if require_all:
            raise DataQualityError(f"G11 {label} quartile state is empty")
        return
    values = available.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise DataQualityError(f"G11 {label} quartile state is non-finite")
    if (
        available["spy_realized_volatility"].le(0.0).any()
        or available["lagged_q25"].le(0.0).any()
        or available["lagged_q50"].le(0.0).any()
        or available["lagged_q75"].le(0.0).any()
        or available["lagged_q25"].gt(available["lagged_q50"]).any()
        or available["lagged_q50"].gt(available["lagged_q75"]).any()
    ):
        raise DataQualityError(f"G11 {label} quartile thresholds are invalid")
    rv = available["spy_realized_volatility"]
    expected = pd.Series(4, index=available.index, dtype=int)
    expected.loc[rv.le(available["lagged_q75"])] = 3
    expected.loc[rv.le(available["lagged_q50"])] = 2
    expected.loc[rv.le(available["lagged_q25"])] = 1
    actual = available["volatility_quartile"]
    if not actual.isin([1, 2, 3, 4]).all() or not np.array_equal(
        actual.to_numpy(dtype=int), expected.to_numpy(dtype=int)
    ):
        raise DataQualityError(f"G11 {label} quartile violates strict boundaries")


def _run_g11_core_batch_worker(
    context: ExperimentRunContext,
    allow_review_dataset: bool,
    strategy_ids: tuple[str, ...],
) -> _G11CoreBatch:
    data = load_experiment_data(context, allow_review_dataset=allow_review_dataset)
    regime, allocation = _prepare_g11_regime(context, data)
    lookup = {strategy.strategy_id: strategy for strategy in context.strategies}
    try:
        strategies = tuple(lookup[strategy_id] for strategy_id in strategy_ids)
    except KeyError as error:
        raise ValueError(f"unknown G11 worker strategy: {error.args[0]}") from error
    return _run_g11_core_batch(
        context=context,
        data=data,
        regime=regime,
        allocation=allocation,
        strategies=strategies,
    )


def _run_g11_core_batch(
    *,
    context: ExperimentRunContext,
    data: LoadedExperimentData,
    regime: pd.DataFrame,
    allocation: pd.Series,
    strategies: tuple[StrategySpec, ...],
) -> _G11CoreBatch:
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
                risk_allocation=allocation,
                risk_free_daily=data.risk_free_daily,
                full_audit=True,
            )
            for cost_bps in costs:
                result = replay_linear_cost(zero_cost, cost_bps=float(cost_bps))
                _validate_core_path_state_identity(zero_cost, result)
                _append_g11_scenario(
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
                    risk_allocation=allocation,
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
                    _append_g11_scenario(
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
        _attach_regime_audit(strategy_summary, rebalances, regime)
        diagnostic_rows.extend(
            _daily_spy_risk_state_diagnostics(
                strategy=strategy,
                primary_cost=primary_cost,
                regime=regime,
                audit_start=_AUDIT_START,
                audit_end=data.evaluation_end,
            )
        )
        summary_frames.append(strategy_summary)
        nav_frames.append(pd.concat(strategy_nav, ignore_index=True))
        rebalance_frames.append(rebalances)
        holding_frames.append(_concat_or_empty(strategy_holdings, "holdings"))
        trade_frames.append(_concat_or_empty(strategy_trades, "trades"))
        diagnostic_frames.append(pd.DataFrame(diagnostic_rows))

    return _G11CoreBatch(
        strategy_count=len(strategies),
        summary=pd.concat(summary_frames, ignore_index=True),
        nav=pd.concat(nav_frames, ignore_index=True),
        rebalances=pd.concat(rebalance_frames, ignore_index=True),
        holdings=pd.concat(holding_frames, ignore_index=True),
        trades=pd.concat(trade_frames, ignore_index=True),
        diagnostics=pd.concat(diagnostic_frames, ignore_index=True),
    )


def _validate_core_path_state_identity(reference: object, candidate: object) -> None:
    """Reject a cost or borrow path that changes state, selection, or targets."""

    identity_columns = [
        "signal_date",
        "execution_date",
        "execution_status",
        "target_risk_allocation",
        "requested_selected_sids",
        "missing_target_sids",
    ]
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
    left = reference.rebalances.reset_index(drop=True)  # type: ignore[attr-defined]
    right = candidate.rebalances.reset_index(drop=True)  # type: ignore[attr-defined]
    if all(column in left and column in right for column in requested_exposure_columns):
        exposure_columns = requested_exposure_columns
    elif all(column in left and column in right for column in target_exposure_columns):
        exposure_columns = target_exposure_columns
    else:
        raise DataQualityError("G11 core-path audit lacks state or targets")
    required = identity_columns + exposure_columns
    for label, frame in (("reference", left), ("candidate", right)):
        missing = set(required).difference(frame.columns)
        if missing:
            raise DataQualityError(
                f"G11 {label} core-path audit lacks columns: {sorted(missing)}"
            )
    if len(left) != len(right):
        raise DataQualityError("G11 cost/borrow path changed rebalance count")
    for column in (
        "signal_date",
        "execution_date",
        "execution_status",
        "requested_selected_sids",
        "missing_target_sids",
    ):
        if not left[column].fillna("").astype(str).equals(
            right[column].fillna("").astype(str)
        ):
            raise DataQualityError(f"G11 cost/borrow path changed frozen {column}")
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
        raise DataQualityError("G11 cost/borrow path changed frozen state or targets")


def _append_g11_scenario(
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


def _append_pnl_attribution_diagnostics(
    *,
    strategy: StrategySpec,
    result: object,
    cost_bps: float,
    borrow_fee: float,
    diagnostic_rows: list[dict[str, object]],
) -> None:
    """Persist the daily P&L bridge and fail closed when it does not close."""

    nav = result.nav.sort_index().copy()  # type: ignore[attr-defined]
    rebalances = result.rebalances.copy()  # type: ignore[attr-defined]
    index = pd.DatetimeIndex(nav.index).normalize()
    if not index.is_unique or not index.is_monotonic_increasing:
        raise DataQualityError("G11 attribution requires a unique ordered NAV")
    flow_long = pd.Series(0.0, index=index)
    flow_short = pd.Series(0.0, index=index)
    transaction_cost = pd.Series(0.0, index=index)
    if not rebalances.empty:
        execution_dates = pd.DatetimeIndex(
            pd.to_datetime(rebalances["execution_date"], errors="raise")
        ).normalize()
        if execution_dates.has_duplicates or len(execution_dates.difference(index)):
            raise DataQualityError("G11 attribution rebalance dates are invalid")
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
            "G11 P&L attribution has an unsupported daily bridge: "
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
        raise DataQualityError("G11 P&L attribution contains non-finite values")
    maximum_error = float(closure.abs().max())
    if maximum_error > 1e-12:
        raise DataQualityError(
            f"G11 daily P&L attribution does not close: {maximum_error:.3e}"
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
    result.nav["pnl_bridge_has_frozen_evidence"] = (  # type: ignore[attr-defined]
        evidenced.astype(int)
    )
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
        "group_id": "G11",
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


def _attach_regime_audit(
    summary: pd.DataFrame, rebalances: pd.DataFrame, regime: pd.DataFrame
) -> None:
    dates = pd.DatetimeIndex(pd.to_datetime(rebalances["signal_date"])).normalize()
    sampled = regime.reindex(dates)
    required = [
        "spy_realized_volatility",
        "lagged_q25",
        "lagged_q50",
        "lagged_q75",
        "volatility_quartile",
        "high_volatility",
        "target_risk_allocation",
        "target_scaled_source_volatility",
        "cap_is_binding",
    ]
    if sampled[required].isna().any().any():
        raise DataQualityError("G11 regime audit is unavailable for a rebalance")
    actual = pd.to_numeric(rebalances["target_risk_allocation"], errors="coerce")
    expected = sampled["target_risk_allocation"].to_numpy(dtype=float)
    if actual.isna().any() or not np.allclose(
        actual.to_numpy(dtype=float), expected, rtol=0.0, atol=1e-12
    ):
        raise DataQualityError("G11 engine allocation differs from the frozen rule")
    for column in required:
        if column != "target_risk_allocation":
            rebalances[column] = sampled[column].to_numpy()
    rebalances["continuous_scale"] = expected

    grouped = rebalances.groupby("strategy_id", observed=True)
    stats = grouped["target_risk_allocation"].agg(["mean", "min", "max"])
    below = grouped["target_risk_allocation"].apply(
        lambda values: float(pd.to_numeric(values, errors="raise").lt(1.0).mean())
    )
    q4_fraction = grouped["high_volatility"].mean()
    summary["average_target_risk_allocation"] = summary["strategy_id"].map(
        stats["mean"]
    )
    summary["minimum_target_risk_allocation"] = summary["strategy_id"].map(
        stats["min"]
    )
    summary["maximum_target_risk_allocation"] = summary["strategy_id"].map(
        stats["max"]
    )
    summary["below_full_investment_fraction"] = summary["strategy_id"].map(below)
    summary["high_vol_rebalance_fraction"] = summary["strategy_id"].map(
        q4_fraction
    )


def _daily_spy_risk_state_diagnostics(
    *,
    strategy: StrategySpec,
    primary_cost: float,
    regime: pd.DataFrame,
    audit_start: object,
    audit_end: object,
) -> list[dict[str, object]]:
    start = pd.Timestamp(audit_start).normalize()
    end = pd.Timestamp(audit_end).normalize()
    frame = regime.loc[start:end].reset_index()
    frame.rename(columns={frame.columns[0]: "date"}, inplace=True)
    primary_borrow = (
        0.0 if strategy.portfolio_mode is PortfolioMode.LONG_ONLY else 0.01
    )
    frame["group_id"] = "G11"
    frame["strategy_id"] = strategy.strategy_id
    frame["portfolio_mode"] = strategy.portfolio_mode.value
    frame["variant_id"] = "base"
    frame["cost_bps"] = float(primary_cost)
    frame["borrow_fee_annual"] = primary_borrow
    frame["scope"] = "daily_spy_risk_state"
    frame["diagnostic"] = "causal_spy_rv21_continuous_scale"
    frame["value"] = frame["target_risk_allocation"]
    return frame.to_dict(orient="records")


def _validate_daily_spy_risk_state(
    diagnostics: pd.DataFrame, data: LoadedExperimentData
) -> None:
    daily = diagnostics.loc[diagnostics["scope"].eq("daily_spy_risk_state")].copy()
    required = {
        "date",
        "strategy_id",
        "spy_realized_volatility",
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
            f"G11 daily risk state lacks columns: {sorted(missing)}"
        )
    if len(daily) != 108_648:
        raise RuntimeError(
            f"G11 requires 108648 persisted daily risk rows, got {len(daily)}"
        )
    daily["date"] = pd.to_datetime(daily["date"], errors="raise").dt.normalize()
    if (
        daily["date"].min() != _AUDIT_START
        or daily["date"].max() != data.evaluation_end
    ):
        raise DataQualityError("G11 daily risk state escaped the frozen interval")
    key = ["strategy_id", "date"]
    if daily.duplicated(key).any() or daily["strategy_id"].nunique() != 36:
        raise DataQualityError("G11 daily risk state path/date identities are invalid")
    counts = daily.groupby("strategy_id", observed=True).size()
    if not counts.eq(3_018).all():
        raise DataQualityError("G11 each core path must persist exactly 3018 risk rows")
    authoritative = pd.DatetimeIndex(pd.to_datetime(data.sessions)).normalize()
    authoritative = authoritative[
        (authoritative >= _AUDIT_START) & (authoritative <= data.evaluation_end)
    ]
    if (
        len(authoritative) != 3_018
        or authoritative.hasnans
        or authoritative.has_duplicates
        or not authoritative.is_monotonic_increasing
    ):
        raise DataQualityError("G11 authoritative daily audit calendar changed")
    for strategy_id, frame in daily.groupby("strategy_id", observed=True, sort=False):
        dates = pd.DatetimeIndex(frame["date"]).sort_values()
        if not dates.equals(authoritative):
            raise DataQualityError(
                f"G11 daily risk state dates differ from the calendar: {strategy_id}"
            )

    rv = pd.to_numeric(daily["spy_realized_volatility"], errors="coerce")
    allocation = pd.to_numeric(daily["target_risk_allocation"], errors="coerce")
    scaled = pd.to_numeric(
        daily["target_scaled_source_volatility"], errors="coerce"
    )
    if (
        rv.isna().any()
        or allocation.isna().any()
        or scaled.isna().any()
        or not np.isfinite(rv).all()
        or not np.isfinite(allocation).all()
        or not np.isfinite(scaled).all()
        or rv.le(0.0).any()
        or allocation.le(0.0).any()
        or allocation.gt(1.0).any()
    ):
        raise DataQualityError("G11 daily RV/allocation state must be finite and positive")
    expected = np.minimum(1.0, 0.15 / rv.to_numpy(dtype=float))
    if not np.allclose(allocation, expected, rtol=0.0, atol=1e-15):
        raise DataQualityError("G11 daily allocation violates min(1, 0.15/RV21)")
    if not np.allclose(scaled, rv * allocation, rtol=0.0, atol=1e-15):
        raise DataQualityError("G11 daily scaled source volatility is inconsistent")
    cap = daily["cap_is_binding"].astype("boolean")
    expected_cap = rv.le(0.15).astype("boolean")
    if cap.isna().any() or not cap.equals(expected_cap):
        raise DataQualityError("G11 daily cap-binding state is inconsistent")

    quartile_columns = [
        "lagged_q25",
        "lagged_q50",
        "lagged_q75",
        "volatility_quartile",
    ]
    availability = daily[quartile_columns].notna()
    partial = availability.any(axis=1) & ~availability.all(axis=1)
    if partial.any():
        raise DataQualityError("G11 daily quartile state is only partially available")
    quartile = pd.to_numeric(daily["volatility_quartile"], errors="coerce")
    if (~quartile.dropna().isin([1, 2, 3, 4])).any():
        raise DataQualityError("G11 daily quartile label is invalid")
    _validate_available_quartile_contract(
        daily, require_all=False, label="daily diagnostic"
    )
    quartile_available = availability.all(axis=1)
    high = daily["high_volatility"].astype("boolean")
    if not high.notna().equals(quartile_available):
        raise DataQualityError("G11 high-volatility availability differs from quartiles")
    if not high.loc[quartile_available].equals(
        quartile.loc[quartile_available].eq(4).astype("boolean")
    ):
        raise DataQualityError("G11 high-volatility flag conflicts with quartile")
    first_available = daily.loc[quartile.notna(), "date"].min()
    if first_available != pd.Timestamp("2016-02-03"):
        raise DataQualityError(
            f"G11 first complete diagnostic quartile changed: {first_available}"
        )

    shared = [
        "spy_realized_volatility",
        "lagged_q25",
        "lagged_q50",
        "lagged_q75",
        "volatility_quartile",
        "high_volatility",
        "target_risk_allocation",
        "target_scaled_source_volatility",
        "cap_is_binding",
    ]
    canonical = daily.sort_values(["date", "strategy_id"]).groupby(
        "date", observed=True, sort=True
    )[shared].first()
    merged = daily.merge(
        canonical.reset_index(),
        on="date",
        how="left",
        suffixes=("", "__canonical"),
        validate="many_to_one",
    )
    for column in shared:
        left = merged[column]
        right = merged[f"{column}__canonical"]
        if pd.api.types.is_numeric_dtype(left) and column not in {
            "volatility_quartile",
            "high_volatility",
            "cap_is_binding",
        }:
            same = np.isclose(
                pd.to_numeric(left, errors="coerce"),
                pd.to_numeric(right, errors="coerce"),
                rtol=0.0,
                atol=0.0,
                equal_nan=True,
            )
            if not bool(np.all(same)):
                raise DataQualityError(
                    f"G11 shared daily field differs across paths: {column}"
                )
        else:
            normalized_left = left.astype("string").fillna("<NA>")
            normalized_right = right.astype("string").fillna("<NA>")
            if not normalized_left.equals(normalized_right):
                raise DataQualityError(
                    f"G11 shared daily field differs across paths: {column}"
                )


def _validate_g00_path_identity(
    rebalances: pd.DataFrame,
    holdings: pd.DataFrame,
    reference: _G00Reference,
) -> None:
    """Hard-gate G11 as G00 targets multiplied only by the frozen scalar."""

    candidate_rebalances = rebalances.copy()
    baseline_rebalances = reference.rebalances.copy()
    for frame in (candidate_rebalances, baseline_rebalances):
        frame["signal_date"] = pd.to_datetime(frame["signal_date"]).dt.normalize()
        frame["execution_date"] = pd.to_datetime(
            frame["execution_date"]
        ).dt.normalize()
    baseline_rebalances["strategy_id"] = baseline_rebalances[
        "strategy_id"
    ].astype(str).str.replace(r"^G00__", "G11__", regex=True)
    rebalance_key = ["strategy_id", "signal_date"]
    if (
        candidate_rebalances.duplicated(rebalance_key).any()
        or baseline_rebalances.duplicated(rebalance_key).any()
    ):
        raise DataQualityError("G11/G00 reference rebalances are not unique")
    candidate_indexed = candidate_rebalances.set_index(rebalance_key).sort_index()
    baseline_indexed = baseline_rebalances.set_index(rebalance_key).sort_index()
    if not candidate_indexed.index.equals(baseline_indexed.index):
        raise DataQualityError("G11 and G00 rebalance identities differ")
    for column in ("execution_date", "execution_status"):
        if not candidate_indexed[column].equals(baseline_indexed[column]):
            raise DataQualityError(f"G11 and G00 {column} paths differ")
    for column in (
        "requested_selected_count",
        "requested_selected_sids",
        "missing_target_count",
        "missing_target_sids",
    ):
        if column in candidate_indexed and column in baseline_indexed:
            if not candidate_indexed[column].fillna("").astype(str).equals(
                baseline_indexed[column].fillna("").astype(str)
            ):
                raise DataQualityError(f"G11 and G00 {column} paths differ")

    allocation = pd.to_numeric(
        candidate_indexed["target_risk_allocation"], errors="coerce"
    )
    if (
        allocation.isna().any()
        or not np.isfinite(allocation).all()
        or allocation.le(0.0).any()
        or allocation.gt(1.0).any()
    ):
        raise DataQualityError("G11 rebalance allocation is invalid")
    mode = candidate_indexed["portfolio_mode"].astype(str)
    expected_long = allocation.where(mode.eq("long_only"), allocation / 2.0)
    expected_short = pd.Series(
        np.where(mode.eq("long_only"), 0.0, allocation / 2.0),
        index=allocation.index,
    )
    expected_net = allocation.where(mode.eq("long_only"), 0.0)
    exposure_expected = {
        "long_exposure": expected_long,
        "short_exposure": expected_short,
        "gross_exposure": allocation,
        "net_exposure": expected_net,
    }
    for suffix, expected_values in exposure_expected.items():
        column = f"requested_{suffix}"
        actual = pd.to_numeric(candidate_indexed[column], errors="coerce")
        if actual.isna().any() or not np.allclose(
            actual, expected_values, rtol=0.0, atol=1e-12
        ):
            raise DataQualityError(f"G11 {column} violates the frozen exposure rule")
    execution_status = candidate_indexed["execution_status"].astype(str)
    executed = execution_status.str.startswith("executed")
    skipped = ~executed
    for suffix in exposure_expected:
        target_column = f"target_{suffix}"
        target = pd.to_numeric(candidate_indexed[target_column], errors="coerce")
        if target.isna().any():
            raise DataQualityError(f"G11 {target_column} is unavailable")
        if skipped.any():
            pretrade_column = f"pretrade_{suffix}"
            pretrade = pd.to_numeric(
                candidate_indexed[pretrade_column], errors="coerce"
            )
            if pretrade.isna().any() or not np.allclose(
                target.loc[skipped],
                pretrade.loc[skipped],
                rtol=0.0,
                atol=1e-12,
            ):
                raise DataQualityError(
                    f"G11 skipped {target_column} changed the held book"
                )
    target_cash_all = pd.to_numeric(
        candidate_indexed["target_cash_weight"], errors="coerce"
    )
    target_net_all = pd.to_numeric(
        candidate_indexed["target_net_exposure"], errors="coerce"
    )
    if target_cash_all.isna().any() or target_net_all.isna().any() or not np.allclose(
        target_cash_all, 1.0 - target_net_all, rtol=0.0, atol=1e-12
    ):
        raise DataQualityError("G11 target cash weight is inconsistent")
    if skipped.any():
        for column in ("l1_turnover", "cost_amount"):
            values = pd.to_numeric(candidate_indexed[column], errors="coerce")
            if values.isna().any() or not np.allclose(
                values.loc[skipped], 0.0, rtol=0.0, atol=1e-12
            ):
                raise DataQualityError(
                    f"G11 skipped rebalances must have zero {column}"
                )

    baseline_holdings = pd.read_parquet(
        reference.root / "artifacts" / "holdings.parquet"
    )
    candidate_holdings = holdings.copy()
    baseline_holdings["strategy_id"] = baseline_holdings["strategy_id"].astype(
        str
    ).str.replace(r"^G00__", "G11__", regex=True)
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
        raise DataQualityError("G11/G00 target holdings are not unique")
    candidate_targets = candidate_holdings.set_index(holding_key).sort_index()
    baseline_targets = baseline_holdings.set_index(holding_key).sort_index()
    if not candidate_targets.index.equals(baseline_targets.index):
        raise DataQualityError("G11 and G00 selected holding identities differ")
    holding_rebalance_index = pd.MultiIndex.from_arrays(
        [
            candidate_targets.index.get_level_values("strategy_id"),
            candidate_targets.index.get_level_values("signal_date"),
        ],
        names=rebalance_key,
    )
    holding_allocation = allocation.reindex(holding_rebalance_index)
    if holding_allocation.isna().any():
        raise DataQualityError("G11 holding allocation lacks a rebalance state")
    expected_weights = (
        pd.to_numeric(
            baseline_targets["target_weight"], errors="coerce"
        ).to_numpy(dtype=float)
        * holding_allocation.to_numpy(dtype=float)
    )
    actual_weights = pd.to_numeric(
        candidate_targets["target_weight"], errors="coerce"
    ).to_numpy(dtype=float)
    if (
        not np.isfinite(expected_weights).all()
        or not np.isfinite(actual_weights).all()
        or not np.allclose(
            actual_weights, expected_weights, rtol=0.0, atol=1e-12
        )
    ):
        raise DataQualityError("G11 target weights are not scalar multiples of G00")

    executed_index = candidate_indexed.index[executed]
    executed_holdings = candidate_holdings.loc[
        candidate_holdings.set_index(rebalance_key).index.isin(executed_index)
    ].copy()
    weights = pd.to_numeric(executed_holdings["target_weight"], errors="coerce")
    if weights.isna().any() or not np.isfinite(weights).all():
        raise DataQualityError("G11 executed target holdings contain invalid weights")
    executed_holdings["long_component"] = weights.clip(lower=0.0)
    executed_holdings["short_component"] = -weights.clip(upper=0.0)
    executed_holdings["gross_component"] = weights.abs()
    executed_holdings["net_component"] = weights
    aggregate = executed_holdings.groupby(rebalance_key, observed=True)[
        [
            "long_component",
            "short_component",
            "gross_component",
            "net_component",
        ]
    ].sum()
    if not aggregate.index.equals(executed_index):
        aggregate = aggregate.reindex(executed_index)
    if aggregate.isna().any().any():
        raise DataQualityError("G11 executed rebalance lacks persisted target holdings")
    aggregate_expected = {
        "target_long_exposure": aggregate["long_component"],
        "target_short_exposure": aggregate["short_component"],
        "target_gross_exposure": aggregate["gross_component"],
        "target_net_exposure": aggregate["net_component"],
    }
    for column, expected_values in aggregate_expected.items():
        actual = pd.to_numeric(candidate_indexed.loc[executed, column], errors="coerce")
        if actual.isna().any() or not np.allclose(
            actual, expected_values, rtol=0.0, atol=1e-12
        ):
            raise DataQualityError(
                f"G11 executed {column} differs from persisted target holdings"
            )


def _validate_main_counts(summary: pd.DataFrame, context: ExperimentRunContext) -> None:
    if len(context.strategies) != 36 or len(summary) != 288:
        raise RuntimeError(
            "G11 requires 36 core paths and 288 scenarios, got "
            f"{len(context.strategies)} and {len(summary)}"
        )
    key = ["strategy_id", "variant_id", "cost_bps", "borrow_fee_annual"]
    if summary.duplicated(key).any():
        raise RuntimeError("G11 summary contains duplicate scenario identities")
    if int(summary["is_primary_scenario"].sum()) != 36:
        raise RuntimeError("G11 must contain exactly 36 primary scenarios")
    mode_counts = summary.groupby("portfolio_mode", observed=True).size().to_dict()
    if mode_counts != {"long_only": 72, "long_short": 216}:
        raise RuntimeError(f"G11 scenario mode counts changed: {mode_counts}")


def _validate_artifact_counts(
    artifacts: dict[str, pd.DataFrame], data: LoadedExperimentData
) -> None:
    nav = artifacts["nav"]
    if len(data.evaluation_sessions) != 2_134 or len(nav) != 614_592:
        raise RuntimeError(
            "G11 requires 2134 sessions and 614592 NAV rows, got "
            f"{len(data.evaluation_sessions)} and {len(nav)}"
        )
    key = ["strategy_id", "cost_bps", "borrow_fee_annual"]
    counts = nav.groupby(key, observed=True).size()
    if len(counts) != 288 or not counts.eq(2_134).all():
        raise RuntimeError("G11 every scenario must contain exactly 2134 NAV rows")
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
        raise DataQualityError(f"G11 NAV attribution lacks columns: {sorted(missing)}")
    numeric = nav[required_pnl].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric).all().all():
        raise DataQualityError("G11 NAV attribution contains non-finite values")
    if numeric["pnl_unexplained_bridge"].abs().max() > 1e-10:
        raise DataQualityError("G11 unexplained daily P&L bridge exceeds tolerance")
    if numeric["pnl_closure_error"].abs().max() > 1e-12:
        raise DataQualityError("G11 daily P&L closure exceeds tolerance")
    unsupported = (
        numeric["pnl_action_execution_bridge"].abs().gt(1e-10)
        & numeric["pnl_bridge_has_frozen_evidence"].ne(1.0)
    )
    if unsupported.any():
        raise DataQualityError("G11 material action bridge lacks frozen evidence")
    _validate_daily_spy_risk_state(artifacts["diagnostics"], data)


def _attach_g00_comparisons(
    summary: pd.DataFrame, reference: pd.DataFrame
) -> pd.DataFrame:
    lookup = reference.set_index(["strategy_id", "cost_bps", "borrow_fee_annual"])
    if not lookup.index.is_unique:
        raise DataQualityError("G00 reference contains duplicate scenario identities")
    rows: list[dict[str, object]] = []
    for index, row in summary.iterrows():
        strategy_id = str(row["strategy_id"])
        parent = strategy_id.replace("G11__", "G00__", 1)
        if parent == strategy_id:
            raise DataQualityError(f"invalid G11 strategy identity: {strategy_id}")
        key = (parent, float(row["cost_bps"]), float(row["borrow_fee_annual"]))
        if key not in lookup.index:
            raise DataQualityError(f"G11 lacks matching G00 scenario: {key}")
        baseline = lookup.loc[key]
        for metric in _COMPARISON_METRICS:
            delta = float(row[metric] - baseline[metric])
            summary.at[index, f"delta_vs_g00_{metric}"] = delta
            rows.append(
                {
                    "group_id": "G11",
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
            f"G11 must record 1440 G00 comparison rows, got {len(comparison)}"
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


def _frozen_parameters(context: ExperimentRunContext) -> dict[str, object]:
    raw = context.group.raw.get("parameters")
    if not isinstance(raw, dict):
        raise ValueError("G11 parameters table is missing")
    expected: dict[str, object] = {
        "realized_vol_window": 21,
        "annual_target_volatility": 0.15,
        "maximum_scale": 1.0,
    }
    for key, value in expected.items():
        actual = raw.get(key)
        if isinstance(value, float):
            matches = isinstance(actual, (int, float)) and not isinstance(
                actual, bool
            ) and np.isclose(float(actual), value, rtol=0.0, atol=1e-15)
        else:
            matches = actual == value
        if not matches:
            raise ValueError(f"G11 frozen parameter changed: {key}={actual!r}")
    return expected


def _render_g11_resolved_config_toml(
    *,
    config: G11RunConfig,
    data: LoadedExperimentData,
    reference_g00: _G00Reference,
) -> str:
    """Render the base resolved spec plus immutable G11 run provenance."""

    context = config.context
    project = context.project_root
    design = (
        project
        / "docs"
        / "20_experiments"
        / "G11_spy_continuous_scale"
        / "design.md"
    )
    program = project / "config" / "experiments" / "program.toml"
    freeze_record = (
        context.data_root / "curated" / context.dataset_version / "FROZEN.json"
    )
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
    if not rendered.endswith("\n"):  # pragma: no cover - serializer contract
        raise RuntimeError("G11 resolved TOML must end with newline")
    return rendered


def _validate_frozen_inputs(
    context: ExperimentRunContext, data: LoadedExperimentData
) -> None:
    _frozen_parameters(context)
    if context.dataset_version != _FROZEN_DATASET_VERSION:
        raise DataQualityError("G11 requires the frozen v3 dataset version")
    if data.dataset_manifest_sha256 != _FROZEN_DATASET_MANIFEST_SHA256:
        raise DataQualityError("G11 frozen dataset manifest hash changed")
    freeze_record = context.data_root / "curated" / context.dataset_version / "FROZEN.json"
    if (
        not freeze_record.is_file()
        or sha256_file(freeze_record) != _FROZEN_RECORD_SHA256
    ):
        raise DataQualityError("G11 frozen dataset record hash changed")
    design = (
        context.project_root
        / "docs"
        / "20_experiments"
        / "G11_spy_continuous_scale"
        / "design.md"
    )
    if not design.is_file():
        raise FileNotFoundError(design)
    if sha256_file(design) != _FROZEN_DESIGN_SHA256:
        raise DataQualityError("G11 preregistered design hash changed")
    program = context.project_root / "config" / "experiments" / "program.toml"
    if not program.is_file() or sha256_file(program) != _FROZEN_PROGRAM_SHA256:
        raise DataQualityError("G11 frozen experiment program hash changed")
    if sha256_file(context.group.path) != _FROZEN_GROUP_CONFIG_SHA256:
        raise DataQualityError("G11 frozen group config hash changed")
    costs = context.group.program.raw.get("costs")
    long_short = context.group.program.raw.get("long_short")
    if not isinstance(costs, dict) or not isinstance(long_short, dict):
        raise DataQualityError("G11 frozen cost tables are missing")
    if (
        costs.get("scenarios_bps") != [0, 5, 10, 20]
        or costs.get("weekly_primary_bps") != 10
        or costs.get("monthly_primary_bps") != 5
        or long_short.get("borrow_fee_scenarios_annual") != [0.0, 0.01, 0.03]
        or long_short.get("primary_borrow_fee_annual") != 0.01
    ):
        raise DataQualityError("G11 frozen cost or borrow-fee contract changed")


def _validate_reference_anchor(reference_g00: _G00Reference) -> None:
    if (
        reference_g00.manifest.get("run_id") != "g00-frozen-v3-v1"
        or reference_g00.manifest_sha256 != _FROZEN_G00_MANIFEST_SHA256
    ):
        raise DataQualityError("G11 G00 reference is not the frozen v3-v1 bundle")


def _validate_runtime_roots(config: G11RunConfig) -> None:
    data_root = config.context.data_root.resolve()
    output_root = config.context.output_root.resolve()
    project_root = config.context.project_root.resolve()
    if (
        data_root.name.lower() != "data"
        or output_root.name.lower() != "results"
        or data_root.parent != output_root.parent
    ):
        raise DataQualityError("G11 requires sibling data/results local runtime roots")
    try:
        output_root.relative_to(project_root)
    except ValueError:
        pass
    else:
        raise DataQualityError("G11 full bundle cannot be written inside the repository")
    try:
        config.reference_g00_root.resolve().relative_to(output_root)
    except ValueError as error:
        raise DataQualityError(
            "G11 G00 reference must come from the local results root"
        ) from error


def _manifest_metadata(
    *,
    config: G11RunConfig,
    data: LoadedExperimentData,
    reference_g00: _G00Reference,
    summary: pd.DataFrame,
    comparison: pd.DataFrame,
    regime: pd.DataFrame,
    rebalances: pd.DataFrame,
) -> dict[str, object]:
    project = config.context.project_root
    design = (
        project
        / "docs"
        / "20_experiments"
        / "G11_spy_continuous_scale"
        / "design.md"
    )
    program = project / "config" / "experiments" / "program.toml"
    audit = regime.loc[_AUDIT_START : data.evaluation_end]
    signal_rows = rebalances.drop_duplicates(["strategy_id", "signal_date"])
    blockers = {str(value) for value in data.dataset_manifest.get("formal_blockers", [])}
    blockers.add("systematic_G11_bundle_is_prototype")
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
            "risk_source": "SPY total-return proxy RV21",
            "return_type": "close-to-close simple return",
            "realized_volatility_window": 21,
            "realized_volatility_ddof": 1,
            "annualization": "sqrt(252)",
            "annual_target_volatility": 0.15,
            "maximum_scale": 1.0,
            "scale_rule": "min(1, 0.15 / current_signal_close_SPY_RV21)",
            "quartile_role": "diagnostic_only",
            "quartile_history_sessions": 756,
            "quartile_current_observation_excluded": True,
            "hysteresis": False,
            "leverage": False,
            "daily_audit_start": str(_AUDIT_START.date()),
            "daily_audit_sessions": len(audit),
            "minimum_daily_allocation": float(
                audit["target_risk_allocation"].min()
            ),
            "maximum_daily_allocation": float(
                audit["target_risk_allocation"].max()
            ),
            "daily_below_full_fraction": float(
                audit["target_risk_allocation"].lt(1.0).mean()
            ),
            "signal_below_full_fraction": float(
                signal_rows["target_risk_allocation"].lt(1.0).mean()
            ),
            "minimum_signal_allocation": float(
                signal_rows["target_risk_allocation"].min()
            ),
        },
        "counts": {
            "core_strategies": len(config.context.strategies),
            "event_paths_simulated": 72,
            "main_scenarios": len(summary),
            "primary_scenarios": int(summary["is_primary_scenario"].sum()),
            "primary_scaled_rebalances": int(
                rebalances["target_risk_allocation"].lt(1.0).sum()
            ),
            "daily_spy_risk_state_rows": len(audit)
            * len(config.context.strategies),
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
            "event_paths_simulated": 72,
            "reported_scenarios": len(summary),
        },
        "runtime_code": {
            "g11_sha256": sha256_file(Path(__file__)),
            "g21_regime_helper_sha256": sha256_file(Path(__file__).with_name("g21.py")),
            "g00_accounting_helper_sha256": sha256_file(Path(__file__).with_name("g00.py")),
            "bundle_sha256": sha256_file(Path(__file__).with_name("bundle.py")),
            "run_context_sha256": sha256_file(Path(__file__).with_name("run_context.py")),
            "volatility_helper_sha256": sha256_file(
                Path(__file__).resolve().parents[1]
                / "experiments"
                / "volatility.py"
            ),
            "engine_sha256": sha256_file(
                Path(__file__).resolve().parents[1] / "backtest" / "engine.py"
            ),
            "calendar_sha256": sha256_file(
                Path(__file__).resolve().parents[1] / "backtest" / "calendar.py"
            ),
        },
        "limitations": [
            "free-research dataset and SPY total-return proxy",
            "15% is a target scale for the SPY risk source, not a portfolio volatility guarantee",
            "continuous unlevered scaling is a mechanism test, not a deployment claim",
        ],
    }
