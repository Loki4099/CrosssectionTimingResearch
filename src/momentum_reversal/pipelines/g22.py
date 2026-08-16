"""G22 strict-Q4 reversal driven by matching naked-book historical volatility."""

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
from momentum_reversal.experiments import (
    PortfolioMode,
    StrategySpec,
    switch_cross_sectional_scores,
)
from momentum_reversal.experiments.spec import toml_dumps
from momentum_reversal.factors import compute_momentum_scores, compute_reversal_scores

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
from .g11 import _stable_artifact_sort
from .g21 import (
    _COMPARISON_METRICS,
    _G00Reference,
    _load_g00_reference,
    _partition_strategies,
    _signal_dates_by_frequency,
    _variant_lookback,
)
from .g32 import strict_lagged_book_quartiles
from .g33 import _append_g33_scenario, _validate_core_path_state_identity
from .run_context import ExperimentRunContext, LoadedExperimentData, load_experiment_data


_FROZEN_DATASET_VERSION = (
    "sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate"
)
_FROZEN_DATASET_MANIFEST_SHA256 = (
    "65b628d604f7e2f456e8d1d43a3c3e88b6bd3e86cc1c9455cdcfe28b856a3ec7"
)
_FROZEN_RECORD_SHA256 = (
    "a3ef9ee72cd3d535c2e5bf06b3d1f520c54667a8552891543ee0f9ca50488296"
)
_FROZEN_G00_MANIFEST_SHA256 = (
    "8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66"
)
_FROZEN_DESIGN_SHA256 = (
    "01d842564b4478d15178c6afbff35e962e02e00a7a6a94274cf547a317bccf50"
)
_FROZEN_IMPLEMENTATION_NOTE_SHA256 = (
    "496a6ec4851d908840ec6292c3904dd63fd0117b9a4d007029d130fdc11cadaa"
)
_FROZEN_GROUP_CONFIG_SHA256 = (
    "ce332c96ab5907ba72bf825b18271678234552e08c5b1de30d62e42fc9cd71dd"
)
_FROZEN_PROGRAM_SHA256 = (
    "11394af02fa028abe4a11434874be31e33e692f55feb73e9236da9bf8d07d413"
)


@dataclass(frozen=True, slots=True)
class G22RunConfig:
    context: ExperimentRunContext
    reference_g00_root: Path
    allow_review_dataset: bool = False
    workers: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_g00_root", Path(self.reference_g00_root))
        if self.context.group_id != "G22":
            raise ValueError("G22 runner requires the registered G22 spec")
        if isinstance(self.workers, bool) or not isinstance(self.workers, int):
            raise ValueError("G22 workers must be an integer")
        if self.workers <= 0 or self.workers > 8:
            raise ValueError("G22 workers must be between 1 and 8")


@dataclass(frozen=True, slots=True)
class G22RunResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    strategy_count: int
    scenario_count: int
    comparison_count: int
    q4_rebalance_count: int
    formal_run_eligible: bool


@dataclass(frozen=True, slots=True)
class _G22Path:
    regime: pd.DataFrame
    switched_scores: pd.Series
    naked_observations: int


@dataclass(frozen=True, slots=True)
class _G22CoreBatch:
    strategy_count: int
    summary: pd.DataFrame
    nav: pd.DataFrame
    rebalances: pd.DataFrame
    holdings: pd.DataFrame
    trades: pd.DataFrame
    diagnostics: pd.DataFrame


def run_g22(config: G22RunConfig) -> G22RunResult:
    """Run and freeze the preregistered 72-path G22 mechanism grid."""

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
            _run_g22_core_batch(context=config.context, data=data, strategies=strategies)
        ]
    else:
        partitions = _partition_strategies(strategies, config.workers)
        batches = []
        with ProcessPoolExecutor(max_workers=len(partitions)) as executor:
            futures = [
                executor.submit(
                    _run_g22_core_batch_worker,
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
    _validate_main_counts(summary, config.context)
    _validate_artifact_counts(artifacts, data)
    _validate_g00_selection_identity(artifacts["rebalances"], reference_g00)
    _validate_reversal_cross_signal_identity(artifacts["rebalances"])
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
        resolved_config_toml=_render_resolved_config_toml(
            config=config, data=data, reference_g00=reference_g00
        ),
        extra_manifest=_manifest_metadata(
            config=config,
            data=data,
            reference_g00=reference_g00,
            summary=summary,
            comparison=comparison,
            rebalances=artifacts["rebalances"],
            diagnostics=artifacts["diagnostics"],
        ),
    )
    return G22RunResult(
        run_id=config.context.run_id,
        output_dir=bundle.output_dir,
        manifest_path=bundle.manifest_path,
        strategy_count=len(strategies),
        scenario_count=len(summary),
        comparison_count=len(comparison),
        q4_rebalance_count=q4_count,
        formal_run_eligible=False,
    )


def _run_g22_core_batch_worker(
    context: ExperimentRunContext,
    allow_review_dataset: bool,
    strategy_ids: tuple[str, ...],
) -> _G22CoreBatch:
    data = load_experiment_data(context, allow_review_dataset=allow_review_dataset)
    lookup = {strategy.strategy_id: strategy for strategy in context.strategies}
    try:
        strategies = tuple(lookup[strategy_id] for strategy_id in strategy_ids)
    except KeyError as error:
        raise ValueError(f"unknown G22 worker strategy: {error.args[0]}") from error
    return _run_g22_core_batch(context=context, data=data, strategies=strategies)


def _run_g22_core_batch(
    *,
    context: ExperimentRunContext,
    data: LoadedExperimentData,
    strategies: tuple[StrategySpec, ...],
) -> _G22CoreBatch:
    paths = _build_strategy_paths(context=context, data=data, strategies=strategies)
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
        path = paths[strategy.strategy_id]
        lookback = _variant_lookback(strategy)
        selection_label = (
            f"{strategy.signal.value}_q1q3__book_hist_q4_reversal_{lookback}"
        )
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
                selection_scores=path.switched_scores,
                selection_label=selection_label,
                selection_score_cache_key=strategy.strategy_id,
                risk_free_daily=data.risk_free_daily,
                full_audit=True,
            )
            for cost_bps in costs:
                result = replay_linear_cost(zero_cost, cost_bps=float(cost_bps))
                _validate_core_path_state_identity(zero_cost, result)
                _append_g33_scenario(
                    strategy,
                    result,
                    float(cost_bps),
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
                    selection_scores=path.switched_scores,
                    selection_label=selection_label,
                    selection_score_cache_key=strategy.strategy_id,
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
                        float(cost_bps),
                        float(annual_borrow_fee),
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
        _attach_regime_audit(strategy_summary, rebalances, path.regime)
        diagnostic_rows.extend(
            _naked_book_diagnostics(
                strategy=strategy,
                primary_cost=primary_cost,
                naked_observations=path.naked_observations,
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
        # The audited P&L helper is shared with G33 and emits its historical
        # group/variant labels.  Normalize provenance before persistence while
        # retaining the computed values byte-for-byte.
        for row in diagnostic_rows:
            row["group_id"] = "G22"
            row["strategy_id"] = strategy.strategy_id
            row["portfolio_mode"] = strategy.portfolio_mode.value
            row["variant_id"] = strategy.variant_id
        summary_frames.append(strategy_summary)
        nav_frames.append(pd.concat(strategy_nav, ignore_index=True))
        rebalance_frames.append(rebalances)
        holding_frames.append(_concat_or_empty(strategy_holdings, "holdings"))
        trade_frames.append(_concat_or_empty(strategy_trades, "trades"))
        diagnostic_frames.append(pd.DataFrame(diagnostic_rows))

    del long_only_engine
    del long_short_engine
    gc.collect()
    return _G22CoreBatch(
        strategy_count=len(strategies),
        summary=pd.concat(summary_frames, ignore_index=True),
        nav=pd.concat(nav_frames, ignore_index=True),
        rebalances=pd.concat(rebalance_frames, ignore_index=True),
        holdings=pd.concat(holding_frames, ignore_index=True),
        trades=pd.concat(trade_frames, ignore_index=True),
        diagnostics=pd.concat(diagnostic_frames, ignore_index=True),
    )


def _build_strategy_paths(
    *,
    context: ExperimentRunContext,
    data: LoadedExperimentData,
    strategies: tuple[StrategySpec, ...],
) -> dict[str, _G22Path]:
    parameters = _frozen_parameters(context)
    history_start = pd.Timestamp(parameters["history_start"])
    expected_sessions = data.sessions[
        (data.sessions >= history_start) & (data.sessions <= data.evaluation_end)
    ]
    if len(expected_sessions) != 3_018:
        raise DataQualityError(
            f"G22 requires 3018 naked-book sessions, got {len(expected_sessions)}"
        )
    signal_dates = _signal_dates_by_frequency(data)
    all_signal_dates = signal_dates["weekly"].union(signal_dates["monthly"])
    momentum = {
        definition: compute_momentum_scores(
            data.prices, all_signal_dates, definition, sessions=data.sessions
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
    regime_cache: dict[str, pd.DataFrame] = {}
    output: dict[str, _G22Path] = {}
    for strategy in strategies:
        parent = strategy.parent_id
        if parent is None:
            raise DataQualityError(f"G22 strategy lacks G00 parent: {strategy.strategy_id}")
        if parent not in regime_cache:
            if strategy.portfolio_mode is PortfolioMode.LONG_ONLY:
                naked = long_only_engine.run(
                    signal=strategy.signal,
                    top_n=strategy.top_n,
                    frequency=strategy.frequency,  # type: ignore[arg-type]
                    cost_bps=0.0,
                    risk_free_daily=None,
                    short_borrow_fee_daily=0.0,
                    full_audit=False,
                )
            else:
                naked = long_short_engine.run(
                    signal=strategy.signal,
                    top_n=strategy.top_n,
                    frequency=strategy.frequency,  # type: ignore[arg-type]
                    cost_bps=0.0,
                    target_weight_generator=_winner_loser_generator(strategy.top_n),
                    target_weight_cache_key=parent,
                    risk_free_daily=None,
                    short_borrow_fee_daily=0.0,
                    signed_missing_execution_policy="terminal_last_close",
                    terminal_last_close_max_sessions=(
                        data.terminal_last_close_max_sessions
                    ),
                    full_audit=False,
                )
            extra = naked.nav.index.difference(expected_sessions)
            missing = expected_sessions.difference(naked.nav.index)
            if len(extra):
                raise DataQualityError(
                    f"G22 naked book has non-authoritative sessions: {extra[:5].tolist()}"
                )
            naked_returns = naked.nav["daily_return"].reindex(expected_sessions)
            if len(missing):
                allowed_initial_cash = pd.DatetimeIndex([history_start])
                if (
                    not missing.equals(allowed_initial_cash)
                    or len(naked.nav.index) == 0
                    or pd.Timestamp(naked.nav.index[0]) != expected_sessions[1]
                ):
                    raise DataQualityError(
                        f"G22 naked book is discontinuous: {missing[:5].tolist()}"
                    )
                naked_returns.loc[history_start] = 0.0
            if naked_returns.isna().any():
                raise DataQualityError(f"G22 naked returns contain gaps for {parent}")
            regime = strict_lagged_book_quartiles(
                naked_returns,
                realized_vol_window=int(parameters["realized_vol_window"]),
                history_sessions=int(parameters["history_sessions"]),
            )
            regime = regime.copy()
            regime["book_return"] = naked_returns
            regime_cache[parent] = regime
        regime = regime_cache[parent]
        formal = regime.reindex(signal_dates[strategy.frequency])
        required = [
            "book_realized_volatility",
            "lagged_q25",
            "lagged_q50",
            "lagged_q75",
            "volatility_quartile",
        ]
        if formal[required].isna().any().any():
            raise DataQualityError(
                f"G22 lacks a complete formal state for {strategy.strategy_id}"
            )
        lookback = _variant_lookback(strategy)
        switched = switch_cross_sectional_scores(
            momentum[strategy.signal],
            reversal[lookback],
            regime["high_volatility"],
        )
        _validate_score_switch(
            momentum[strategy.signal], reversal[lookback], switched, regime
        )
        output[strategy.strategy_id] = _G22Path(
            regime=regime,
            switched_scores=switched,
            naked_observations=len(regime),
        )
    del long_only_engine
    del long_short_engine
    gc.collect()
    return output


def _validate_score_switch(
    momentum: pd.Series,
    reversal: pd.Series,
    switched: pd.Series,
    regime: pd.DataFrame,
) -> None:
    dates = pd.DatetimeIndex(switched.index.get_level_values("signal_date"))
    high = regime["high_volatility"].reindex(dates).to_numpy(dtype=bool)
    actual = switched.to_numpy(dtype=float)
    expected = np.where(
        high, reversal.to_numpy(dtype=float), momentum.to_numpy(dtype=float)
    )
    if not np.allclose(actual, expected, rtol=0.0, atol=0.0, equal_nan=True):
        raise DataQualityError("G22 score switch differs from the frozen direct rule")


def _attach_regime_audit(
    summary: pd.DataFrame,
    rebalances: pd.DataFrame,
    regime: pd.DataFrame,
) -> None:
    dates = pd.DatetimeIndex(pd.to_datetime(rebalances["signal_date"])).normalize()
    sampled = regime.reindex(dates)
    required = [
        "book_realized_volatility",
        "lagged_q25",
        "lagged_q50",
        "lagged_q75",
        "volatility_quartile",
        "high_volatility",
    ]
    if sampled[required].isna().any().any():
        raise DataQualityError("G22 book regime is unavailable for a rebalance")
    allocation = pd.to_numeric(
        rebalances["target_risk_allocation"], errors="coerce"
    )
    if allocation.isna().any() or not np.allclose(
        allocation.to_numpy(dtype=float), 1.0, rtol=0.0, atol=1e-12
    ):
        raise DataQualityError("G22 direct reversal must retain full requested exposure")
    for column in required:
        rebalances[column] = sampled[column].to_numpy()
    rebalances["derisk_scale"] = 1.0
    grouped = rebalances.groupby("strategy_id", observed=True)["high_volatility"]
    stats = grouped.agg(["sum", "mean"])
    summary["high_vol_rebalance_count"] = summary["strategy_id"].map(stats["sum"])
    summary["high_vol_rebalance_fraction"] = summary["strategy_id"].map(stats["mean"])
    summary["below_full_investment_fraction"] = 0.0


def _naked_book_diagnostics(
    *,
    strategy: StrategySpec,
    primary_cost: float,
    naked_observations: int,
    rebalances: pd.DataFrame,
) -> list[dict[str, object]]:
    primary_borrow = (
        0.0 if strategy.portfolio_mode is PortfolioMode.LONG_ONLY else 0.01
    )
    values = {
        "naked_book_observations": float(naked_observations),
        "formal_signal_rows": float(len(rebalances)),
        "q4_signal_rows": float(rebalances["high_volatility"].sum()),
        "reversal_lookback": float(_variant_lookback(strategy)),
    }
    return [
        {
            "group_id": "G22",
            "strategy_id": strategy.strategy_id,
            "portfolio_mode": strategy.portfolio_mode.value,
            "variant_id": strategy.variant_id,
            "cost_bps": float(primary_cost),
            "borrow_fee_annual": float(primary_borrow),
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
    output: list[dict[str, object]] = []
    for date, row in regime.iterrows():
        quartile = row["volatility_quartile"]
        output.append(
            {
                "group_id": "G22",
                "strategy_id": strategy.strategy_id,
                "portfolio_mode": strategy.portfolio_mode.value,
                "variant_id": strategy.variant_id,
                "cost_bps": float(primary_cost),
                "borrow_fee_annual": float(primary_borrow),
                "scope": "daily_naked_regime",
                "diagnostic": "causal_book_rv126_state",
                "value": float(row["book_realized_volatility"])
                if pd.notna(row["book_realized_volatility"])
                else np.nan,
                "date": pd.Timestamp(date),
                "book_return": float(row["book_return"]),
                "book_realized_volatility": float(row["book_realized_volatility"])
                if pd.notna(row["book_realized_volatility"])
                else np.nan,
                "lagged_q25": float(row["lagged_q25"])
                if pd.notna(row["lagged_q25"])
                else np.nan,
                "lagged_q50": float(row["lagged_q50"])
                if pd.notna(row["lagged_q50"])
                else np.nan,
                "lagged_q75": float(row["lagged_q75"])
                if pd.notna(row["lagged_q75"])
                else np.nan,
                "volatility_quartile": int(quartile)
                if pd.notna(quartile)
                else pd.NA,
                "high_volatility": bool(row["high_volatility"]),
                "reversal_lookback": int(_variant_lookback(strategy)),
            }
        )
    return output


def _frozen_parameters(context: ExperimentRunContext) -> dict[str, object]:
    raw = context.group.raw.get("parameters")
    dates = context.group.program.raw.get("dates")
    if not isinstance(raw, dict) or not isinstance(dates, dict):
        raise ValueError("G22 parameters or program dates are missing")
    expected: dict[str, object] = {
        "book_realized_vol_window": 126,
        "state_history_sessions": 756,
        "high_vol_quantile": 0.75,
        "state_rule": "strict_q4_no_hysteresis",
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
            raise ValueError(f"G22 frozen parameter changed: {key}={actual!r}")
    history_start = str(dates.get("strategy_forecast_history_start", ""))
    if history_start != "2014-06-30":
        raise ValueError(f"G22 frozen history start changed: {history_start!r}")
    if tuple(context.group.reversal_lookbacks) != (5, 20):
        raise ValueError("G22 reversal lookbacks must remain exactly 5 and 20")
    return {
        "realized_vol_window": 126,
        "history_sessions": 756,
        "history_start": history_start,
    }


def _validate_frozen_inputs(
    context: ExperimentRunContext, data: LoadedExperimentData
) -> None:
    _frozen_parameters(context)
    if context.run_id != "g22-frozen-v3-v2":
        raise DataQualityError("G22 corrected run must use g22-frozen-v3-v2")
    if context.dataset_version != _FROZEN_DATASET_VERSION:
        raise DataQualityError("G22 requires the frozen v3 dataset version")
    if data.dataset_manifest_sha256 != _FROZEN_DATASET_MANIFEST_SHA256:
        raise DataQualityError("G22 frozen dataset manifest hash changed")
    freeze_record = (
        context.data_root / "curated" / context.dataset_version / "FROZEN.json"
    )
    if (
        not freeze_record.is_file()
        or sha256_file(freeze_record) != _FROZEN_RECORD_SHA256
    ):
        raise DataQualityError("G22 frozen dataset record hash changed")
    design = (
        context.project_root
        / "docs"
        / "20_experiments"
        / "G22_book_hist_reversal"
        / "design.md"
    )
    if not design.is_file() or sha256_file(design) != _FROZEN_DESIGN_SHA256:
        raise DataQualityError("G22 preregistered design hash changed")
    implementation_note = design.parent / "implementation_note.md"
    if (
        not implementation_note.is_file()
        or sha256_file(implementation_note) != _FROZEN_IMPLEMENTATION_NOTE_SHA256
    ):
        raise DataQualityError("G22 v2 implementation-note hash changed")
    program = context.project_root / "config" / "experiments" / "program.toml"
    if not program.is_file() or sha256_file(program) != _FROZEN_PROGRAM_SHA256:
        raise DataQualityError("G22 frozen experiment program hash changed")
    if sha256_file(context.group.path) != _FROZEN_GROUP_CONFIG_SHA256:
        raise DataQualityError("G22 frozen group config hash changed")
    costs = context.group.program.raw.get("costs")
    long_short = context.group.program.raw.get("long_short")
    if not isinstance(costs, dict) or not isinstance(long_short, dict):
        raise DataQualityError("G22 frozen cost tables are missing")
    if (
        costs.get("scenarios_bps") != [0, 5, 10, 20]
        or costs.get("weekly_primary_bps") != 10
        or costs.get("monthly_primary_bps") != 5
        or long_short.get("borrow_fee_scenarios_annual") != [0.0, 0.01, 0.03]
        or long_short.get("primary_borrow_fee_annual") != 0.01
    ):
        raise DataQualityError("G22 frozen cost or borrow-fee contract changed")


def _validate_reference_anchor(reference_g00: _G00Reference) -> None:
    if (
        reference_g00.manifest.get("run_id") != "g00-frozen-v3-v1"
        or reference_g00.manifest_sha256 != _FROZEN_G00_MANIFEST_SHA256
    ):
        raise DataQualityError("G22 G00 reference is not the frozen v3-v1 bundle")


def _validate_runtime_roots(config: G22RunConfig) -> None:
    data_root = config.context.data_root.resolve()
    output_root = config.context.output_root.resolve()
    project_root = config.context.project_root.resolve()
    if (
        data_root.name.lower() != "data"
        or output_root.name.lower() != "results"
        or data_root.parent != output_root.parent
    ):
        raise DataQualityError("G22 requires sibling data/results local runtime roots")
    try:
        output_root.relative_to(project_root)
    except ValueError:
        pass
    else:
        raise DataQualityError("G22 full bundle cannot be written inside the repository")
    try:
        config.reference_g00_root.resolve().relative_to(output_root)
    except ValueError as error:
        raise DataQualityError(
            "G22 G00 reference must come from the local results root"
        ) from error


def _validate_main_counts(
    summary: pd.DataFrame, context: ExperimentRunContext
) -> None:
    if len(context.strategies) != 72 or len(summary) != 576:
        raise RuntimeError(
            "G22 requires 72 core paths and 576 scenarios, got "
            f"{len(context.strategies)} and {len(summary)}"
        )
    key = ["strategy_id", "variant_id", "cost_bps", "borrow_fee_annual"]
    if summary.duplicated(key).any():
        raise RuntimeError("G22 summary contains duplicate scenario identities")
    if int(summary["is_primary_scenario"].sum()) != 72:
        raise RuntimeError("G22 must contain exactly 72 primary scenarios")
    modes = summary.groupby("portfolio_mode", observed=True).size().to_dict()
    if modes != {"long_only": 144, "long_short": 432}:
        raise RuntimeError(f"G22 scenario mode counts changed: {modes}")


def _validate_artifact_counts(
    artifacts: dict[str, pd.DataFrame], data: LoadedExperimentData
) -> None:
    nav = artifacts["nav"]
    if len(data.evaluation_sessions) != 2_134 or len(nav) != 1_229_184:
        raise RuntimeError(
            "G22 requires 2134 sessions and 1229184 NAV rows, got "
            f"{len(data.evaluation_sessions)} and {len(nav)}"
        )
    scenario_key = ["strategy_id", "cost_bps", "borrow_fee_annual"]
    counts = nav.groupby(scenario_key, observed=True).size()
    if len(counts) != 576 or not counts.eq(2_134).all():
        raise RuntimeError("G22 every scenario must contain exactly 2134 NAV rows")
    rebalances = artifacts["rebalances"]
    if len(rebalances) != 19_656:
        raise RuntimeError(
            f"G22 requires 19656 primary rebalances, got {len(rebalances)}"
        )
    daily = artifacts["diagnostics"].loc[
        artifacts["diagnostics"]["scope"].eq("daily_naked_regime")
    ]
    diagnostics = artifacts["diagnostics"]
    if not diagnostics["group_id"].astype(str).eq("G22").all():
        raise DataQualityError("G22 diagnostics contain foreign group provenance")
    if len(daily) != 217_296:
        raise RuntimeError(
            f"G22 requires 217296 daily regime rows, got {len(daily)}"
        )
    daily_counts = daily.groupby("strategy_id", observed=True).size()
    if len(daily_counts) != 72 or not daily_counts.eq(3_018).all():
        raise RuntimeError("G22 every core path must contain 3018 daily states")
    if not nav["nav"].map(np.isfinite).all() or not nav["nav"].gt(0.0).all():
        raise DataQualityError("G22 NAV must be finite and positive")
    pnl_columns = [
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
    missing = set(pnl_columns).difference(nav.columns)
    if missing:
        raise DataQualityError(f"G22 NAV lacks P&L columns: {sorted(missing)}")
    numeric = nav[pnl_columns].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy()).all():
        raise DataQualityError("G22 P&L attribution contains invalid values")
    if float(numeric["pnl_closure_error"].abs().max()) > 1e-12:
        raise DataQualityError("G22 P&L attribution does not close")
    if float(numeric["pnl_unexplained_bridge"].abs().max()) > 1e-10:
        raise DataQualityError("G22 unexplained P&L bridge exceeds tolerance")
    unsupported = (
        numeric["pnl_action_execution_bridge"].abs().gt(1e-10)
        & numeric["pnl_bridge_has_frozen_evidence"].ne(1.0)
    )
    if unsupported.any():
        raise DataQualityError("G22 material action bridge lacks frozen evidence")
    _validate_daily_regimes(daily)


def _validate_daily_regimes(daily: pd.DataFrame) -> None:
    for strategy_id, frame in daily.groupby("strategy_id", observed=True):
        ordered = frame.sort_values("date")
        dates = pd.DatetimeIndex(pd.to_datetime(ordered["date"])).normalize()
        if dates.has_duplicates or not dates.is_monotonic_increasing:
            raise DataQualityError(f"G22 daily dates are invalid for {strategy_id}")
        returns = pd.Series(
            pd.to_numeric(ordered["book_return"], errors="coerce").to_numpy(float),
            index=dates,
        )
        direct = strict_lagged_book_quartiles(
            returns, realized_vol_window=126, history_sessions=756
        )
        for column in (
            "book_realized_volatility",
            "lagged_q25",
            "lagged_q50",
            "lagged_q75",
        ):
            actual = pd.to_numeric(ordered[column], errors="coerce").to_numpy(float)
            expected = direct[column].to_numpy(float)
            if not np.allclose(
                actual, expected, rtol=0.0, atol=0.0, equal_nan=True
            ):
                raise DataQualityError(
                    f"G22 persisted daily {column} is not reproducible"
                )
        actual_q = pd.to_numeric(ordered["volatility_quartile"], errors="coerce")
        expected_q = pd.to_numeric(direct["volatility_quartile"], errors="coerce")
        if not np.array_equal(actual_q.fillna(-1).to_numpy(), expected_q.fillna(-1).to_numpy()):
            raise DataQualityError("G22 persisted daily quartile is not reproducible")


def _parent_strategy_id(strategy_id: str) -> str:
    if not strategy_id.startswith("G22__"):
        raise DataQualityError(f"invalid G22 strategy id: {strategy_id}")
    return strategy_id.replace("G22__", "G00__", 1).rsplit("__", 1)[0]


def _validate_g00_selection_identity(
    rebalances: pd.DataFrame, reference: _G00Reference
) -> None:
    candidate = rebalances.copy()
    baseline = reference.rebalances.copy()
    for frame in (candidate, baseline):
        frame["signal_date"] = pd.to_datetime(frame["signal_date"]).dt.normalize()
    baseline_lookup = baseline.set_index(
        ["strategy_id", "signal_date"], verify_integrity=True
    )
    non_q4 = candidate.loc[~candidate["high_volatility"].astype(bool)]
    for row in non_q4.itertuples(index=False):
        key = (_parent_strategy_id(str(row.strategy_id)), row.signal_date)
        if key not in baseline_lookup.index:
            raise DataQualityError(f"G22 lacks matching G00 rebalance: {key}")
        reference_row = baseline_lookup.loc[key]
        for column in (
            "execution_date",
            "requested_selected_count",
            "requested_selected_sids",
            "requested_long_exposure",
            "requested_short_exposure",
            "requested_gross_exposure",
            "requested_net_exposure",
        ):
            left = getattr(row, column)
            right = reference_row[column]
            numeric = column.startswith("requested_") and column.endswith("exposure")
            matches = (
                np.isclose(float(left), float(right), rtol=0.0, atol=1e-12)
                if numeric
                else str(left) == str(right)
            )
            if not matches:
                raise DataQualityError(
                    f"G22 non-Q4 {column} differs from G00 for {key}"
                )


def _validate_reversal_cross_signal_identity(rebalances: pd.DataFrame) -> None:
    frame = rebalances.loc[rebalances["high_volatility"].astype(bool)].copy()
    parsed = frame["strategy_id"].astype(str).str.extract(
        r"^G22__(?P<signal>[^_]+(?:_[^_]+){2})__top(?P<top>\d+)__"
        r"(?P<frequency>weekly|monthly)__(?P<mode>long_only|long_short)__"
        r"(?P<variant>rev(?:5|20))$"
    )
    if parsed.isna().any().any():
        raise DataQualityError("G22 strategy ids do not match the registered grid")
    frame = pd.concat([frame.reset_index(drop=True), parsed.reset_index(drop=True)], axis=1)
    # Where all three momentum definitions independently classify the same
    # signal as Q4, pure reversal must produce one identical requested book.
    key = ["top", "frequency", "mode", "variant", "signal_date"]
    grouped = frame.groupby(key, observed=True)
    complete = grouped.filter(lambda group: group["signal"].nunique() == 3)
    if complete.empty:
        raise DataQualityError("G22 has no common-Q4 cross-signal selection audit rows")
    selected_counts = complete.groupby(key, observed=True)[
        "requested_selected_sids"
    ].nunique()
    if not selected_counts.eq(1).all():
        raise DataQualityError("G22 Q4 pure-reversal selection depends on momentum signal")


def _attach_g00_comparisons(
    summary: pd.DataFrame, reference: pd.DataFrame
) -> pd.DataFrame:
    lookup = reference.set_index(
        ["strategy_id", "cost_bps", "borrow_fee_annual"], verify_integrity=True
    )
    rows: list[dict[str, object]] = []
    delta_columns = {metric: [] for metric in _COMPARISON_METRICS}
    for row in summary.itertuples(index=False):
        parent = _parent_strategy_id(str(row.strategy_id))
        key = (parent, float(row.cost_bps), float(row.borrow_fee_annual))
        if key not in lookup.index:
            raise DataQualityError(f"G22 lacks matching G00 scenario: {key}")
        baseline = lookup.loc[key]
        for metric in _COMPARISON_METRICS:
            delta = float(getattr(row, metric) - baseline[metric])
            delta_columns[metric].append(delta)
            rows.append(
                {
                    "group_id": "G22",
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
    for metric, values in delta_columns.items():
        summary[f"delta_vs_g00_{metric}"] = values
    comparison = pd.DataFrame(rows)
    if len(comparison) != 2_880:
        raise RuntimeError(
            f"G22 must record 2880 G00 comparison rows, got {len(comparison)}"
        )
    return comparison


def _render_resolved_config_toml(
    *,
    config: G22RunConfig,
    data: LoadedExperimentData,
    reference_g00: _G00Reference,
) -> str:
    context = config.context
    project = context.project_root
    design = (
        project
        / "docs"
        / "20_experiments"
        / "G22_book_hist_reversal"
        / "design.md"
    )
    implementation_note = design.parent / "implementation_note.md"
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
        "implementation_note_sha256": sha256_file(implementation_note),
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
        raise RuntimeError("G22 resolved TOML must end with newline")
    return rendered


def _manifest_metadata(
    *,
    config: G22RunConfig,
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
        / "G22_book_hist_reversal"
        / "design.md"
    )
    implementation_note = design.parent / "implementation_note.md"
    program = project / "config" / "experiments" / "program.toml"
    daily = diagnostics.loc[diagnostics["scope"].eq("daily_naked_regime")]
    blockers = {
        str(value) for value in data.dataset_manifest.get("formal_blockers", [])
    }
    blockers.add("systematic_G22_bundle_is_prototype")
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
        "risk_rule": {
            "risk_source": "matching G00 naked-book causal RV126",
            "naked_book_history_start": "2014-06-30",
            "naked_book_cost_bps": 0.0,
            "naked_book_borrow_fee_annual": 0.0,
            "naked_book_cash_return": 0.0,
            "realized_volatility_window": 126,
            "annualization": "sqrt(252) with ddof=1",
            "quartile_history_sessions": 756,
            "quartile_current_observation_excluded": True,
            "q4_rule": "current RV126 strictly greater than lagged q75",
            "q1_q3_action": "unchanged matching G00 momentum ranking",
            "q4_action": "pure direct reversal ranking",
            "reversal_lookbacks": [5, 20],
            "hysteresis": False,
            "allocation_scaling": False,
            "daily_q4_fraction": float(daily["high_volatility"].mean()),
            "q4_rebalance_rows": int(rebalances["high_volatility"].sum()),
        },
        "counts": {
            "core_strategies": len(config.context.strategies),
            "main_scenarios": len(summary),
            "primary_scenarios": int(summary["is_primary_scenario"].sum()),
            "comparison_rows": len(comparison),
            "nav_rows": 1_229_184,
            "daily_regime_rows": len(daily),
        },
        "execution": {
            "worker_processes": config.workers,
            "cost_scenarios": (
                "exact homogeneous-NAV replay from one zero-cost event path "
                "per borrow-fee scenario"
            ),
            "event_paths_simulated": 36 * 2 + 36 * 2 * 3,
            "reported_scenarios": len(summary),
        },
        "runtime_code": _runtime_code_hashes(),
        "limitations": [
            "free-research dataset; formal_run_eligible is false",
            "strict Q4 direct reversal is a mechanism test, not a deployment claim",
            "G12/G21/G32 may only be used after completion for descriptive interpretation",
        ],
    }


def _runtime_code_hashes() -> dict[str, str]:
    pipeline_root = Path(__file__).resolve().parent
    source_root = pipeline_root.parent
    paths = {
        "g22_sha256": Path(__file__),
        "g21_sha256": pipeline_root / "g21.py",
        "g32_sha256": pipeline_root / "g32.py",
        "g33_sha256": pipeline_root / "g33.py",
        "g00_sha256": pipeline_root / "g00.py",
        "bundle_sha256": pipeline_root / "bundle.py",
        "run_context_sha256": pipeline_root / "run_context.py",
        "engine_sha256": source_root / "backtest" / "engine.py",
        "regime_sha256": source_root / "experiments" / "regime.py",
    }
    return {label: sha256_file(path) for label, path in paths.items()}
