from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import shutil

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
from momentum_reversal.experiments import ExperimentCatalog, PortfolioMode
from momentum_reversal.pipelines import (
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
from momentum_reversal.pipelines.run_context import (
    _verify_dataset_files,
    _terminal_last_close_max_sessions,
    _validate_review_dataset_gates,
)
from momentum_reversal.data.storage import DatasetLayout, ManifestStore
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
            )
            with self.assertRaisesRegex(DataQualityError, "hash mismatch"):
                _verify_dataset_files(
                    layout,
                    {"files": [{"path": str(data_file), "sha256": "not-current"}]},
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
