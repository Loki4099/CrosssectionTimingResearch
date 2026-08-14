"""Event-driven engine for deterministic long-only and signed portfolios."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

import numpy as np
import pandas as pd

from momentum_reversal.data.corporate_actions import CorporateActionLedger
from momentum_reversal.data.membership import PITUniverse
from momentum_reversal.data.schema import canonicalize_prices, normalize_session_date
from momentum_reversal.factors import MomentumDefinition, compute_momentum_scores
from momentum_reversal.portfolio import rank_and_select

from .calendar import RebalanceFrequency, rebalance_schedule


class MissingExecutionPriceError(ValueError):
    """A selected security cannot be traded at the required next-session open."""


class MissingValuationPriceError(ValueError):
    """An existing position cannot be valued without inventing a price."""


class MissingCorporateActionPriceError(ValueError):
    """A merger conversion lacks a historical source or target adjustment price."""


CORPORATE_ACTION_EVENT_COLUMNS = (
    "action_id",
    "action_type",
    "legal_effective_date",
    "apply_session",
    "status",
    "source_sid",
    "target_sid",
    "source_factor_date",
    "source_adjusted_units",
    "source_adjustment_factor",
    "source_actual_shares",
    "cash_per_source_share",
    "currency",
    "cash_received",
    "target_shares_per_source_share",
    "target_actual_shares",
    "target_adjustment_factor",
    "target_adjusted_units",
    "fractional_treatment",
    "forced_l1_turnover_charged",
    "forced_cost_amount",
    "cash_balance_after",
)


@dataclass(frozen=True)
class BacktestResult:
    """Auditable outputs from one signal/width/frequency/cost path."""

    nav: pd.DataFrame
    rebalances: pd.DataFrame
    trades: pd.DataFrame
    target_weights: pd.DataFrame
    rankings: pd.DataFrame
    corporate_action_events: pd.DataFrame
    valuation_fallbacks: pd.DataFrame
    signal: MomentumDefinition
    top_n: int
    frequency: RebalanceFrequency
    cost_bps: float

    def summary(self, risk_free_daily: pd.Series | None = None) -> pd.Series:
        from momentum_reversal.analytics.performance import performance_summary

        metrics = performance_summary(
            self.nav["daily_return"],
            nav=self.nav["nav"],
            risk_free_daily=risk_free_daily,
        )
        metrics["average_l1_turnover"] = self.rebalances["l1_turnover"].mean()
        metrics["annualized_l1_turnover"] = (
            self.rebalances["l1_turnover"].sum() * 252.0 / max(len(self.nav), 1)
        )
        metrics["total_cost"] = self.rebalances["cost_amount"].sum()
        metrics["corporate_action_events_applied"] = int(
            self.corporate_action_events.get("status", pd.Series(dtype=str))
            .eq("applied")
            .sum()
        )
        metrics["valuation_fallback_count"] = int(len(self.valuation_fallbacks))
        metrics["valuation_fallback_sid_count"] = int(
            self.valuation_fallbacks.get("sid", pd.Series(dtype=str)).nunique()
        )
        metrics["unfilled_execution_count"] = int(
            self.rebalances.get("unfilled_selected_count", pd.Series(dtype=float)).sum()
        )
        metrics["skipped_signed_rebalance_count"] = int(
            self.rebalances.get("execution_status", pd.Series(dtype=str))
            .eq("skipped_signed_missing_open")
            .sum()
        )
        if "target_risk_allocation" in self.rebalances:
            allocation = self.rebalances["target_risk_allocation"].astype(float)
            metrics["average_target_risk_allocation"] = float(allocation.mean())
            metrics["minimum_target_risk_allocation"] = float(allocation.min())
            metrics["maximum_target_risk_allocation"] = float(allocation.max())
            metrics["below_full_investment_fraction"] = float(allocation.lt(1.0).mean())
            metrics["leveraged_rebalance_fraction"] = float(allocation.gt(1.0).mean())
        if "risky_weight" in self.nav:
            metrics["average_actual_risky_weight"] = float(
                self.nav["risky_weight"].mean()
            )
        for column in (
            "long_exposure",
            "short_exposure",
            "gross_exposure",
            "net_exposure",
        ):
            if column in self.nav:
                metrics[f"average_{column}"] = float(self.nav[column].mean())
        if "short_borrow_fee_amount" in self.nav:
            metrics["total_short_borrow_fee"] = float(
                self.nav["short_borrow_fee_amount"].sum()
            )
        return metrics


def replay_linear_cost(
    zero_cost_result: BacktestResult, *, cost_bps: float
) -> BacktestResult:
    """Derive an exact proportional-cost scenario from a zero-cost path.

    Target weights, corporate actions, borrow rates and rebalance decisions are
    fixed by the zero-cost run.  Because the engine is homogeneous in NAV,
    proportional execution costs only multiply wealth on rebalance dates.  A
    full event-loop replay for every reporting cost is therefore unnecessary.
    """

    if not np.isclose(float(zero_cost_result.cost_bps), 0.0):
        raise ValueError("linear cost replay requires a zero-cost result")
    if not np.isfinite(cost_bps) or cost_bps < 0:
        raise ValueError("cost_bps must be finite and non-negative")
    cost_bps = float(cost_bps)
    if np.isclose(cost_bps, 0.0):
        return zero_cost_result

    nav = zero_cost_result.nav.copy(deep=True)
    rebalances = zero_cost_result.rebalances.copy(deep=True)
    required = {"execution_date", "l1_turnover", "pretrade_nav"}
    missing = required.difference(rebalances.columns)
    if missing:
        raise ValueError(
            f"zero-cost rebalances are missing columns: {sorted(missing)}"
        )
    dates = pd.DatetimeIndex(
        pd.to_datetime(rebalances["execution_date"], errors="raise")
    ).normalize()
    if dates.has_duplicates:
        raise ValueError("rebalance execution dates must be unique")
    turnover = pd.Series(
        pd.to_numeric(rebalances["l1_turnover"], errors="raise").to_numpy(
            dtype=float
        ),
        index=dates,
    )
    if not np.isfinite(turnover).all() or turnover.lt(0.0).any():
        raise ValueError("L1 turnover must be finite and non-negative")

    multiplier = pd.Series(1.0, index=nav.index, dtype=float)
    missing_dates = dates.difference(multiplier.index)
    if len(missing_dates):
        raise ValueError(
            f"rebalance dates are absent from NAV: {missing_dates.tolist()}"
        )
    multiplier.loc[dates] = 1.0 - cost_bps / 10_000.0 * turnover
    if multiplier.le(0.0).any():
        raise ValueError("transaction cost multiplier must remain positive")
    scale_after = multiplier.cumprod()
    scale_before = scale_after / multiplier

    absolute_nav_columns = (
        "nav",
        "risky_value",
        "long_value",
        "short_value",
        "cash_value",
        "short_borrow_fee_amount",
    )
    for column in absolute_nav_columns:
        if column in nav:
            nav[column] = pd.to_numeric(nav[column], errors="raise") * scale_after
    nav["daily_return"] = (
        (1.0 + pd.to_numeric(zero_cost_result.nav["daily_return"], errors="raise"))
        * multiplier
        - 1.0
    )

    prior_scale = scale_before.reindex(dates).to_numpy(dtype=float)
    pretrade = (
        pd.to_numeric(rebalances["pretrade_nav"], errors="raise").to_numpy(
            dtype=float
        )
        * prior_scale
    )
    cost_amount = pretrade * (cost_bps / 10_000.0) * turnover.to_numpy(dtype=float)
    rebalances["pretrade_nav"] = pretrade
    rebalances["cost_bps"] = cost_bps
    rebalances["cost_amount"] = cost_amount
    rebalances["postcost_nav"] = pretrade - cost_amount

    events = zero_cost_result.corporate_action_events.copy(deep=True)
    if not events.empty and "apply_session" in events:
        event_dates = pd.DatetimeIndex(
            pd.to_datetime(events["apply_session"], errors="raise")
        ).normalize()
        event_scale = scale_before.reindex(event_dates).to_numpy(dtype=float)
        for column in (
            "source_adjusted_units",
            "source_actual_shares",
            "cash_received",
            "target_actual_shares",
            "target_adjusted_units",
            "cash_balance_after",
        ):
            if column in events:
                events[column] = (
                    pd.to_numeric(events[column], errors="coerce") * event_scale
                )

    return replace(
        zero_cost_result,
        nav=nav,
        rebalances=rebalances,
        corporate_action_events=events,
        cost_bps=cost_bps,
    )


class _PositionBook(Protocol):
    def members_on(self, value: object) -> tuple[str, ...]: ...


class TargetWeightGenerator(Protocol):
    """Generate unscaled signed targets from one close's observable data."""

    def __call__(
        self,
        signal_date: pd.Timestamp,
        scores: pd.Series,
        members: tuple[str, ...],
    ) -> pd.Series: ...


class BaselineBacktester:
    """Run one frozen Top-K equal-weight baseline.

    Signals are observed at a session close.  Trades occur at the next supplied
    exchange session's total-return-adjusted open.  Between rebalances, adjusted
    shares remain fixed; PIT deletion never causes an unscheduled trade.
    """

    def __init__(
        self,
        prices: pd.DataFrame,
        membership: PITUniverse,
        *,
        sessions: object | None = None,
        signal_start: object | None = None,
        signal_end: object | None = None,
        evaluation_start: object | None = None,
        initial_capital: float = 1.0,
        corporate_actions: CorporateActionLedger | pd.DataFrame | None = None,
        missing_valuation_policy: str = "strict",
        missing_execution_policy: str = "strict",
    ) -> None:
        self.corporate_actions = (
            corporate_actions
            if isinstance(corporate_actions, CorporateActionLedger)
            else CorporateActionLedger(corporate_actions)
        )
        required_price_columns = (
            ("tr_open", "tr_close")
            if self.corporate_actions.is_empty
            else ("tr_open", "tr_close", "raw_open", "raw_close")
        )
        self.prices = canonicalize_prices(
            prices, required_columns=required_price_columns
        )
        self.membership = membership
        if missing_valuation_policy not in {"strict", "carry_last_close"}:
            raise ValueError(
                "missing_valuation_policy must be 'strict' or 'carry_last_close'"
            )
        self.missing_valuation_policy = missing_valuation_policy
        if missing_execution_policy not in {"strict", "leave_cash"}:
            raise ValueError(
                "missing_execution_policy must be 'strict' or 'leave_cash'"
            )
        self.missing_execution_policy = missing_execution_policy
        self._valuation_fallback_rows: list[dict[str, object]] = []
        self._score_cache: dict[tuple[str, str], pd.Series] = {}
        self._ranking_cache: dict[
            tuple[str, int, str], tuple[pd.DataFrame, list[pd.DataFrame]]
        ] = {}
        self._custom_score_cache: dict[
            tuple[str, str], tuple[pd.Series, pd.Series]
        ] = {}
        self._custom_ranking_cache: dict[
            tuple[str, int, str, str], tuple[pd.DataFrame, list[pd.DataFrame]]
        ] = {}
        self._signed_ranking_cache: dict[
            tuple[str, int, str, str, str],
            tuple[pd.DataFrame, list[pd.DataFrame]],
        ] = {}
        if initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        self.initial_capital = float(initial_capital)
        price_sessions = pd.DatetimeIndex(
            self.prices.index.get_level_values("date").unique()
        ).sort_values()
        if sessions is None:
            self.sessions = price_sessions
        else:
            authoritative = pd.DatetimeIndex(pd.to_datetime(list(sessions)))
            if authoritative.tz is not None:
                raise ValueError("sessions must be timezone-naive")
            authoritative = authoritative.normalize()
            if authoritative.empty:
                raise ValueError("sessions cannot be empty")
            if authoritative.hasnans:
                raise ValueError("sessions cannot contain NaT")
            if (
                authoritative.has_duplicates
                or not authoritative.is_monotonic_increasing
            ):
                raise ValueError("sessions must be unique and strictly increasing")
            non_sessions = price_sessions.difference(authoritative)
            if len(non_sessions):
                raise ValueError(
                    "price dates are absent from the authoritative calendar: "
                    f"{non_sessions[:5].tolist()}"
                )
            missing_sessions = authoritative.difference(price_sessions)
            if len(missing_sessions):
                raise ValueError(
                    "authoritative exchange sessions have no price rows: "
                    f"{missing_sessions[:5].tolist()}"
                )
            self.sessions = authoritative

        self._price_panels = {
            column: self.prices[column].unstack("sid").reindex(self.sessions)
            for column in ("tr_open", "tr_close")
        }

        self.corporate_actions.validate_against_sessions(self.sessions)
        action_frame = self.corporate_actions.to_frame()
        self._corporate_actions_by_session = {
            pd.Timestamp(session): group.reset_index(drop=True)
            for session, group in action_frame.groupby(
                "apply_session", sort=False, observed=True
            )
        }

        self.signal_start = (
            None if signal_start is None else normalize_session_date(signal_start)
        )
        self.signal_end = None if signal_end is None else normalize_session_date(signal_end)
        self.evaluation_start = (
            None
            if evaluation_start is None
            else normalize_session_date(evaluation_start)
        )
        if (
            self.signal_start is not None
            and self.signal_end is not None
            and self.signal_start > self.signal_end
        ):
            raise ValueError("signal_start must be on or before signal_end")

    def run(
        self,
        *,
        signal: MomentumDefinition | str,
        top_n: int,
        frequency: RebalanceFrequency,
        cost_bps: float = 0.0,
        selection_scores: pd.Series | None = None,
        selection_label: str | None = None,
        selection_score_cache_key: str | None = None,
        target_weights: pd.Series | None = None,
        target_weight_generator: TargetWeightGenerator | None = None,
        target_weight_cache_key: str | None = None,
        risk_allocation: pd.Series | None = None,
        risk_free_daily: pd.Series | None = None,
        short_borrow_fee_daily: float | pd.Series | None = None,
        signed_missing_execution_policy: str = "strict",
        terminal_last_close_max_sessions: int = 25,
        full_audit: bool = True,
    ) -> BacktestResult:
        """Run a close-signal/next-open portfolio path.

        The default remains the historical Top-K long-only baseline.  For a
        signed portfolio, callers may instead supply either ``target_weights``
        (a Series indexed by ``['signal_date', 'sid']``) or a
        ``target_weight_generator``.  Generator inputs contain only information
        observable at the signal close: the date, that date's score vector and
        its PIT member tuple.  Returned/supplied targets are multiplied by
        ``risk_allocation`` after validation.  A stable non-empty
        ``target_weight_cache_key`` may be supplied for a generator when the
        same engine will rerun that base portfolio under reporting costs; risk
        allocation remains outside this base-target cache.

        Cash -- including collateral cash created by a short sale -- earns
        ``risk_free_daily``.  ``short_borrow_fee_daily`` accepts a non-negative
        scalar daily rate, a date-indexed daily rate, or per-security rates with
        a MultiIndex named ``['date', 'sid']``.  Borrow expense is charged on
        each short's close market value.  A signed two-leg rebalance with any
        missing target or existing-position open fails closed by default;
        ``signed_missing_execution_policy='skip_rebalance'`` instead preserves
        the complete pre-open book and records a zero-turnover skipped event.
        ``'terminal_last_close'`` additionally permits a missing-open existing
        position that has left the signal-date PIT universe to be liquidated at
        an explicitly audited, strictly-prior close no more than
        ``terminal_last_close_max_sessions`` authoritative sessions old.
        Missing target opens and missing opens for current PIT members still
        skip the complete signed rebalance.  Any held missing-open SID with a
        pre-open terminal action in the next 25 sessions also preserves the
        complete book until that action can be applied.
        """

        signal = MomentumDefinition(signal)
        if top_n <= 0:
            raise ValueError("top_n must be positive")
        if cost_bps < 0:
            raise ValueError("cost_bps cannot be negative")
        if target_weights is not None and target_weight_generator is not None:
            raise ValueError(
                "target_weights and target_weight_generator are mutually exclusive"
            )
        if selection_score_cache_key is not None:
            if selection_scores is None:
                raise ValueError(
                    "selection_score_cache_key requires selection_scores"
                )
            if (
                not isinstance(selection_score_cache_key, str)
                or not selection_score_cache_key.strip()
            ):
                raise ValueError(
                    "selection_score_cache_key must be a non-empty string"
                )
            selection_score_cache_key = selection_score_cache_key.strip()
        if target_weight_cache_key is not None:
            if target_weight_generator is None:
                raise ValueError(
                    "target_weight_cache_key is only valid with "
                    "target_weight_generator"
                )
            if (
                not isinstance(target_weight_cache_key, str)
                or not target_weight_cache_key.strip()
            ):
                raise ValueError(
                    "target_weight_cache_key must be a non-empty string"
                )
            target_weight_cache_key = target_weight_cache_key.strip()
        if signed_missing_execution_policy not in {
            "strict",
            "skip_rebalance",
            "terminal_last_close",
        }:
            raise ValueError(
                "signed_missing_execution_policy must be 'strict' or "
                "'skip_rebalance' or 'terminal_last_close'"
            )
        if (
            isinstance(terminal_last_close_max_sessions, bool)
            or not isinstance(terminal_last_close_max_sessions, int)
            or terminal_last_close_max_sessions <= 0
        ):
            raise ValueError(
                "terminal_last_close_max_sessions must be a positive integer"
            )
        self._valuation_fallback_rows = []

        schedule = rebalance_schedule(self.sessions, frequency)
        if self.signal_start is not None:
            schedule = schedule.loc[schedule["signal_date"] >= self.signal_start]
        if self.signal_end is not None:
            schedule = schedule.loc[schedule["signal_date"] <= self.signal_end]
        if self.evaluation_start is not None:
            # The portfolio is initialized from cash at the first execution on
            # or after the common evaluation boundary.  Its signal may be from
            # the preceding session (for example 2017-12-29 close ->
            # 2018-01-02 open), so filtering on signal_date would start weekly
            # and monthly strategies on different dates.
            schedule = schedule.loc[
                schedule["execution_date"] >= self.evaluation_start
            ]
        if schedule.empty:
            raise ValueError("not enough sessions to form a rebalance schedule")
        custom_targets = target_weights is not None or target_weight_generator is not None
        if selection_scores is None:
            if selection_label is not None:
                raise ValueError("selection_label requires selection_scores")
            score_key = (signal.value, frequency)
            scores = self._score_cache.get(score_key)
            if scores is None:
                scores = compute_momentum_scores(
                    self.prices,
                    schedule["signal_date"],
                    signal,
                    sessions=self.sessions,
                )
                self._score_cache[score_key] = scores
            if not custom_targets:
                ranking_key = (signal.value, top_n, frequency)
                cached_ranking = self._ranking_cache.get(ranking_key)
                if cached_ranking is None:
                    cached_ranking = self._prepare_rebalances(
                        schedule, scores, top_n, signal.value
                    )
                    self._ranking_cache[ranking_key] = cached_ranking
        else:
            if not isinstance(selection_label, str) or not selection_label.strip():
                raise ValueError(
                    "selection_label must be a non-empty string for custom scores"
                )
            score_cache_identity = (
                None
                if selection_score_cache_key is None
                else (selection_score_cache_key, str(frequency))
            )
            cached_scores = (
                None
                if score_cache_identity is None
                else self._custom_score_cache.get(score_cache_identity)
            )
            if cached_scores is None:
                scores = self._validate_selection_scores(selection_scores, schedule)
                if score_cache_identity is not None:
                    self._custom_score_cache[score_cache_identity] = (
                        selection_scores,
                        scores,
                    )
            else:
                source_scores, scores = cached_scores
                if source_scores is not selection_scores:
                    raise ValueError(
                        "selection_score_cache_key was reused for a different "
                        "selection_scores object"
                    )
            if not custom_targets:
                custom_ranking_key = (
                    None
                    if selection_score_cache_key is None
                    else (
                        selection_score_cache_key,
                        top_n,
                        str(frequency),
                        selection_label.strip(),
                    )
                )
                cached_ranking = (
                    None
                    if custom_ranking_key is None
                    else self._custom_ranking_cache.get(custom_ranking_key)
                )
                if cached_ranking is None:
                    cached_ranking = self._prepare_rebalances(
                        schedule, scores, top_n, selection_label.strip()
                    )
                    if custom_ranking_key is not None:
                        self._custom_ranking_cache[custom_ranking_key] = cached_ranking
        if custom_targets:
            supplied_targets = (
                None
                if target_weights is None
                else self._validate_target_weights(target_weights, schedule)
            )
            score_label = (
                signal.value
                if selection_label is None
                else selection_label.strip()
            )
            signed_cache_key = (
                None
                if target_weight_cache_key is None
                else (
                    signal.value,
                    top_n,
                    str(frequency),
                    score_label,
                    target_weight_cache_key,
                )
            )
            cached_ranking = (
                None
                if signed_cache_key is None
                else self._signed_ranking_cache.get(signed_cache_key)
            )
            if cached_ranking is None:
                cached_ranking = self._prepare_signed_rebalances(
                    schedule=schedule,
                    scores=scores,
                    top_n=top_n,
                    score_label=score_label,
                    supplied_targets=supplied_targets,
                    generator=target_weight_generator,
                )
                if signed_cache_key is not None:
                    self._signed_ranking_cache[signed_cache_key] = cached_ranking
        usable, ranking_frames = cached_ranking
        if usable.empty:
            raise ValueError(f"no signal date has {top_n} securities with valid {signal.value}")

        result = self._simulate(
            usable=usable,
            ranking_frames=ranking_frames,
            signal=signal,
            top_n=top_n,
            frequency=frequency,
            cost_bps=float(cost_bps),
            risk_allocation=risk_allocation,
            risk_free_daily=risk_free_daily,
            short_borrow_fee_daily=short_borrow_fee_daily,
            signed_missing_execution_policy=signed_missing_execution_policy,
            terminal_last_close_max_sessions=terminal_last_close_max_sessions,
            full_audit=full_audit,
        )
        return result

    def _prepare_rebalances(
        self,
        schedule: pd.DataFrame,
        scores: pd.Series,
        top_n: int,
        score_label: str,
    ) -> tuple[pd.DataFrame, list[pd.DataFrame]]:
        rows: list[dict[str, object]] = []
        audits: list[pd.DataFrame] = []
        started = False

        for item in schedule.itertuples(index=False):
            signal_date = pd.Timestamp(item.signal_date)
            members = tuple(str(sid) for sid in self.membership.members_on(signal_date))
            date_scores = scores.xs(signal_date, level="signal_date")
            finite_count = int(np.isfinite(date_scores.reindex(members).to_numpy(dtype=float)).sum())
            if finite_count < top_n:
                if not started and (
                    self.signal_start is not None
                    or self.evaluation_start is not None
                ):
                    raise ValueError(
                        f"{score_label}: first scheduled research signal "
                        f"{signal_date.date()} has only {finite_count} valid PIT "
                        f"scores for Top{top_n}; formation history is incomplete"
                    )
                if started:
                    raise ValueError(
                        f"{signal_date.date()}: only {finite_count} valid PIT scores for Top{top_n}"
                    )
                continue

            started = True
            ranking = rank_and_select(date_scores, members, top_n).reset_index()
            ranking.insert(0, "signal_date", signal_date)
            ranking["target_weight"] = np.where(ranking["selected"], 1.0 / top_n, 0.0)
            audits.append(ranking)
            selected = tuple(ranking.loc[ranking["selected"], "sid"].astype(str))
            rows.append(
                {
                    "signal_date": signal_date,
                    "execution_date": pd.Timestamp(item.execution_date),
                    "selected": selected,
                    "base_target_weights": pd.Series(
                        1.0 / top_n,
                        index=pd.Index(selected, name="sid", dtype="object"),
                        name="target_weight",
                    ),
                }
            )
        return pd.DataFrame(rows), audits

    def _prepare_signed_rebalances(
        self,
        *,
        schedule: pd.DataFrame,
        scores: pd.Series,
        top_n: int,
        score_label: str,
        supplied_targets: pd.Series | None,
        generator: TargetWeightGenerator | None,
    ) -> tuple[pd.DataFrame, list[pd.DataFrame]]:
        """Prepare authoritative signed targets without peeking past the close."""

        rows: list[dict[str, object]] = []
        audits: list[pd.DataFrame] = []
        started = False
        supplied_dates = (
            pd.DatetimeIndex([])
            if supplied_targets is None
            else pd.DatetimeIndex(
                supplied_targets.index.get_level_values("signal_date").unique()
            )
        )

        for item in schedule.itertuples(index=False):
            signal_date = pd.Timestamp(item.signal_date)
            members = tuple(
                str(sid) for sid in self.membership.members_on(signal_date)
            )
            date_scores = scores.xs(signal_date, level="signal_date")
            finite_count = int(
                np.isfinite(
                    date_scores.reindex(members).to_numpy(dtype=float)
                ).sum()
            )

            if supplied_targets is not None and signal_date not in supplied_dates:
                if started:
                    raise ValueError(
                        f"target_weights omit scheduled signal date "
                        f"{signal_date.date()} after target history began"
                    )
                if self.signal_start is not None or self.evaluation_start is not None:
                    raise ValueError(
                        "target_weights omit the first scheduled research signal "
                        f"{signal_date.date()}"
                    )
                continue

            if generator is not None and finite_count < top_n:
                if not started and (
                    self.signal_start is not None
                    or self.evaluation_start is not None
                ):
                    raise ValueError(
                        f"{score_label}: first scheduled research signal "
                        f"{signal_date.date()} has only {finite_count} valid PIT "
                        f"scores for target generation; formation history is incomplete"
                    )
                if started:
                    raise ValueError(
                        f"{signal_date.date()}: only {finite_count} valid PIT scores "
                        "for target generation"
                    )
                continue

            if supplied_targets is not None:
                raw_targets = supplied_targets.xs(
                    signal_date, level="signal_date"
                )
            else:
                if generator is None:  # protected by ``run``
                    raise RuntimeError("signed target source is missing")
                raw_targets = generator(signal_date, date_scores.copy(), members)
            targets = self._validate_target_vector(
                raw_targets, signal_date=signal_date, members=members
            )

            # A score audit remains useful even when the portfolio constructor
            # is custom.  ``selected`` describes actual portfolio inclusion;
            # ``rank`` continues to describe descending score order.
            if finite_count >= top_n:
                ranking = rank_and_select(
                    date_scores, members, top_n
                ).reset_index()
            else:
                member_index = pd.Index(
                    sorted(set(members)), name="sid", dtype="object"
                )
                member_scores = pd.to_numeric(
                    date_scores.reindex(member_index), errors="coerce"
                )
                finite = np.isfinite(member_scores.to_numpy(dtype=float))
                ranking = pd.DataFrame(
                    {
                        "sid": member_index,
                        "score": member_scores.to_numpy(dtype=float),
                        "eligible": finite,
                        "exclusion_reason": np.where(
                            finite, pd.NA, "missing_factor"
                        ),
                        "rank": pd.array([pd.NA] * len(member_index), dtype="Int64"),
                        "selected": False,
                    }
                )
            ranking.insert(0, "signal_date", signal_date)
            ranking["target_weight"] = (
                ranking["sid"].map(targets).fillna(0.0).astype(float)
            )
            ranking["selected"] = ranking["target_weight"].ne(0.0)
            ranking["selection_leg"] = np.select(
                [
                    ranking["target_weight"].gt(0.0),
                    ranking["target_weight"].lt(0.0),
                ],
                ["long", "short"],
                default="none",
            )
            audits.append(ranking)
            selected = tuple(targets.index.astype(str))
            rows.append(
                {
                    "signal_date": signal_date,
                    "execution_date": pd.Timestamp(item.execution_date),
                    "selected": selected,
                    "base_target_weights": targets,
                    "pit_members": members,
                }
            )
            started = True

        return pd.DataFrame(rows), audits

    @staticmethod
    def _validate_target_weights(
        weights: pd.Series, schedule: pd.DataFrame
    ) -> pd.Series:
        if not isinstance(weights, pd.Series):
            raise TypeError("target_weights must be a pandas Series")
        if not isinstance(weights.index, pd.MultiIndex) or list(weights.index.names) != [
            "signal_date",
            "sid",
        ]:
            raise ValueError(
                "target_weights must use a MultiIndex named "
                "['signal_date', 'sid']"
            )
        frame = weights.rename("target_weight").reset_index()
        dates = pd.to_datetime(frame["signal_date"], errors="coerce")
        if dates.isna().any() or getattr(dates.dt, "tz", None) is not None:
            raise ValueError("target_weights dates must be valid and timezone-naive")
        frame["signal_date"] = dates.dt.normalize()
        if frame["sid"].isna().any():
            raise ValueError("target_weights sid cannot be missing")
        frame["sid"] = frame["sid"].astype(str)
        if frame.duplicated(["signal_date", "sid"]).any():
            raise ValueError("target_weights contains duplicate date/sid rows")
        frame["target_weight"] = pd.to_numeric(
            frame["target_weight"], errors="coerce"
        )
        invalid = frame["target_weight"].isna() | ~np.isfinite(
            frame["target_weight"]
        )
        if invalid.any():
            raise ValueError("target_weights must contain only finite values")
        scheduled = pd.DatetimeIndex(schedule["signal_date"])
        if not frame["signal_date"].isin(scheduled).any():
            raise ValueError("target_weights contain no scheduled signal date")
        return (
            frame.set_index(["signal_date", "sid"])["target_weight"]
            .sort_index()
            .astype(float)
        )

    @staticmethod
    def _validate_target_vector(
        weights: pd.Series,
        *,
        signal_date: pd.Timestamp,
        members: tuple[str, ...],
    ) -> pd.Series:
        if not isinstance(weights, pd.Series):
            raise TypeError("a target weight generator must return a pandas Series")
        if isinstance(weights.index, pd.MultiIndex):
            raise ValueError("one-date target weights must use a one-level sid index")
        if weights.index.has_duplicates:
            raise ValueError(
                f"target weights contain duplicate sid on {signal_date.date()}"
            )
        if weights.index.isna().any():
            raise ValueError(f"target weights contain missing sid on {signal_date.date()}")
        vector = weights.copy()
        vector.index = pd.Index(vector.index.astype(str), name="sid")
        vector = pd.to_numeric(vector, errors="coerce")
        invalid = vector.isna() | ~np.isfinite(vector)
        if invalid.any():
            raise ValueError(
                f"target weights must be finite on {signal_date.date()}"
            )
        outside = vector.index.difference(pd.Index(members, dtype="object"))
        if len(outside):
            raise ValueError(
                f"target weights contain non-PIT members on {signal_date.date()}: "
                f"{outside[:5].tolist()}"
            )
        vector = vector.loc[vector.ne(0.0)].sort_index().astype(float)
        vector.name = "target_weight"
        return vector

    @staticmethod
    def _validate_selection_scores(
        scores: pd.Series, schedule: pd.DataFrame
    ) -> pd.Series:
        if not isinstance(scores, pd.Series):
            raise TypeError("selection_scores must be a pandas Series")
        if not isinstance(scores.index, pd.MultiIndex) or list(scores.index.names) != [
            "signal_date",
            "sid",
        ]:
            raise ValueError(
                "selection_scores must use a MultiIndex named "
                "['signal_date', 'sid']"
            )
        frame = scores.rename("score").reset_index()
        dates = pd.to_datetime(frame["signal_date"], errors="coerce")
        if dates.isna().any() or getattr(dates.dt, "tz", None) is not None:
            raise ValueError("selection_scores dates must be valid and timezone-naive")
        frame["signal_date"] = dates.dt.normalize()
        if frame["sid"].isna().any():
            raise ValueError("selection_scores sid cannot be missing")
        frame["sid"] = frame["sid"].astype(str)
        if frame.duplicated(["signal_date", "sid"]).any():
            raise ValueError("selection_scores contains duplicate date/sid rows")
        try:
            frame["score"] = pd.to_numeric(frame["score"], errors="raise")
        except (TypeError, ValueError) as exc:
            raise ValueError("selection_scores must be numeric or missing") from exc
        required_dates = pd.DatetimeIndex(schedule["signal_date"])
        available_dates = pd.DatetimeIndex(frame["signal_date"].unique())
        missing_dates = required_dates.difference(available_dates)
        if len(missing_dates):
            raise ValueError(
                "selection_scores omit scheduled dates: "
                f"{missing_dates[:5].tolist()}"
            )
        return (
            frame.set_index(["signal_date", "sid"])["score"]
            .sort_index()
            .astype(float)
        )

    def _price_vector(
        self,
        date: pd.Timestamp,
        sids: pd.Index,
        column: str,
        *,
        execution: bool,
    ) -> pd.Series:
        if sids.empty:
            return pd.Series(dtype=float, index=sids)
        try:
            day = self._price_panels[column].loc[date]
        except KeyError as exc:
            raise ValueError(f"missing entire price session {date.date()}") from exc
        values = pd.to_numeric(day, errors="coerce").reindex(sids)
        invalid = values.isna() | ~np.isfinite(values) | (values <= 0)
        if invalid.any() and not execution and self.missing_valuation_policy == "carry_last_close":
            for sid in values.index[invalid]:
                if str(sid) not in self._price_panels["tr_close"].columns:
                    continue
                prior = pd.to_numeric(
                    self._price_panels["tr_close"].loc[
                        self._price_panels["tr_close"].index < date, str(sid)
                    ],
                    errors="coerce",
                )
                prior = prior[np.isfinite(prior) & prior.gt(0)]
                if prior.empty:
                    continue
                fallback_date = pd.Timestamp(prior.index[-1])
                values.loc[sid] = float(prior.iloc[-1])
                self._valuation_fallback_rows.append(
                    {
                        "date": date,
                        "sid": str(sid),
                        "requested_column": column,
                        "fallback_date": fallback_date,
                        "fallback_column": "tr_close",
                        "fallback_value": float(prior.iloc[-1]),
                    }
                )
            invalid = values.isna() | ~np.isfinite(values) | (values <= 0)
        if invalid.any():
            if execution and self.missing_execution_policy == "leave_cash":
                return values.loc[~invalid].astype(float)
            missing = values.index[invalid].tolist()
            exception = MissingExecutionPriceError if execution else MissingValuationPriceError
            raise exception(f"{date.date()} {column} missing/invalid for {missing}")
        return values.astype(float)

    def _available_price_vector(
        self,
        date: pd.Timestamp,
        sids: pd.Index,
        column: str,
    ) -> pd.Series:
        """Return only executable positive finite prices, without policy effects."""

        if sids.empty:
            return pd.Series(dtype=float, index=sids)
        try:
            day = self._price_panels[column].loc[date]
        except KeyError as exc:
            raise ValueError(f"missing entire price session {date.date()}") from exc
        values = pd.to_numeric(day, errors="coerce").reindex(sids)
        valid = values.notna() & np.isfinite(values) & values.gt(0.0)
        return values.loc[valid].astype(float)

    def _causal_pretrade_audit_prices(
        self,
        *,
        date: pd.Timestamp,
        sids: pd.Index,
        available_open: pd.Series,
    ) -> pd.Series:
        """Causally mark an unchanged skipped book for rebalance auditing.

        A missing open makes execution impossible but does not erase the held
        security.  The most recent strictly-prior total-return close is used
        solely for the skipped rebalance's exposure audit.  Close NAV on the
        actual session continues to use that session's close via
        ``_price_vector``.
        """

        if sids.empty:
            return pd.Series(dtype=float, index=sids)
        values = available_open.reindex(sids).astype(float)
        for sid in values.index[values.isna()]:
            if str(sid) not in self._price_panels["tr_close"].columns:
                raise MissingValuationPriceError(
                    f"no historical close available to audit skipped position {sid}"
                )
            prior = pd.to_numeric(
                self._price_panels["tr_close"].loc[
                    self._price_panels["tr_close"].index < date, str(sid)
                ],
                errors="coerce",
            )
            prior = prior[np.isfinite(prior) & prior.gt(0.0)]
            if prior.empty:
                raise MissingValuationPriceError(
                    f"no pre-{date.date()} close available to audit skipped "
                    f"position {sid}"
                )
            values.loc[sid] = float(prior.iloc[-1])
        return values.astype(float)

    def _terminal_last_close_prices(
        self,
        *,
        date: pd.Timestamp,
        sids: pd.Index,
        max_age_sessions: int = 25,
    ) -> tuple[pd.Series, dict[str, pd.Timestamp]]:
        """Return explicitly audited terminal liquidation marks.

        Only strictly-prior closes are eligible.  A stale or absent mark fails
        closed because using it would manufacture an executable exit price.
        """

        prices: dict[str, float] = {}
        fallback_dates: dict[str, pd.Timestamp] = {}
        execution_location = int(self.sessions.get_loc(date))
        for raw_sid in sids:
            sid = str(raw_sid)
            if sid not in self._price_panels["tr_close"].columns:
                raise MissingExecutionPriceError(
                    f"no historical close for terminal liquidation of {sid}"
                )
            prior = pd.to_numeric(
                self._price_panels["tr_close"].loc[
                    self._price_panels["tr_close"].index < date, sid
                ],
                errors="coerce",
            )
            prior = prior[np.isfinite(prior) & prior.gt(0.0)]
            if prior.empty:
                raise MissingExecutionPriceError(
                    f"no strictly-prior close for terminal liquidation of {sid} "
                    f"on {date.date()}"
                )
            fallback_date = pd.Timestamp(prior.index[-1])
            fallback_location = int(self.sessions.get_loc(fallback_date))
            age_sessions = execution_location - fallback_location
            if age_sessions > max_age_sessions:
                raise MissingExecutionPriceError(
                    f"terminal liquidation close for {sid} is {age_sessions} "
                    f"authoritative sessions old on {date.date()}, exceeding "
                    f"the {max_age_sessions}-session limit"
                )
            fallback_value = float(prior.iloc[-1])
            prices[sid] = fallback_value
            fallback_dates[sid] = fallback_date

        for sid, fallback_date in fallback_dates.items():
            self._valuation_fallback_rows.append(
                {
                    "date": date,
                    "sid": sid,
                    "requested_column": "tr_open_terminal_liquidation",
                    "fallback_date": fallback_date,
                    "fallback_column": "tr_close",
                    "fallback_value": prices[sid],
                }
            )
        return (
            pd.Series(prices, dtype=float, name="tr_open").reindex(sids),
            fallback_dates,
        )

    def _pending_corporate_action_sids(
        self, date: pd.Timestamp, sids: pd.Index, *, max_sessions: int = 25
    ) -> pd.Index:
        """Held missing-open SIDs with a near-future pre-open terminal action."""

        if sids.empty or self.corporate_actions.is_empty:
            return pd.Index([], dtype="object")
        future_sessions = self.sessions[self.sessions > date][:max_sessions]
        actions = self.corporate_actions.to_frame()
        pending = pd.Index(
            actions.loc[
                actions["apply_session"].isin(future_sessions), "source_sid"
            ],
            dtype="object",
        )
        return sids.intersection(pending, sort=False)

    def _simulate(
        self,
        *,
        usable: pd.DataFrame,
        ranking_frames: list[pd.DataFrame],
        signal: MomentumDefinition,
        top_n: int,
        frequency: RebalanceFrequency,
        cost_bps: float,
        risk_allocation: pd.Series | None,
        risk_free_daily: pd.Series | None,
        short_borrow_fee_daily: float | pd.Series | None,
        signed_missing_execution_policy: str,
        terminal_last_close_max_sessions: int,
        full_audit: bool,
    ) -> BacktestResult:
        execution_map = {pd.Timestamp(row.execution_date): row for row in usable.itertuples(index=False)}
        first_execution = pd.Timestamp(usable.iloc[0]["execution_date"])
        simulation_sessions = self.sessions[self.sessions >= first_execution]
        allocation_by_signal = self._validate_risk_allocation(
            risk_allocation, pd.DatetimeIndex(usable["signal_date"])
        )
        daily_rf = self._validate_risk_free(risk_free_daily, simulation_sessions)
        borrow_fee = self._validate_short_borrow_fee(
            short_borrow_fee_daily, simulation_sessions
        )
        shares = pd.Series(dtype=float)
        cash = self.initial_capital
        previous_close_nav = self.initial_capital
        cost_rate = cost_bps / 10_000.0

        nav_rows: list[dict[str, object]] = []
        rebalance_rows: list[dict[str, object]] = []
        trade_rows: list[dict[str, object]] = []
        weight_rows: list[dict[str, object]] = []
        corporate_action_rows: list[dict[str, object]] = []

        for date in simulation_sessions:
            date = pd.Timestamp(date)
            shares, cash, date_action_rows = self._apply_corporate_actions(
                date=date,
                shares=shares,
                cash=cash,
            )
            corporate_action_rows.extend(date_action_rows)
            if date in execution_map:
                event = execution_map[date]
                target_risk_allocation = float(
                    allocation_by_signal.loc[pd.Timestamp(event.signal_date)]
                )
                base_targets = event.base_target_weights.astype(float)
                requested_targets = base_targets * target_risk_allocation
                requested_selected = pd.Index(base_targets.index, dtype="object")
                existing = pd.Index(shares.index, dtype="object")
                signed_request = (
                    requested_targets.gt(0.0).any()
                    and requested_targets.lt(0.0).any()
                )
                skip_rebalance_status: str | None = None
                terminal_liquidation_sids = pd.Index([], dtype="object")
                terminal_fallback_dates: dict[str, pd.Timestamp] = {}
                missing_targets = pd.Index([], dtype="object")
                available_existing_open = self._available_price_vector(
                    date, existing, "tr_open"
                )
                missing_existing = existing.difference(
                    available_existing_open.index, sort=False
                )
                pending_action_sids = self._pending_corporate_action_sids(
                    date, missing_existing
                )

                if len(pending_action_sids):
                    available_target_open = self._available_price_vector(
                        date, requested_selected, "tr_open"
                    )
                    missing_targets = requested_selected.difference(
                        available_target_open.index, sort=False
                    )
                    existing_open = self._causal_pretrade_audit_prices(
                        date=date,
                        sids=existing,
                        available_open=available_existing_open,
                    )
                    selected = existing
                    selected_open = existing_open
                    filled_selected = existing
                    unfilled_selected = pending_action_sids
                    skip_rebalance_status = "skipped_pending_corporate_action"
                elif signed_request:
                    available_target_open = self._available_price_vector(
                        date, requested_selected, "tr_open"
                    )
                    missing_targets = requested_selected.difference(
                        available_target_open.index, sort=False
                    )
                    blocked_sids = missing_targets.union(
                        missing_existing, sort=False
                    )
                    if len(blocked_sids):
                        if signed_missing_execution_policy == "strict":
                            raise MissingExecutionPriceError(
                                f"{date.date()} signed long-short rebalance has "
                                "missing/invalid target or existing-position opens; "
                                "refusing to break portfolio neutrality: "
                                f"{blocked_sids.tolist()}"
                            )
                        terminal_execution_allowed = False
                        if (
                            signed_missing_execution_policy
                            == "terminal_last_close"
                            and not len(missing_targets)
                        ):
                            pit_members = pd.Index(
                                event.pit_members, dtype="object"
                            )
                            missing_current_members = (
                                missing_existing.intersection(
                                    pit_members, sort=False
                                )
                            )
                            if not len(missing_current_members):
                                terminal_liquidation_sids = missing_existing
                                terminal_prices, terminal_fallback_dates = (
                                    self._terminal_last_close_prices(
                                        date=date,
                                        sids=terminal_liquidation_sids,
                                        max_age_sessions=(
                                            terminal_last_close_max_sessions
                                        ),
                                    )
                                )
                                existing_open = pd.concat(
                                    [available_existing_open, terminal_prices]
                                ).reindex(existing)
                                terminal_execution_allowed = True

                        if terminal_execution_allowed:
                            selected = requested_selected
                            selected_open = available_target_open
                            filled_selected = requested_selected
                            unfilled_selected = pd.Index([], dtype="object")
                        else:
                            skip_rebalance_status = "skipped_signed_missing_open"
                            # The strategy does not trade.  Prior closes are
                            # used only to express the unchanged book in the
                            # rebalance audit; actual same-day NAV still uses
                            # that session's close prices.
                            existing_open = self._causal_pretrade_audit_prices(
                                date=date,
                                sids=existing,
                                available_open=available_existing_open,
                            )
                            selected = existing
                            selected_open = existing_open
                            filled_selected = existing
                            unfilled_selected = blocked_sids
                    else:
                        selected = requested_selected
                        selected_open = available_target_open
                        filled_selected = requested_selected
                        unfilled_selected = pd.Index([], dtype="object")
                        existing_open = available_existing_open
                else:
                    # Preserve the historical long-only execution semantics.
                    selected = requested_selected
                    selected_open = self._price_vector(
                        date, selected, "tr_open", execution=True
                    )
                    filled_selected = pd.Index(
                        selected_open.index, dtype="object"
                    )
                    unfilled_selected = selected.difference(
                        filled_selected, sort=False
                    )
                    missing_targets = unfilled_selected
                    existing_open = (
                        self._price_vector(
                            date, existing, "tr_open", execution=False
                        )
                        if len(existing)
                        else pd.Series(dtype=float)
                    )

                old_values = (
                    shares * existing_open
                    if len(existing)
                    else pd.Series(dtype=float)
                )
                pretrade_nav = float(old_values.sum() + cash)
                if not np.isfinite(pretrade_nav) or pretrade_nav <= 0:
                    raise ValueError(f"non-positive NAV before rebalance on {date.date()}")

                union = existing.union(selected, sort=False)
                pre_weights = old_values.reindex(union, fill_value=0.0) / pretrade_nav
                if skip_rebalance_status is not None:
                    target_weights_at_open = pre_weights.copy()
                else:
                    target_weights_at_open = pd.Series(0.0, index=union)
                    target_weights_at_open.loc[filled_selected] = (
                        requested_targets.reindex(filled_selected).astype(float)
                    )
                l1_turnover = float(
                    (target_weights_at_open - pre_weights).abs().sum()
                )
                cost_amount = pretrade_nav * cost_rate * l1_turnover
                postcost_nav = pretrade_nav - cost_amount
                if postcost_nav <= 0:
                    raise ValueError("transaction costs exhausted portfolio capital")

                if skip_rebalance_status is None:
                    shares = (
                        postcost_nav
                        * requested_targets.reindex(filled_selected)
                        / selected_open
                    ).rename("shares")
                    shares = shares.loc[shares.ne(0.0)]
                filled_weights = target_weights_at_open
                requested_long = float(requested_targets.clip(lower=0.0).sum())
                requested_short = float(-requested_targets.clip(upper=0.0).sum())
                requested_gross = requested_long + requested_short
                requested_net = requested_long - requested_short
                filled_long = float(filled_weights.clip(lower=0.0).sum())
                filled_short = float(-filled_weights.clip(upper=0.0).sum())
                filled_gross = filled_long + filled_short
                filled_net = filled_long - filled_short
                pretrade_long = float(pre_weights.clip(lower=0.0).sum())
                pretrade_short = float(-pre_weights.clip(upper=0.0).sum())
                pretrade_gross = pretrade_long + pretrade_short
                pretrade_net = pretrade_long - pretrade_short
                filled_risk_allocation = filled_gross
                if skip_rebalance_status is None:
                    cash = postcost_nav * (1.0 - filled_net)

                rebalance_rows.append(
                    {
                        "signal_date": pd.Timestamp(event.signal_date),
                        "execution_date": date,
                        "execution_status": (
                            skip_rebalance_status
                            if skip_rebalance_status is not None
                            else (
                                "executed_with_terminal_last_close"
                                if len(terminal_liquidation_sids)
                                else "executed"
                            )
                        ),
                        "pretrade_nav": pretrade_nav,
                        "l1_turnover": l1_turnover,
                        "one_way_turnover": 0.5 * l1_turnover,
                        "cost_bps": cost_bps,
                        "cost_amount": cost_amount,
                        "postcost_nav": postcost_nav,
                        # Backward-compatible name: risky weight is signed net
                        # market value.  Explicit exposure fields remove any
                        # ambiguity for a short book.
                        "pretrade_risky_weight": pretrade_net,
                        "pretrade_long_exposure": pretrade_long,
                        "pretrade_short_exposure": pretrade_short,
                        "pretrade_gross_exposure": pretrade_gross,
                        "pretrade_net_exposure": pretrade_net,
                        "target_risk_allocation": target_risk_allocation,
                        "requested_long_exposure": requested_long,
                        "requested_short_exposure": requested_short,
                        "requested_gross_exposure": requested_gross,
                        "requested_net_exposure": requested_net,
                        "target_long_exposure": filled_long,
                        "target_short_exposure": filled_short,
                        "target_gross_exposure": filled_gross,
                        "target_net_exposure": filled_net,
                        "filled_risk_allocation": filled_risk_allocation,
                        "target_cash_weight": 1.0 - filled_net,
                        "selected_count": (
                            0
                            if skip_rebalance_status is not None
                            else len(filled_selected)
                        ),
                        "requested_selected_count": len(requested_selected),
                        "requested_selected_sids": "|".join(
                            map(str, requested_selected)
                        ),
                        "unfilled_selected_count": len(unfilled_selected),
                        "unfilled_selected_sids": "|".join(map(str, unfilled_selected)),
                        "missing_target_count": len(missing_targets),
                        "missing_target_sids": "|".join(
                            map(str, missing_targets)
                        ),
                        "missing_existing_count": len(missing_existing),
                        "missing_existing_sids": "|".join(
                            map(str, missing_existing)
                        ),
                        "terminal_liquidation_count": len(
                            terminal_liquidation_sids
                        ),
                        "terminal_liquidation_sids": "|".join(
                            map(str, terminal_liquidation_sids)
                        ),
                        "terminal_liquidation_fallback_dates": "|".join(
                            terminal_fallback_dates[str(sid)].date().isoformat()
                            for sid in terminal_liquidation_sids
                        ),
                        "corporate_actions_applied_pre_open": sum(
                            row["status"] == "applied" for row in date_action_rows
                        ),
                    }
                )
                if full_audit and skip_rebalance_status is None:
                    for sid in union:
                        trade_rows.append(
                            {
                                "signal_date": pd.Timestamp(event.signal_date),
                                "execution_date": date,
                                "sid": sid,
                                "pretrade_weight": float(pre_weights.loc[sid]),
                                "target_weight": float(target_weights_at_open.loc[sid]),
                                "trade_weight": float(
                                    target_weights_at_open.loc[sid]
                                    - pre_weights.loc[sid]
                                ),
                            }
                        )
                    for sid in filled_selected:
                        weight_rows.append(
                            {
                                "signal_date": pd.Timestamp(event.signal_date),
                                "execution_date": date,
                                "sid": sid,
                                "target_weight": float(
                                    target_weights_at_open.loc[sid]
                                ),
                            }
                        )

            close_values = self._price_vector(
                date, pd.Index(shares.index), "tr_close", execution=False
            )
            cash *= 1.0 + float(daily_rf.loc[date])
            position_values = shares * close_values
            long_value = float(position_values.clip(lower=0.0).sum())
            short_value = float(-position_values.clip(upper=0.0).sum())
            risky_value = long_value - short_value
            short_sids = pd.Index(position_values.index[position_values.lt(0.0)])
            short_rates = self._short_borrow_rates(
                borrow_fee, date=date, short_sids=short_sids
            )
            borrow_fee_amount = float(
                (-position_values.reindex(short_sids) * short_rates).sum()
            )
            cash -= borrow_fee_amount
            close_nav = float(risky_value + cash)
            if not np.isfinite(close_nav) or close_nav <= 0:
                raise ValueError(f"non-positive NAV at close on {date.date()}")
            daily_return = close_nav / previous_close_nav - 1.0
            nav_rows.append(
                {
                    "date": date,
                    "nav": close_nav,
                    "daily_return": daily_return,
                    "invested_count": len(shares),
                    "long_count": int(position_values.gt(0.0).sum()),
                    "short_count": int(position_values.lt(0.0).sum()),
                    "risky_value": risky_value,
                    "long_value": long_value,
                    "short_value": short_value,
                    "cash_value": cash,
                    "risky_weight": risky_value / close_nav,
                    "long_exposure": long_value / close_nav,
                    "short_exposure": short_value / close_nav,
                    "gross_exposure": (long_value + short_value) / close_nav,
                    "net_exposure": (long_value - short_value) / close_nav,
                    "cash_weight": cash / close_nav,
                    "rf_return": float(daily_rf.loc[date]),
                    "short_borrow_fee_amount": borrow_fee_amount,
                }
            )
            previous_close_nav = close_nav

        nav = pd.DataFrame(nav_rows).set_index("date")
        rebalances = pd.DataFrame(rebalance_rows).set_index("execution_date", drop=False)
        trades = pd.DataFrame(trade_rows)
        target_weights = pd.DataFrame(weight_rows)
        rankings = (
            pd.concat(ranking_frames, ignore_index=True)
            if full_audit
            else pd.DataFrame()
        )
        corporate_action_events = pd.DataFrame(
            corporate_action_rows, columns=CORPORATE_ACTION_EVENT_COLUMNS
        )
        return BacktestResult(
            nav=nav,
            rebalances=rebalances,
            trades=trades,
            target_weights=target_weights,
            rankings=rankings,
            corporate_action_events=corporate_action_events,
            valuation_fallbacks=pd.DataFrame.from_records(
                self._valuation_fallback_rows,
                columns=(
                    "date",
                    "sid",
                    "requested_column",
                    "fallback_date",
                    "fallback_column",
                    "fallback_value",
                ),
            ),
            signal=signal,
            top_n=top_n,
            frequency=frequency,
            cost_bps=cost_bps,
        )

    @staticmethod
    def _validate_risk_allocation(
        allocation: pd.Series | None, signal_dates: pd.DatetimeIndex
    ) -> pd.Series:
        if allocation is None:
            return pd.Series(1.0, index=signal_dates, dtype=float)
        if not isinstance(allocation, pd.Series):
            raise TypeError("risk_allocation must be a pandas Series")
        values = allocation.copy()
        dates = pd.DatetimeIndex(pd.to_datetime(values.index))
        if dates.tz is not None:
            raise ValueError("risk_allocation index must be timezone-naive")
        dates = dates.normalize()
        if dates.has_duplicates:
            raise ValueError("risk_allocation index must be unique")
        values.index = dates
        values = pd.to_numeric(values, errors="coerce").reindex(signal_dates)
        invalid = values.isna() | ~np.isfinite(values) | values.lt(0.0)
        if invalid.any():
            raise ValueError(
                "risk_allocation must contain finite non-negative values for "
                f"every signal date: {values.index[invalid][:5].tolist()}"
            )
        return values.astype(float)

    @staticmethod
    def _validate_risk_free(
        risk_free_daily: pd.Series | None, sessions: pd.DatetimeIndex
    ) -> pd.Series:
        if risk_free_daily is None:
            return pd.Series(0.0, index=sessions, dtype=float)
        if not isinstance(risk_free_daily, pd.Series):
            raise TypeError("risk_free_daily must be a pandas Series")
        values = risk_free_daily.copy()
        dates = pd.DatetimeIndex(pd.to_datetime(values.index))
        if dates.tz is not None:
            raise ValueError("risk_free_daily index must be timezone-naive")
        dates = dates.normalize()
        if dates.has_duplicates:
            raise ValueError("risk_free_daily index must be unique")
        values.index = dates
        values = pd.to_numeric(values, errors="coerce").reindex(sessions)
        invalid = values.isna() | ~np.isfinite(values) | values.le(-1.0)
        if invalid.any():
            raise ValueError(
                "risk_free_daily must contain finite returns above -100% for "
                f"every simulation session: {values.index[invalid][:5].tolist()}"
            )
        return values.astype(float)

    @staticmethod
    def _validate_short_borrow_fee(
        fee: float | pd.Series | None,
        sessions: pd.DatetimeIndex,
    ) -> float | pd.Series:
        """Canonicalize scalar, portfolio-wide, or sid-specific daily fees."""

        if fee is None or np.isscalar(fee):
            value = 0.0 if fee is None else float(fee)
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(
                    "short_borrow_fee_daily must be finite and non-negative"
                )
            return value
        if not isinstance(fee, pd.Series):
            raise TypeError(
                "short_borrow_fee_daily must be a scalar or pandas Series"
            )

        if isinstance(fee.index, pd.MultiIndex):
            if list(fee.index.names) != ["date", "sid"]:
                raise ValueError(
                    "security-specific short_borrow_fee_daily must use a "
                    "MultiIndex named ['date', 'sid']"
                )
            frame = fee.rename("fee").reset_index()
            dates = pd.to_datetime(frame["date"], errors="coerce")
            if dates.isna().any() or getattr(dates.dt, "tz", None) is not None:
                raise ValueError(
                    "short_borrow_fee_daily dates must be valid and timezone-naive"
                )
            frame["date"] = dates.dt.normalize()
            if frame["sid"].isna().any():
                raise ValueError("short_borrow_fee_daily sid cannot be missing")
            frame["sid"] = frame["sid"].astype(str)
            if frame.duplicated(["date", "sid"]).any():
                raise ValueError(
                    "short_borrow_fee_daily contains duplicate date/sid rows"
                )
            frame["fee"] = pd.to_numeric(frame["fee"], errors="coerce")
            invalid = (
                frame["fee"].isna()
                | ~np.isfinite(frame["fee"])
                | frame["fee"].lt(0.0)
            )
            if invalid.any():
                raise ValueError(
                    "short_borrow_fee_daily must contain finite non-negative rates"
                )
            return (
                frame.set_index(["date", "sid"])["fee"].sort_index().astype(float)
            )

        values = fee.copy()
        dates = pd.DatetimeIndex(pd.to_datetime(values.index))
        if dates.tz is not None:
            raise ValueError("short_borrow_fee_daily index must be timezone-naive")
        dates = dates.normalize()
        if dates.has_duplicates:
            raise ValueError("short_borrow_fee_daily index must be unique")
        values.index = dates
        values = pd.to_numeric(values, errors="coerce").reindex(sessions)
        invalid = values.isna() | ~np.isfinite(values) | values.lt(0.0)
        if invalid.any():
            raise ValueError(
                "short_borrow_fee_daily must contain finite non-negative rates "
                f"for every simulation session: {values.index[invalid][:5].tolist()}"
            )
        return values.astype(float)

    @staticmethod
    def _short_borrow_rates(
        fee: float | pd.Series,
        *,
        date: pd.Timestamp,
        short_sids: pd.Index,
    ) -> pd.Series:
        if short_sids.empty:
            return pd.Series(dtype=float, index=short_sids)
        if np.isscalar(fee):
            return pd.Series(float(fee), index=short_sids, dtype=float)
        if isinstance(fee.index, pd.MultiIndex):
            try:
                date_rates = fee.xs(date, level="date")
            except KeyError as exc:
                raise ValueError(
                    f"short_borrow_fee_daily omits held shorts on {date.date()}"
                ) from exc
            rates = date_rates.reindex(short_sids)
            if rates.isna().any():
                raise ValueError(
                    "short_borrow_fee_daily omits held securities on "
                    f"{date.date()}: {rates.index[rates.isna()][:5].tolist()}"
                )
            return rates.astype(float)
        return pd.Series(float(fee.loc[date]), index=short_sids, dtype=float)

    def _apply_corporate_actions(
        self,
        *,
        date: pd.Timestamp,
        shares: pd.Series,
        cash: float,
    ) -> tuple[pd.Series, float, list[dict[str, object]]]:
        """Convert held terminal actions before valuation/rebalancing at open.

        Holdings use total-return-adjusted units.  For source units ``u_s`` and
        source close adjustment factor ``a_s = tr_close / raw_close``, actual
        shares are ``q_s = u_s * a_s``.  Target actual shares are converted back
        to the target's adjusted-unit coordinate using its apply-open factor.
        The forced conversion itself never enters strategy turnover or costs.
        """

        actions = self._corporate_actions_by_session.get(date)
        if actions is None:
            return shares, cash, []

        updated = shares.copy()
        audit_rows: list[dict[str, object]] = []
        for action in actions.itertuples(index=False):
            source_sid = str(action.source_sid)
            target_sid = None if pd.isna(action.target_sid) else str(action.target_sid)
            base = {
                "action_id": str(action.action_id),
                "action_type": str(action.action_type),
                "legal_effective_date": pd.Timestamp(action.legal_effective_date),
                "apply_session": date,
                "source_sid": source_sid,
                "target_sid": target_sid,
                "cash_per_source_share": float(action.cash_per_source_share),
                "currency": None if pd.isna(action.currency) else str(action.currency),
                "target_shares_per_source_share": float(
                    action.target_shares_per_source_share
                ),
                "fractional_treatment": str(action.fractional_treatment),
                "forced_l1_turnover_charged": 0.0,
                "forced_cost_amount": 0.0,
            }
            if source_sid not in updated.index:
                audit_rows.append(
                    {
                        **base,
                        "status": "source_not_held",
                        "source_factor_date": pd.NaT,
                        "source_adjusted_units": 0.0,
                        "source_adjustment_factor": np.nan,
                        "source_actual_shares": 0.0,
                        "cash_received": 0.0,
                        "target_actual_shares": 0.0,
                        "target_adjustment_factor": np.nan,
                        "target_adjusted_units": 0.0,
                        "cash_balance_after": cash,
                    }
                )
                continue

            source_units = float(updated.loc[source_sid])
            if not np.isfinite(source_units) or source_units == 0:
                raise ValueError(
                    f"invalid adjusted units for corporate-action source {source_sid}"
                )
            source_factor_date, source_factor = self._source_adjustment_factor(
                source_sid=source_sid,
                legal_effective_date=pd.Timestamp(action.legal_effective_date),
                apply_session=date,
            )
            source_actual_shares = source_units * source_factor

            cash_per_share = float(action.cash_per_source_share)
            if cash_per_share > 0 and str(action.currency).upper() != "USD":
                raise ValueError(
                    f"corporate action {action.action_id} uses unsupported cash "
                    f"currency {action.currency!r}; the engine base currency is USD"
                )
            cash_received = source_actual_shares * cash_per_share
            cash += cash_received
            updated = updated.drop(source_sid)

            stock_ratio = float(action.target_shares_per_source_share)
            target_actual_shares = source_actual_shares * stock_ratio
            target_factor = np.nan
            target_units = 0.0
            if stock_ratio > 0:
                if target_sid is None:  # guarded by ledger validation
                    raise ValueError(f"corporate action {action.action_id} has no target_sid")
                target_factor = self._target_open_adjustment_factor(
                    target_sid=target_sid, apply_session=date
                )
                target_units = target_actual_shares / target_factor
                existing_target_units = (
                    float(updated.loc[target_sid]) if target_sid in updated.index else 0.0
                )
                updated.loc[target_sid] = existing_target_units + target_units
                if np.isclose(float(updated.loc[target_sid]), 0.0, atol=1e-15):
                    updated = updated.drop(target_sid)

            audit_rows.append(
                {
                    **base,
                    "status": "applied",
                    "source_factor_date": source_factor_date,
                    "source_adjusted_units": source_units,
                    "source_adjustment_factor": source_factor,
                    "source_actual_shares": source_actual_shares,
                    "cash_received": cash_received,
                    "target_actual_shares": target_actual_shares,
                    "target_adjustment_factor": target_factor,
                    "target_adjusted_units": target_units,
                    "cash_balance_after": cash,
                }
            )

        return updated.astype(float), float(cash), audit_rows

    def _source_adjustment_factor(
        self,
        *,
        source_sid: str,
        legal_effective_date: pd.Timestamp,
        apply_session: pd.Timestamp,
    ) -> tuple[pd.Timestamp, float]:
        try:
            history = self.prices.xs(source_sid, level="sid")
        except KeyError as error:
            raise MissingCorporateActionPriceError(
                f"no price history for corporate-action source {source_sid}"
            ) from error
        # ``apply_session`` is pre-open, so even when the legal date equals the
        # apply date no same-session close may leak into the conversion factor.
        eligible = history.loc[
            (history.index <= legal_effective_date)
            & (history.index < apply_session),
            ["tr_close", "raw_close"],
        ].apply(pd.to_numeric, errors="coerce")
        valid = (
            np.isfinite(eligible["tr_close"])
            & np.isfinite(eligible["raw_close"])
            & eligible["tr_close"].gt(0)
            & eligible["raw_close"].gt(0)
        )
        if not valid.any():
            raise MissingCorporateActionPriceError(
                f"no valid pre-event raw/tr close pair for {source_sid} on or "
                f"before {legal_effective_date.date()}"
            )
        factor_date = pd.Timestamp(eligible.index[valid][-1])
        row = eligible.loc[factor_date]
        factor = float(row["tr_close"] / row["raw_close"])
        if not np.isfinite(factor) or factor <= 0:
            raise MissingCorporateActionPriceError(
                f"invalid source adjustment factor for {source_sid} on "
                f"{factor_date.date()}"
            )
        return factor_date, factor

    def _target_open_adjustment_factor(
        self, *, target_sid: str, apply_session: pd.Timestamp
    ) -> float:
        try:
            row = self.prices.loc[(apply_session, target_sid)]
        except KeyError as error:
            raise MissingCorporateActionPriceError(
                f"{apply_session.date()} target open missing for corporate-action "
                f"security {target_sid}"
            ) from error
        tr_open = pd.to_numeric(pd.Series([row["tr_open"]]), errors="coerce").iloc[0]
        raw_open = pd.to_numeric(pd.Series([row["raw_open"]]), errors="coerce").iloc[0]
        if (
            not np.isfinite(tr_open)
            or not np.isfinite(raw_open)
            or tr_open <= 0
            or raw_open <= 0
        ):
            raise MissingCorporateActionPriceError(
                f"{apply_session.date()} raw/tr target open missing or invalid for "
                f"{target_sid}"
            )
        factor = float(tr_open / raw_open)
        if not np.isfinite(factor) or factor <= 0:
            raise MissingCorporateActionPriceError(
                f"invalid target adjustment factor for {target_sid} on "
                f"{apply_session.date()}"
            )
        return factor


def run_cost_scenarios(
    backtester: BaselineBacktester,
    *,
    signal: MomentumDefinition | str,
    top_n: int,
    frequency: RebalanceFrequency,
    cost_bps: tuple[float, ...] = (0.0, 5.0, 10.0, 20.0),
) -> dict[float, BacktestResult]:
    """Run reporting cost assumptions without counting them as new strategies."""

    return {
        float(cost): backtester.run(
            signal=signal,
            top_n=top_n,
            frequency=frequency,
            cost_bps=float(cost),
        )
        for cost in cost_bps
    }
