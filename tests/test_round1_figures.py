from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIGURE_SCRIPT = REPOSITORY_ROOT / "scripts" / "build_round1_figures.py"


def _load_figure_module():
    spec = importlib.util.spec_from_file_location(
        "build_round1_figures_under_test", FIGURE_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import figure builder: {FIGURE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


figures = _load_figure_module()


class OptionalDependencyBoundaryTests(unittest.TestCase):
    def test_pure_helpers_import_without_visualization_extra(self) -> None:
        blocked = {
            "matplotlib": None,
            "matplotlib.pyplot": None,
            "pyarrow": None,
            "PIL": None,
        }
        with mock.patch.dict(sys.modules, blocked):
            module = _load_figure_module()

        self.assertTrue(callable(module.relative_wealth))
        self.assertTrue(callable(module.held_allocation))


class NavConstructionTests(unittest.TestCase):
    def test_spy_first_session_is_open_to_close_then_close_to_close(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=3)
        prices = pd.DataFrame(
            {
                "date": dates,
                "benchmark_tr_open": [100.0, 103.0, 105.0],
                "benchmark_tr_close": [102.0, 101.0, 106.0],
            }
        )

        result = figures.benchmark_nav(prices, dates)

        expected = pd.Series(
            [102.0 / 100.0, 101.0 / 100.0, 106.0 / 100.0],
            index=dates,
            name="SPY",
        )
        pd.testing.assert_series_equal(
            result, expected, check_names=False, check_freq=False
        )
        self.assertNotEqual(result.iloc[0], 1.0)

    def test_strategy_nav_preserves_raw_first_session_return(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=3)
        raw = pd.DataFrame(
            {
                "date": dates,
                "nav": [1.02, 1.01, 1.06],
            }
        )

        result = figures.strategy_nav(raw)

        expected = pd.Series([1.02, 1.01, 1.06], index=dates, name="nav")
        pd.testing.assert_series_equal(
            result, expected, check_names=False, check_freq=False
        )
        self.assertEqual(result.iloc[0], 1.02)

    def test_relative_wealth_is_matched_nav_ratio_not_nav_difference(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=3)
        strategy = pd.Series([1.02, 1.10, 1.08], index=dates)
        reference = pd.Series([1.01, 1.05, 1.10], index=dates)

        result = figures.relative_wealth(strategy, reference)

        expected = strategy / reference - 1.0
        pd.testing.assert_series_equal(result, expected, check_names=False)


class ExposureConstructionTests(unittest.TestCase):
    def test_held_allocation_updates_only_on_successful_execution(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=7)
        actions = pd.DataFrame(
            {
                "signal_date": [dates[0], dates[2], dates[4]],
                "execution_date": [dates[1], dates[3], dates[5]],
                "execution_status": [
                    "executed",
                    "skipped_signed_missing_open",
                    "executed",
                ],
                "target_risk_allocation": [0.60, 0.20, 0.80],
            }
        )

        result = figures.held_allocation(actions, dates)

        expected = pd.Series(
            [1.0, 0.60, 0.60, 0.60, 0.60, 0.80, 0.80],
            index=dates,
            name="held_allocation",
        )
        pd.testing.assert_series_equal(result, expected)
        self.assertEqual(result.loc[dates[0]], 1.0, "signal date must not look ahead")
        self.assertEqual(result.loc[dates[3]], 0.60, "skips must carry prior allocation")

    def test_gross_exposure_uses_absolute_short_leg(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=2)
        long_only = pd.DataFrame(
            {
                "date": dates,
                "long_exposure": [0.80, 0.90],
                "short_exposure": [0.0, -0.0],
            }
        )
        long_short = pd.DataFrame(
            {
                "date": dates,
                "long_exposure": [0.55, 0.60],
                # Runtime artifacts store this as a positive absolute exposure.
                "short_exposure": [0.45, 0.40],
            }
        )

        lo_result = figures.gross_exposure(long_only, "long_only")
        ls_result = figures.gross_exposure(long_short, "long_short")

        pd.testing.assert_series_equal(
            lo_result,
            pd.Series([0.80, 0.90], index=dates, name="gross_exposure"),
            check_names=False,
            check_freq=False,
        )
        pd.testing.assert_series_equal(
            ls_result,
            pd.Series([1.00, 1.00], index=dates, name="gross_exposure"),
            check_names=False,
            check_freq=False,
        )


class CounterfactualControlTests(unittest.TestCase):
    def test_fixed_average_control_formula_and_index_are_exact(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=4)
        base = pd.Series([0.02, -0.01, 0.03, -0.02], index=dates)
        risk_free = pd.Series([0.001, 0.001, 0.001, 0.001], index=dates)
        allocations = pd.Series([0.40, 0.60, 0.80, 0.60], index=dates)
        average_allocation = allocations.mean()
        expected_returns = (
            average_allocation * base + (1.0 - average_allocation) * risk_free
        )

        result = figures.fixed_average_control(base, risk_free, allocations)

        expected = (1.0 + expected_returns).cumprod().rename("fixed_average")
        pd.testing.assert_series_equal(result, expected, check_names=False)
        self.assertEqual(result.index.tolist(), dates.tolist())
        self.assertAlmostEqual(result.iloc[0], 1.0 + expected_returns.iloc[0])

    def test_same_vol_control_uses_excess_vol_ratio_without_index_shift(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=4)
        base = pd.Series([0.02, -0.01, 0.03, -0.02], index=dates)
        risk_free = pd.Series([0.001, 0.001, 0.001, 0.001], index=dates)
        target = risk_free + 0.50 * (base - risk_free)
        expected_returns = 0.50 * base + 0.50 * risk_free

        result = figures.same_vol_control(base, risk_free, target)

        expected = (1.0 + expected_returns).cumprod().rename("same_vol")
        pd.testing.assert_series_equal(result, expected, check_names=False)
        self.assertEqual(result.index.tolist(), dates.tolist())
        self.assertAlmostEqual(result.iloc[0], 1.0 + expected_returns.iloc[0])


class FrozenScenarioSelectionTests(unittest.TestCase):
    def test_g22_resolves_only_to_the_valid_v2_bundle(self) -> None:
        self.assertEqual(figures.select_run_id("G22"), "g22-frozen-v3-v2")

    def test_primary_selection_enforces_frequency_cost_and_borrow_contract(self) -> None:
        rows = [
            ("monthly_lo", "monthly", "long_only", 5.0, 0.00, True, True),
            ("weekly_lo", "weekly", "long_only", 10.0, 0.00, True, True),
            ("monthly_ls", "monthly", "long_short", 5.0, 0.01, True, True),
            ("weekly_ls", "weekly", "long_short", 10.0, 0.01, True, True),
            ("bad_monthly_cost", "monthly", "long_only", 10.0, 0.00, True, True),
            ("bad_weekly_cost", "weekly", "long_only", 5.0, 0.00, True, True),
            ("bad_ls_borrow", "monthly", "long_short", 5.0, 0.03, True, True),
            ("invalid", "monthly", "long_only", 5.0, 0.00, True, False),
            ("not_primary", "monthly", "long_only", 5.0, 0.00, False, True),
        ]
        summary = pd.DataFrame(
            rows,
            columns=[
                "strategy_id",
                "frequency",
                "portfolio_mode",
                "cost_bps",
                "borrow_fee_annual",
                "is_primary_scenario",
                "valid_scenario",
            ],
        )

        result = figures.select_primary(summary)

        self.assertEqual(
            set(result["strategy_id"]),
            {"monthly_lo", "weekly_lo", "monthly_ls", "weekly_ls"},
        )


class ManifestVerificationTests(unittest.TestCase):
    def test_manifest_hashes_are_verified_and_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            artifact_dir = run_dir / "artifacts"
            artifact_dir.mkdir()
            artifact = artifact_dir / "sample.bin"
            payload = b"frozen-round-one-artifact\n"
            artifact.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            manifest = {
                "schema_version": "momentum_reversal.experiment_manifest.v1",
                "run_id": "synthetic-run",
                "files": [
                    {
                        "path": "artifacts/sample.bin",
                        "bytes": len(payload),
                        "sha256": digest,
                    }
                ],
            }
            (run_dir / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            verified = figures.verify_manifest(run_dir)
            self.assertEqual(verified["run_id"], "synthetic-run")
            self.assertEqual(figures.sha256_file(artifact), digest)

            artifact.write_bytes(payload + b"tampered")
            with self.assertRaisesRegex(ValueError, "size|hash|sha256"):
                figures.verify_manifest(run_dir)


if __name__ == "__main__":
    unittest.main()
