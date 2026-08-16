from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import shutil
from types import SimpleNamespace

import pandas as pd
import numpy as np

from momentum_reversal.data import (
    AssetRef,
    CorporateActionLedger,
    PITMembership,
    PriceRequest,
    SecurityMaster,
)
from momentum_reversal.data.qa import DataQualityError, build_universe_audit
from momentum_reversal.pipelines.dataset import (
    _benchmark_frame,
    _complete_signal_date_summary,
    _validate_pit_date_semantics,
    build_yfinance_download_plan,
    download_yfinance_symbols,
)
from momentum_reversal.pipelines.baseline import (
    BaselineRunConfig,
    run_frozen_baselines,
)
from momentum_reversal.experiments import (
    ExperimentCatalog,
    PortfolioMode,
    switch_cross_sectional_scores,
)
from momentum_reversal.pipelines import (
    ARTIFACT_SCHEMAS,
    G00RunConfig,
    LegacyReproductionError,
    LongOnlyReuseError,
    empty_comparison_frame,
    empty_summary_frame,
    prepare_experiment_run,
    run_g00,
    validate_experiment_manifest,
    write_experiment_bundle,
)
from momentum_reversal.pipelines.g00 import (
    _compare_navs,
    _skipped_rebalance_count,
    _validate_signed_execution_audit,
)
from momentum_reversal.pipelines.g21 import (
    _conditional_period_summary,
    _partition_strategies,
    strict_lagged_spy_quartiles,
)
from momentum_reversal.pipelines.g22 import (
    G22RunConfig,
    _parent_strategy_id as _g22_parent_strategy_id,
    _validate_reference_anchor as _validate_g22_reference_anchor,
    _validate_reversal_cross_signal_identity,
    _validate_score_switch,
)
from momentum_reversal.pipelines.g23 import (
    G23RunConfig,
    _parent_strategy_id as _g23_parent_strategy_id,
    _validate_reference_anchor as _validate_g23_reference_anchor,
    _validate_reversal_cross_signal_identity as _validate_g23_cross_signal_identity,
    _validate_score_switch as _validate_g23_score_switch,
)
from momentum_reversal.pipelines.g11 import (
    G11RunConfig,
    _append_pnl_attribution_diagnostics as _append_g11_pnl_attribution,
    _attach_g00_comparisons as _attach_g11_g00_comparisons,
    _daily_spy_risk_state_diagnostics as _g11_daily_risk_diagnostics,
    _prepare_g11_regime,
    _render_g11_resolved_config_toml,
    _validate_core_path_state_identity as _validate_g11_core_path_state_identity,
    _validate_daily_spy_risk_state,
    _validate_formal_signal_regime,
    _validate_g00_path_identity as _validate_g11_g00_path_identity,
    _validate_reference_anchor as _validate_g11_reference_anchor,
    continuous_spy_allocation,
    run_g11,
)
from momentum_reversal.pipelines.g12 import (
    G12RunConfig,
    _attach_g00_comparisons as _attach_g12_g00_comparisons,
    _validate_reference_anchor as _validate_g12_reference_anchor,
    continuous_book_allocation,
    run_g12,
)
from momentum_reversal.pipelines.g13 import (
    G13RunConfig,
    _attach_g00_comparisons as _attach_g13_g00_comparisons,
    _validate_reference_anchor as _validate_g13_reference_anchor,
    continuous_forecast_allocation,
    run_g13,
)
from momentum_reversal.pipelines.g31 import (
    _attach_g00_comparisons as _attach_g31_g00_comparisons,
    strict_q4_derisk_allocation,
)
from momentum_reversal.pipelines.g32 import (
    G32RunConfig,
    strict_lagged_book_quartiles,
)
from momentum_reversal.pipelines.g33 import (
    G33RunConfig,
    _append_pnl_attribution_diagnostics as _append_g33_pnl_attribution,
    _attach_g00_comparisons as _attach_g33_g00_comparisons,
    _daily_naked_regime_diagnostics as _g33_daily_regime_diagnostics,
    _validate_core_path_state_identity,
    _validate_reference_anchor as _validate_g33_reference_anchor,
    forecast_engine_start,
    forecast_input_returns,
    run_g33,
    strict_lagged_book_forecast_quartiles,
    strict_q4_forecast_allocation,
)
from momentum_reversal.pipelines.run_context import (
    _verify_dataset_files,
    _terminal_last_close_max_sessions,
    _validate_review_dataset_gates,
)
from momentum_reversal.data.storage import DatasetLayout, ManifestStore, sha256_file
from momentum_reversal.analytics import benchmark_returns_from_total_return_prices
from momentum_reversal.backtest import rebalance_schedule


class G00SignedExecutionAuditTests(unittest.TestCase):
    @staticmethod
    def _row(strategy_id: str, status: str, **overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "strategy_id": strategy_id,
            "execution_status": status,
            "missing_target_count": 0,
            "missing_existing_count": 0,
            "unfilled_selected_count": 0,
            "corporate_actions_applied_pre_open": 0,
        }
        row.update(overrides)
        return row

    def test_accepts_multiple_evidenced_skips_and_later_recovery(self) -> None:
        strategy = "G00__fixture__weekly__long_short"
        rows = [
            self._row(strategy, "executed"),
            self._row(
                strategy,
                "skipped_signed_missing_open",
                missing_target_count=1,
                unfilled_selected_count=1,
                corporate_actions_applied_pre_open=1,
            ),
            self._row(strategy, "executed"),
            self._row(
                strategy,
                "skipped_pending_corporate_action",
                missing_existing_count=2,
                unfilled_selected_count=2,
            ),
            self._row(
                strategy,
                "skipped_pending_corporate_action",
                missing_existing_count=2,
                unfilled_selected_count=2,
            ),
            self._row(strategy, "executed"),
        ]
        frame = pd.DataFrame(rows)
        _validate_signed_execution_audit(frame)
        self.assertEqual(_skipped_rebalance_count(frame), 3)

    def test_rejects_missing_open_without_same_open_action(self) -> None:
        strategy = "G00__fixture__weekly__long_short"
        frame = pd.DataFrame(
            [
                self._row(
                    strategy,
                    "skipped_signed_missing_open",
                    missing_target_count=1,
                    unfilled_selected_count=1,
                ),
                self._row(strategy, "executed"),
            ]
        )
        with self.assertRaisesRegex(DataQualityError, "same-open frozen corporate"):
            _validate_signed_execution_audit(frame)

    def test_accepts_unheld_target_removed_by_frozen_same_open_action(self) -> None:
        strategy = "G00__fixture__monthly__long_short"
        frame = pd.DataFrame(
            [
                self._row(
                    strategy,
                    "skipped_signed_missing_open",
                    execution_date="2019-01-02",
                    missing_target_count=1,
                    missing_target_sids="sec::SCG",
                    unfilled_selected_count=1,
                ),
                self._row(
                    strategy,
                    "executed",
                    execution_date="2019-02-01",
                    missing_target_sids="",
                ),
            ]
        )
        ledger = CorporateActionLedger(
            pd.DataFrame(
                [
                    {
                        "action_id": "SCG_D_20190101",
                        "action_type": "stock_merger",
                        "legal_effective_date": "2019-01-01",
                        "apply_session": "2019-01-02",
                        "apply_phase": "pre_open",
                        "source_sid": "sec::SCG",
                        "target_sid": "sec::D",
                        "cash_per_source_share": 0.0,
                        "currency": "",
                        "target_shares_per_source_share": 0.669,
                        "fractional_treatment": "cash_in_lieu",
                        "evidence_url": "research://fixture",
                        "notes": "fixture",
                    }
                ]
            )
        )
        _validate_signed_execution_audit(frame, corporate_actions=ledger)

    def test_accepts_halted_target_with_frozen_future_action(self) -> None:
        strategy = "G00__fixture__weekly__long_short"
        frame = pd.DataFrame(
            [
                self._row(
                    strategy,
                    "skipped_signed_missing_open",
                    execution_date="2023-03-13",
                    missing_target_count=1,
                    missing_target_sids="sec::SBNY",
                    unfilled_selected_count=1,
                ),
                self._row(
                    strategy,
                    "executed",
                    execution_date="2023-04-03",
                    missing_target_sids="",
                ),
            ]
        )
        ledger = CorporateActionLedger(
            pd.DataFrame(
                [
                    {
                        "action_id": "LIQ_SBNY_20230328",
                        "action_type": "cash_liquidation",
                        "legal_effective_date": "2023-03-28",
                        "apply_session": "2023-03-28",
                        "apply_phase": "pre_open",
                        "source_sid": "sec::SBNY",
                        "target_sid": "",
                        "cash_per_source_share": 0.41,
                        "currency": "USD",
                        "target_shares_per_source_share": 0.0,
                        "fractional_treatment": "not_applicable",
                        "evidence_url": "research://fixture",
                        "notes": "fixture",
                    }
                ]
            )
        )
        sessions = pd.bdate_range("2023-03-13", "2023-04-03")
        _validate_signed_execution_audit(
            frame, corporate_actions=ledger, sessions=sessions
        )

    def test_rejects_strategy_that_never_resumes(self) -> None:
        strategy = "G00__fixture__weekly__long_short"
        frame = pd.DataFrame(
            [
                self._row(strategy, "executed"),
                self._row(
                    strategy,
                    "skipped_pending_corporate_action",
                    missing_existing_count=1,
                    unfilled_selected_count=1,
                ),
            ]
        )
        with self.assertRaisesRegex(DataQualityError, "never resumed"):
            _validate_signed_execution_audit(frame)


class DatasetFileVerificationTests(unittest.TestCase):
    def test_code_hash_is_provenance_but_data_hash_remains_hard_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout = DatasetLayout(root / "data").create()
            code = root / "src" / "fixture.py"
            code.parent.mkdir(parents=True)
            code.write_text("runtime revision\n", encoding="utf-8")
            data_file = layout.root / "curated" / "fixture.csv"
            data_file.parent.mkdir(parents=True, exist_ok=True)
            data_file.write_text("frozen data\n", encoding="utf-8")

            _verify_dataset_files(
                layout,
                {"files": [{"path": str(code), "sha256": "not-current"}]},
                project_root=root,
            )
            with self.assertRaisesRegex(DataQualityError, "hash mismatch"):
                _verify_dataset_files(
                    layout,
                    {"files": [{"path": str(data_file), "sha256": "not-current"}]},
                    project_root=root,
                )

    def test_external_data_root_still_treats_repository_code_as_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "repository"
            layout = DatasetLayout(root / "runtime" / "data").create()
            code = project / "src" / "fixture.py"
            code.parent.mkdir(parents=True)
            code.write_text("changed code\n", encoding="utf-8")

            _verify_dataset_files(
                layout,
                {"files": [{"path": str(code), "sha256": "frozen-old-hash"}]},
                project_root=project,
            )


class G21RegimeTests(unittest.TestCase):
    def test_parallel_partitions_balance_simulated_event_paths(self) -> None:
        catalog = ExperimentCatalog.load(Path("config/experiments"))
        strategies = catalog.group("G21").strategies()
        partitions = _partition_strategies(strategies, 4)
        loads = [
            sum(
                (3 if strategy.portfolio_mode is PortfolioMode.LONG_SHORT else 1)
                * (3 if strategy.frequency == "weekly" else 2)
                for strategy in partition
            )
            for partition in partitions
        ]
        self.assertEqual(sum(loads), 360)
        self.assertLessEqual(max(loads) - min(loads), 2)
        self.assertTrue(
            all(
                {strategy.frequency for strategy in partition}
                == {"weekly", "monthly"}
                for partition in partitions
            )
        )

    def test_strict_quartile_threshold_uses_only_prior_history(self) -> None:
        dates = pd.bdate_range("2014-01-02", periods=820)
        returns = 0.001 * np.sin(np.arange(len(dates)) / 7.0)
        close = 100.0 * np.cumprod(1.0 + returns)
        benchmark = pd.DataFrame(
            {"date": dates, "benchmark_tr_close": close}
        )
        original = strict_lagged_spy_quartiles(
            benchmark, realized_vol_window=5, history_sessions=756
        )
        changed = benchmark.copy()
        changed.loc[800, "benchmark_tr_close"] *= 1.25
        revised = strict_lagged_spy_quartiles(
            changed, realized_vol_window=5, history_sessions=756
        )
        self.assertAlmostEqual(
            float(original.loc[dates[800], "lagged_q75"]),
            float(revised.loc[dates[800], "lagged_q75"]),
        )
        self.assertFalse(pd.isna(original.loc[dates[800], "volatility_quartile"]))
        self.assertEqual(int(revised.loc[dates[800], "volatility_quartile"]), 4)

    def test_conditional_period_summary_covers_all_quartiles(self) -> None:
        dates = pd.bdate_range("2020-01-02", periods=9)
        rebalances = pd.DataFrame(
            {
                "signal_date": dates,
                "execution_date": dates,
                "execution_status": ["executed"] * len(dates),
                "pretrade_nav": [1.00, 1.01, 1.00, 1.03, 1.02, 1.06, 1.05, 1.08, 1.10],
            }
        )
        rebalances.index = pd.Index(dates, name="execution_date")
        regime = pd.DataFrame(
            {"volatility_quartile": pd.Series([1, 2, 3, 4, 1, 2, 3, 4, 1], index=dates, dtype="Int64")}
        )
        result = _conditional_period_summary(rebalances, regime)
        self.assertEqual(result["volatility_quartile"].tolist(), [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(int(result["event_count"].sum()), 8)


class G22BookHistReversalTests(unittest.TestCase):
    def test_score_switch_is_exact_and_current_state_only(self) -> None:
        dates = pd.bdate_range("2020-01-02", periods=4)
        index = pd.MultiIndex.from_product(
            [dates, ["A", "B"]], names=["signal_date", "sid"]
        )
        momentum = pd.Series(np.arange(8, dtype=float), index=index, name="score")
        reversal = pd.Series(-np.arange(8, dtype=float), index=index, name="score")
        regime = pd.DataFrame(
            {"high_volatility": [False, True, False, True]}, index=dates
        )
        switched = switch_cross_sectional_scores(
            momentum, reversal, regime["high_volatility"]
        )
        _validate_score_switch(momentum, reversal, switched, regime)
        expected = momentum.copy()
        high_mask = expected.index.get_level_values("signal_date").isin(
            dates[[1, 3]]
        )
        expected.loc[high_mask] = reversal.loc[high_mask]
        pd.testing.assert_series_equal(switched, expected, check_exact=True)

        tampered = switched.copy()
        tampered.iloc[0] += 1.0
        with self.assertRaisesRegex(DataQualityError, "score switch"):
            _validate_score_switch(momentum, reversal, tampered, regime)

    def test_config_and_catalog_freeze_72_core_paths(self) -> None:
        context = prepare_experiment_run(
            "config/experiments/G22.toml",
            run_id="g22-config-test",
            dataset_version="fixture-v1",
        )
        config = G22RunConfig(
            context=context, reference_g00_root=Path("g00-reference"), workers=8
        )
        self.assertEqual(
            set(G22RunConfig.__dataclass_fields__),
            {"context", "reference_g00_root", "allow_review_dataset", "workers"},
        )
        self.assertEqual(len(context.strategies), 72)
        self.assertEqual(
            {strategy.variant_id for strategy in context.strategies}, {"rev5", "rev20"}
        )
        self.assertEqual(
            {strategy.portfolio_mode for strategy in context.strategies},
            {PortfolioMode.LONG_ONLY, PortfolioMode.LONG_SHORT},
        )
        for workers in (True, 1.5, 0, 9):
            with self.subTest(workers=workers):
                with self.assertRaisesRegex(ValueError, "workers"):
                    G22RunConfig(
                        context=context,
                        reference_g00_root=Path("g00-reference"),
                        workers=workers,  # type: ignore[arg-type]
                    )

    def test_parent_mapping_and_frozen_g00_anchor(self) -> None:
        strategy = "G22__mom_255_0__top20__weekly__long_short__rev20"
        self.assertEqual(
            _g22_parent_strategy_id(strategy),
            "G00__mom_255_0__top20__weekly__long_short",
        )
        expected_hash = (
            "8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66"
        )
        _validate_g22_reference_anchor(
            SimpleNamespace(
                manifest={"run_id": "g00-frozen-v3-v1"},
                manifest_sha256=expected_hash,
            )
        )
        with self.assertRaisesRegex(DataQualityError, "G00 reference"):
            _validate_g22_reference_anchor(
                SimpleNamespace(
                    manifest={"run_id": "g00-frozen-v3-v1"},
                    manifest_sha256="0" * 64,
                )
            )

    def test_common_q4_selection_must_ignore_momentum_definition(self) -> None:
        rows = []
        for signal in ("mom_12_1", "mom_255_0", "mom_255_21"):
            rows.append(
                {
                    "strategy_id": f"G22__{signal}__top10__weekly__long_only__rev5",
                    "signal_date": pd.Timestamp("2020-03-20"),
                    "high_volatility": True,
                    "requested_selected_sids": "A|B",
                }
            )
        frame = pd.DataFrame(rows)
        _validate_reversal_cross_signal_identity(frame)
        frame.loc[2, "requested_selected_sids"] = "A|C"
        with self.assertRaisesRegex(DataQualityError, "momentum signal"):
            _validate_reversal_cross_signal_identity(frame)


class G23BookForecastReversalTests(unittest.TestCase):
    def test_score_switch_is_exact_and_current_state_only(self) -> None:
        dates = pd.bdate_range("2020-01-02", periods=4)
        index = pd.MultiIndex.from_product(
            [dates, ["A", "B"]], names=["signal_date", "sid"]
        )
        momentum = pd.Series(np.arange(8, dtype=float), index=index, name="score")
        reversal = pd.Series(-np.arange(8, dtype=float), index=index, name="score")
        regime = pd.DataFrame(
            {"high_volatility": [False, True, False, True]}, index=dates
        )
        switched = switch_cross_sectional_scores(
            momentum, reversal, regime["high_volatility"]
        )
        _validate_g23_score_switch(momentum, reversal, switched, regime)
        tampered = switched.copy()
        tampered.iloc[0] += 1.0
        with self.assertRaisesRegex(DataQualityError, "score switch"):
            _validate_g23_score_switch(momentum, reversal, tampered, regime)

    def test_config_and_catalog_freeze_72_core_paths(self) -> None:
        context = prepare_experiment_run(
            "config/experiments/G23.toml",
            run_id="g23-config-test",
            dataset_version="fixture-v1",
        )
        config = G23RunConfig(
            context=context, reference_g00_root=Path("g00-reference"), workers=8
        )
        self.assertEqual(
            set(G23RunConfig.__dataclass_fields__),
            {"context", "reference_g00_root", "allow_review_dataset", "workers"},
        )
        self.assertEqual(len(context.strategies), 72)
        self.assertEqual(
            {strategy.variant_id for strategy in context.strategies}, {"rev5", "rev20"}
        )
        for workers in (True, 1.5, 0, 9):
            with self.subTest(workers=workers):
                with self.assertRaisesRegex(ValueError, "workers"):
                    G23RunConfig(
                        context=context,
                        reference_g00_root=Path("g00-reference"),
                        workers=workers,  # type: ignore[arg-type]
                    )

    def test_parent_mapping_and_frozen_g00_anchor(self) -> None:
        strategy = "G23__mom_255_0__top20__weekly__long_short__rev20"
        self.assertEqual(
            _g23_parent_strategy_id(strategy),
            "G00__mom_255_0__top20__weekly__long_short",
        )
        expected_hash = (
            "8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66"
        )
        _validate_g23_reference_anchor(
            SimpleNamespace(
                manifest={"run_id": "g00-frozen-v3-v1"},
                manifest_sha256=expected_hash,
            )
        )
        with self.assertRaisesRegex(DataQualityError, "G00 reference"):
            _validate_g23_reference_anchor(
                SimpleNamespace(
                    manifest={"run_id": "g00-frozen-v3-v1"},
                    manifest_sha256="0" * 64,
                )
            )

    def test_common_q4_selection_must_ignore_momentum_definition(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "strategy_id": f"G23__{signal}__top10__weekly__long_only__rev5",
                    "signal_date": pd.Timestamp("2020-03-20"),
                    "high_volatility": True,
                    "requested_selected_sids": "A|B",
                }
                for signal in ("mom_12_1", "mom_255_0", "mom_255_21")
            ]
        )
        _validate_g23_cross_signal_identity(frame)
        frame.loc[2, "requested_selected_sids"] = "A|C"
        with self.assertRaisesRegex(DataQualityError, "momentum signal"):
            _validate_g23_cross_signal_identity(frame)


class G11ContinuousSpyScaleTests(unittest.TestCase):
    @staticmethod
    def _benchmark(periods: int = 820) -> pd.DataFrame:
        dates = pd.bdate_range("2013-01-02", periods=periods)
        returns = 0.001 + 0.012 * np.sin(np.arange(periods - 1) / 9.0)
        close = np.concatenate([[100.0], 100.0 * np.cumprod(1.0 + returns)])
        return pd.DataFrame(
            {"date": dates, "benchmark_tr_close": close}
        )

    def test_rv21_and_continuous_formula_use_current_signal_close(self) -> None:
        dates = pd.bdate_range("2020-01-02", periods=22)
        returns = np.linspace(-0.025, 0.025, 21)
        close = np.concatenate([[100.0], 100.0 * np.cumprod(1.0 + returns)])
        benchmark = pd.DataFrame(
            {"date": dates, "benchmark_tr_close": close}
        )
        regime = strict_lagged_spy_quartiles(
            benchmark, realized_vol_window=21, history_sessions=4
        )
        expected_rv = float(np.std(returns, ddof=1) * np.sqrt(252.0))
        self.assertAlmostEqual(
            float(regime.loc[dates[-1], "spy_realized_volatility"]), expected_rv
        )
        allocation = continuous_spy_allocation(regime, dates[-1:])
        self.assertAlmostEqual(
            float(allocation.loc[dates[-1]]), min(1.0, 0.15 / expected_rv)
        )

        original = strict_lagged_spy_quartiles(
            self._benchmark(), realized_vol_window=21, history_sessions=4
        )
        cutoff = original.index[790]
        current_changed = self._benchmark()
        current_changed.loc[
            current_changed["date"].eq(cutoff), "benchmark_tr_close"
        ] *= 1.25
        revised_current = strict_lagged_spy_quartiles(
            current_changed, realized_vol_window=21, history_sessions=4
        )
        self.assertNotEqual(
            float(original.loc[cutoff, "spy_realized_volatility"]),
            float(revised_current.loc[cutoff, "spy_realized_volatility"]),
        )
        self.assertNotEqual(
            float(continuous_spy_allocation(original, [cutoff]).iloc[0]),
            float(continuous_spy_allocation(revised_current, [cutoff]).iloc[0]),
        )

        future_changed = self._benchmark()
        future_changed.loc[
            future_changed["date"].gt(cutoff), "benchmark_tr_close"
        ] *= np.linspace(
            0.7,
            1.3,
            int(future_changed["date"].gt(cutoff).sum()),
        )
        revised_future = strict_lagged_spy_quartiles(
            future_changed, realized_vol_window=21, history_sessions=4
        )
        pd.testing.assert_frame_equal(
            original.loc[:cutoff], revised_future.loc[:cutoff], check_exact=True
        )

    def test_formula_caps_at_one_ignores_quartiles_and_fails_closed(self) -> None:
        dates = pd.bdate_range("2020-01-02", periods=3)
        regime = pd.DataFrame(
            {
                "spy_realized_volatility": [0.10, 0.30, 0.60],
                "lagged_q75": [99.0, -99.0, np.nan],
                "volatility_quartile": pd.Series(
                    [4, 1, pd.NA], index=dates, dtype="Int64"
                ),
                "high_volatility": [True, False, False],
            },
            index=dates,
        )
        allocation = continuous_spy_allocation(regime, dates)
        np.testing.assert_allclose(allocation.to_numpy(), [1.0, 0.5, 0.25])
        stripped = continuous_spy_allocation(
            regime[["spy_realized_volatility"]], dates
        )
        pd.testing.assert_series_equal(allocation, stripped, check_exact=True)

        with self.assertRaisesRegex(ValueError, "annual_target_volatility=0.15"):
            continuous_spy_allocation(
                regime, dates, annual_target_volatility=0.20
            )
        with self.assertRaisesRegex(ValueError, "maximum_scale=1.0"):
            continuous_spy_allocation(regime, dates, maximum_scale=1.5)
        invalid = regime.copy()
        invalid.loc[dates[1], "spy_realized_volatility"] = 0.0
        with self.assertRaisesRegex(DataQualityError, "finite and positive"):
            continuous_spy_allocation(invalid, dates)
        with self.assertRaisesRegex(DataQualityError, "unavailable"):
            continuous_spy_allocation(
                regime,
                dates.append(pd.DatetimeIndex([pd.Timestamp("2020-01-07")])),
            )

    def test_formal_state_is_complete_and_exactly_the_frozen_g31_spy_state(self) -> None:
        benchmark = self._benchmark()
        g31_state = strict_lagged_spy_quartiles(
            benchmark, realized_vol_window=21, history_sessions=756
        )
        context = prepare_experiment_run(
            "config/experiments/G11.toml",
            run_id="g11-regime-fixture",
            dataset_version="fixture-v1",
        )
        sessions = pd.DatetimeIndex(benchmark["date"])
        data = SimpleNamespace(
            benchmark=benchmark.copy(deep=True),
            sessions=sessions,
            evaluation_start=sessions[790],
            evaluation_end=sessions[-1],
        )
        g11_state, formal_allocation = _prepare_g11_regime(context, data)
        shared_state = [
            "spy_realized_volatility",
            "lagged_q25",
            "lagged_q50",
            "lagged_q75",
            "volatility_quartile",
        ]
        pd.testing.assert_frame_equal(
            g11_state[shared_state], g31_state[shared_state], check_exact=True
        )
        formal_dates = formal_allocation.index
        self.assertGreater(len(formal_dates), 0)
        _validate_formal_signal_regime(g11_state, formal_dates)
        pd.testing.assert_series_equal(
            g11_state.loc[formal_dates, "target_risk_allocation"],
            formal_allocation,
            check_names=False,
            check_exact=True,
            check_freq=False,
        )

        incomplete = g11_state.copy()
        incomplete.loc[formal_dates[0], "lagged_q75"] = np.nan
        with self.assertRaisesRegex(DataQualityError, "incomplete"):
            _validate_formal_signal_regime(incomplete, formal_dates)

    def test_config_immutable_run_and_exact_g00_anchor(self) -> None:
        g11 = prepare_experiment_run(
            "config/experiments/G11.toml",
            run_id="g11-config-test",
            dataset_version="fixture-v1",
        )
        config = G11RunConfig(
            context=g11, reference_g00_root=Path("g00-reference"), workers=8
        )
        self.assertEqual(config.reference_g00_root, Path("g00-reference"))
        self.assertEqual(
            set(G11RunConfig.__dataclass_fields__),
            {"context", "reference_g00_root", "allow_review_dataset", "workers"},
        )

        wrong_group = prepare_experiment_run(
            "config/experiments/G31.toml",
            run_id="g31-config-test-for-g11",
            dataset_version="fixture-v1",
        )
        with self.assertRaisesRegex(ValueError, "registered G11 spec"):
            G11RunConfig(
                context=wrong_group, reference_g00_root=Path("g00-reference")
            )
        for workers in (True, 1.5, 0, 9):
            with self.subTest(workers=workers):
                with self.assertRaisesRegex(ValueError, "workers"):
                    G11RunConfig(
                        context=g11,
                        reference_g00_root=Path("g00-reference"),
                        workers=workers,  # type: ignore[arg-type]
                    )

        expected_hash = (
            "8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66"
        )
        _validate_g11_reference_anchor(
            SimpleNamespace(
                manifest={"run_id": "g00-frozen-v3-v1"},
                manifest_sha256=expected_hash,
            )
        )
        for wrong in (
            SimpleNamespace(
                manifest={"run_id": "g31-frozen-v3-v1"},
                manifest_sha256=expected_hash,
            ),
            SimpleNamespace(
                manifest={"run_id": "g00-frozen-v3-v1"},
                manifest_sha256="0" * 64,
            ),
        ):
            with self.assertRaisesRegex(DataQualityError, "G00 reference"):
                _validate_g11_reference_anchor(wrong)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            immutable_context = prepare_experiment_run(
                "config/experiments/G11.toml",
                run_id="g11-existing-fixture",
                dataset_version="fixture-v1",
                data_root=root / "data",
                output_root=root / "results",
            )
            immutable_context.bundle_dir.mkdir(parents=True)
            with self.assertRaisesRegex(FileExistsError, "immutable"):
                run_g11(
                    G11RunConfig(
                        context=immutable_context,
                        reference_g00_root=root / "results" / "g00-reference",
                    )
                )

    def test_resolved_config_includes_deterministic_run_and_reference_anchors(self) -> None:
        import tomllib

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            version = "fixture-v1"
            freeze_record = root / "data" / "curated" / version / "FROZEN.json"
            freeze_record.parent.mkdir(parents=True)
            freeze_record.write_text('{"frozen": true}\n', encoding="utf-8")
            context = prepare_experiment_run(
                "config/experiments/G11.toml",
                run_id="g11-resolved-config-fixture",
                dataset_version=version,
                data_root=root / "data",
                output_root=root / "results",
            )
            config = G11RunConfig(
                context=context,
                reference_g00_root=root / "results" / "g00-reference",
                allow_review_dataset=True,
                workers=3,
            )
            data = SimpleNamespace(
                dataset_manifest_sha256="1" * 64,
                dataset_status="review",
                dataset_research_tier="free_research_candidate",
            )
            reference = SimpleNamespace(
                root=config.reference_g00_root,
                manifest={"run_id": "g00-frozen-v3-v1"},
                manifest_sha256="2" * 64,
            )
            first = _render_g11_resolved_config_toml(
                config=config, data=data, reference_g00=reference
            )
            second = _render_g11_resolved_config_toml(
                config=config, data=data, reference_g00=reference
            )
            self.assertEqual(first, second)
            self.assertTrue(first.endswith("\n"))
            resolved = tomllib.loads(first)
            self.assertIn("program", resolved)
            self.assertIn("group", resolved)
            self.assertIn("resolved", resolved)
            run = resolved["run"]
            self.assertEqual(run["run_id"], "g11-resolved-config-fixture")
            self.assertEqual(run["dataset_version"], version)
            self.assertTrue(run["allow_review_dataset"])
            self.assertEqual(run["workers"], 3)
            self.assertFalse(run["formal_run_eligible"])
            self.assertEqual(run["data_root"], str((root / "data").resolve()))
            self.assertEqual(
                run["dataset_anchor"]["manifest_sha256"], "1" * 64
            )
            self.assertEqual(
                run["dataset_anchor"]["freeze_record_sha256"],
                sha256_file(freeze_record),
            )
            self.assertEqual(
                run["reference_g00"]["root"],
                str(config.reference_g00_root.resolve()),
            )
            self.assertEqual(
                run["reference_g00"]["run_id"], "g00-frozen-v3-v1"
            )
            self.assertEqual(
                run["reference_g00"]["manifest_sha256"], "2" * 64
            )
            self.assertEqual(
                run["design_sha256"],
                "c0e41c31fc5d8dc1fd53b466c7440fd5d02dc1cf77c48bd83f3a63bb452594c8",
            )

    def test_runner_has_no_g31_runtime_input_and_hard_gates_design(self) -> None:
        root = Path(__file__).resolve().parents[1]
        design = (
            root
            / "docs"
            / "20_experiments"
            / "G11_spy_continuous_scale"
            / "design.md"
        )
        expected_design = (
            "c0e41c31fc5d8dc1fd53b466c7440fd5d02dc1cf77c48bd83f3a63bb452594c8"
        )
        self.assertEqual(sha256_file(design), expected_design)
        source_path = (
            root / "src" / "momentum_reversal" / "pipelines" / "g11.py"
        )
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules = {
            str(node.module)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertFalse(
            any(module.casefold().endswith("g31") for module in imported_modules)
        )
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        identifiers.update(
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        )
        self.assertNotIn("reference_g31_root", identifiers)
        functions = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        self.assertIn(expected_design, source)
        self.assertIn(
            "_FROZEN_DESIGN_SHA256", functions["_validate_frozen_inputs"]
        )
        self.assertIn("design_sha256", functions["_manifest_metadata"])

    def test_catalog_scenarios_and_comparisons_map_one_to_one_to_g00(self) -> None:
        catalog = ExperimentCatalog.load(Path("config/experiments"))
        rows: list[dict[str, object]] = []
        references: list[dict[str, object]] = []
        metrics = (
            "cagr",
            "sharpe_excess_rf",
            "max_drawdown",
            "annualized_volatility",
            "annualized_l1_turnover",
        )
        for strategy in catalog.group("G11").strategies():
            borrow_fees = (
                (0.0,)
                if strategy.portfolio_mode is PortfolioMode.LONG_ONLY
                else (0.0, 0.01, 0.03)
            )
            for cost_bps in (0.0, 5.0, 10.0, 20.0):
                for borrow_fee in borrow_fees:
                    identity = {
                        "strategy_id": strategy.strategy_id,
                        "portfolio_mode": strategy.portfolio_mode.value,
                        "variant_id": "base",
                        "cost_bps": cost_bps,
                        "borrow_fee_annual": borrow_fee,
                    }
                    rows.append({**identity, **{metric: 1.0 for metric in metrics}})
                    references.append(
                        {
                            **identity,
                            "strategy_id": strategy.parent_id,
                            **{metric: 0.5 for metric in metrics},
                        }
                    )
        summary = pd.DataFrame(rows)
        comparison = _attach_g11_g00_comparisons(
            summary, pd.DataFrame(references)
        )
        self.assertEqual(catalog.group("G11").strategy_count, 36)
        self.assertEqual(len(summary), 288)
        self.assertEqual(len(comparison), 1_440)
        self.assertEqual(
            summary.groupby("portfolio_mode", observed=True).size().to_dict(),
            {"long_only": 72, "long_short": 216},
        )
        self.assertTrue(comparison["estimate"].eq(0.5).all())
        self.assertTrue(
            comparison["reference_strategy_id"]
            .astype(str)
            .str.startswith("G00__")
            .all()
        )

    def test_cost_and_borrow_paths_cannot_change_state_selection_or_targets(self) -> None:
        rebalances = pd.DataFrame(
            {
                "signal_date": pd.to_datetime(["2020-01-03", "2020-01-10"]),
                "execution_date": pd.to_datetime(["2020-01-06", "2020-01-13"]),
                "execution_status": ["executed", "executed"],
                "target_risk_allocation": [1.0, 0.5],
                "requested_selected_sids": ["A|B", "A|C"],
                "missing_target_sids": ["", ""],
                "requested_long_exposure": [1.0, 0.25],
                "requested_short_exposure": [0.0, 0.25],
                "requested_gross_exposure": [1.0, 0.5],
                "requested_net_exposure": [1.0, 0.0],
            }
        )
        reference = SimpleNamespace(rebalances=rebalances)
        identical = SimpleNamespace(rebalances=rebalances.copy(deep=True))
        _validate_g11_core_path_state_identity(reference, identical)

        for column, value in (
            ("target_risk_allocation", 0.6),
            ("requested_selected_sids", "A|D"),
            ("requested_gross_exposure", 0.6),
        ):
            mutated = SimpleNamespace(rebalances=rebalances.copy(deep=True))
            mutated.rebalances.loc[1, column] = value
            with self.subTest(column=column):
                with self.assertRaisesRegex(DataQualityError, "state|targets|selected"):
                    _validate_g11_core_path_state_identity(reference, mutated)

    def test_g00_scalar_identity_accepts_leave_cash_and_signed_skip(self) -> None:
        catalog = ExperimentCatalog.load(Path("config/experiments"))
        strategies = catalog.group("G11").strategies()
        long_only = next(
            strategy
            for strategy in strategies
            if strategy.portfolio_mode is PortfolioMode.LONG_ONLY
        )
        long_short = next(
            strategy
            for strategy in strategies
            if strategy.portfolio_mode is PortfolioMode.LONG_SHORT
        )
        signal_date = pd.Timestamp("2020-01-03")
        execution_date = pd.Timestamp("2020-01-06")
        skip_signal_date = pd.Timestamp("2020-01-10")
        skip_execution_date = pd.Timestamp("2020-01-13")
        allocation = 0.4

        lo_common = {
            "signal_date": signal_date,
            "execution_date": execution_date,
            "execution_status": "executed",
            "requested_selected_count": 2,
            "requested_selected_sids": "A|B",
            "missing_target_count": 1,
            "missing_target_sids": "B",
        }
        ls_common = {
            "signal_date": signal_date,
            "execution_date": execution_date,
            "execution_status": "executed",
            "requested_selected_count": 4,
            "requested_selected_sids": "A|B|C|D",
            "missing_target_count": 0,
            "missing_target_sids": "",
        }
        skip_common = {
            "signal_date": skip_signal_date,
            "execution_date": skip_execution_date,
            "execution_status": "skipped_signed_missing_open",
            "requested_selected_count": 4,
            "requested_selected_sids": "A|B|C|D",
            "missing_target_count": 1,
            "missing_target_sids": "D",
        }
        baseline_rebalances = pd.DataFrame(
            [
                {"strategy_id": long_only.parent_id, **lo_common},
                {"strategy_id": long_short.parent_id, **ls_common},
                {"strategy_id": long_short.parent_id, **skip_common},
            ]
        )
        candidate_rebalances = pd.DataFrame(
            [
                {
                    "strategy_id": long_only.strategy_id,
                    "portfolio_mode": "long_only",
                    "target_risk_allocation": allocation,
                    "pretrade_long_exposure": 0.0,
                    "pretrade_short_exposure": 0.0,
                    "pretrade_gross_exposure": 0.0,
                    "pretrade_net_exposure": 0.0,
                    "requested_long_exposure": allocation,
                    "requested_short_exposure": 0.0,
                    "requested_gross_exposure": allocation,
                    "requested_net_exposure": allocation,
                    # B has no open.  The filled target is therefore smaller
                    # than the requested allocation and the balance stays cash.
                    "target_long_exposure": 0.2,
                    "target_short_exposure": 0.0,
                    "target_gross_exposure": 0.2,
                    "target_net_exposure": 0.2,
                    "target_cash_weight": 0.8,
                    "l1_turnover": 0.2,
                    "cost_amount": 0.0,
                    **lo_common,
                },
                {
                    "strategy_id": long_short.strategy_id,
                    "portfolio_mode": "long_short",
                    "target_risk_allocation": allocation,
                    "pretrade_long_exposure": 0.0,
                    "pretrade_short_exposure": 0.0,
                    "pretrade_gross_exposure": 0.0,
                    "pretrade_net_exposure": 0.0,
                    "requested_long_exposure": 0.2,
                    "requested_short_exposure": 0.2,
                    "requested_gross_exposure": allocation,
                    "requested_net_exposure": 0.0,
                    "target_long_exposure": 0.2,
                    "target_short_exposure": 0.2,
                    "target_gross_exposure": allocation,
                    "target_net_exposure": 0.0,
                    "target_cash_weight": 1.0,
                    "l1_turnover": allocation,
                    "cost_amount": 0.0,
                    **ls_common,
                },
                {
                    "strategy_id": long_short.strategy_id,
                    "portfolio_mode": "long_short",
                    "target_risk_allocation": allocation,
                    "pretrade_long_exposure": 0.18,
                    "pretrade_short_exposure": 0.17,
                    "pretrade_gross_exposure": 0.35,
                    "pretrade_net_exposure": 0.01,
                    "requested_long_exposure": 0.2,
                    "requested_short_exposure": 0.2,
                    "requested_gross_exposure": allocation,
                    "requested_net_exposure": 0.0,
                    "target_long_exposure": 0.18,
                    "target_short_exposure": 0.17,
                    "target_gross_exposure": 0.35,
                    "target_net_exposure": 0.01,
                    "target_cash_weight": 0.99,
                    "l1_turnover": 0.0,
                    "cost_amount": 0.0,
                    **skip_common,
                },
            ]
        )
        baseline_holdings: list[dict[str, object]] = []
        candidate_holdings: list[dict[str, object]] = []
        for strategy, sids, weights in (
            (long_only, ["A"], [0.5]),
            (long_short, ["A", "B", "C", "D"], [0.25, 0.25, -0.25, -0.25]),
        ):
            for sid, weight in zip(sids, weights, strict=True):
                common = {
                    "signal_date": signal_date,
                    "execution_date": execution_date,
                    "sid": sid,
                }
                baseline_holdings.append(
                    {
                        "strategy_id": strategy.parent_id,
                        "target_weight": weight,
                        **common,
                    }
                )
                candidate_holdings.append(
                    {
                        "strategy_id": strategy.strategy_id,
                        "target_weight": allocation * weight,
                        **common,
                    }
                )
        with patch(
            "momentum_reversal.pipelines.g11.pd.read_parquet",
            return_value=pd.DataFrame(baseline_holdings),
        ):
            reference = SimpleNamespace(
                root=Path("g00-reference"), rebalances=baseline_rebalances
            )
            candidate = pd.DataFrame(candidate_holdings)
            _validate_g11_g00_path_identity(
                candidate_rebalances, candidate, reference
            )
            mutated = candidate.copy()
            mutated.loc[0, "target_weight"] += 0.01
            with self.assertRaisesRegex(DataQualityError, "scalar multiples"):
                _validate_g11_g00_path_identity(
                    candidate_rebalances, mutated, reference
                )
            wrong_cash = candidate_rebalances.copy()
            wrong_cash.loc[0, "target_cash_weight"] = 0.6
            with self.assertRaisesRegex(DataQualityError, "cash"):
                _validate_g11_g00_path_identity(wrong_cash, candidate, reference)
            wrong_skip = candidate_rebalances.copy()
            wrong_skip.loc[2, "l1_turnover"] = 0.01
            with self.assertRaisesRegex(DataQualityError, "zero l1_turnover"):
                _validate_g11_g00_path_identity(wrong_skip, candidate, reference)
            wrong_skip_cash = candidate_rebalances.copy()
            wrong_skip_cash.loc[2, "target_cash_weight"] = 1.0
            with self.assertRaisesRegex(DataQualityError, "cash"):
                _validate_g11_g00_path_identity(
                    wrong_skip_cash, candidate, reference
                )

    def test_daily_shared_state_has_exact_count_range_and_cross_path_identity(self) -> None:
        start = pd.Timestamp("2014-06-30")
        end = pd.Timestamp("2026-06-30")
        business_days = pd.bdate_range(start, end)
        sessions = business_days[:3_017].append(pd.DatetimeIndex([end]))
        rv = pd.Series(
            0.12 + 0.02 * np.sin(np.arange(len(sessions)) / 17.0),
            index=sessions,
        )
        regime = pd.DataFrame(
            {
                "spy_realized_volatility": rv,
                "lagged_q25": 0.10,
                "lagged_q50": 0.12,
                "lagged_q75": 0.14,
                "volatility_quartile": pd.Series(
                    np.select(
                        [
                            rv.to_numpy() <= 0.10,
                            rv.to_numpy() <= 0.12,
                            rv.to_numpy() <= 0.14,
                        ],
                        [1, 2, 3],
                        default=4,
                    ),
                    index=sessions,
                    dtype="Int64",
                ),
                "high_volatility": rv.gt(0.14),
            },
            index=sessions,
        )
        warmup = regime.index < pd.Timestamp("2016-02-03")
        regime.loc[warmup, ["lagged_q25", "lagged_q50", "lagged_q75"]] = np.nan
        regime.loc[warmup, "volatility_quartile"] = pd.NA
        regime["high_volatility"] = regime["high_volatility"].astype("boolean")
        regime.loc[warmup, "high_volatility"] = pd.NA
        regime["target_risk_allocation"] = continuous_spy_allocation(regime)
        regime["target_scaled_source_volatility"] = (
            regime["spy_realized_volatility"]
            * regime["target_risk_allocation"]
        )
        regime["cap_is_binding"] = regime["spy_realized_volatility"].le(0.15)
        rows: list[dict[str, object]] = []
        catalog = ExperimentCatalog.load(Path("config/experiments"))
        for strategy in catalog.group("G11").strategies():
            primary_cost = 10.0 if strategy.frequency == "weekly" else 5.0
            rows.extend(
                _g11_daily_risk_diagnostics(
                    strategy=strategy,
                    primary_cost=primary_cost,
                    regime=regime,
                    audit_start=start,
                    audit_end=end,
                )
            )
        diagnostics = pd.DataFrame(rows)
        data = SimpleNamespace(
            sessions=sessions,
            evaluation_start=pd.Timestamp("2018-01-02"),
            evaluation_end=end,
        )
        _validate_daily_spy_risk_state(diagnostics, data)
        self.assertEqual(len(diagnostics), 108_648)
        self.assertEqual(pd.to_datetime(diagnostics["date"]).min(), start)
        self.assertEqual(pd.to_datetime(diagnostics["date"]).max(), end)

        mutated = diagnostics.copy()
        first_complete = mutated["lagged_q25"].first_valid_index()
        self.assertIsNotNone(first_complete)
        q3_index = mutated.index[
            mutated["volatility_quartile"].astype("Int64").eq(3).fillna(False)
        ][0]
        mutated.loc[q3_index, "lagged_q25"] += 0.001
        with self.assertRaisesRegex(DataQualityError, "shared|identical"):
            _validate_daily_spy_risk_state(mutated, data)
        wrong_high = diagnostics.copy()
        wrong_high.loc[first_complete, "high_volatility"] = not bool(
            wrong_high.loc[first_complete, "high_volatility"]
        )
        with self.assertRaisesRegex(DataQualityError, "high-volatility"):
            _validate_daily_spy_risk_state(wrong_high, data)
        wrong_cap = diagnostics.copy()
        cap_date = pd.Timestamp(wrong_cap.loc[0, "date"])
        same_date = pd.to_datetime(wrong_cap["date"]).eq(cap_date)
        wrong_cap.loc[same_date, "cap_is_binding"] = ~wrong_cap.loc[
            same_date, "cap_is_binding"
        ].astype(bool)
        with self.assertRaisesRegex(DataQualityError, "cap"):
            _validate_daily_spy_risk_state(wrong_cap, data)
        with self.assertRaisesRegex(RuntimeError, "108648"):
            _validate_daily_spy_risk_state(diagnostics.iloc[:-1], data)

    def test_daily_pnl_attribution_schema_closes_and_records_g11_provenance(self) -> None:
        dates = pd.bdate_range("2018-01-02", periods=2)
        nav = pd.DataFrame(
            {
                "daily_return": [0.0, 0.0],
                "nav": [1.0, 1.0],
                "long_value": [0.0, 0.0],
                "short_value": [0.0, 0.0],
                "cash_value": [1.0, 1.0],
                "short_borrow_fee_amount": [0.0, 0.0],
                "rf_return": [0.0, 0.0],
            },
            index=dates,
        )
        result = SimpleNamespace(
            nav=nav,
            rebalances=pd.DataFrame(),
            corporate_action_events=pd.DataFrame(),
            valuation_fallbacks=pd.DataFrame(),
        )
        strategy = ExperimentCatalog.load(Path("config/experiments")).group(
            "G11"
        ).strategies()[0]
        diagnostics: list[dict[str, object]] = []
        _append_g11_pnl_attribution(
            strategy=strategy,
            result=result,
            cost_bps=0.0,
            borrow_fee=0.0,
            diagnostic_rows=diagnostics,
        )
        pnl_columns = {
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
        }
        self.assertTrue(pnl_columns.issubset(result.nav.columns))
        self.assertTrue(
            result.nav[list(pnl_columns)].fillna(0.0).eq(0.0).all().all()
        )
        self.assertTrue(diagnostics)
        self.assertEqual({row["group_id"] for row in diagnostics}, {"G11"})


class G31DeriskTests(unittest.TestCase):
    def test_q4_allocation_is_full_at_or_below_q75_and_ratio_above(self) -> None:
        dates = pd.bdate_range("2020-01-02", periods=3)
        regime = pd.DataFrame(
            {
                "spy_realized_volatility": [0.10, 0.20, 0.40],
                "lagged_q75": [0.20, 0.20, 0.20],
                "volatility_quartile": pd.Series(
                    [2, 3, 4], index=dates, dtype="Int64"
                ),
                "high_volatility": [False, False, True],
            },
            index=dates,
        )
        allocation = strict_q4_derisk_allocation(regime, dates)
        np.testing.assert_allclose(allocation.to_numpy(), [1.0, 1.0, 0.5])
        self.assertEqual(allocation.name, "target_risk_allocation")
        self.assertTrue(allocation.index.equals(dates))

    def test_q4_allocation_fails_closed_on_missing_or_inconsistent_state(self) -> None:
        dates = pd.bdate_range("2020-01-02", periods=2)
        regime = pd.DataFrame(
            {
                "spy_realized_volatility": [0.10, 0.30],
                "lagged_q75": [0.20, 0.20],
                "volatility_quartile": pd.Series(
                    [2, 4], index=dates, dtype="Int64"
                ),
                "high_volatility": [False, True],
            },
            index=dates,
        )
        with self.assertRaisesRegex(DataQualityError, "unavailable"):
            strict_q4_derisk_allocation(
                regime, dates.append(pd.DatetimeIndex([pd.Timestamp("2020-01-06")]))
            )
        inconsistent = regime.copy()
        inconsistent.loc[dates[1], "volatility_quartile"] = 3
        inconsistent.loc[dates[1], "high_volatility"] = False
        with self.assertRaisesRegex(ValueError, "non-Q4"):
            strict_q4_derisk_allocation(inconsistent, dates)

    def test_g31_scenarios_map_one_to_one_to_g00(self) -> None:
        catalog = ExperimentCatalog.load(Path("config/experiments"))
        rows: list[dict[str, object]] = []
        references: list[dict[str, object]] = []
        metrics = (
            "cagr",
            "sharpe_excess_rf",
            "max_drawdown",
            "annualized_volatility",
            "annualized_l1_turnover",
        )
        for strategy in catalog.group("G31").strategies():
            borrow_fees = (
                (0.0,)
                if strategy.portfolio_mode is PortfolioMode.LONG_ONLY
                else (0.0, 0.01, 0.03)
            )
            for cost_bps in (0.0, 5.0, 10.0, 20.0):
                for borrow_fee in borrow_fees:
                    identity = {
                        "strategy_id": strategy.strategy_id,
                        "portfolio_mode": strategy.portfolio_mode.value,
                        "variant_id": "base",
                        "cost_bps": cost_bps,
                        "borrow_fee_annual": borrow_fee,
                    }
                    rows.append({**identity, **{metric: 1.0 for metric in metrics}})
                    references.append(
                        {
                            **identity,
                            "strategy_id": strategy.parent_id,
                            **{metric: 0.5 for metric in metrics},
                        }
                    )
        summary = pd.DataFrame(rows)
        comparison = _attach_g31_g00_comparisons(
            summary, pd.DataFrame(references)
        )
        self.assertEqual(catalog.group("G31").strategy_count, 36)
        self.assertEqual(len(summary), 288)
        self.assertEqual(len(comparison), 1440)
        self.assertTrue(comparison["estimate"].eq(0.5).all())
        self.assertTrue(
            comparison["reference_strategy_id"].astype(str).str.startswith("G00__").all()
        )


class G12ContinuousBookScaleTests(unittest.TestCase):
    def test_rv126_formula_uses_current_return_and_future_is_causal(self) -> None:
        dates = pd.bdate_range("2014-06-30", periods=900)
        values = pd.Series(
            0.001 + 0.015 * np.sin(np.arange(len(dates)) / 11.0), index=dates
        )
        regime = strict_lagged_book_quartiles(
            values, realized_vol_window=126, history_sessions=756
        )
        cutoff = dates[890]
        expected_rv = float(values.loc[:cutoff].tail(126).std(ddof=1) * np.sqrt(252.0))
        self.assertAlmostEqual(
            float(regime.loc[cutoff, "book_realized_volatility"]), expected_rv
        )
        allocation = continuous_book_allocation(regime, [cutoff])
        self.assertAlmostEqual(
            float(allocation.loc[cutoff]), min(1.0, 0.15 / expected_rv)
        )

        revised_current = values.copy()
        revised_current.loc[cutoff] += 0.20
        current_regime = strict_lagged_book_quartiles(
            revised_current, realized_vol_window=126, history_sessions=756
        )
        self.assertNotEqual(
            float(regime.loc[cutoff, "book_realized_volatility"]),
            float(current_regime.loc[cutoff, "book_realized_volatility"]),
        )
        revised_future = values.copy()
        revised_future.loc[revised_future.index > cutoff] *= -7.0
        future_regime = strict_lagged_book_quartiles(
            revised_future, realized_vol_window=126, history_sessions=756
        )
        pd.testing.assert_frame_equal(
            regime.loc[:cutoff], future_regime.loc[:cutoff], check_exact=True
        )

    def test_formula_caps_at_one_ignores_quartiles_and_fails_closed(self) -> None:
        dates = pd.bdate_range("2020-01-02", periods=4)
        regime = pd.DataFrame(
            {
                "book_realized_volatility": [np.nan, 0.10, 0.30, 0.60],
                "lagged_q75": [np.nan, 99.0, -99.0, 0.01],
                "volatility_quartile": pd.Series(
                    [pd.NA, 4, 1, 2], index=dates, dtype="Int64"
                ),
            },
            index=dates,
        )
        daily = continuous_book_allocation(regime)
        self.assertTrue(np.isnan(float(daily.iloc[0])))
        np.testing.assert_allclose(daily.iloc[1:].to_numpy(), [1.0, 0.5, 0.25])
        sampled = continuous_book_allocation(regime, dates[1:])
        stripped = continuous_book_allocation(
            regime[["book_realized_volatility"]], dates[1:]
        )
        pd.testing.assert_series_equal(sampled, stripped, check_exact=True)

        with self.assertRaisesRegex(ValueError, "annual_target_volatility=0.15"):
            continuous_book_allocation(
                regime, dates[1:], annual_target_volatility=0.20
            )
        with self.assertRaisesRegex(ValueError, "maximum_scale=1.0"):
            continuous_book_allocation(regime, dates[1:], maximum_scale=1.5)
        invalid = regime.copy()
        invalid.loc[dates[2], "book_realized_volatility"] = 0.0
        with self.assertRaisesRegex(DataQualityError, "finite and positive"):
            continuous_book_allocation(invalid, dates[1:])
        with self.assertRaisesRegex(DataQualityError, "unavailable"):
            continuous_book_allocation(regime, dates)

    def test_config_immutable_and_only_exact_g00_reference(self) -> None:
        context = prepare_experiment_run(
            "config/experiments/G12.toml",
            run_id="g12-config-fixture",
            dataset_version="fixture-v1",
        )
        config = G12RunConfig(
            context=context, reference_g00_root=Path("g00-reference"), workers=8
        )
        self.assertEqual(
            set(G12RunConfig.__dataclass_fields__),
            {"context", "reference_g00_root", "allow_review_dataset", "workers"},
        )
        self.assertFalse(hasattr(config, "reference_g11_root"))
        self.assertFalse(hasattr(config, "reference_g32_root"))
        wrong = prepare_experiment_run(
            "config/experiments/G32.toml",
            run_id="g32-not-g12",
            dataset_version="fixture-v1",
        )
        with self.assertRaisesRegex(ValueError, "registered G12 spec"):
            G12RunConfig(context=wrong, reference_g00_root=Path("g00"))
        for workers in (True, 1.5, 0, 9):
            with self.subTest(workers=workers):
                with self.assertRaisesRegex(ValueError, "workers"):
                    G12RunConfig(
                        context=context,
                        reference_g00_root=Path("g00"),
                        workers=workers,  # type: ignore[arg-type]
                    )

        expected_hash = (
            "8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66"
        )
        _validate_g12_reference_anchor(
            SimpleNamespace(
                manifest={"run_id": "g00-frozen-v3-v1"},
                manifest_sha256=expected_hash,
            )
        )
        with self.assertRaisesRegex(DataQualityError, "G00 reference"):
            _validate_g12_reference_anchor(
                SimpleNamespace(
                    manifest={"run_id": "g32-frozen-v3-v1"},
                    manifest_sha256=expected_hash,
                )
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            immutable = prepare_experiment_run(
                "config/experiments/G12.toml",
                run_id="g12-existing",
                dataset_version="fixture-v1",
                data_root=root / "data",
                output_root=root / "results",
            )
            immutable.bundle_dir.mkdir(parents=True)
            with self.assertRaisesRegex(FileExistsError, "immutable"):
                run_g12(
                    G12RunConfig(
                        context=immutable,
                        reference_g00_root=root / "results" / "g00-reference",
                    )
                )

    def test_comparison_contract_has_1440_rows_and_g12_identity(self) -> None:
        context = prepare_experiment_run(
            "config/experiments/G12.toml",
            run_id="g12-comparison-fixture",
            dataset_version="fixture-v1",
        )
        rows: list[dict[str, object]] = []
        reference_rows: list[dict[str, object]] = []
        for strategy in context.strategies:
            borrow_fees = [0.0] if strategy.portfolio_mode is PortfolioMode.LONG_ONLY else [0.0, 0.01, 0.03]
            for cost in (0.0, 5.0, 10.0, 20.0):
                for borrow in borrow_fees:
                    metrics = {
                        "cagr": 0.10,
                        "sharpe_excess_rf": 0.50,
                        "max_drawdown": -0.20,
                        "annualized_volatility": 0.15,
                        "annualized_l1_turnover": 2.0,
                    }
                    rows.append(
                        {
                            "strategy_id": strategy.strategy_id,
                            "portfolio_mode": strategy.portfolio_mode.value,
                            "variant_id": "base",
                            "cost_bps": cost,
                            "borrow_fee_annual": borrow,
                            **metrics,
                        }
                    )
                    reference_rows.append(
                        {
                            "strategy_id": strategy.strategy_id.replace("G12__", "G00__"),
                            "cost_bps": cost,
                            "borrow_fee_annual": borrow,
                            **{key: value - 0.01 for key, value in metrics.items()},
                        }
                    )
        summary = pd.DataFrame(rows)
        comparison = _attach_g12_g00_comparisons(
            summary, pd.DataFrame(reference_rows)
        )
        self.assertEqual(len(summary), 288)
        self.assertEqual(len(comparison), 1_440)
        self.assertEqual(set(comparison["group_id"]), {"G12"})
        self.assertTrue(comparison["strategy_id"].str.startswith("G12__").all())
        np.testing.assert_allclose(comparison["estimate"], 0.01)

    def test_catalog_contains_exactly_36_g12_core_paths(self) -> None:
        catalog = ExperimentCatalog.load(Path("config/experiments"))
        self.assertEqual(catalog.group("G12").strategy_count, 36)
        self.assertEqual(
            {strategy.portfolio_mode for strategy in catalog.group("G12").strategies()},
            {PortfolioMode.LONG_ONLY, PortfolioMode.LONG_SHORT},
        )


class G13ContinuousBookForecastScaleTests(unittest.TestCase):
    def test_ewma_forecast_formula_uses_current_return_and_future_is_causal(self) -> None:
        dates = pd.bdate_range("2014-06-30", periods=800)
        returns = pd.Series(
            0.004 + 0.012 * np.sin(np.arange(len(dates)) / 13.0),
            index=dates,
            name="book_return",
        )
        regime = strict_lagged_book_forecast_quartiles(returns)
        cutoff = dates[790]
        expected = float(np.sqrt(252.0 * regime.loc[cutoff, "ewma_variance"]))
        self.assertAlmostEqual(
            float(regime.loc[cutoff, "book_forecast_volatility"]), expected
        )
        allocation = continuous_forecast_allocation(regime, [cutoff])
        self.assertAlmostEqual(
            float(allocation.loc[cutoff]), min(1.0, 0.15 / expected)
        )

        changed_current = returns.copy()
        changed_current.loc[cutoff] += 0.20
        current_regime = strict_lagged_book_forecast_quartiles(changed_current)
        self.assertNotEqual(
            float(regime.loc[cutoff, "book_forecast_volatility"]),
            float(current_regime.loc[cutoff, "book_forecast_volatility"]),
        )
        changed_future = returns.copy()
        changed_future.loc[changed_future.index > cutoff] *= -9.0
        future_regime = strict_lagged_book_forecast_quartiles(changed_future)
        pd.testing.assert_frame_equal(
            regime.loc[:cutoff], future_regime.loc[:cutoff], check_exact=True
        )

    def test_formula_caps_at_one_ignores_quartiles_and_fails_closed(self) -> None:
        dates = pd.bdate_range("2020-01-02", periods=4)
        regime = pd.DataFrame(
            {
                "book_forecast_volatility": [np.nan, 0.10, 0.30, 0.60],
                "lagged_q75": [np.nan, 99.0, -99.0, 0.01],
                "volatility_quartile": pd.Series(
                    [pd.NA, 4, 1, 2], index=dates, dtype="Int64"
                ),
            },
            index=dates,
        )
        daily = continuous_forecast_allocation(regime)
        self.assertTrue(np.isnan(float(daily.iloc[0])))
        np.testing.assert_allclose(daily.iloc[1:].to_numpy(), [1.0, 0.5, 0.25])
        sampled = continuous_forecast_allocation(regime, dates[1:])
        stripped = continuous_forecast_allocation(
            regime[["book_forecast_volatility"]], dates[1:]
        )
        pd.testing.assert_series_equal(sampled, stripped, check_exact=True)
        with self.assertRaisesRegex(ValueError, "annual_target_volatility=0.15"):
            continuous_forecast_allocation(
                regime, dates[1:], annual_target_volatility=0.20
            )
        with self.assertRaisesRegex(ValueError, "maximum_scale=1.0"):
            continuous_forecast_allocation(regime, dates[1:], maximum_scale=1.5)
        invalid = regime.copy()
        invalid.loc[dates[2], "book_forecast_volatility"] = 0.0
        with self.assertRaisesRegex(DataQualityError, "finite and positive"):
            continuous_forecast_allocation(invalid, dates[1:])
        with self.assertRaisesRegex(DataQualityError, "unavailable"):
            continuous_forecast_allocation(regime, dates)

    def test_config_immutable_and_only_exact_g00_reference(self) -> None:
        context = prepare_experiment_run(
            "config/experiments/G13.toml",
            run_id="g13-config-fixture",
            dataset_version="fixture-v1",
        )
        config = G13RunConfig(
            context=context, reference_g00_root=Path("g00-reference"), workers=8
        )
        self.assertEqual(
            set(G13RunConfig.__dataclass_fields__),
            {"context", "reference_g00_root", "allow_review_dataset", "workers"},
        )
        self.assertFalse(hasattr(config, "reference_g11_root"))
        self.assertFalse(hasattr(config, "reference_g12_root"))
        self.assertFalse(hasattr(config, "reference_g33_root"))
        source = Path("src/momentum_reversal/pipelines/g13.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("actual_future_volatility", source)
        wrong = prepare_experiment_run(
            "config/experiments/G33.toml",
            run_id="g33-not-g13",
            dataset_version="fixture-v1",
        )
        with self.assertRaisesRegex(ValueError, "registered G13 spec"):
            G13RunConfig(context=wrong, reference_g00_root=Path("g00"))
        for workers in (True, 1.5, 0, 9):
            with self.subTest(workers=workers):
                with self.assertRaisesRegex(ValueError, "workers"):
                    G13RunConfig(
                        context=context,
                        reference_g00_root=Path("g00"),
                        workers=workers,  # type: ignore[arg-type]
                    )

        expected_hash = (
            "8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66"
        )
        _validate_g13_reference_anchor(
            SimpleNamespace(
                manifest={"run_id": "g00-frozen-v3-v1"},
                manifest_sha256=expected_hash,
            )
        )
        with self.assertRaisesRegex(DataQualityError, "G00 reference"):
            _validate_g13_reference_anchor(
                SimpleNamespace(
                    manifest={"run_id": "g33-frozen-v3-v1"},
                    manifest_sha256=expected_hash,
                )
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            immutable = prepare_experiment_run(
                "config/experiments/G13.toml",
                run_id="g13-existing",
                dataset_version="fixture-v1",
                data_root=root / "data",
                output_root=root / "results",
            )
            immutable.bundle_dir.mkdir(parents=True)
            with self.assertRaisesRegex(FileExistsError, "immutable"):
                run_g13(
                    G13RunConfig(
                        context=immutable,
                        reference_g00_root=root / "results" / "g00-reference",
                    )
                )

    def test_comparison_contract_has_1440_rows_and_g13_identity(self) -> None:
        context = prepare_experiment_run(
            "config/experiments/G13.toml",
            run_id="g13-comparison-fixture",
            dataset_version="fixture-v1",
        )
        rows: list[dict[str, object]] = []
        references: list[dict[str, object]] = []
        metrics = {
            "cagr": 0.10,
            "sharpe_excess_rf": 0.50,
            "max_drawdown": -0.20,
            "annualized_volatility": 0.15,
            "annualized_l1_turnover": 2.0,
        }
        for strategy in context.strategies:
            borrow_fees = (
                [0.0]
                if strategy.portfolio_mode is PortfolioMode.LONG_ONLY
                else [0.0, 0.01, 0.03]
            )
            for cost in (0.0, 5.0, 10.0, 20.0):
                for borrow in borrow_fees:
                    identity = {
                        "strategy_id": strategy.strategy_id,
                        "portfolio_mode": strategy.portfolio_mode.value,
                        "variant_id": "base",
                        "cost_bps": cost,
                        "borrow_fee_annual": borrow,
                    }
                    rows.append({**identity, **metrics})
                    references.append(
                        {
                            "strategy_id": strategy.strategy_id.replace(
                                "G13__", "G00__"
                            ),
                            "cost_bps": cost,
                            "borrow_fee_annual": borrow,
                            **{key: value - 0.01 for key, value in metrics.items()},
                        }
                    )
        comparison = _attach_g13_g00_comparisons(
            pd.DataFrame(rows), pd.DataFrame(references)
        )
        self.assertEqual(len(comparison), 1_440)
        self.assertEqual(set(comparison["group_id"]), {"G13"})
        self.assertTrue(comparison["strategy_id"].str.startswith("G13__").all())
        np.testing.assert_allclose(comparison["estimate"], 0.01)

    def test_catalog_contains_exactly_36_g13_core_paths(self) -> None:
        catalog = ExperimentCatalog.load(Path("config/experiments"))
        self.assertEqual(catalog.group("G13").strategy_count, 36)
        self.assertEqual(
            {strategy.portfolio_mode for strategy in catalog.group("G13").strategies()},
            {PortfolioMode.LONG_ONLY, PortfolioMode.LONG_SHORT},
        )


class G32BookHistDeriskTests(unittest.TestCase):
    @staticmethod
    def _alternating_returns(periods: int = 883) -> pd.Series:
        dates = pd.bdate_range("2014-06-30", periods=periods)
        values = np.resize(np.array([-0.01, 0.01]), periods)
        return pd.Series(values, index=dates, name="book_return")

    def test_rv126_uses_sample_std_and_sqrt252_annualization(self) -> None:
        dates = pd.bdate_range("2014-06-30", periods=126)
        returns = pd.Series(np.linspace(-0.02, 0.02, 126), index=dates)
        result = strict_lagged_book_quartiles(returns)

        self.assertEqual(
            result.columns.tolist(),
            [
                "book_realized_volatility",
                "lagged_q25",
                "lagged_q50",
                "lagged_q75",
                "volatility_quartile",
                "high_volatility",
            ],
        )
        expected = float(returns.std(ddof=1) * np.sqrt(252.0))
        self.assertTrue(result["book_realized_volatility"].iloc[:-1].isna().all())
        self.assertAlmostEqual(
            float(result.loc[dates[-1], "book_realized_volatility"]), expected
        )

    def test_current_rv_is_excluded_and_q4_is_strictly_above_q75(self) -> None:
        returns = self._alternating_returns()
        boundary = strict_lagged_book_quartiles(returns)
        target = returns.index[-1]
        self.assertAlmostEqual(
            float(boundary.loc[target, "book_realized_volatility"]),
            float(boundary.loc[target, "lagged_q75"]),
        )
        self.assertNotEqual(int(boundary.loc[target, "volatility_quartile"]), 4)
        self.assertFalse(bool(boundary.loc[target, "high_volatility"]))

        changed = returns.copy()
        changed.loc[target] = 0.20
        q4 = strict_lagged_book_quartiles(changed)
        self.assertEqual(
            float(q4.loc[target, "lagged_q75"]),
            float(boundary.loc[target, "lagged_q75"]),
        )
        self.assertEqual(int(q4.loc[target, "volatility_quartile"]), 4)
        self.assertTrue(bool(q4.loc[target, "high_volatility"]))

    def test_book_regime_composes_with_frozen_g31_q4_allocation(self) -> None:
        returns = self._alternating_returns()
        target = returns.index[-1]
        returns.loc[target] = 0.20
        regime = strict_lagged_book_quartiles(returns)
        allocation = strict_q4_derisk_allocation(
            regime.rename(
                columns={"book_realized_volatility": "spy_realized_volatility"}
            ),
            pd.DatetimeIndex([target]),
        )
        expected = (
            regime.loc[target, "lagged_q75"]
            / regime.loc[target, "book_realized_volatility"]
        )
        self.assertAlmostEqual(float(allocation.loc[target]), float(expected))
        self.assertGreater(float(allocation.loc[target]), 0.0)
        self.assertLess(float(allocation.loc[target]), 1.0)

    def test_future_return_changes_do_not_revise_prior_states(self) -> None:
        returns = self._alternating_returns(periods=910)
        original = strict_lagged_book_quartiles(returns)
        cutoff = returns.index[890]
        changed = returns.copy()
        changed.loc[changed.index > cutoff] = np.linspace(
            -0.30, 0.30, int((changed.index > cutoff).sum())
        )
        revised = strict_lagged_book_quartiles(changed)
        pd.testing.assert_frame_equal(
            original.loc[:cutoff], revised.loc[:cutoff], check_exact=True
        )

    def test_config_rejects_wrong_group_and_invalid_workers(self) -> None:
        g32 = prepare_experiment_run(
            "config/experiments/G32.toml",
            run_id="g32-config-test",
            dataset_version="fixture-v1",
        )
        kwargs = {
            "reference_g00_root": Path("g00-reference"),
            "reference_g31_root": Path("g31-reference"),
        }
        config = G32RunConfig(context=g32, workers=8, **kwargs)
        self.assertEqual(config.reference_g00_root, Path("g00-reference"))
        self.assertEqual(config.reference_g31_root, Path("g31-reference"))

        wrong_group = prepare_experiment_run(
            "config/experiments/G31.toml",
            run_id="g31-config-test",
            dataset_version="fixture-v1",
        )
        with self.assertRaisesRegex(ValueError, "registered G32 spec"):
            G32RunConfig(context=wrong_group, **kwargs)
        for workers in (True, 1.5, 0, 9):
            with self.subTest(workers=workers):
                with self.assertRaisesRegex(ValueError, "workers"):
                    G32RunConfig(
                        context=g32,
                        workers=workers,  # type: ignore[arg-type]
                        **kwargs,
                    )


class G33BookForecastDeriskTests(unittest.TestCase):
    @staticmethod
    def _constant_magnitude_returns(periods: int = 757) -> pd.Series:
        dates = pd.bdate_range("2014-06-30", periods=periods)
        values = np.resize(np.array([-0.01, 0.01]), periods)
        return pd.Series(values, index=dates, name="book_return")

    def test_ewma_adjust_false_and_constant_variance_21_session_forecast(self) -> None:
        dates = pd.bdate_range("2014-06-30", periods=6)
        returns = pd.Series(
            [0.01, -0.02, 0.015, -0.005, 0.03, -0.01],
            index=dates,
            name="book_return",
        )
        result = strict_lagged_book_forecast_quartiles(
            returns, history_sessions=4
        )

        self.assertEqual(
            result.columns.tolist(),
            [
                "book_forecast_volatility",
                "ewma_variance",
                "forecast_variance_21",
                "lagged_q25",
                "lagged_q50",
                "lagged_q75",
                "volatility_quartile",
                "high_volatility",
            ],
        )
        expected_variance = returns.pow(2).ewm(alpha=0.06, adjust=False).mean()
        direct_variance = np.empty(len(returns), dtype=float)
        direct_variance[0] = float(returns.iloc[0] ** 2)
        for offset in range(1, len(returns)):
            direct_variance[offset] = (
                0.94 * direct_variance[offset - 1]
                + 0.06 * float(returns.iloc[offset] ** 2)
            )
        np.testing.assert_allclose(
            result["ewma_variance"].to_numpy(),
            expected_variance.to_numpy(),
            rtol=0.0,
            atol=1e-15,
        )
        np.testing.assert_allclose(
            result["ewma_variance"].to_numpy(),
            direct_variance,
            rtol=0.0,
            atol=1e-15,
        )
        np.testing.assert_allclose(
            result["forecast_variance_21"].to_numpy(),
            21.0 * expected_variance.to_numpy(),
            rtol=0.0,
            atol=1e-15,
        )
        np.testing.assert_allclose(
            result["book_forecast_volatility"].to_numpy(),
            np.sqrt((252.0 / 21.0) * result["forecast_variance_21"]),
            rtol=0.0,
            atol=1e-15,
        )
        self.assertEqual(float(result.loc[dates[0], "ewma_variance"]), 0.01**2)
        with self.assertRaisesRegex(ValueError, "ewma_decay=0.94"):
            strict_lagged_book_forecast_quartiles(
                returns, ewma_decay=0.95, history_sessions=4
            )
        with self.assertRaisesRegex(ValueError, "forecast_horizon_sessions=21"):
            strict_lagged_book_forecast_quartiles(
                returns, forecast_horizon_sessions=20, history_sessions=4
            )

    def test_zero_initial_variance_fails_closed(self) -> None:
        dates = pd.bdate_range("2014-06-30", periods=5)
        returns = pd.Series([0.0, 0.01, -0.01, 0.02, -0.02], index=dates)
        with self.assertRaisesRegex(DataQualityError, "positive|variance"):
            strict_lagged_book_forecast_quartiles(
                returns, history_sessions=4
            )

    def test_engine_start_is_t0_weekly_and_prior_execution_monthly(self) -> None:
        sessions = pd.bdate_range("2014-04-01", "2014-07-10")
        history_start = pd.Timestamp("2014-06-30")
        schedule = rebalance_schedule(sessions, "monthly")
        execution_dates = pd.to_datetime(schedule["execution_date"])
        candidates = schedule.loc[execution_dates.lt(history_start)]
        expected = candidates.iloc[-1]
        signal_date, execution_date = forecast_engine_start(
            sessions=sessions,
            frequency="monthly",
            history_start=history_start,
        )
        self.assertEqual(signal_date, pd.Timestamp(expected["signal_date"]))
        self.assertEqual(execution_date, pd.Timestamp(expected["execution_date"]))
        self.assertLess(execution_date, history_start)
        self.assertFalse(
            (execution_dates.gt(execution_date) & execution_dates.lt(history_start)).any()
        )
        weekly_signal, weekly_start = forecast_engine_start(
            sessions=sessions,
            frequency="weekly",
            history_start=history_start,
        )
        self.assertIsNone(weekly_signal)
        self.assertEqual(weekly_start, history_start)

    def test_forecast_input_starts_at_t0_and_ignores_pre_t0_values(self) -> None:
        sessions = pd.bdate_range("2014-06-25", "2014-07-04")
        history_start = pd.Timestamp("2014-06-30")
        evaluation_end = sessions[-1]
        daily_return = pd.Series(
            [0.50, -0.40, 0.01, -0.02, 0.03, -0.01, 0.02, -0.015],
            index=sessions,
        )
        naked_nav = pd.DataFrame({"daily_return": daily_return})
        original = forecast_input_returns(
            naked_nav=naked_nav,
            sessions=sessions,
            history_start=history_start,
            evaluation_end=evaluation_end,
        )
        changed = naked_nav.copy()
        changed.loc[changed.index < history_start, "daily_return"] = [np.nan, -2.0, 9.0]
        revised = forecast_input_returns(
            naked_nav=changed,
            sessions=sessions,
            history_start=history_start,
            evaluation_end=evaluation_end,
        )
        self.assertEqual(original.index[0], history_start)
        self.assertTrue(original.index.equals(sessions[sessions >= history_start]))
        pd.testing.assert_series_equal(original, revised, check_exact=True)
        regime = strict_lagged_book_forecast_quartiles(
            original, history_sessions=4
        )
        self.assertEqual(
            float(regime.loc[history_start, "ewma_variance"]),
            float(original.loc[history_start] ** 2),
        )

        zero_t0 = naked_nav.copy()
        zero_t0.loc[history_start, "daily_return"] = 0.0
        with self.assertRaisesRegex(DataQualityError, "2014-06-30|initial|positive"):
            forecast_input_returns(
                naked_nav=zero_t0,
                sessions=sessions,
                history_start=history_start,
                evaluation_end=evaluation_end,
            )

    def test_current_forecast_is_excluded_and_q75_equality_is_not_q4(self) -> None:
        returns = self._constant_magnitude_returns()
        boundary = strict_lagged_book_forecast_quartiles(returns)
        target = returns.index[-1]
        self.assertTrue(pd.isna(boundary.loc[returns.index[-2], "lagged_q75"]))
        self.assertFalse(pd.isna(boundary.loc[target, "lagged_q75"]))
        self.assertAlmostEqual(
            float(boundary.loc[target, "book_forecast_volatility"]),
            float(boundary.loc[target, "lagged_q75"]),
        )
        self.assertNotEqual(int(boundary.loc[target, "volatility_quartile"]), 4)
        self.assertFalse(bool(boundary.loc[target, "high_volatility"]))
        self.assertEqual(
            float(strict_q4_forecast_allocation(boundary, [target]).loc[target]),
            1.0,
        )
        with self.assertRaisesRegex(DataQualityError, "unavailable"):
            strict_q4_forecast_allocation(
                boundary,
                pd.DatetimeIndex([target, target + pd.offsets.BDay(1)]),
            )

        changed = returns.copy()
        changed.loc[target] = 0.20
        q4 = strict_lagged_book_forecast_quartiles(changed)
        self.assertEqual(
            float(q4.loc[target, "lagged_q75"]),
            float(boundary.loc[target, "lagged_q75"]),
        )
        self.assertEqual(int(q4.loc[target, "volatility_quartile"]), 4)
        self.assertTrue(bool(q4.loc[target, "high_volatility"]))

    def test_forecast_regime_composes_with_frozen_g31_allocation(self) -> None:
        returns = self._constant_magnitude_returns()
        target = returns.index[-1]
        returns.loc[target] = 0.20
        regime = strict_lagged_book_forecast_quartiles(returns)
        signal_dates = pd.DatetimeIndex([target])
        allocation = strict_q4_forecast_allocation(regime, signal_dates)
        frozen_g31 = strict_q4_derisk_allocation(
            regime.rename(
                columns={"book_forecast_volatility": "spy_realized_volatility"}
            ),
            signal_dates,
        )
        pd.testing.assert_series_equal(allocation, frozen_g31, check_exact=True)
        expected = (
            regime.loc[target, "lagged_q75"]
            / regime.loc[target, "book_forecast_volatility"]
        )
        self.assertAlmostEqual(float(allocation.loc[target]), float(expected))
        self.assertGreater(float(allocation.loc[target]), 0.0)
        self.assertLess(float(allocation.loc[target]), 1.0)

    def test_future_return_mutation_does_not_revise_prior_forecasts(self) -> None:
        dates = pd.bdate_range("2014-06-30", periods=800)
        returns = pd.Series(np.linspace(-0.02, 0.02, len(dates)), index=dates)
        original = strict_lagged_book_forecast_quartiles(returns)
        cutoff = dates[780]
        changed = returns.copy()
        changed.loc[changed.index > cutoff] = np.linspace(
            -0.30, 0.30, int((changed.index > cutoff).sum())
        )
        revised = strict_lagged_book_forecast_quartiles(changed)
        pd.testing.assert_frame_equal(
            original.loc[:cutoff], revised.loc[:cutoff], check_exact=True
        )

    def test_runner_has_no_actual_future_volatility_or_g32_input(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "momentum_reversal"
            / "pipelines"
            / "g33.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = {
            str(node.module)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertFalse(
            any(module.casefold().endswith("g32") for module in imported_modules)
        )
        subscript_keys = {
            node.slice.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        }
        self.assertNotIn("actual_future_volatility", subscript_keys)
        identifiers = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        }
        identifiers.update(
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        )
        exact_string_literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertNotIn("actual_future_volatility", identifiers)
        self.assertNotIn("actual_future_volatility", exact_string_literals)
        self.assertEqual(
            set(G33RunConfig.__dataclass_fields__),
            {"context", "reference_g00_root", "allow_review_dataset", "workers"},
        )

    def test_implementation_note_hash_is_hard_gated_and_manifested(self) -> None:
        root = Path(__file__).resolve().parents[1]
        note = (
            root
            / "docs"
            / "20_experiments"
            / "G33_book_forecast_derisk"
            / "implementation_note.md"
        )
        expected = "61104f8376c4845d35ef52cab8cbb6fe72ffc1af984d54bf3796aa923c833079"
        self.assertEqual(sha256_file(note), expected)
        source_path = root / "src" / "momentum_reversal" / "pipelines" / "g33.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        self.assertIn(expected, source)
        self.assertIn(
            "_FROZEN_IMPLEMENTATION_NOTE_SHA256",
            functions["_validate_frozen_inputs"],
        )
        self.assertIn("implementation_note_sha256", functions["_manifest_metadata"])

    def test_config_rejects_wrong_group_and_invalid_workers(self) -> None:
        g33 = prepare_experiment_run(
            "config/experiments/G33.toml",
            run_id="g33-config-test",
            dataset_version="fixture-v1",
        )
        config = G33RunConfig(
            context=g33,
            reference_g00_root=Path("g00-reference"),
            workers=8,
        )
        self.assertEqual(config.reference_g00_root, Path("g00-reference"))

        wrong_group = prepare_experiment_run(
            "config/experiments/G32.toml",
            run_id="g32-config-test-for-g33",
            dataset_version="fixture-v1",
        )
        with self.assertRaisesRegex(ValueError, "registered G33 spec"):
            G33RunConfig(
                context=wrong_group,
                reference_g00_root=Path("g00-reference"),
            )
        for workers in (True, 1.5, 0, 9):
            with self.subTest(workers=workers):
                with self.assertRaisesRegex(ValueError, "workers"):
                    G33RunConfig(
                        context=g33,
                        reference_g00_root=Path("g00-reference"),
                        workers=workers,  # type: ignore[arg-type]
                    )

    def test_immutable_rerun_fails_before_loading_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = prepare_experiment_run(
                "config/experiments/G33.toml",
                run_id="g33-existing-fixture",
                dataset_version="fixture-v1",
                data_root=root / "data",
                output_root=root / "results",
            )
            context.bundle_dir.mkdir(parents=True)
            with self.assertRaisesRegex(FileExistsError, "immutable"):
                run_g33(
                    G33RunConfig(
                        context=context,
                        reference_g00_root=root / "results" / "g00-reference",
                    )
                )

    def test_only_exact_frozen_g00_reference_anchor_is_accepted(self) -> None:
        expected_hash = (
            "8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66"
        )
        valid = SimpleNamespace(
            manifest={"run_id": "g00-frozen-v3-v1"},
            manifest_sha256=expected_hash,
        )
        _validate_g33_reference_anchor(valid)
        for wrong in (
            SimpleNamespace(
                manifest={"run_id": "g32-frozen-v3-v1"},
                manifest_sha256=expected_hash,
            ),
            SimpleNamespace(
                manifest={"run_id": "g00-frozen-v3-v1"},
                manifest_sha256="0" * 64,
            ),
        ):
            with self.subTest(wrong=wrong.manifest["run_id"]):
                with self.assertRaisesRegex(DataQualityError, "G00 reference"):
                    _validate_g33_reference_anchor(wrong)

    def test_g33_catalog_and_scenarios_map_one_to_one_to_g00(self) -> None:
        catalog = ExperimentCatalog.load(Path("config/experiments"))
        rows: list[dict[str, object]] = []
        references: list[dict[str, object]] = []
        metrics = (
            "cagr",
            "sharpe_excess_rf",
            "max_drawdown",
            "annualized_volatility",
            "annualized_l1_turnover",
        )
        for strategy in catalog.group("G33").strategies():
            borrow_fees = (
                (0.0,)
                if strategy.portfolio_mode is PortfolioMode.LONG_ONLY
                else (0.0, 0.01, 0.03)
            )
            for cost_bps in (0.0, 5.0, 10.0, 20.0):
                for borrow_fee in borrow_fees:
                    identity = {
                        "strategy_id": strategy.strategy_id,
                        "portfolio_mode": strategy.portfolio_mode.value,
                        "variant_id": "base",
                        "cost_bps": cost_bps,
                        "borrow_fee_annual": borrow_fee,
                    }
                    rows.append({**identity, **{metric: 1.0 for metric in metrics}})
                    references.append(
                        {
                            **identity,
                            "strategy_id": strategy.parent_id,
                            **{metric: 0.5 for metric in metrics},
                        }
                    )
        summary = pd.DataFrame(rows)
        comparison = _attach_g33_g00_comparisons(
            summary, pd.DataFrame(references)
        )
        self.assertEqual(catalog.group("G33").strategy_count, 36)
        self.assertEqual(len(summary), 288)
        self.assertEqual(len(comparison), 1440)
        self.assertTrue(comparison["estimate"].eq(0.5).all())
        self.assertTrue(
            comparison["reference_strategy_id"].astype(str).str.startswith("G00__").all()
        )

    def test_cost_and_borrow_paths_cannot_change_forecast_state_or_targets(self) -> None:
        rebalances = pd.DataFrame(
            {
                "signal_date": pd.to_datetime(["2020-01-03", "2020-01-10"]),
                "execution_date": pd.to_datetime(["2020-01-06", "2020-01-13"]),
                "execution_status": ["executed", "executed"],
                "target_risk_allocation": [1.0, 0.5],
                "requested_selected_sids": ["A|B", "A|C"],
                "missing_target_sids": ["", ""],
                "requested_long_exposure": [1.0, 0.25],
                "requested_short_exposure": [0.0, 0.25],
                "requested_gross_exposure": [1.0, 0.5],
                "requested_net_exposure": [1.0, 0.0],
            }
        )
        reference = SimpleNamespace(rebalances=rebalances)
        identical = SimpleNamespace(rebalances=rebalances.copy(deep=True))
        _validate_core_path_state_identity(reference, identical)

        mutated = SimpleNamespace(rebalances=rebalances.copy(deep=True))
        mutated.rebalances.loc[1, "target_risk_allocation"] = 0.6
        with self.assertRaisesRegex(DataQualityError, "state or targets"):
            _validate_core_path_state_identity(reference, mutated)

    def test_daily_pnl_attribution_schema_closes_on_cash_only_fixture(self) -> None:
        dates = pd.bdate_range("2018-01-02", periods=2)
        nav = pd.DataFrame(
            {
                "daily_return": [0.0, 0.0],
                "nav": [1.0, 1.0],
                "long_value": [0.0, 0.0],
                "short_value": [0.0, 0.0],
                "cash_value": [1.0, 1.0],
                "short_borrow_fee_amount": [0.0, 0.0],
                "rf_return": [0.0, 0.0],
            },
            index=dates,
        )
        result = SimpleNamespace(
            nav=nav,
            rebalances=pd.DataFrame(),
            corporate_action_events=pd.DataFrame(),
            valuation_fallbacks=pd.DataFrame(),
        )
        strategy = ExperimentCatalog.load(Path("config/experiments")).group(
            "G33"
        ).strategies()[0]
        diagnostics: list[dict[str, object]] = []
        _append_g33_pnl_attribution(
            strategy=strategy,
            result=result,
            cost_bps=0.0,
            borrow_fee=0.0,
            diagnostic_rows=diagnostics,
        )
        pnl_columns = {
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
        }
        self.assertTrue(pnl_columns.issubset(result.nav.columns))
        self.assertTrue(result.nav[list(pnl_columns)].fillna(0.0).eq(0.0).all().all())
        self.assertTrue(diagnostics)
        self.assertEqual({row["group_id"] for row in diagnostics}, {"G33"})

    def test_diagnostics_mixed_scopes_round_trip_through_parquet(self) -> None:
        returns = self._constant_magnitude_returns()
        regime = strict_lagged_book_forecast_quartiles(returns)
        regime["book_return"] = returns
        regime["target_risk_allocation"] = strict_q4_forecast_allocation(regime)
        strategy = ExperimentCatalog.load(Path("config/experiments")).group(
            "G33"
        ).strategies()[0]
        rows = _g33_daily_regime_diagnostics(
            strategy=strategy,
            primary_cost=10.0,
            regime=regime,
        )
        rows.append(
            {
                "group_id": "G33",
                "strategy_id": strategy.strategy_id,
                "portfolio_mode": strategy.portfolio_mode.value,
                "variant_id": "base",
                "cost_bps": 10.0,
                "borrow_fee_annual": 0.0,
                "scope": "naked_book_audit",
                "diagnostic": "t0_ewma_variance",
                "value": float(regime["ewma_variance"].iloc[0]),
            }
        )
        diagnostics = pd.DataFrame(rows)
        self.assertTrue(
            set(ARTIFACT_SCHEMAS["diagnostics"]).issubset(diagnostics.columns)
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "diagnostics.parquet"
            try:
                diagnostics.to_parquet(path, index=False)
            except ImportError:
                self.skipTest("pyarrow is not installed")
            restored = pd.read_parquet(path)
        self.assertEqual(len(restored), len(diagnostics))
        self.assertTrue(
            set(ARTIFACT_SCHEMAS["diagnostics"]).issubset(restored.columns)
        )
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(restored["date"]))
        for column in (
            "value",
            "ewma_variance",
            "forecast_variance_21",
            "book_forecast_volatility",
        ):
            self.assertTrue(pd.api.types.is_float_dtype(restored[column]))


class FakePartialProvider:
    """Omit B from batch calls, succeed for both individual retries."""

    def fetch_prices(self, request: PriceRequest) -> pd.DataFrame:
        assets = request.assets if len(request.assets) == 1 else request.assets[:1]
        rows = []
        for asset in assets:
            for date in pd.date_range(request.start, request.end, freq="B"):
                rows.append(
                    {
                        "date": date,
                        "sid": asset.sid,
                        "tr_open": 100.0,
                        "tr_close": 101.0,
                    }
                )
        return pd.DataFrame(rows).set_index(["date", "sid"])


class FakeImpossibleBarProvider:
    """Return one valid symbol and one symbol with impossible Yahoo OHLC."""

    def fetch_prices(self, request: PriceRequest) -> pd.DataFrame:
        rows = []
        for asset in request.assets:
            high = 99.0 if asset.symbol == "BAD" else 102.0
            rows.append(
                {
                    "date": request.start,
                    "sid": asset.sid,
                    "tr_open": 100.0,
                    "tr_high": high,
                    "tr_low": 98.0,
                    "tr_close": 101.0,
                }
            )
        return pd.DataFrame(rows).set_index(["date", "sid"])


class PipelineTests(unittest.TestCase):
    def test_review_runner_gates_and_terminal_limit_fail_closed(self) -> None:
        manifest = {
            "terminal_gate": {"passed": True},
            "corporate_actions": {"valuation_gate_passed": True},
            "prototype_terminal_last_close_max_sessions": 7,
        }
        _validate_review_dataset_gates(manifest, status="review")
        _validate_review_dataset_gates({}, status="valid")
        self.assertEqual(
            _terminal_last_close_max_sessions(manifest, status="review"), 7
        )
        self.assertEqual(_terminal_last_close_max_sessions({}, status="valid"), 25)
        with self.assertRaises(DataQualityError):
            _terminal_last_close_max_sessions({}, status="review")

        for bad_manifest, message in (
            ({}, "terminal_gate.passed"),
            (
                {"terminal_gate": {"passed": True}},
                "corporate_actions.valuation_gate_passed",
            ),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(
                DataQualityError, message
            ):
                _validate_review_dataset_gates(bad_manifest, status="review")
        for invalid in (None, 0, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(DataQualityError):
                _terminal_last_close_max_sessions(
                    {"prototype_terminal_last_close_max_sessions": invalid},
                    status="review",
                )

    def test_zero_member_signal_date_remains_visible_to_dataset_qa(self) -> None:
        signal_dates = (
            pd.Timestamp("2020-01-03"),
            pd.Timestamp("2020-01-10"),
        )
        partial = pd.DataFrame(
            {
                "signal_date": [signal_dates[1]],
                "member_count": [500],
                "history_complete_count": [500],
                "eligible_count": [500],
                "execution_open_count": [500],
                "membership_snapshot_age_days": [0.0],
                "history_coverage": [1.0],
                "execution_open_coverage": [1.0],
            }
        )
        completed = _complete_signal_date_summary(partial, signal_dates)
        first = completed.set_index("signal_date").loc[signal_dates[0]]
        self.assertEqual(int(first["member_count"]), 0)
        self.assertEqual(float(first["history_coverage"]), 0.0)

    def test_download_batch_fallback_does_not_duplicate_successes(self) -> None:
        prices, failures, _ = download_yfinance_symbols(
            FakePartialProvider(),
            ["A", "B"],
            start="2020-01-02",
            end="2020-01-03",
            batch_size=2,
        )
        self.assertFalse(prices.index.has_duplicates)
        self.assertEqual(prices.index.get_level_values("sid").nunique(), 2)
        self.assertTrue(failures.empty)

    def test_impossible_bar_is_isolated_as_symbol_failure(self) -> None:
        prices, failures, acquisition_ids = download_yfinance_symbols(
            FakeImpossibleBarProvider(),
            ["GOOD", "BAD"],
            start="2020-01-02",
            end="2020-01-03",
            batch_size=2,
        )
        self.assertEqual(
            set(prices.index.get_level_values("sid")),
            {acquisition_ids["GOOD"]},
        )
        self.assertEqual(failures["symbol"].tolist(), ["BAD"])
        self.assertEqual(failures["error_type"].tolist(), ["DataSchemaError"])

    def test_download_plan_fails_before_network_when_pit_sid_has_no_mapping(self) -> None:
        master = SecurityMaster(
            pd.DataFrame(
                {
                    "sid": ["A"],
                    "provider": ["yfinance"],
                    "ticker": ["AAA"],
                }
            )
        )
        with self.assertRaises(DataQualityError):
            build_yfinance_download_plan(
                master,
                ["A", "MISSING"],
                price_start="2010-01-01",
                end="2020-01-01",
            )

    def test_download_plan_rejects_unscaled_ticker_change(self) -> None:
        master = SecurityMaster(
            pd.DataFrame(
                {
                    "sid": ["A", "A"],
                    "provider": ["yfinance", "yfinance"],
                    "ticker": ["OLD", "NEW"],
                    "valid_from": ["2010-01-01", "2020-01-01"],
                    "valid_to": ["2020-01-01", None],
                }
            )
        )
        with self.assertRaisesRegex(DataQualityError, "externally audited link factor"):
            build_yfinance_download_plan(
                master,
                ["A"],
                price_start="2010-01-01",
                end="2025-01-01",
            )

    def test_download_plan_rejects_concurrent_symbol_reuse(self) -> None:
        master = SecurityMaster(
            pd.DataFrame(
                {
                    "sid": ["A", "B"],
                    "provider": ["yfinance", "yfinance"],
                    "ticker": ["SAME", "SAME"],
                    "valid_from": ["2010-01-01", "2015-01-01"],
                    "valid_to": ["2020-01-01", "2025-01-01"],
                }
            )
        )
        with self.assertRaisesRegex(DataQualityError, "multiple stable sids"):
            build_yfinance_download_plan(
                master,
                ["A", "B"],
                price_start="2010-01-01",
                end="2025-01-01",
            )

    def test_declared_pit_semantics_must_match_storage_format(self) -> None:
        snapshots = PITMembership.from_snapshots(
            pd.DataFrame({"date": ["2020-01-03"], "sid": ["A"]})
        )
        with self.assertRaisesRegex(DataQualityError, "requires intervals"):
            _validate_pit_date_semantics(snapshots, "effective")
        _validate_pit_date_semantics(snapshots, "snapshot_asof")

    def test_snapshot_audit_exposes_staleness_on_every_signal_date(self) -> None:
        sessions = pd.date_range("2020-01-02", "2020-01-10", freq="B")
        prices = pd.DataFrame(
            {
                "date": sessions,
                "sid": "A",
                "tr_open": 100.0,
                "tr_close": 100.0,
            }
        )
        membership = PITMembership.from_snapshots(
            pd.DataFrame({"date": ["2020-01-03"], "sid": ["A"]})
        )
        from momentum_reversal.data import TradingCalendar

        audit = build_universe_audit(
            prices,
            membership,
            ["2020-01-03", "2020-01-10"],
            TradingCalendar(sessions),
        )
        ages = audit.set_index("signal_date")["membership_snapshot_age_days"]
        self.assertEqual(int(ages.loc[pd.Timestamp("2020-01-03")]), 0)
        self.assertEqual(int(ages.loc[pd.Timestamp("2020-01-10")]), 7)

    def test_benchmark_first_day_is_open_to_close_then_close_to_close(self) -> None:
        dates = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
        prices = pd.DataFrame(
            {
                "date": dates,
                "sid": "benchmark",
                "tr_open": [100.0, 102.0, 103.0],
                "tr_close": [101.0, 103.0, 106.0],
            }
        ).set_index(["date", "sid"])
        benchmark = _benchmark_frame(prices, label="SPY_TR", symbol="SPY")
        returns = benchmark_returns_from_total_return_prices(
            benchmark.rename(
                columns={
                    "benchmark_tr_open": "tr_open",
                    "benchmark_tr_close": "tr_close",
                }
            ),
            pd.DatetimeIndex(dates[1:]),
        )
        self.assertAlmostEqual(returns.iloc[0], 103.0 / 102.0 - 1.0)
        self.assertAlmostEqual(returns.iloc[1], 106.0 / 103.0 - 1.0)

    def test_benchmark_rejects_shortened_sample(self) -> None:
        strategy_dates = pd.to_datetime(["2020-01-02", "2020-01-03"])
        benchmark = pd.DataFrame(
            {"date": ["2020-01-02"], "tr_open": [100.0], "tr_close": [101.0]}
        )
        with self.assertRaises(DataQualityError):
            try:
                benchmark_returns_from_total_return_prices(benchmark, strategy_dates)
            except ValueError as error:
                raise DataQualityError(str(error)) from error

    def test_module_help_runs_without_optional_data_dependencies(self) -> None:
        root = Path(__file__).resolve().parents[1]
        environment = {"PYTHONPATH": str(root / "src")}
        completed = subprocess.run(
            [sys.executable, "-m", "momentum_reversal", "--help"],
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("build-data", completed.stdout)
        self.assertIn("run-baseline", completed.stdout)
        self.assertIn("run-experiment", completed.stdout)

    def test_systematic_catalog_registers_complete_main_grid_and_xs_branch(self) -> None:
        root = Path(__file__).resolve().parents[1]
        catalog = ExperimentCatalog.load(root / "config" / "experiments")
        self.assertEqual(len(catalog.groups), 11)
        self.assertEqual(catalog.main_strategy_count, 468)
        self.assertEqual(catalog.supplemental_strategy_count, 72)
        self.assertEqual(catalog.group("G00").strategy_count, 36)
        self.assertEqual(catalog.group("G21").strategy_count, 72)
        self.assertEqual(catalog.group("G31").strategy_count, 36)
        self.assertEqual(catalog.group("G32").strategy_count, 36)
        self.assertEqual(catalog.group("G33").strategy_count, 36)
        self.assertEqual(catalog.group("XS01").strategy_count, 72)
        ids = [strategy.strategy_id for strategy in catalog.strategies()]
        self.assertEqual(len(ids), len(set(ids)))
        g00_modes = {item.portfolio_mode for item in catalog.group("G00").strategies()}
        self.assertEqual(g00_modes, set(PortfolioMode))
        self.assertIn(
            "G21__mom_255_0__top10__weekly__long_only__rev5",
            ids,
        )

    def test_registered_run_context_and_prepared_bundle_are_portable_and_immutable(
        self,
    ) -> None:
        import json
        import tomllib

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            context = prepare_experiment_run(
                root / "config" / "experiments" / "G00.toml",
                run_id="fixture-systematic-run",
                dataset_version="fixture-v1",
                data_root=temporary_root / "data",
                output_root=temporary_root / "results",
            )
            self.assertEqual(len(context.strategies), 36)
            self.assertEqual(
                context.bundle_dir,
                temporary_root
                / "results"
                / "experiments"
                / "G00"
                / "runs"
                / "fixture-systematic-run",
            )
            result = write_experiment_bundle(
                context,
                summary=empty_summary_frame(),
                comparison=empty_comparison_frame(),
                status="prepared",
            )
            self.assertTrue(result.manifest_path.is_file())
            self.assertTrue((result.output_dir / "config_resolved.toml").is_file())
            self.assertTrue((result.output_dir / "artifacts").is_dir())
            with (result.output_dir / "config_resolved.toml").open("rb") as handle:
                resolved = tomllib.load(handle)
            self.assertEqual(resolved["resolved"]["strategy_count"], 36)
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            validate_experiment_manifest(manifest)
            self.assertEqual(manifest["group_id"], "G00")
            self.assertEqual(manifest["status"], "prepared")
            self.assertEqual(
                set(manifest["portfolio_modes"]), {"long_only", "long_short"}
            )
            self.assertTrue(
                all(not Path(record["path"]).is_absolute() for record in manifest["files"])
            )
            with self.assertRaises(FileExistsError):
                write_experiment_bundle(
                    context,
                    summary=empty_summary_frame(),
                    comparison=empty_comparison_frame(),
                    status="prepared",
                )

    def test_bundle_writer_accepts_optional_prerendered_resolved_config(self) -> None:
        import tomllib

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            context = prepare_experiment_run(
                root / "config" / "experiments" / "G00.toml",
                run_id="fixture-custom-resolved-config",
                dataset_version="fixture-v1",
                output_root=Path(temporary) / "results",
            )
            rendered = (
                'schema_version = 1\n\n[run]\nrun_id = "fixture-custom"\n'
            )
            result = write_experiment_bundle(
                context,
                summary=empty_summary_frame(),
                comparison=empty_comparison_frame(),
                status="prepared",
                resolved_config_toml=rendered,
            )
            written = (result.output_dir / "config_resolved.toml").read_text(
                encoding="utf-8"
            )
            self.assertEqual(written, rendered)
            self.assertEqual(tomllib.loads(written)["run"]["run_id"], "fixture-custom")
            with self.assertRaisesRegex(ValueError, "end with newline"):
                write_experiment_bundle(
                    prepare_experiment_run(
                        root / "config" / "experiments" / "G00.toml",
                        run_id="fixture-bad-resolved-config",
                        dataset_version="fixture-v1",
                        output_root=Path(temporary) / "results",
                    ),
                    summary=empty_summary_frame(),
                    comparison=empty_comparison_frame(),
                    status="prepared",
                    resolved_config_toml="schema_version = 1",
                )

    def test_legacy_reproduction_gate_fails_on_nav_or_return_drift(self) -> None:
        root = Path(__file__).resolve().parents[1]
        context = prepare_experiment_run(
            root / "config" / "experiments" / "G00.toml",
            run_id="comparison-fixture",
            dataset_version="fixture-v1",
        )
        strategy = next(
            item
            for item in context.strategies
            if item.portfolio_mode is PortfolioMode.LONG_ONLY
        )
        dates = pd.to_datetime(["2020-01-02", "2020-01-03"])
        reference = pd.DataFrame(
            {"nav": [1.0, 1.01], "daily_return": [0.0, 0.01]}, index=dates
        )
        candidate = reference.copy()
        candidate.loc[dates[-1], "nav"] += 1e-6
        with self.assertRaises(LegacyReproductionError):
            _compare_navs(
                strategy,
                cost_bps=0.0,
                borrow_fee_annual=0.0,
                candidate=candidate,
                reference=reference,
                comparison_type="engine_reproduction_legacy_zero_cash",
                nav_tolerance=1e-12,
                daily_return_tolerance=1e-12,
                hard_fail=True,
            )

    def test_curated_dataset_runs_and_exports_all_18_by_4_scenarios(self) -> None:
        sessions = pd.bdate_range("2018-01-02", periods=330)
        weekly_executions = pd.DatetimeIndex(
            rebalance_schedule(sessions, "weekly")["execution_date"]
        )
        monthly_executions = pd.DatetimeIndex(
            rebalance_schedule(sessions, "monthly")["execution_date"]
        )
        common_executions = weekly_executions.intersection(monthly_executions)
        evaluation_start = common_executions[common_executions >= sessions[300]][0]
        # Top/Bottom 50 WML requires at least 100 distinct eligible securities.
        sids = [f"S{number:03d}" for number in range(110)]
        index = pd.MultiIndex.from_product(
            [sessions, sids], names=["date", "sid"]
        )
        day = index.get_level_values("date").map(
            pd.Series(range(len(sessions)), index=sessions)
        ).to_numpy(dtype=float)
        security = index.get_level_values("sid").str[1:].astype(int).to_numpy()
        close = 100.0 * (1.0 + 0.0002 * day) * (
            1.0 + 0.00001 * security * day
        )
        prices = pd.DataFrame(
            {"date": index.get_level_values("date"), "sid": index.get_level_values("sid")}
        )
        prices["tr_open"] = close * 0.999
        prices["tr_close"] = close
        membership = pd.DataFrame(
            {"sid": sids, "effective_from": sessions[0], "effective_to": pd.NaT}
        )
        benchmark = pd.DataFrame(
            {
                "date": sessions,
                "benchmark_tr_open": 100.0 + 0.01 * np.arange(len(sessions)),
                "benchmark_tr_close": 100.01 + 0.01 * np.arange(len(sessions)),
                "benchmark_label": "fixture",
                "provider_symbol": "FIXTURE",
            }
        )
        calendar = pd.DataFrame({"session_date": sessions})
        risk_free = pd.DataFrame(
            {"date": sessions, "rf_return": 0.00001}
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout = DatasetLayout(root / "data").create()
            curated = layout.curated_dir("fixture-v1")
            curated.mkdir(parents=True)
            files = []
            for frame, name in (
                (prices, "prices_daily"),
                (membership, "membership"),
                (benchmark, "benchmark_daily"),
                (calendar, "calendar"),
                (risk_free, "risk_free_daily"),
            ):
                path = curated / f"{name}.parquet"
                # CSV data with a .parquet name plus patched pandas readers keeps
                # the end-to-end control flow offline without pyarrow.
                frame.to_csv(path, index=False)
                files.append(path)
            ManifestStore(layout).write(
                "fixture-v1",
                {
                    "status": "valid",
                    "calendar_source": "XNYS",
                    "prototype_terminal_last_close_max_sessions": 25,
                    "research_tier": "synthetic_formal_fixture",
                    "formal_eligible": True,
                    "formal_blockers": ["synthetic_fixture"],
                    "benchmark": {"kind": "total_return_index"},
                    "risk_free": {
                        "provided": True,
                        "source": "synthetic daily T-bill fixture",
                        "units": "decimal_return_per_exchange_session",
                    },
                    "request": {
                        "research_start": str(evaluation_start.date()),
                        "end": str(sessions[-1].date()),
                    },
                },
                referenced_files=files,
            )

            import momentum_reversal.data.storage as storage

            original_read = storage._read_parquet
            storage._read_parquet = lambda path: pd.read_csv(path)
            try:
                result = run_frozen_baselines(
                    BaselineRunConfig(
                        data_root=layout.root,
                        dataset_version="fixture-v1",
                        output_root=root / "results",
                        run_id="fixture-run",
                    )
                )
                experiment_config = root / "experiment-config"
                shutil.copytree(
                    Path(__file__).resolve().parents[1] / "config" / "experiments",
                    experiment_config,
                )
                program_path = experiment_config / "program.toml"
                program_text = program_path.read_text(encoding="utf-8")
                program_text = program_text.replace(
                    'evaluation_start_open = "2018-01-02"',
                    f'evaluation_start_open = "{evaluation_start.date()}"',
                ).replace(
                    'evaluation_end_close = "2026-06-30"',
                    f'evaluation_end_close = "{sessions[-1].date()}"',
                ).replace(
                    'sids = ["yf_ticker::COL"]',
                    'sids = ["S000"]',
                )
                program_path.write_text(program_text, encoding="utf-8")
                systematic_context = prepare_experiment_run(
                    experiment_config / "G00.toml",
                    run_id="g00-fixture-run",
                    dataset_version="fixture-v1",
                    data_root=layout.root,
                    output_root=root / "systematic-results",
                )
                g00_result = run_g00(
                    G00RunConfig(
                        context=systematic_context,
                        legacy_baseline_root=result.output_dir,
                    )
                )
            finally:
                storage._read_parquet = original_read
            self.assertEqual(result.path_count, 18)
            self.assertEqual(result.scenario_count, 72)
            self.assertFalse(result.formal_run_eligible)
            summary = pd.read_csv(result.output_dir / "all_results_summary.csv")
            self.assertEqual(len(summary), 72)
            self.assertEqual(summary["experiment_id"].nunique(), 18)
            self.assertEqual(
                len(list(result.output_dir.glob("**/benchmark_returns.csv"))), 72
            )
            self.assertEqual(
                len(list(result.output_dir.glob("**/risk_free_returns.csv"))), 72
            )
            import json

            run_manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertFalse(run_manifest["formal_run_eligible"])
            self.assertFalse(result.formal_run_eligible)
            self.assertTrue(run_manifest["dataset_declares_formal_eligible"])
            self.assertIn("synthetic_fixture", run_manifest["formal_blockers"])
            self.assertNotIn(
                "benchmark_is_not_total_return_index", run_manifest["formal_blockers"]
            )
            self.assertTrue(run_manifest["risk_free"]["t_bill_series_loaded"])
            self.assertEqual(
                run_manifest["risk_free"]["reported_sharpe_without_rf"],
                "sharpe_zero_rf",
            )
            self.assertEqual(
                run_manifest["risk_free"]["reported_sharpe_with_rf"],
                "sharpe_excess_rf",
            )
            self.assertIn("sharpe_zero_rf", summary.columns)
            self.assertIn("sharpe_excess_rf", summary.columns)
            self.assertTrue(summary["sharpe_excess_rf"].notna().any())
            self.assertNotIn("sharpe", summary.columns)

            self.assertEqual(g00_result.strategy_count, 36)
            self.assertEqual(g00_result.scenario_count, 288)
            self.assertEqual(g00_result.legacy_control_count, 72)
            self.assertFalse(g00_result.formal_run_eligible)
            systematic_summary = pd.read_csv(g00_result.output_dir / "summary.csv")
            self.assertEqual(len(systematic_summary), 288)
            self.assertEqual(
                systematic_summary["portfolio_mode"].value_counts().to_dict(),
                {"long_short": 216, "long_only": 72},
            )
            scenario_key = [
                "strategy_id",
                "variant_id",
                "cost_bps",
                "borrow_fee_annual",
            ]
            self.assertEqual(
                len(systematic_summary.drop_duplicates(scenario_key)), 288
            )
            self.assertEqual(int(systematic_summary["is_primary_scenario"].sum()), 36)
            self.assertIn("annualized_l1_turnover", systematic_summary.columns)
            self.assertTrue(systematic_summary["valid_scenario"].all())
            self.assertTrue(
                systematic_summary["invalid_reason"].fillna("").eq("").all()
            )

            nav_long = pd.read_parquet(
                g00_result.output_dir / "artifacts" / "nav.parquet"
            )
            self.assertEqual(len(nav_long.drop_duplicates(scenario_key)), 288)
            self.assertEqual(
                set(nav_long.groupby(scenario_key, dropna=False).size()),
                {len(sessions[sessions >= evaluation_start])},
            )
            long_short_nav = nav_long[nav_long["portfolio_mode"].eq("long_short")]
            self.assertTrue(long_short_nav["factor_excess_return"].notna().all())
            self.assertTrue(
                long_short_nav["derived_gross2_factor_return"].notna().all()
            )

            for artifact in ("rebalances", "holdings", "trades", "diagnostics"):
                frame = pd.read_parquet(
                    g00_result.output_dir / "artifacts" / f"{artifact}.parquet"
                )
                self.assertTrue(set(scenario_key).issubset(frame.columns))
                expected_scenarios = 288 if artifact == "diagnostics" else 36
                self.assertEqual(
                    len(frame.drop_duplicates(scenario_key)), expected_scenarios
                )
            diagnostics = pd.read_parquet(
                g00_result.output_dir / "artifacts" / "diagnostics.parquet"
            )
            self.assertIn(
                "derived_gross2_factor_sharpe",
                set(diagnostics["diagnostic"]),
            )
            self.assertNotIn("derived_gross2_cagr", systematic_summary.columns)

            comparison = pd.read_csv(g00_result.output_dir / "comparison.csv")
            self.assertEqual(len(comparison), 432)
            reproduction = comparison[
                comparison["comparison_type"].eq(
                    "engine_reproduction_legacy_zero_cash"
                )
            ]
            self.assertEqual(len(reproduction), 216)
            self.assertLessEqual(
                reproduction.loc[
                    reproduction["metric"].eq("max_abs_nav_diff"), "estimate"
                ].max(),
                1e-12,
            )
            g00_manifest = json.loads(
                g00_result.manifest_path.read_text(encoding="utf-8")
            )
            self.assertFalse(g00_manifest["formal_run_eligible"])
            self.assertEqual(g00_manifest["counts"]["main_scenarios"], 288)
            self.assertEqual(
                g00_manifest["execution"]["signed_missing_execution_policy"],
                "terminal_last_close",
            )
            self.assertEqual(
                g00_manifest["execution"]["terminal_last_close_max_sessions"],
                25,
            )
            self.assertEqual(
                g00_manifest["accounting"]["borrow_fee_daily_conversion"],
                "(1 + annual_rate) ** (1 / 252) - 1",
            )

            reused_context = prepare_experiment_run(
                experiment_config / "G00.toml",
                run_id="g00-fixture-run-v2",
                dataset_version="fixture-v1",
                data_root=layout.root,
                output_root=root / "systematic-results",
            )
            original_read = storage._read_parquet
            storage._read_parquet = lambda path: pd.read_csv(path)
            try:
                reused_result = run_g00(
                    G00RunConfig(
                        context=reused_context,
                        legacy_baseline_root=result.output_dir,
                        reuse_long_only_bundle=g00_result.output_dir,
                    )
                )
            finally:
                storage._read_parquet = original_read
            self.assertEqual(reused_result.scenario_count, 288)
            self.assertEqual(reused_result.reused_long_only_scenario_count, 72)
            self.assertEqual(
                reused_result.computed_long_short_scenario_count,
                216,
            )
            reused_summary = pd.read_csv(reused_result.output_dir / "summary.csv")
            self.assertEqual(len(reused_summary.drop_duplicates(scenario_key)), 288)
            self.assertEqual(
                reused_summary["portfolio_mode"].value_counts().to_dict(),
                {"long_short": 216, "long_only": 72},
            )
            self.assertTrue(reused_summary["valid_scenario"].all())
            self.assertTrue(reused_summary["invalid_reason"].fillna("").eq("").all())
            reused_manifest = json.loads(
                reused_result.manifest_path.read_text(encoding="utf-8")
            )
            reused_block = reused_manifest["reused_long_only_bundle"]
            self.assertEqual(reused_block["source_run_id"], g00_result.run_id)
            self.assertEqual(reused_block["summary_rows"], 72)
            self.assertEqual(
                reused_manifest["counts"]["computed_long_short_scenarios"],
                216,
            )

            tampered = root / "tampered-g00-v1"
            shutil.copytree(g00_result.output_dir, tampered)
            tampered_summary = tampered / "summary.csv"
            tampered_bytes = tampered_summary.read_bytes()
            comma = tampered_bytes.index(b",")
            tampered_summary.write_bytes(
                tampered_bytes[:comma] + b";" + tampered_bytes[comma + 1 :]
            )
            rejected_context = prepare_experiment_run(
                experiment_config / "G00.toml",
                run_id="g00-fixture-run-v2-tamper-check",
                dataset_version="fixture-v1",
                data_root=layout.root,
                output_root=root / "systematic-results",
            )
            original_read = storage._read_parquet
            storage._read_parquet = lambda path: pd.read_csv(path)
            try:
                with self.assertRaisesRegex(LongOnlyReuseError, "hash mismatch"):
                    run_g00(
                        G00RunConfig(
                            context=rejected_context,
                            legacy_baseline_root=result.output_dir,
                            reuse_long_only_bundle=tampered,
                        )
                    )
            finally:
                storage._read_parquet = original_read


if __name__ == "__main__":
    unittest.main()
