from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from momentum_reversal.backtest import (
    BaselineBacktester,
    MissingExecutionPriceError,
    rebalance_schedule,
    run_cost_scenarios,
)
from momentum_reversal.experiments import (
    apply_linear_cost,
    baseline_specs,
    run_baseline_grid,
    v1_volatility_specs,
    v3_regime_specs,
    v4_forecast_specs,
)
from momentum_reversal.backtest import replay_linear_cost
from momentum_reversal.factors import compute_momentum_scores
from momentum_reversal.portfolio import winner_loser_weights

from tests.synthetic import SnapshotMembership, StaticMembership, make_prices


class BaselineBacktestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prices = make_prices(sessions=330, assets=60)
        self.dates = self.prices.index.get_level_values("date").unique()
        self.membership = StaticMembership(tuple(f"S{i:03d}" for i in range(60)))

    def _pending_action_result(self, *, signed: bool):
        first_targets = {"S000": 0.5, "S001": -0.5} if signed else {"S000": 1.0}

        def initial_targets(signal_date, scores, members):
            del signal_date, scores, members
            return pd.Series(first_targets, name="target_weight")

        probe = BaselineBacktester(self.prices, self.membership).run(
            signal="mom_255_0",
            top_n=1,
            frequency="monthly",
            target_weight_generator=initial_targets,
            full_audit=False,
        )
        second = probe.rebalances.iloc[1]
        second_signal = pd.Timestamp(second["signal_date"])
        second_execution = pd.Timestamp(second["execution_date"])

        def rotating_targets(signal_date, scores, members):
            del scores, members
            if signal_date < second_signal:
                return pd.Series(first_targets, name="target_weight")
            next_targets = (
                {"S002": 0.5, "S003": -0.5} if signed else {"S002": 1.0}
            )
            return pd.Series(next_targets, name="target_weight")

        apply_session = pd.Timestamp(
            self.dates[self.dates.get_loc(second_execution) + 5]
        )
        action = pd.DataFrame(
            [
                {
                    "action_id": "S000-CASH-LIQUIDATION",
                    "action_type": "cash_liquidation",
                    "legal_effective_date": apply_session,
                    "apply_session": apply_session,
                    "apply_phase": "pre_open",
                    "source_sid": "S000",
                    "target_sid": None,
                    "cash_per_source_share": 100.0,
                    "currency": "USD",
                    "target_shares_per_source_share": 0.0,
                    "fractional_treatment": "not_applicable",
                    "evidence_url": "https://example.test/liquidation",
                    "notes": "synthetic pending action",
                }
            ]
        )
        prices = self.prices.assign(
            raw_open=self.prices["tr_open"], raw_close=self.prices["tr_close"]
        )
        prices.loc[(second_execution, "S000"), "tr_open"] = np.nan
        result = BaselineBacktester(
            prices, self.membership, corporate_actions=action
        ).run(
            signal="mom_255_0",
            top_n=1,
            frequency="monthly",
            cost_bps=10.0,
            target_weight_generator=rotating_targets,
            signed_missing_execution_policy="terminal_last_close",
        )
        return result, second_execution, apply_session

    def test_registry_contains_exactly_eighteen_unique_paths(self) -> None:
        specs = baseline_specs()
        self.assertEqual(len(specs), 18)
        self.assertEqual(len({spec.experiment_id for spec in specs}), 18)

    def test_v1_registry_contains_eight_frozen_overlays(self) -> None:
        specs = v1_volatility_specs()
        self.assertEqual(len(specs), 8)
        self.assertEqual(len({spec.experiment_suffix for spec in specs}), 8)
        self.assertEqual(sum(spec.max_exposure == 1.5 for spec in specs), 2)

    def test_v3_registry_contains_six_frozen_regime_actions(self) -> None:
        specs = v3_regime_specs()
        self.assertEqual(len(specs), 6)
        self.assertEqual(len({spec.experiment_suffix for spec in specs}), 6)

    def test_v4_registry_contains_six_frozen_forecast_scalers(self) -> None:
        specs = v4_forecast_specs()
        self.assertEqual(len(specs), 6)
        self.assertEqual(len({spec.experiment_suffix for spec in specs}), 6)
        self.assertEqual(sum(spec.max_exposure == 1.5 for spec in specs), 3)

    def test_custom_cross_sectional_scores_change_only_selection(self) -> None:
        schedule = rebalance_schedule(self.dates, "monthly")
        raw = compute_momentum_scores(
            self.prices,
            schedule["signal_date"],
            "mom_255_0",
            sessions=self.dates,
        )
        engine = BaselineBacktester(self.prices, self.membership)
        naked = engine.run(signal="mom_255_0", top_n=10, frequency="monthly")
        custom = engine.run(
            signal="mom_255_0",
            top_n=10,
            frequency="monthly",
            selection_scores=-raw,
            selection_label="reversed_momentum_test",
        )
        first_naked = set(
            naked.target_weights.loc[
                naked.target_weights["execution_date"].eq(
                    naked.target_weights["execution_date"].min()
                ),
                "sid",
            ]
        )
        first_custom = set(
            custom.target_weights.loc[
                custom.target_weights["execution_date"].eq(
                    custom.target_weights["execution_date"].min()
                ),
                "sid",
            ]
        )
        self.assertNotEqual(first_naked, first_custom)
        self.assertTrue(custom.target_weights["target_weight"].eq(0.1).all())

    def test_holiday_shortened_week_uses_next_session(self) -> None:
        sessions = pd.DatetimeIndex(
            ["2024-03-25", "2024-03-26", "2024-03-27", "2024-03-28", "2024-04-01"]
        )
        schedule = rebalance_schedule(sessions, "weekly")
        self.assertEqual(schedule.iloc[0]["signal_date"], pd.Timestamp("2024-03-28"))
        self.assertEqual(schedule.iloc[0]["execution_date"], pd.Timestamp("2024-04-01"))

    def test_each_rebalance_restores_exact_equal_weights(self) -> None:
        result = BaselineBacktester(self.prices, self.membership).run(
            signal="mom_255_0", top_n=10, frequency="weekly", cost_bps=10
        )
        grouped = result.target_weights.groupby("execution_date")["target_weight"]
        self.assertTrue((grouped.size() == 10).all())
        self.assertTrue(np.allclose(result.target_weights["target_weight"], 0.1))
        later = result.trades[result.trades["execution_date"] > result.trades["execution_date"].min()]
        self.assertTrue((later["trade_weight"].abs() > 0).any())

    def test_cost_is_l1_and_higher_cost_never_improves_final_nav(self) -> None:
        engine = BaselineBacktester(self.prices, self.membership)
        scenarios = run_cost_scenarios(
            engine, signal="mom_255_0", top_n=10, frequency="weekly"
        )
        finals = [scenarios[cost].nav["nav"].iloc[-1] for cost in (0.0, 5.0, 10.0, 20.0)]
        self.assertEqual(finals, sorted(finals, reverse=True))
        first = scenarios[10.0].rebalances.iloc[0]
        self.assertAlmostEqual(first["l1_turnover"], 1.0)
        self.assertAlmostEqual(first["cost_amount"], 0.001)

    def test_pit_removal_waits_until_next_scheduled_open(self) -> None:
        members = tuple(f"S{i:03d}" for i in range(60))
        removal_date = pd.Timestamp(self.dates[270])
        fastest = "S059"
        reduced = tuple(sid for sid in members if sid != fastest)
        pit = SnapshotMembership({pd.Timestamp(self.dates[0]): members, removal_date: reduced})
        result = BaselineBacktester(self.prices, pit).run(
            signal="mom_255_0", top_n=10, frequency="weekly"
        )
        # Weekly equal-weight restoration may trim the winner before removal;
        # the PIT exit is the row whose target falls all the way to zero.
        sale = result.trades[
            (result.trades["sid"] == fastest)
            & (result.trades["trade_weight"] < 0)
            & (result.trades["target_weight"] == 0)
        ]
        self.assertEqual(len(sale), 1)
        sale_date = pd.Timestamp(sale.iloc[0]["execution_date"])
        self.assertGreater(sale_date, removal_date)
        relevant_signal = result.rebalances.loc[sale_date, "signal_date"]
        self.assertGreaterEqual(pd.Timestamp(relevant_signal), removal_date)

    def test_signal_bounds_do_not_discard_formation_history(self) -> None:
        signal_start = pd.Timestamp(self.dates[300])
        signal_end = pd.Timestamp(self.dates[320])
        result = BaselineBacktester(
            self.prices,
            self.membership,
            sessions=self.dates,
            signal_start=signal_start,
            signal_end=signal_end,
        ).run(signal="mom_255_0", top_n=10, frequency="weekly")
        self.assertGreaterEqual(result.rankings["signal_date"].min(), signal_start)
        self.assertLessEqual(result.rankings["signal_date"].max(), signal_end)
        # The first bounded weekly signal is immediately usable because closes
        # before signal_start remain available as formation history.
        expected_first = rebalance_schedule(self.dates, "weekly").query(
            "signal_date >= @signal_start and signal_date <= @signal_end"
        )["signal_date"].iloc[0]
        self.assertEqual(result.rankings["signal_date"].min(), expected_first)

    def test_common_evaluation_start_uses_preceding_close_signal(self) -> None:
        schedule = rebalance_schedule(self.dates, "monthly")
        chosen = schedule.iloc[-2]
        evaluation_start = pd.Timestamp(chosen["execution_date"])
        result = BaselineBacktester(
            self.prices,
            self.membership,
            sessions=self.dates,
            evaluation_start=evaluation_start,
            signal_end=self.dates[-1],
        ).run(signal="mom_255_0", top_n=10, frequency="monthly")
        self.assertEqual(result.nav.index[0], evaluation_start)
        self.assertEqual(
            pd.Timestamp(result.rebalances.iloc[0]["signal_date"]),
            pd.Timestamp(chosen["signal_date"]),
        )
        self.assertLess(
            pd.Timestamp(result.rebalances.iloc[0]["signal_date"]),
            evaluation_start,
        )

    def test_explicit_research_start_cannot_slide_past_incomplete_formation(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "first scheduled research signal.*formation history"
        ):
            BaselineBacktester(
                self.prices,
                self.membership,
                sessions=self.dates,
                signal_start=self.dates[10],
                signal_end=self.dates[-1],
            ).run(signal="mom_255_0", top_n=10, frequency="weekly")

    def test_missing_authoritative_exchange_session_is_fatal(self) -> None:
        missing_date = self.dates[275]
        gapped = self.prices.drop(index=missing_date, level="date")
        with self.assertRaisesRegex(ValueError, "have no price rows"):
            BaselineBacktester(
                gapped,
                self.membership,
                sessions=self.dates,
                signal_start=self.dates[300],
            )

    def test_prototype_can_carry_last_close_for_an_existing_position(self) -> None:
        first = BaselineBacktester(self.prices, self.membership).run(
            signal="mom_255_0", top_n=10, frequency="weekly"
        )
        execution = pd.Timestamp(first.rebalances.iloc[0]["execution_date"])
        missing_date = self.dates[self.dates.get_loc(execution) + 1]
        held_sid = str(first.target_weights.iloc[0]["sid"])
        gapped = self.prices.copy()
        gapped.loc[(missing_date, held_sid), "tr_close"] = np.nan

        with self.assertRaisesRegex(ValueError, "tr_close missing/invalid"):
            BaselineBacktester(gapped, self.membership).run(
                signal="mom_255_0", top_n=10, frequency="weekly"
            )
        result = BaselineBacktester(
            gapped,
            self.membership,
            missing_valuation_policy="carry_last_close",
        ).run(signal="mom_255_0", top_n=10, frequency="weekly")
        self.assertEqual(len(result.valuation_fallbacks), 1)
        self.assertEqual(result.valuation_fallbacks.iloc[0]["sid"], held_sid)

    def test_prototype_leaves_unfilled_open_allocation_in_cash(self) -> None:
        first = BaselineBacktester(self.prices, self.membership).run(
            signal="mom_255_0", top_n=10, frequency="weekly"
        )
        execution = pd.Timestamp(first.rebalances.iloc[0]["execution_date"])
        selected_sid = str(
            first.target_weights.loc[
                first.target_weights["execution_date"].eq(execution), "sid"
            ].iloc[0]
        )
        gapped = self.prices.copy()
        gapped.loc[(execution, selected_sid), "tr_open"] = np.nan
        result = BaselineBacktester(
            gapped,
            self.membership,
            missing_execution_policy="leave_cash",
        ).run(signal="mom_255_0", top_n=10, frequency="weekly")
        rebalance = result.rebalances.iloc[0]
        self.assertEqual(rebalance["requested_selected_count"], 10)
        self.assertEqual(rebalance["selected_count"], 9)
        self.assertEqual(rebalance["unfilled_selected_count"], 1)
        self.assertNotIn(
            selected_sid,
            set(result.target_weights.loc[result.target_weights["execution_date"].eq(execution), "sid"]),
        )

    def test_zero_risky_allocation_compounds_daily_risk_free(self) -> None:
        schedule = rebalance_schedule(self.dates, "weekly")
        allocation = pd.Series(0.0, index=schedule["signal_date"])
        risk_free = pd.Series(0.0001, index=self.dates)
        result = BaselineBacktester(self.prices, self.membership).run(
            signal="mom_255_0",
            top_n=10,
            frequency="weekly",
            risk_allocation=allocation,
            risk_free_daily=risk_free,
            full_audit=False,
        )
        expected = (1.0001) ** len(result.nav)
        self.assertAlmostEqual(result.nav["nav"].iloc[-1], expected, places=10)
        self.assertTrue(result.rebalances["target_risk_allocation"].eq(0.0).all())
        self.assertTrue(result.nav["risky_value"].eq(0.0).all())
        self.assertTrue(result.rankings.empty)

    def test_leveraged_target_creates_negative_financing_cash(self) -> None:
        schedule = rebalance_schedule(self.dates, "monthly")
        allocation = pd.Series(1.5, index=schedule["signal_date"])
        risk_free = pd.Series(0.0001, index=self.dates)
        result = BaselineBacktester(self.prices, self.membership).run(
            signal="mom_255_0",
            top_n=20,
            frequency="monthly",
            risk_allocation=allocation,
            risk_free_daily=risk_free,
            full_audit=False,
        )
        first = result.rebalances.iloc[0]
        self.assertAlmostEqual(first["target_risk_allocation"], 1.5)
        self.assertAlmostEqual(first["target_cash_weight"], -0.5)
        self.assertLess(result.nav["cash_value"].iloc[0], 0.0)

    def test_winner_loser_helper_is_deterministic_gross_one_and_net_zero(self) -> None:
        scores = pd.Series(
            [3.0, 3.0, 2.0, 1.0, 0.0, 0.0],
            index=pd.Index(["B", "A", "C", "D", "F", "E"], name="sid"),
        )
        weights = winner_loser_weights(
            scores, tuple(scores.index), 2, gross_exposure=1.0
        )
        self.assertAlmostEqual(float(weights.abs().sum()), 1.0)
        self.assertAlmostEqual(float(weights.sum()), 0.0)
        self.assertEqual(set(weights.loc[weights.gt(0)].index), {"A", "B"})
        self.assertEqual(set(weights.loc[weights.lt(0)].index), {"E", "F"})
        self.assertTrue(weights.loc[weights.gt(0)].eq(0.25).all())
        self.assertTrue(weights.loc[weights.lt(0)].eq(-0.25).all())

        tied = pd.Series(
            1.0,
            index=pd.Index(["D", "C", "B", "A"], name="sid"),
        )
        tied_weights = winner_loser_weights(tied, tuple(tied.index), 2)
        self.assertEqual(set(tied_weights.loc[tied_weights.gt(0)].index), {"A", "B"})
        self.assertEqual(set(tied_weights.loc[tied_weights.lt(0)].index), {"C", "D"})
        self.assertAlmostEqual(float(tied_weights.abs().sum()), 1.0)
        self.assertAlmostEqual(float(tied_weights.sum()), 0.0)

    def test_signed_generator_records_wml_exposures_turnover_and_collateral(self) -> None:
        risk_free = pd.Series(0.0001, index=self.dates)

        def wml_generator(
            signal_date: pd.Timestamp,
            scores: pd.Series,
            members: tuple[str, ...],
        ) -> pd.Series:
            del signal_date
            return winner_loser_weights(scores, members, 10, gross_exposure=1.0)

        result = BaselineBacktester(self.prices, self.membership).run(
            signal="mom_255_0",
            top_n=10,
            frequency="monthly",
            target_weight_generator=wml_generator,
            risk_free_daily=risk_free,
            cost_bps=10.0,
        )
        first = result.rebalances.iloc[0]
        self.assertAlmostEqual(first["target_long_exposure"], 0.5)
        self.assertAlmostEqual(first["target_short_exposure"], 0.5)
        self.assertAlmostEqual(first["target_gross_exposure"], 1.0)
        self.assertAlmostEqual(first["target_net_exposure"], 0.0)
        self.assertAlmostEqual(first["target_cash_weight"], 1.0)
        self.assertAlmostEqual(first["l1_turnover"], 1.0)
        self.assertAlmostEqual(first["cost_amount"], 0.001)
        first_targets = result.target_weights.loc[
            result.target_weights["execution_date"].eq(
                result.target_weights["execution_date"].min()
            ),
            "target_weight",
        ]
        self.assertEqual(int(first_targets.gt(0.0).sum()), 10)
        self.assertEqual(int(first_targets.lt(0.0).sum()), 10)
        self.assertAlmostEqual(float(first_targets.abs().sum()), 1.0)
        self.assertAlmostEqual(float(first_targets.sum()), 0.0)
        self.assertTrue(result.nav["short_value"].gt(0.0).all())
        self.assertTrue(result.nav["short_exposure"].gt(0.0).all())

    def test_signed_generator_targets_are_cached_across_cost_runs_without_nav_change(self) -> None:
        generator_calls = 0

        def counted_wml(
            signal_date: pd.Timestamp,
            scores: pd.Series,
            members: tuple[str, ...],
        ) -> pd.Series:
            nonlocal generator_calls
            del signal_date
            generator_calls += 1
            return winner_loser_weights(scores, members, 10, gross_exposure=1.0)

        cached_engine = BaselineBacktester(self.prices, self.membership)
        cached_zero = cached_engine.run(
            signal="mom_255_0",
            top_n=10,
            frequency="monthly",
            cost_bps=0.0,
            target_weight_generator=counted_wml,
            target_weight_cache_key="G00.mom255.top10.monthly.long_short",
            full_audit=False,
        )
        calls_after_first_schedule = generator_calls
        self.assertGreater(calls_after_first_schedule, 0)
        cached_ten = cached_engine.run(
            signal="mom_255_0",
            top_n=10,
            frequency="monthly",
            cost_bps=10.0,
            target_weight_generator=counted_wml,
            target_weight_cache_key="G00.mom255.top10.monthly.long_short",
            full_audit=False,
        )
        self.assertEqual(generator_calls, calls_after_first_schedule)

        def uncached_wml(
            signal_date: pd.Timestamp,
            scores: pd.Series,
            members: tuple[str, ...],
        ) -> pd.Series:
            del signal_date
            return winner_loser_weights(scores, members, 10, gross_exposure=1.0)

        uncached_engine = BaselineBacktester(self.prices, self.membership)
        uncached_zero = uncached_engine.run(
            signal="mom_255_0",
            top_n=10,
            frequency="monthly",
            cost_bps=0.0,
            target_weight_generator=uncached_wml,
            full_audit=False,
        )
        uncached_ten = uncached_engine.run(
            signal="mom_255_0",
            top_n=10,
            frequency="monthly",
            cost_bps=10.0,
            target_weight_generator=uncached_wml,
            full_audit=False,
        )
        np.testing.assert_allclose(
            cached_zero.nav["nav"].to_numpy(),
            uncached_zero.nav["nav"].to_numpy(),
            rtol=0.0,
            atol=0.0,
        )
        np.testing.assert_allclose(
            cached_ten.nav["nav"].to_numpy(),
            uncached_ten.nav["nav"].to_numpy(),
            rtol=0.0,
            atol=0.0,
        )

    def test_signed_wml_fails_closed_when_any_execution_open_is_missing(self) -> None:
        def wml_generator(
            signal_date: pd.Timestamp,
            scores: pd.Series,
            members: tuple[str, ...],
        ) -> pd.Series:
            del signal_date
            return winner_loser_weights(scores, members, 10, gross_exposure=1.0)

        complete = BaselineBacktester(self.prices, self.membership).run(
            signal="mom_255_0",
            top_n=10,
            frequency="monthly",
            target_weight_generator=wml_generator,
            full_audit=False,
        )
        first_execution = pd.Timestamp(complete.rebalances.iloc[0]["execution_date"])
        target_sid = str(
            complete.rebalances.iloc[0]["requested_selected_sids"].split("|")[0]
        )
        gapped = self.prices.copy()
        gapped.loc[(first_execution, target_sid), "tr_open"] = np.nan
        with self.assertRaisesRegex(
            MissingExecutionPriceError, "refusing to break portfolio neutrality"
        ):
            BaselineBacktester(
                gapped,
                self.membership,
                missing_execution_policy="leave_cash",
            ).run(
                signal="mom_255_0",
                top_n=10,
                frequency="monthly",
                target_weight_generator=wml_generator,
                full_audit=False,
            )

    def test_custom_selection_scores_and_rankings_are_explicitly_cached(self) -> None:
        schedule = rebalance_schedule(self.dates, "monthly")
        scores = compute_momentum_scores(
            self.prices,
            schedule["signal_date"],
            "mom_255_0",
            sessions=self.dates,
        )
        cached_engine = BaselineBacktester(self.prices, self.membership)
        cached_zero = cached_engine.run(
            signal="mom_255_0",
            top_n=10,
            frequency="monthly",
            cost_bps=0.0,
            selection_scores=scores,
            selection_label="fixture_custom",
            selection_score_cache_key="fixture_custom_scores",
            full_audit=False,
        )
        cached_ten = cached_engine.run(
            signal="mom_255_0",
            top_n=10,
            frequency="monthly",
            cost_bps=10.0,
            selection_scores=scores,
            selection_label="fixture_custom",
            selection_score_cache_key="fixture_custom_scores",
            full_audit=False,
        )
        uncached_engine = BaselineBacktester(self.prices, self.membership)
        uncached_zero = uncached_engine.run(
            signal="mom_255_0",
            top_n=10,
            frequency="monthly",
            cost_bps=0.0,
            selection_scores=scores,
            selection_label="fixture_custom",
            full_audit=False,
        )
        uncached_ten = uncached_engine.run(
            signal="mom_255_0",
            top_n=10,
            frequency="monthly",
            cost_bps=10.0,
            selection_scores=scores,
            selection_label="fixture_custom",
            full_audit=False,
        )
        np.testing.assert_allclose(cached_zero.nav, uncached_zero.nav, rtol=0.0, atol=0.0)
        np.testing.assert_allclose(cached_ten.nav, uncached_ten.nav, rtol=0.0, atol=0.0)
        with self.assertRaisesRegex(ValueError, "different selection_scores object"):
            cached_engine.run(
                signal="mom_255_0",
                top_n=10,
                frequency="monthly",
                selection_scores=scores.copy(),
                selection_label="fixture_custom",
                selection_score_cache_key="fixture_custom_scores",
                full_audit=False,
            )

    def test_signed_skip_policy_keeps_initial_cash_and_audits_event(self) -> None:
        def fixed_targets(
            signal_date: pd.Timestamp,
            scores: pd.Series,
            members: tuple[str, ...],
        ) -> pd.Series:
            del signal_date, scores, members
            return pd.Series({"S000": 0.5, "S001": -0.5}, name="target_weight")

        probe = BaselineBacktester(self.prices, self.membership).run(
            signal="mom_255_0",
            top_n=1,
            frequency="monthly",
            target_weight_generator=fixed_targets,
            full_audit=False,
        )
        first_execution = pd.Timestamp(probe.rebalances.iloc[0]["execution_date"])
        gapped = self.prices.copy()
        gapped.loc[(first_execution, "S000"), "tr_open"] = np.nan
        result = BaselineBacktester(gapped, self.membership).run(
            signal="mom_255_0",
            top_n=1,
            frequency="monthly",
            target_weight_generator=fixed_targets,
            signed_missing_execution_policy="skip_rebalance",
        )
        first = result.rebalances.iloc[0]
        self.assertEqual(first["execution_status"], "skipped_signed_missing_open")
        self.assertEqual(first["selected_count"], 0)
        self.assertEqual(first["unfilled_selected_count"], 1)
        self.assertEqual(first["unfilled_selected_sids"], "S000")
        self.assertAlmostEqual(first["requested_long_exposure"], 0.5)
        self.assertAlmostEqual(first["requested_short_exposure"], 0.5)
        self.assertAlmostEqual(first["target_long_exposure"], 0.0)
        self.assertAlmostEqual(first["target_short_exposure"], 0.0)
        self.assertAlmostEqual(first["target_cash_weight"], 1.0)
        self.assertAlmostEqual(first["l1_turnover"], 0.0)
        self.assertAlmostEqual(first["cost_amount"], 0.0)
        self.assertAlmostEqual(result.nav.iloc[0]["nav"], 1.0)
        self.assertEqual(
            len(result.trades.loc[result.trades["execution_date"].eq(first_execution)]),
            0,
        )
        self.assertEqual(result.summary()["skipped_signed_rebalance_count"], 1)

    def test_signed_skip_preserves_book_and_strict_detects_missing_exit_open(self) -> None:
        def initial_targets(
            signal_date: pd.Timestamp,
            scores: pd.Series,
            members: tuple[str, ...],
        ) -> pd.Series:
            del signal_date, scores, members
            return pd.Series({"S000": 0.5, "S001": -0.5}, name="target_weight")

        probe = BaselineBacktester(self.prices, self.membership).run(
            signal="mom_255_0",
            top_n=1,
            frequency="monthly",
            target_weight_generator=initial_targets,
            full_audit=False,
        )
        first = probe.rebalances.iloc[0]
        second = probe.rebalances.iloc[1]
        second_signal = pd.Timestamp(second["signal_date"])
        first_execution = pd.Timestamp(first["execution_date"])
        second_execution = pd.Timestamp(second["execution_date"])

        def rotating_targets(
            signal_date: pd.Timestamp,
            scores: pd.Series,
            members: tuple[str, ...],
        ) -> pd.Series:
            del scores, members
            if signal_date < second_signal:
                return pd.Series(
                    {"S000": 0.5, "S001": -0.5}, name="target_weight"
                )
            return pd.Series(
                {"S002": 0.5, "S003": -0.5}, name="target_weight"
            )

        gapped = self.prices.copy()
        # S000 is an existing long that the second target wants to exit; all
        # requested S002/S003 target opens remain valid.
        gapped.loc[(second_execution, "S000"), "tr_open"] = np.nan
        with self.assertRaisesRegex(
            MissingExecutionPriceError, "target or existing-position opens"
        ):
            BaselineBacktester(gapped, self.membership).run(
                signal="mom_255_0",
                top_n=1,
                frequency="monthly",
                target_weight_generator=rotating_targets,
            )

        result = BaselineBacktester(gapped, self.membership).run(
            signal="mom_255_0",
            top_n=1,
            frequency="monthly",
            target_weight_generator=rotating_targets,
            signed_missing_execution_policy="skip_rebalance",
        )
        skipped = result.rebalances.loc[second_execution]
        self.assertEqual(
            skipped["execution_status"], "skipped_signed_missing_open"
        )
        self.assertEqual(skipped["unfilled_selected_sids"], "S000")
        self.assertAlmostEqual(skipped["l1_turnover"], 0.0)
        self.assertAlmostEqual(skipped["cost_amount"], 0.0)
        self.assertGreater(skipped["target_long_exposure"], 0.0)
        self.assertGreater(skipped["target_short_exposure"], 0.0)
        self.assertEqual(
            len(result.trades.loc[result.trades["execution_date"].eq(second_execution)]),
            0,
        )
        self.assertEqual(
            len(
                result.target_weights.loc[
                    result.target_weights["execution_date"].eq(second_execution)
                ]
            ),
            0,
        )

        first_open_long = float(gapped.loc[(first_execution, "S000"), "tr_open"])
        first_open_short = float(gapped.loc[(first_execution, "S001"), "tr_open"])
        long_units = 0.5 / first_open_long
        short_units = -0.5 / first_open_short
        expected_long_value = long_units * float(
            gapped.loc[(second_execution, "S000"), "tr_close"]
        )
        expected_short_value = -short_units * float(
            gapped.loc[(second_execution, "S001"), "tr_close"]
        )
        self.assertAlmostEqual(
            result.nav.loc[second_execution, "long_value"], expected_long_value
        )
        self.assertAlmostEqual(
            result.nav.loc[second_execution, "short_value"], expected_short_value
        )

    def test_terminal_last_close_exits_nonmember_and_charges_full_l1_cost(self) -> None:
        def initial_targets(
            signal_date: pd.Timestamp,
            scores: pd.Series,
            members: tuple[str, ...],
        ) -> pd.Series:
            del signal_date, scores, members
            return pd.Series({"S000": 0.5, "S001": -0.5}, name="target_weight")

        probe = BaselineBacktester(self.prices, self.membership).run(
            signal="mom_255_0",
            top_n=1,
            frequency="monthly",
            target_weight_generator=initial_targets,
            full_audit=False,
        )
        second_signal = pd.Timestamp(probe.rebalances.iloc[1]["signal_date"])
        second_execution = pd.Timestamp(
            probe.rebalances.iloc[1]["execution_date"]
        )

        def rotating_targets(
            signal_date: pd.Timestamp,
            scores: pd.Series,
            members: tuple[str, ...],
        ) -> pd.Series:
            del scores, members
            targets = (
                {"S000": 0.5, "S001": -0.5}
                if signal_date < second_signal
                else {"S002": 0.5, "S003": -0.5}
            )
            return pd.Series(targets, name="target_weight")

        all_members = tuple(f"S{i:03d}" for i in range(60))
        without_terminal = tuple(sid for sid in all_members if sid != "S000")
        pit = SnapshotMembership(
            {
                pd.Timestamp(self.dates[0]): all_members,
                second_signal: without_terminal,
            }
        )
        gapped = self.prices.copy()
        gapped.loc[(second_execution, "S000"), "tr_open"] = np.nan
        result = BaselineBacktester(gapped, pit).run(
            signal="mom_255_0",
            top_n=1,
            frequency="monthly",
            target_weight_generator=rotating_targets,
            signed_missing_execution_policy="terminal_last_close",
            cost_bps=10.0,
        )
        terminal = result.rebalances.loc[second_execution]
        self.assertEqual(
            terminal["execution_status"],
            "executed_with_terminal_last_close",
        )
        self.assertEqual(terminal["terminal_liquidation_count"], 1)
        self.assertEqual(terminal["terminal_liquidation_sids"], "S000")
        self.assertEqual(terminal["missing_target_count"], 0)
        self.assertEqual(terminal["missing_existing_count"], 1)
        self.assertEqual(terminal["missing_existing_sids"], "S000")
        expected_fallback_date = pd.Timestamp(
            self.dates[self.dates.get_loc(second_execution) - 1]
        )
        self.assertEqual(
            terminal["terminal_liquidation_fallback_dates"],
            expected_fallback_date.date().isoformat(),
        )
        self.assertAlmostEqual(terminal["target_gross_exposure"], 1.0)
        self.assertAlmostEqual(terminal["target_net_exposure"], 0.0)
        terminal_trade = result.trades.loc[
            result.trades["execution_date"].eq(second_execution)
            & result.trades["sid"].eq("S000")
        ].iloc[0]
        self.assertAlmostEqual(terminal_trade["target_weight"], 0.0)
        self.assertAlmostEqual(
            abs(terminal_trade["trade_weight"]),
            abs(terminal_trade["pretrade_weight"]),
        )
        self.assertAlmostEqual(
            terminal["cost_amount"],
            terminal["pretrade_nav"] * 0.001 * terminal["l1_turnover"],
        )
        self.assertGreater(terminal["cost_amount"], 0.0)
        fallback = result.valuation_fallbacks.loc[
            result.valuation_fallbacks["date"].eq(second_execution)
            & result.valuation_fallbacks["sid"].eq("S000")
        ].iloc[0]
        self.assertEqual(
            fallback["requested_column"], "tr_open_terminal_liquidation"
        )
        self.assertEqual(pd.Timestamp(fallback["fallback_date"]), expected_fallback_date)

    def test_terminal_last_close_still_skips_member_and_target_open_failures(self) -> None:
        def initial_targets(
            signal_date: pd.Timestamp,
            scores: pd.Series,
            members: tuple[str, ...],
        ) -> pd.Series:
            del signal_date, scores, members
            return pd.Series({"S000": 0.5, "S001": -0.5}, name="target_weight")

        probe = BaselineBacktester(self.prices, self.membership).run(
            signal="mom_255_0",
            top_n=1,
            frequency="monthly",
            target_weight_generator=initial_targets,
            full_audit=False,
        )
        second_signal = pd.Timestamp(probe.rebalances.iloc[1]["signal_date"])
        second_execution = pd.Timestamp(
            probe.rebalances.iloc[1]["execution_date"]
        )

        def rotating_targets(
            signal_date: pd.Timestamp,
            scores: pd.Series,
            members: tuple[str, ...],
        ) -> pd.Series:
            del scores, members
            targets = (
                {"S000": 0.5, "S001": -0.5}
                if signal_date < second_signal
                else {"S002": 0.5, "S003": -0.5}
            )
            return pd.Series(targets, name="target_weight")

        for missing_sid in ("S000", "S002"):
            with self.subTest(missing_sid=missing_sid):
                gapped = self.prices.copy()
                gapped.loc[(second_execution, missing_sid), "tr_open"] = np.nan
                result = BaselineBacktester(gapped, self.membership).run(
                    signal="mom_255_0",
                    top_n=1,
                    frequency="monthly",
                    target_weight_generator=rotating_targets,
                    signed_missing_execution_policy="terminal_last_close",
                )
                skipped = result.rebalances.loc[second_execution]
                self.assertEqual(
                    skipped["execution_status"],
                    "skipped_signed_missing_open",
                )
                self.assertAlmostEqual(skipped["l1_turnover"], 0.0)
                self.assertEqual(skipped["terminal_liquidation_count"], 0)
                self.assertIn(missing_sid, skipped["unfilled_selected_sids"])
                if missing_sid == "S002":
                    self.assertEqual(skipped["missing_target_sids"], "S002")
                    self.assertEqual(skipped["missing_existing_count"], 0)
                else:
                    self.assertEqual(skipped["missing_existing_sids"], "S000")
                    self.assertEqual(skipped["missing_target_count"], 0)

    def test_terminal_last_close_uses_configured_session_limit(self) -> None:
        def initial_targets(
            signal_date: pd.Timestamp,
            scores: pd.Series,
            members: tuple[str, ...],
        ) -> pd.Series:
            del signal_date, scores, members
            return pd.Series({"S000": 0.5, "S001": -0.5}, name="target_weight")

        probe = BaselineBacktester(self.prices, self.membership).run(
            signal="mom_255_0",
            top_n=1,
            frequency="weekly",
            target_weight_generator=initial_targets,
            full_audit=False,
        )
        terminal_rebalance = probe.rebalances.iloc[7]
        terminal_signal = pd.Timestamp(terminal_rebalance["signal_date"])
        terminal_execution = pd.Timestamp(terminal_rebalance["execution_date"])

        def rotating_targets(
            signal_date: pd.Timestamp,
            scores: pd.Series,
            members: tuple[str, ...],
        ) -> pd.Series:
            del scores, members
            targets = (
                {"S000": 0.5, "S001": -0.5}
                if signal_date < terminal_signal
                else {"S002": 0.5, "S003": -0.5}
            )
            return pd.Series(targets, name="target_weight")

        all_members = tuple(f"S{i:03d}" for i in range(60))
        without_terminal = tuple(sid for sid in all_members if sid != "S000")
        pit = SnapshotMembership(
            {
                pd.Timestamp(self.dates[0]): all_members,
                terminal_signal: without_terminal,
            }
        )
        execution_location = self.dates.get_loc(terminal_execution)
        last_valid_location = execution_location - 6
        stale_sessions = self.dates[
            last_valid_location + 1 : execution_location + 1
        ]
        gapped = self.prices.copy()
        gapped.loc[(stale_sessions, "S000"), "tr_close"] = np.nan
        gapped.loc[(terminal_execution, "S000"), "tr_open"] = np.nan
        default_result = BaselineBacktester(
            gapped,
            pit,
            missing_valuation_policy="carry_last_close",
        ).run(
            signal="mom_255_0",
            top_n=1,
            frequency="weekly",
            target_weight_generator=rotating_targets,
            signed_missing_execution_policy="terminal_last_close",
        )
        self.assertEqual(
            default_result.rebalances.loc[terminal_execution, "execution_status"],
            "executed_with_terminal_last_close",
        )
        with self.assertRaisesRegex(
            MissingExecutionPriceError, "6 authoritative sessions old"
        ):
            BaselineBacktester(
                gapped,
                pit,
                missing_valuation_policy="carry_last_close",
            ).run(
                signal="mom_255_0",
                top_n=1,
                frequency="weekly",
                target_weight_generator=rotating_targets,
                signed_missing_execution_policy="terminal_last_close",
                terminal_last_close_max_sessions=5,
            )

    def test_pending_action_skips_signed_rebalance_and_preserves_book(self) -> None:
        result, execution, apply_session = self._pending_action_result(signed=True)
        skipped = result.rebalances.loc[execution]
        self.assertEqual(
            skipped["execution_status"], "skipped_pending_corporate_action"
        )
        self.assertEqual(skipped["unfilled_selected_sids"], "S000")
        self.assertAlmostEqual(skipped["l1_turnover"], 0.0)
        self.assertAlmostEqual(
            skipped["target_gross_exposure"], skipped["pretrade_gross_exposure"]
        )
        self.assertFalse(result.trades["execution_date"].eq(execution).any())
        applied = result.corporate_action_events.loc[
            result.corporate_action_events["apply_session"].eq(apply_session)
        ]
        self.assertEqual(applied["status"].tolist(), ["applied"])

    def test_pending_action_skips_long_only_rebalance_and_preserves_book(self) -> None:
        result, execution, apply_session = self._pending_action_result(signed=False)
        skipped = result.rebalances.loc[execution]
        self.assertEqual(
            skipped["execution_status"], "skipped_pending_corporate_action"
        )
        self.assertEqual(skipped["unfilled_selected_sids"], "S000")
        self.assertAlmostEqual(skipped["l1_turnover"], 0.0)
        self.assertAlmostEqual(
            skipped["target_long_exposure"], skipped["pretrade_long_exposure"]
        )
        self.assertFalse(result.trades["execution_date"].eq(execution).any())
        applied = result.corporate_action_events.loc[
            result.corporate_action_events["apply_session"].eq(apply_session)
        ]
        self.assertEqual(applied["status"].tolist(), ["applied"])

    def test_explicit_signed_targets_match_generator_and_borrow_fee_is_charged(self) -> None:
        schedule = rebalance_schedule(self.dates, "monthly")
        scores = compute_momentum_scores(
            self.prices,
            schedule["signal_date"],
            "mom_255_0",
            sessions=self.dates,
        )
        target_pieces: list[pd.Series] = []
        for signal_date in schedule["signal_date"]:
            date_scores = scores.xs(signal_date, level="signal_date")
            if int(np.isfinite(date_scores.to_numpy(dtype=float)).sum()) < 20:
                continue
            weights = winner_loser_weights(
                date_scores, self.membership.members, 10, gross_exposure=1.0
            )
            weights.index = pd.MultiIndex.from_product(
                [[pd.Timestamp(signal_date)], weights.index],
                names=["signal_date", "sid"],
            )
            target_pieces.append(weights)
        explicit_targets = pd.concat(target_pieces).sort_index()

        engine = BaselineBacktester(self.prices, self.membership)
        direct = engine.run(
            signal="mom_255_0",
            top_n=10,
            frequency="monthly",
            target_weights=explicit_targets,
            short_borrow_fee_daily=0.0,
            full_audit=False,
        )
        charged = engine.run(
            signal="mom_255_0",
            top_n=10,
            frequency="monthly",
            target_weights=explicit_targets,
            short_borrow_fee_daily=0.0001,
            full_audit=False,
        )
        self.assertGreater(charged.nav["short_borrow_fee_amount"].sum(), 0.0)
        self.assertLess(charged.nav["nav"].iloc[-1], direct.nav["nav"].iloc[-1])
        self.assertTrue(direct.rebalances["target_net_exposure"].abs().lt(1e-12).all())

    def test_terminal_corporate_action_converts_negative_units_symmetrically(self) -> None:
        prices = self.prices.copy()
        prices["raw_open"] = prices["tr_open"]
        prices["raw_close"] = prices["tr_close"]
        apply_session = pd.Timestamp(self.dates[300])
        action = pd.DataFrame(
            [
                {
                    "action_id": "SHORT-MIXED-1",
                    "action_type": "cash_and_stock_merger",
                    "legal_effective_date": apply_session,
                    "apply_session": apply_session,
                    "apply_phase": "pre_open",
                    "source_sid": "S000",
                    "target_sid": "S001",
                    "cash_per_source_share": 10.0,
                    "currency": "USD",
                    "target_shares_per_source_share": 2.0,
                    "fractional_treatment": "fractional_shares",
                    "evidence_url": "https://example.test/action",
                    "notes": "synthetic short conversion",
                }
            ]
        )
        engine = BaselineBacktester(
            prices, self.membership, corporate_actions=action
        )
        updated, cash, audit = engine._apply_corporate_actions(
            date=apply_session,
            shares=pd.Series({"S000": -0.01}, dtype=float),
            cash=1.0,
        )
        self.assertNotIn("S000", updated.index)
        self.assertAlmostEqual(float(updated.loc["S001"]), -0.02)
        self.assertAlmostEqual(cash, 0.9)
        self.assertAlmostEqual(float(audit[0]["source_actual_shares"]), -0.01)
        self.assertAlmostEqual(float(audit[0]["cash_received"]), -0.1)
        self.assertAlmostEqual(float(audit[0]["target_actual_shares"]), -0.02)

    def test_linear_cost_transform_matches_rebalance_multiplier(self) -> None:
        gross = pd.DataFrame(
            {"daily_return": [0.01, -0.02, 0.03]},
            index=pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-04"]),
        )
        rebalances = pd.DataFrame(
            {"execution_date": [pd.Timestamp("2024-01-03")], "l1_turnover": [1.5]}
        )
        net = apply_linear_cost(gross, rebalances, cost_bps=10.0)
        expected = 1.01 * (0.98 * (1.0 - 0.0015)) * 1.03
        self.assertAlmostEqual(net["nav"].iloc[-1], expected)

    def test_linear_cost_transform_matches_direct_scaled_backtest(self) -> None:
        schedule = rebalance_schedule(self.dates, "weekly")
        allocation = pd.Series(0.65, index=schedule["signal_date"])
        risk_free = pd.Series(0.0001, index=self.dates)
        engine = BaselineBacktester(self.prices, self.membership)
        gross = engine.run(
            signal="mom_255_0",
            top_n=10,
            frequency="weekly",
            cost_bps=0.0,
            risk_allocation=allocation,
            risk_free_daily=risk_free,
            full_audit=False,
        )
        direct = engine.run(
            signal="mom_255_0",
            top_n=10,
            frequency="weekly",
            cost_bps=10.0,
            risk_allocation=allocation,
            risk_free_daily=risk_free,
            full_audit=False,
        )
        transformed = apply_linear_cost(gross.nav, gross.rebalances, cost_bps=10.0)
        np.testing.assert_allclose(
            transformed["nav"].to_numpy(), direct.nav["nav"].to_numpy(), rtol=1e-12
        )

        replayed = replay_linear_cost(gross, cost_bps=10.0)
        np.testing.assert_allclose(
            replayed.nav["nav"].to_numpy(), direct.nav["nav"].to_numpy(), rtol=1e-12
        )
        np.testing.assert_allclose(
            replayed.nav["daily_return"].to_numpy(),
            direct.nav["daily_return"].to_numpy(),
            rtol=1e-12,
            atol=1e-14,
        )
        np.testing.assert_allclose(
            replayed.rebalances["pretrade_nav"].to_numpy(),
            direct.rebalances["pretrade_nav"].to_numpy(),
            rtol=1e-12,
        )
        np.testing.assert_allclose(
            replayed.rebalances["cost_amount"].to_numpy(),
            direct.rebalances["cost_amount"].to_numpy(),
            rtol=1e-12,
            atol=1e-14,
        )

    def test_linear_cost_replay_matches_signed_borrow_fee_path(self) -> None:
        def generator(signal_date, scores, members):
            del signal_date
            return winner_loser_weights(scores, members, 10, gross_exposure=1.0)

        risk_free = pd.Series(0.0001, index=self.dates)
        engine = BaselineBacktester(self.prices, self.membership)
        common = {
            "signal": "mom_255_0",
            "top_n": 10,
            "frequency": "weekly",
            "target_weight_generator": generator,
            "risk_free_daily": risk_free,
            "short_borrow_fee_daily": 0.00005,
            "full_audit": False,
        }
        gross = engine.run(**common, cost_bps=0.0)
        direct = engine.run(**common, cost_bps=10.0)
        replayed = replay_linear_cost(gross, cost_bps=10.0)
        for column in ("nav", "daily_return", "short_borrow_fee_amount"):
            np.testing.assert_allclose(
                replayed.nav[column].to_numpy(),
                direct.nav[column].to_numpy(),
                rtol=1e-12,
                atol=1e-14,
            )
        np.testing.assert_allclose(
            replayed.rebalances["cost_amount"].to_numpy(),
            direct.rebalances["cost_amount"].to_numpy(),
            rtol=1e-12,
            atol=1e-14,
        )

    def test_public_grid_runner_cannot_infer_a_gapped_calendar(self) -> None:
        missing_date = self.dates[275]
        gapped = self.prices.drop(index=missing_date, level="date")
        with self.assertRaisesRegex(ValueError, "have no price rows"):
            run_baseline_grid(
                gapped,
                self.membership,
                sessions=self.dates,
                evaluation_start=self.dates[300],
                signal_end=self.dates[-1],
                costs_bps=(0.0,),
            )


if __name__ == "__main__":
    unittest.main()
