"""Unified G00 naked-momentum control for long-only and gross-one WML."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import json
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from momentum_reversal.analytics import (
    benchmark_returns_from_total_return_prices,
    relative_performance_summary,
)
from momentum_reversal.backtest import BacktestResult, BaselineBacktester
from momentum_reversal.data import CorporateActionLedger
from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.storage import sha256_file
from momentum_reversal.experiments import PortfolioMode, StrategySpec
from momentum_reversal.portfolio import winner_loser_weights

from .bundle import (
    ARTIFACT_SCHEMAS,
    BundleWriteResult,
    write_experiment_bundle,
)
from .run_context import (
    ExperimentRunContext,
    LoadedExperimentData,
    load_experiment_data,
)
from .g00_reuse import ReusedLongOnlyBundle, load_reusable_long_only_bundle


_PERIODS_PER_YEAR = 252


class LegacyReproductionError(DataQualityError):
    """The zero-cash-yield control no longer reproduces the frozen baseline."""


@dataclass(frozen=True, slots=True)
class G00RunConfig:
    context: ExperimentRunContext
    legacy_baseline_root: Path
    reuse_long_only_bundle: Path | None = None
    allow_review_dataset: bool = False
    nav_tolerance: float = 1e-12
    daily_return_tolerance: float = 1e-12

    def __post_init__(self) -> None:
        object.__setattr__(self, "legacy_baseline_root", Path(self.legacy_baseline_root))
        object.__setattr__(
            self,
            "reuse_long_only_bundle",
            (
                None
                if self.reuse_long_only_bundle is None
                else Path(self.reuse_long_only_bundle)
            ),
        )
        if self.context.group_id != "G00":
            raise ValueError("G00 runner requires the registered G00 spec")
        if self.nav_tolerance < 0 or self.daily_return_tolerance < 0:
            raise ValueError("legacy reproduction tolerances cannot be negative")


@dataclass(frozen=True, slots=True)
class G00RunResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    strategy_count: int
    scenario_count: int
    legacy_control_count: int
    reused_long_only_scenario_count: int
    computed_long_short_scenario_count: int
    formal_run_eligible: bool


def run_g00(config: G00RunConfig) -> G00RunResult:
    """Run 36 core strategies and freeze one 288-scenario systematic bundle.

    The main long-only book earns T-bill returns on unallocated cash.  A second,
    non-exported zero-cash-yield control is run solely to reproduce all 72 legacy
    long-only paths.  Gross-one long-short books earn T-bill collateral returns
    and explicitly charge each registered borrow-fee scenario.
    """

    data = load_experiment_data(
        config.context, allow_review_dataset=config.allow_review_dataset
    )
    legacy = _load_legacy_contract(config.legacy_baseline_root, data)
    reused_long_only: ReusedLongOnlyBundle | None = None
    if config.reuse_long_only_bundle is not None:
        reused_long_only = load_reusable_long_only_bundle(
            config.reuse_long_only_bundle,
            context=config.context,
            data=data,
            legacy_manifest_sha256=legacy.manifest_sha256,
        )
    costs = _frozen_costs(config.context)
    annual_borrow_fees = _frozen_borrow_fees(config.context)
    long_only_engine = (
        None
        if reused_long_only is not None
        else BaselineBacktester(
            data.prices,
            data.membership,
            sessions=data.sessions,
            evaluation_start=data.evaluation_start,
            signal_end=data.evaluation_end,
            corporate_actions=data.corporate_actions,
            missing_valuation_policy=data.missing_valuation_policy,
            missing_execution_policy=data.legacy_missing_execution_policy,
        )
    )
    long_short_engine = BaselineBacktester(
        data.prices,
        data.membership,
        sessions=data.sessions,
        evaluation_start=data.evaluation_start,
        signal_end=data.evaluation_end,
        corporate_actions=data.corporate_actions,
        missing_valuation_policy=data.missing_valuation_policy,
        # Signed targets use their own whole-basket skip policy.  The base
        # execution policy stays strict so a partial signed fill is impossible.
        missing_execution_policy="strict",
    )
    benchmark_prices = data.benchmark.rename(
        columns={
            "benchmark_tr_open": "tr_open",
            "benchmark_tr_close": "tr_close",
        }
    )

    summary_rows: list[dict[str, object]] = []
    comparison_rows: list[dict[str, object]] = []
    nav_frames: list[pd.DataFrame] = []
    rebalance_frames: list[pd.DataFrame] = []
    holding_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    diagnostic_rows: list[dict[str, object]] = []
    legacy_control_count = 0
    reused_long_only_scenario_count = 0
    computed_long_short_scenario_count = 0
    if reused_long_only is not None:
        summary_rows.extend(reused_long_only.summary.to_dict(orient="records"))
        comparison_rows.extend(
            reused_long_only.comparison.to_dict(orient="records")
        )
        nav_frames.append(reused_long_only.nav)
        rebalance_frames.append(reused_long_only.rebalances)
        holding_frames.append(reused_long_only.holdings)
        trade_frames.append(reused_long_only.trades)
        diagnostic_rows.extend(
            reused_long_only.diagnostics.to_dict(orient="records")
        )
        legacy_control_count = 72
        reused_long_only_scenario_count = 72

    for strategy in config.context.strategies:
        primary_cost = _primary_cost(config.context, strategy.frequency)
        if strategy.portfolio_mode is PortfolioMode.LONG_ONLY:
            if reused_long_only is not None:
                continue
            if long_only_engine is None:  # pragma: no cover - invariant guard
                raise RuntimeError("long-only engine is unavailable")
            for cost_bps in costs:
                primary = cost_bps == primary_cost
                main_result = long_only_engine.run(
                    signal=strategy.signal,
                    top_n=strategy.top_n,
                    frequency=strategy.frequency,  # type: ignore[arg-type]
                    cost_bps=cost_bps,
                    risk_free_daily=data.risk_free_daily,
                    short_borrow_fee_daily=0.0,
                    full_audit=primary,
                )
                legacy_control = long_only_engine.run(
                    signal=strategy.signal,
                    top_n=strategy.top_n,
                    frequency=strategy.frequency,  # type: ignore[arg-type]
                    cost_bps=cost_bps,
                    risk_free_daily=None,
                    short_borrow_fee_daily=0.0,
                    full_audit=False,
                )
                _validate_result_bounds(main_result, data)
                _validate_result_bounds(legacy_control, data)
                legacy_nav = _read_legacy_nav(
                    legacy.root, strategy=strategy, cost_bps=cost_bps
                )
                comparison_rows.extend(
                    _compare_navs(
                        strategy,
                        cost_bps=cost_bps,
                        borrow_fee_annual=0.0,
                        candidate=legacy_control.nav,
                        reference=legacy_nav,
                        comparison_type="engine_reproduction_legacy_zero_cash",
                        nav_tolerance=config.nav_tolerance,
                        daily_return_tolerance=config.daily_return_tolerance,
                        hard_fail=True,
                    )
                )
                comparison_rows.extend(
                    _compare_navs(
                        strategy,
                        cost_bps=cost_bps,
                        borrow_fee_annual=0.0,
                        candidate=main_result.nav,
                        reference=legacy_nav,
                        comparison_type="cash_policy_delta_tbill_vs_legacy",
                        nav_tolerance=config.nav_tolerance,
                        daily_return_tolerance=config.daily_return_tolerance,
                        hard_fail=False,
                    )
                )
                legacy_control_count += 1
                benchmark_returns = benchmark_returns_from_total_return_prices(
                    benchmark_prices, main_result.nav["daily_return"]
                )
                _append_scenario(
                    strategy=strategy,
                    result=main_result,
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
            for cost_bps in costs:
                for annual_borrow_fee in annual_borrow_fees:
                    primary = (
                        cost_bps == primary_cost
                        and np.isclose(annual_borrow_fee, 0.01)
                    )
                    daily_borrow_fee = annual_borrow_fee_to_daily(
                        annual_borrow_fee
                    )
                    result = long_short_engine.run(
                        signal=strategy.signal,
                        top_n=strategy.top_n,
                        frequency=strategy.frequency,  # type: ignore[arg-type]
                        cost_bps=cost_bps,
                        target_weight_generator=generator,
                        target_weight_cache_key=strategy.strategy_id,
                        risk_free_daily=data.risk_free_daily,
                        short_borrow_fee_daily=daily_borrow_fee,
                        signed_missing_execution_policy="terminal_last_close",
                        terminal_last_close_max_sessions=(
                            data.terminal_last_close_max_sessions
                        ),
                        full_audit=primary,
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
                    computed_long_short_scenario_count += 1

    summary = pd.DataFrame(summary_rows).sort_values(
        ["strategy_id", "cost_bps", "borrow_fee_annual"], ignore_index=True
    )
    comparison = pd.DataFrame(comparison_rows).sort_values(
        ["strategy_id", "cost_bps", "comparison_type", "metric"],
        ignore_index=True,
    )
    artifacts = {
        "nav": pd.concat(nav_frames, ignore_index=True),
        "rebalances": _concat_or_empty(rebalance_frames, "rebalances"),
        "holdings": _concat_or_empty(holding_frames, "holdings"),
        "trades": _concat_or_empty(trade_frames, "trades"),
        "diagnostics": pd.DataFrame(diagnostic_rows),
    }
    if len(summary) != 288:
        raise RuntimeError(f"G00 must produce 288 main scenarios, got {len(summary)}")
    scenario_key = [
        "strategy_id",
        "variant_id",
        "cost_bps",
        "borrow_fee_annual",
    ]
    if summary.duplicated(scenario_key).any():
        raise RuntimeError("G00 summary contains duplicate scenario identities")
    if int(summary["is_primary_scenario"].sum()) != 36:
        raise RuntimeError("G00 must contain exactly 36 primary audit scenarios")
    long_short_summary = summary.loc[
        summary["portfolio_mode"].eq(PortfolioMode.LONG_SHORT.value)
    ]
    signed_primary_rebalances = artifacts["rebalances"].loc[
        artifacts["rebalances"]["portfolio_mode"].eq(
            PortfolioMode.LONG_SHORT.value
        )
    ]
    _validate_signed_execution_audit(
        signed_primary_rebalances,
        corporate_actions=data.corporate_actions,
        sessions=data.sessions,
    )
    terminal_rows = signed_primary_rebalances.loc[
        signed_primary_rebalances["execution_status"].eq(
            "executed_with_terminal_last_close"
        )
    ]
    if not terminal_rows.empty:
        missing_existing = pd.to_numeric(
            terminal_rows["missing_existing_count"], errors="coerce"
        ).fillna(0)
        liquidated = pd.to_numeric(
            terminal_rows["terminal_liquidation_count"], errors="coerce"
        ).fillna(0)
        missing_targets = pd.to_numeric(
            terminal_rows["missing_target_count"], errors="coerce"
        ).fillna(0)
        if (
            missing_existing.le(0).any()
            or missing_targets.ne(0).any()
            or not np.array_equal(
                missing_existing.to_numpy(dtype=float),
                liquidated.to_numpy(dtype=float),
            )
        ):
            raise DataQualityError(
                "G00 terminal-last-close audit columns are internally inconsistent"
            )
    summary.loc[
        summary["portfolio_mode"].eq(PortfolioMode.LONG_SHORT.value),
        "valid_scenario",
    ] = True
    summary.loc[
        summary["portfolio_mode"].eq(PortfolioMode.LONG_SHORT.value),
        "invalid_reason",
    ] = ""
    if not summary["valid_scenario"].eq(True).all():
        invalid = summary.loc[
            ~summary["valid_scenario"].eq(True),
            ["strategy_id", "cost_bps", "borrow_fee_annual", "invalid_reason"],
        ]
        raise DataQualityError(
            "G00 completion gate found invalid scenarios: "
            f"{invalid.head(5).to_dict(orient='records')}"
        )
    if legacy_control_count != 72:
        raise RuntimeError(
            f"G00 must reproduce 72 legacy controls, got {legacy_control_count}"
        )
    if len(comparison) != 432:
        raise RuntimeError(
            f"G00 must record 432 legacy comparison rows, got {len(comparison)}"
        )
    execution_status = artifacts["rebalances"].get(
        "execution_status", pd.Series(dtype=str)
    ).astype(str)
    skipped = int(execution_status.eq("skipped_signed_missing_open").sum())
    terminal_liquidations = int(
        artifacts["rebalances"].get(
            "terminal_liquidation_count", pd.Series(dtype=float)
        ).fillna(0).sum()
    )
    manifest_extra = _manifest_metadata(
        config=config,
        data=data,
        legacy=legacy,
        summary=summary,
        comparison=comparison,
        skipped_signed_rebalances=skipped,
        terminal_liquidation_count=terminal_liquidations,
        reused_long_only=reused_long_only,
        computed_long_short_scenario_count=computed_long_short_scenario_count,
    )
    bundle: BundleWriteResult = write_experiment_bundle(
        config.context,
        summary=summary,
        comparison=comparison,
        artifacts=artifacts,
        status="completed",
        extra_manifest=manifest_extra,
    )
    return G00RunResult(
        run_id=config.context.run_id,
        output_dir=bundle.output_dir,
        manifest_path=bundle.manifest_path,
        strategy_count=36,
        scenario_count=len(summary),
        legacy_control_count=legacy_control_count,
        reused_long_only_scenario_count=reused_long_only_scenario_count,
        computed_long_short_scenario_count=computed_long_short_scenario_count,
        formal_run_eligible=False,
    )


@dataclass(frozen=True, slots=True)
class _LegacyContract:
    root: Path
    manifest: dict[str, object]
    manifest_path: Path
    manifest_sha256: str


def _load_legacy_contract(
    root: Path, data: LoadedExperimentData
) -> _LegacyContract:
    source = root.resolve()
    manifest_path = source / "run_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dataset_version") != data.context.dataset_version:
        raise DataQualityError("legacy baseline uses a different dataset version")
    if manifest.get("dataset_manifest_sha256") != data.dataset_manifest_sha256:
        raise DataQualityError("legacy baseline dataset manifest hash does not match")
    if manifest.get("evaluation_start") != str(data.evaluation_start.date()):
        raise DataQualityError("legacy baseline evaluation start does not match G00")
    if manifest.get("signal_end") != str(data.evaluation_end.date()):
        raise DataQualityError("legacy baseline evaluation end does not match G00")
    expected = {
        _legacy_nav_path(source, strategy, cost)
        for strategy in data.context.strategies
        if strategy.portfolio_mode is PortfolioMode.LONG_ONLY
        for cost in _frozen_costs(data.context)
    }
    missing = sorted(str(path) for path in expected if not path.is_file())
    if missing:
        raise DataQualityError(
            f"legacy baseline is missing expected NAV files: {missing[:5]}"
        )
    actual = set(source.glob("baseline__*/cost_*bps/nav.csv"))
    if actual != expected:
        raise DataQualityError(
            f"legacy baseline NAV inventory mismatch: expected={len(expected)}, "
            f"actual={len(actual)}"
        )
    return _LegacyContract(
        root=source,
        manifest=manifest,
        manifest_path=manifest_path,
        manifest_sha256=sha256_file(manifest_path),
    )


def _append_scenario(
    *,
    strategy: StrategySpec,
    result: BacktestResult,
    cost_bps: float,
    borrow_fee_annual: float,
    primary: bool,
    risk_free_daily: pd.Series,
    benchmark_returns: pd.Series,
    summary_rows: list[dict[str, object]],
    nav_frames: list[pd.DataFrame],
    rebalance_frames: list[pd.DataFrame],
    holding_frames: list[pd.DataFrame],
    trade_frames: list[pd.DataFrame],
    diagnostic_rows: list[dict[str, object]],
) -> None:
    metrics = result.summary(risk_free_daily=risk_free_daily).to_dict()
    relative = relative_performance_summary(
        result.nav["daily_return"],
        benchmark_returns,
        risk_free_daily=risk_free_daily,
    ).to_dict()
    skipped = _skipped_rebalance_count(result.rebalances)
    terminal_liquidations = _terminal_liquidation_count(result.rebalances)
    row: dict[str, object] = {
        "group_id": strategy.group_id,
        "strategy_id": strategy.strategy_id,
        "portfolio_mode": strategy.portfolio_mode.value,
        "signal": strategy.signal.value,
        "top_n": strategy.top_n,
        "frequency": strategy.frequency,
        "variant_id": strategy.variant_id or "base",
        "cost_bps": float(cost_bps),
        "borrow_fee_annual": float(borrow_fee_annual),
        "valid_scenario": strategy.portfolio_mode is PortfolioMode.LONG_ONLY,
        "invalid_reason": (
            "" if strategy.portfolio_mode is PortfolioMode.LONG_ONLY else "pending_execution_QA"
        ),
        "is_primary_scenario": bool(primary),
        "target_gross_exposure": 1.0,
        "target_net_exposure": (
            1.0 if strategy.portfolio_mode is PortfolioMode.LONG_ONLY else 0.0
        ),
        "collateralized_total_return": True,
        "signed_skipped_rebalance_count": skipped,
        "terminal_last_close_count": terminal_liquidations,
        **metrics,
        **relative,
    }
    summary_rows.append(row)

    identity = _scenario_identity(strategy, cost_bps, borrow_fee_annual)
    nav = result.nav.reset_index().copy()
    for key, value in identity.items():
        nav[key] = value
    aligned_rf = risk_free_daily.reindex(result.nav.index)
    if strategy.portfolio_mode is PortfolioMode.LONG_SHORT:
        factor = result.nav["daily_return"] - aligned_rf
        nav["factor_excess_return"] = factor.to_numpy(dtype=float)
        nav["derived_gross2_factor_return"] = (2.0 * factor).to_numpy(dtype=float)
    else:
        nav["factor_excess_return"] = np.nan
        nav["derived_gross2_factor_return"] = np.nan
    nav_frames.append(nav)

    generic_diagnostics = {
        "corporate_action_events_applied": metrics.get(
            "corporate_action_events_applied", np.nan
        ),
        "valuation_fallback_count": metrics.get("valuation_fallback_count", np.nan),
        "unfilled_execution_count": metrics.get("unfilled_execution_count", np.nan),
        "signed_skipped_rebalance_count": skipped,
        "terminal_last_close_count": terminal_liquidations,
    }
    for name, value in generic_diagnostics.items():
        diagnostic_rows.append(
            {**identity, "scope": "scenario_audit", "diagnostic": name, "value": value}
        )
    if strategy.portfolio_mode is PortfolioMode.LONG_SHORT:
        factor = result.nav["daily_return"] - aligned_rf
        for prefix, values in (
            ("gross1_factor_excess", factor),
            ("derived_gross2_factor", 2.0 * factor),
        ):
            diagnostics = _annualized_return_diagnostics(values)
            for name, value in diagnostics.items():
                diagnostic_rows.append(
                    {
                        **identity,
                        "scope": "non_nav_factor_return",
                        "diagnostic": f"{prefix}_{name}",
                        "value": value,
                    }
                )

    if not primary:
        return
    rebalances = result.rebalances.reset_index(drop=True).copy()
    if "execution_status" not in rebalances:
        rebalances["execution_status"] = "executed"
    if "unfilled_selected_count" not in rebalances:
        rebalances["unfilled_selected_count"] = 0
    if "unfilled_selected_sids" not in rebalances:
        rebalances["unfilled_selected_sids"] = ""
    for key, value in identity.items():
        rebalances[key] = value
    rebalance_frames.append(rebalances)

    holdings = result.target_weights.copy()
    for key, value in identity.items():
        holdings[key] = value
    holding_frames.append(holdings)
    trades = result.trades.copy()
    for key, value in identity.items():
        trades[key] = value
    trade_frames.append(trades)


def _scenario_identity(
    strategy: StrategySpec, cost_bps: float, borrow_fee_annual: float
) -> dict[str, object]:
    return {
        "group_id": strategy.group_id,
        "strategy_id": strategy.strategy_id,
        "portfolio_mode": strategy.portfolio_mode.value,
        "variant_id": strategy.variant_id or "base",
        "cost_bps": float(cost_bps),
        "borrow_fee_annual": float(borrow_fee_annual),
    }


def _compare_navs(
    strategy: StrategySpec,
    *,
    cost_bps: float,
    borrow_fee_annual: float,
    candidate: pd.DataFrame,
    reference: pd.DataFrame,
    comparison_type: str,
    nav_tolerance: float,
    daily_return_tolerance: float,
    hard_fail: bool,
) -> list[dict[str, object]]:
    candidate_index = pd.DatetimeIndex(candidate.index).normalize()
    reference_index = pd.DatetimeIndex(reference.index).normalize()
    if not candidate_index.equals(reference_index):
        raise LegacyReproductionError(
            f"{strategy.strategy_id} cost={cost_bps:g}: legacy dates differ"
        )
    nav_diff = float(
        np.max(
            np.abs(
                candidate["nav"].to_numpy(dtype=float)
                - reference["nav"].to_numpy(dtype=float)
            )
        )
    )
    return_diff = float(
        np.max(
            np.abs(
                candidate["daily_return"].to_numpy(dtype=float)
                - reference["daily_return"].to_numpy(dtype=float)
            )
        )
    )
    if hard_fail and (
        nav_diff > nav_tolerance or return_diff > daily_return_tolerance
    ):
        raise LegacyReproductionError(
            f"{strategy.strategy_id} cost={cost_bps:g} failed legacy reproduction: "
            f"max_abs_nav_diff={nav_diff:.3e}, "
            f"max_abs_daily_return_diff={return_diff:.3e}"
        )
    identity = _scenario_identity(strategy, cost_bps, borrow_fee_annual)
    reference_id = _legacy_experiment_id(strategy)
    return [
        {
            **identity,
            "reference_strategy_id": reference_id,
            "comparison_type": comparison_type,
            "metric": "date_index_equal",
            "estimate": 1.0,
        },
        {
            **identity,
            "reference_strategy_id": reference_id,
            "comparison_type": comparison_type,
            "metric": "max_abs_nav_diff",
            "estimate": nav_diff,
        },
        {
            **identity,
            "reference_strategy_id": reference_id,
            "comparison_type": comparison_type,
            "metric": "max_abs_daily_return_diff",
            "estimate": return_diff,
        },
    ]


def _read_legacy_nav(
    root: Path, *, strategy: StrategySpec, cost_bps: float
) -> pd.DataFrame:
    path = _legacy_nav_path(root, strategy, cost_bps)
    frame = pd.read_csv(path)
    if not {"date", "nav", "daily_return"}.issubset(frame.columns):
        raise DataQualityError(f"legacy NAV has an unknown schema: {path}")
    dates = pd.DatetimeIndex(pd.to_datetime(frame.pop("date"), errors="raise")).normalize()
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise DataQualityError(f"legacy NAV dates are malformed: {path}")
    frame.index = dates
    return frame


def _legacy_nav_path(
    root: Path, strategy: StrategySpec, cost_bps: float
) -> Path:
    return (
        root
        / _legacy_experiment_id(strategy)
        / _cost_directory_name(cost_bps)
        / "nav.csv"
    )


def _legacy_experiment_id(strategy: StrategySpec) -> str:
    return (
        f"baseline__{strategy.signal.value}__top{strategy.top_n}__"
        f"{strategy.frequency}"
    )


def _cost_directory_name(cost: float) -> str:
    text = f"{cost:g}".replace("-", "m").replace(".", "p")
    return f"cost_{text}bps"


def _winner_loser_generator(top_n: int) -> Callable[..., pd.Series]:
    constructor = partial(winner_loser_weights, n_each=top_n, gross_exposure=1.0)

    def generate(
        _signal_date: pd.Timestamp, scores: pd.Series, members: tuple[str, ...]
    ) -> pd.Series:
        return constructor(scores, members)

    return generate


def annual_borrow_fee_to_daily(annual_rate: float) -> float:
    if not np.isfinite(annual_rate) or annual_rate < 0.0:
        raise ValueError("annual borrow fee must be finite and non-negative")
    return float((1.0 + annual_rate) ** (1.0 / _PERIODS_PER_YEAR) - 1.0)


def _annualized_return_diagnostics(values: pd.Series) -> dict[str, float]:
    series = pd.to_numeric(values, errors="coerce").dropna().astype(float)
    mean = float(series.mean() * _PERIODS_PER_YEAR)
    volatility = float(series.std(ddof=1) * np.sqrt(_PERIODS_PER_YEAR))
    sharpe = (
        float(mean / volatility) if volatility > 0.0 else float("nan")
    )
    return {
        "annualized_mean": mean,
        "annualized_volatility": volatility,
        "sharpe": sharpe,
    }


def _validate_result_bounds(
    result: BacktestResult, data: LoadedExperimentData
) -> None:
    if pd.Timestamp(result.nav.index[0]) != data.evaluation_start:
        raise DataQualityError("strategy did not start at the frozen evaluation open")
    if pd.Timestamp(result.nav.index[-1]) != data.evaluation_end:
        raise DataQualityError("strategy did not end at the frozen evaluation close")
    if not pd.DatetimeIndex(result.nav.index).equals(data.evaluation_sessions):
        raise DataQualityError("strategy NAV does not cover every evaluation session")


def _frozen_costs(context: ExperimentRunContext) -> tuple[float, ...]:
    costs = context.group.program.raw.get("costs")
    if not isinstance(costs, dict) or not isinstance(costs.get("scenarios_bps"), list):
        raise ValueError("program costs.scenarios_bps is missing")
    values = tuple(float(value) for value in costs["scenarios_bps"])
    if values != (0.0, 5.0, 10.0, 20.0):
        raise ValueError(f"G00 frozen costs changed unexpectedly: {values}")
    return values


def _primary_cost(context: ExperimentRunContext, frequency: str) -> float:
    costs = context.group.program.raw.get("costs")
    if not isinstance(costs, dict):
        raise ValueError("program costs table is missing")
    key = f"{frequency}_primary_bps"
    try:
        return float(costs[key])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"program primary cost is missing: {key}") from error


def _frozen_borrow_fees(context: ExperimentRunContext) -> tuple[float, ...]:
    settings = context.group.program.raw.get("long_short")
    if not isinstance(settings, dict) or not isinstance(
        settings.get("borrow_fee_scenarios_annual"), list
    ):
        raise ValueError("program long_short borrow fee scenarios are missing")
    values = tuple(float(value) for value in settings["borrow_fee_scenarios_annual"])
    if values != (0.0, 0.01, 0.03):
        raise ValueError(f"G00 frozen borrow fees changed unexpectedly: {values}")
    return values


def _concat_or_empty(frames: list[pd.DataFrame], artifact: str) -> pd.DataFrame:
    if frames:
        return pd.concat(frames, ignore_index=True)
    return pd.DataFrame(columns=ARTIFACT_SCHEMAS[artifact])


def _skipped_rebalance_count(rebalances: pd.DataFrame) -> int:
    if "execution_status" not in rebalances:
        return 0
    return int(
        rebalances["execution_status"]
        .astype(str)
        .str.startswith("skipped_")
        .sum()
    )


def _validate_signed_execution_audit(
    rebalances: pd.DataFrame,
    *,
    corporate_actions: CorporateActionLedger | None = None,
    sessions: pd.Index | None = None,
    future_action_max_sessions: int = 25,
) -> None:
    """Require every signed skip to have an explicit, recoverable cause.

    A raw count ceiling is not meaningful across weekly/monthly schedules or
    Top-K widths.  The frozen-data contract instead permits only two engine
    statuses: a same-open corporate action that invalidates a requested source
    ticker, or a held position protected until an already-frozen future action.
    Every affected strategy must subsequently execute another rebalance.
    """

    if rebalances.empty:
        return
    if future_action_max_sessions <= 0:
        raise ValueError("future_action_max_sessions must be positive")
    required = {
        "strategy_id",
        "execution_status",
        "missing_target_count",
        "missing_existing_count",
        "unfilled_selected_count",
        "corporate_actions_applied_pre_open",
    }
    if corporate_actions is not None:
        required.update({"execution_date", "missing_target_sids"})
    missing = sorted(required.difference(rebalances.columns))
    if missing:
        raise DataQualityError(
            f"G00 signed execution audit is missing columns: {missing}"
        )

    frame = rebalances.copy()
    status = frame["execution_status"].astype(str)
    skipped = status.str.startswith("skipped_")
    allowed = {
        "skipped_signed_missing_open",
        "skipped_pending_corporate_action",
    }
    unknown = sorted(set(status.loc[skipped]).difference(allowed))
    if unknown:
        raise DataQualityError(
            f"G00 signed execution QA found unsupported skip statuses: {unknown}"
        )

    def numeric(column: str) -> pd.Series:
        return pd.to_numeric(frame[column], errors="coerce").fillna(0)

    missing_target = numeric("missing_target_count")
    missing_existing = numeric("missing_existing_count")
    unfilled = numeric("unfilled_selected_count")
    actions = numeric("corporate_actions_applied_pre_open")

    same_open = status.eq("skipped_signed_missing_open")
    action_evidence = actions.gt(0)
    if corporate_actions is not None:
        calendar = None
        if sessions is not None:
            calendar = pd.DatetimeIndex(pd.to_datetime(list(sessions))).normalize()
            if calendar.has_duplicates or not calendar.is_monotonic_increasing:
                raise ValueError("signed execution audit sessions must be sorted and unique")
        ledger = corporate_actions.to_frame()
        for index, row in frame.loc[same_open & ~action_evidence].iterrows():
            execution_date = pd.Timestamp(row["execution_date"]).normalize()
            ledger_rows = corporate_actions.actions_on(execution_date)
            if calendar is not None:
                location = calendar.get_indexer([execution_date])
                if int(location[0]) < 0:
                    raise DataQualityError(
                        f"signed execution date is absent from the calendar: {execution_date}"
                    )
                cutoff = calendar[
                    min(int(location[0]) + future_action_max_sessions, len(calendar) - 1)
                ]
                future = ledger.loc[
                    ledger["apply_session"].gt(execution_date)
                    & ledger["apply_session"].le(cutoff)
                ]
                ledger_rows = pd.concat([ledger_rows, future], ignore_index=True)
            action_sources = set(ledger_rows["source_sid"].astype(str))
            missing_sids = {
                value
                for value in str(row["missing_target_sids"]).split("|")
                if value
            }
            action_evidence.loc[index] = bool(missing_sids) and missing_sids.issubset(
                action_sources
            )
    bad_same_open = same_open & (
        missing_target.le(0)
        | missing_existing.ne(0)
        | unfilled.lt(missing_target)
        | ~action_evidence
    )
    if bad_same_open.any():
        rows = frame.loc[
            bad_same_open,
            ["strategy_id", "execution_status", "missing_target_count",
             "missing_existing_count", "unfilled_selected_count",
             "corporate_actions_applied_pre_open"],
        ]
        raise DataQualityError(
            "G00 signed execution QA found a target missing-open skip without "
            "a same-open frozen corporate action: "
            f"{rows.head(5).to_dict(orient='records')}"
        )

    pending = status.eq("skipped_pending_corporate_action")
    bad_pending = pending & (missing_existing.le(0) | unfilled.le(0))
    if bad_pending.any():
        rows = frame.loc[
            bad_pending,
            ["strategy_id", "execution_status", "missing_target_count",
             "missing_existing_count", "unfilled_selected_count"],
        ]
        raise DataQualityError(
            "G00 signed execution QA found an unevidenced pending-action skip: "
            f"{rows.head(5).to_dict(orient='records')}"
        )

    for strategy_id, group in frame.groupby("strategy_id", sort=False):
        group_status = group["execution_status"].astype(str).reset_index(drop=True)
        skip_positions = np.flatnonzero(
            group_status.str.startswith("skipped_").to_numpy()
        )
        if len(skip_positions) and int(skip_positions[-1]) == len(group_status) - 1:
            raise DataQualityError(
                "G00 signed execution QA found a strategy that never resumed "
                f"after its final skipped rebalance: {strategy_id}"
            )


def _terminal_liquidation_count(rebalances: pd.DataFrame) -> int:
    if "terminal_liquidation_count" not in rebalances:
        return 0
    return int(
        pd.to_numeric(
            rebalances["terminal_liquidation_count"], errors="coerce"
        ).fillna(0).sum()
    )


def _manifest_metadata(
    *,
    config: G00RunConfig,
    data: LoadedExperimentData,
    legacy: _LegacyContract,
    summary: pd.DataFrame,
    comparison: pd.DataFrame,
    skipped_signed_rebalances: int,
    terminal_liquidation_count: int,
    reused_long_only: ReusedLongOnlyBundle | None,
    computed_long_short_scenario_count: int,
) -> dict[str, object]:
    dataset_blockers = list(data.dataset_manifest.get("formal_blockers", []))
    blockers = {str(value) for value in dataset_blockers}
    blockers.add("systematic_G00_bundle_is_prototype")
    if data.dataset_status != "valid":
        blockers.add(f"dataset_status_{data.dataset_status}")
    if data.benchmark_kind != "total_return_index":
        blockers.add("benchmark_is_not_total_return_index")
    risk_free = data.dataset_manifest.get("risk_free")
    corporate_actions = data.dataset_manifest.get("corporate_actions")
    return {
        "research_tier": "prototype",
        "formal_run_eligible": False,
        "formal_blockers": sorted(blockers),
        "dataset": {
            "status": data.dataset_status,
            "research_tier": data.dataset_research_tier,
            "declares_formal_eligible": data.dataset_declares_formal_eligible,
            "manifest_sha256": data.dataset_manifest_sha256,
            "calendar_source": data.calendar_source,
            "benchmark_kind": data.benchmark_kind,
            "review_override_explicit": config.allow_review_dataset,
        },
        "runtime_code": {
            "g00_sha256": sha256_file(Path(__file__)),
            "run_context_sha256": sha256_file(Path(__file__).with_name("run_context.py")),
            "engine_sha256": sha256_file(
                Path(__file__).resolve().parents[1] / "backtest" / "engine.py"
            ),
            "dataset_manifest_code_hashes": (
                "build provenance only; immutable data artifact hashes remain enforced"
            ),
        },
        "evaluation": {
            "start_open": str(data.evaluation_start.date()),
            "end_close": str(data.evaluation_end.date()),
            "sessions": len(data.evaluation_sessions),
        },
        "legacy_reference": {
            "run_id": str(legacy.manifest.get("run_id", legacy.root.name)),
            "manifest_sha256": legacy.manifest_sha256,
            "control_policy": "risk_free_daily_none",
            "hard_gate_scenarios": 72,
            "nav_tolerance": config.nav_tolerance,
            "daily_return_tolerance": config.daily_return_tolerance,
            "comparison_rows": len(comparison),
        },
        "counts": {
            "core_strategies": 36,
            "main_scenarios": len(summary),
            "long_only_scenarios": int(
                summary["portfolio_mode"].eq("long_only").sum()
            ),
            "long_short_scenarios": int(
                summary["portfolio_mode"].eq("long_short").sum()
            ),
            "primary_audit_scenarios": int(summary["is_primary_scenario"].sum()),
            "primary_signed_skipped_rebalance_events": skipped_signed_rebalances,
            "scenario_level_signed_skips": int(
                summary["signed_skipped_rebalance_count"].sum()
            ),
            "affected_signed_core_strategies": int(
                summary.loc[
                    summary["signed_skipped_rebalance_count"].gt(0), "strategy_id"
                ].nunique()
            ),
            "max_skipped_rebalances_per_signed_core_path": int(
                summary.loc[summary["portfolio_mode"].eq("long_short")]
                .groupby("strategy_id", observed=True)[
                    "skipped_signed_rebalance_count"
                ]
                .max()
                .max()
            ),
            "primary_terminal_last_close_liquidations": terminal_liquidation_count,
            "scenario_level_terminal_last_close_liquidations": int(
                summary["terminal_last_close_count"].fillna(0).sum()
            ),
            "reused_long_only_scenarios": (
                0 if reused_long_only is None else reused_long_only.scenario_count
            ),
            "computed_long_short_scenarios": computed_long_short_scenario_count,
        },
        "accounting": {
            "main_long_only_cash": "unallocated cash compounds at daily T-bill RF",
            "legacy_control_cash": "zero daily cash yield for exact old-engine bridge",
            "long_short": "gross=1 dollar-neutral; +0.5 winners and -0.5 losers",
            "collateral": "cash including short-sale collateral compounds at daily T-bill RF",
            "borrow_fee_annual": [0.0, 0.01, 0.03],
            "borrow_fee_daily_conversion": "(1 + annual_rate) ** (1 / 252) - 1",
            "factor_excess": "collateralized_daily_return - daily_T_bill_return",
            "derived_gross2": (
                "2 * gross1 factor_excess; arithmetic mean/volatility/Sharpe "
                "diagnostic only, never represented as an investable CAGR or NAV"
            ),
            "turnover": "annualized_l1_turnover = sum(abs(delta_weight)) * 252 / NAV_observations",
        },
        "execution": {
            "signal_to_trade": "signal close to next XNYS session open",
            "long_only_missing_execution_policy": data.legacy_missing_execution_policy,
            "signed_missing_execution_policy": "terminal_last_close",
            "terminal_last_close_max_sessions": (
                data.terminal_last_close_max_sessions
            ),
            "signed_target_missing_open": (
                "skip whole rebalance, retain prior book, and record anomaly"
            ),
            "terminal_existing_nonmember_missing_open": (
                "liquidate at the most recent causal close within "
                f"{data.terminal_last_close_max_sessions} sessions"
            ),
        },
        "reused_long_only_bundle": (
            None
            if reused_long_only is None
            else {
                "source_run_id": reused_long_only.source_run_id,
                "manifest_sha256": reused_long_only.manifest_sha256,
                "summary_rows": len(reused_long_only.summary),
                "nav_rows": reused_long_only.nav_rows,
                "primary_rebalance_scenarios": len(
                    reused_long_only.rebalances[
                        ["strategy_id", "variant_id", "cost_bps", "borrow_fee_annual"]
                    ].drop_duplicates()
                ),
                "primary_holding_scenarios": len(
                    reused_long_only.holdings[
                        ["strategy_id", "variant_id", "cost_bps", "borrow_fee_annual"]
                    ].drop_duplicates()
                ),
                "primary_trade_scenarios": len(
                    reused_long_only.trades[
                        ["strategy_id", "variant_id", "cost_bps", "borrow_fee_annual"]
                    ].drop_duplicates()
                ),
            }
        ),
        "data_quality_exclusions": {
            "sids": list(data.excluded_sids),
            "reason": data.exclusion_reason,
            "membership_rows_removed": data.excluded_membership_rows,
            "timing": "before PITMembership construction and all signal ranking",
        },
        "risk_free": {
            "source": risk_free.get("source") if isinstance(risk_free, dict) else None,
            "units": risk_free.get("units") if isinstance(risk_free, dict) else None,
        },
        "corporate_actions": {
            "provided": isinstance(corporate_actions, dict)
            and corporate_actions.get("provided") is True,
            "ledger_record_count": len(data.corporate_actions.to_frame()),
        },
        "limitations": [
            "free-data short locate, recall, and security-specific special borrow are unavailable",
            "target missing-open baskets are skipped rather than partially filled",
            "terminal last-close liquidation is a bounded causal research fallback",
            "SPY is an investable total-return proxy for the S&P 500 benchmark in this prototype",
            "all long-short results are research tradability evidence, not live-trading claims",
        ],
    }
