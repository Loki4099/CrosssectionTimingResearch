from __future__ import annotations

import unittest

import numpy as np
import pandas as pd
import pandas.testing as pdt

from momentum_reversal.factors.cross_sectional_market import (
    FACTOR_IDS,
    OUTPUT_COLUMNS,
    materialize_cross_sectional_market_factors,
)


def _calendar(sessions: pd.DatetimeIndex) -> pd.DataFrame:
    month = pd.Series(sessions.to_period("M"))
    week = pd.Series(sessions.to_period("W-FRI"))
    return pd.DataFrame(
        {
            "session_date": sessions,
            "month_last_session": ~month.duplicated(keep="last"),
            "week_last_session": ~week.duplicated(keep="last"),
            "next_session": pd.Series(sessions).shift(-1),
        }
    )


def _membership(sessions: pd.DatetimeIndex, sids: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sid": sids,
            "effective_from": [sessions[0]] * len(sids),
            "effective_to": [pd.NaT] * len(sids),
        }
    )


def _long_prices(
    sessions: pd.DatetimeIndex,
    closes: dict[str, np.ndarray],
    *,
    raw_closes: dict[str, np.ndarray] | None = None,
    volumes: dict[str, np.ndarray] | None = None,
    splits: dict[str, np.ndarray] | None = None,
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for position, (sid, tr_close) in enumerate(closes.items()):
        n = len(sessions)
        rows.append(
            pd.DataFrame(
                {
                    "date": sessions,
                    "sid": sid,
                    "tr_close": tr_close,
                    "raw_close": (
                        raw_closes[sid] if raw_closes is not None else tr_close
                    ),
                    "volume": (
                        volumes[sid]
                        if volumes is not None
                        else 1_000_000.0
                        + 10_000.0 * np.sin(np.arange(n) / (7.0 + position))
                    ),
                    "stock_splits": (
                        splits[sid] if splits is not None else np.zeros(n)
                    ),
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def _rich_fixture() -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.Timestamp,
]:
    sessions = pd.bdate_range("2014-01-02", periods=1_400)
    x = np.arange(len(sessions), dtype=float)
    market_log_return = (
        0.00025 + 0.0045 * np.sin(x / 11.0) + 0.0025 * np.cos(x / 7.0)
    )
    benchmark_close = 100.0 * np.exp(np.cumsum(market_log_return))
    stock_log_returns = {
        "A": 0.55 * market_log_return + 0.0025 * np.sin(x / 3.7),
        "B": 1.45 * market_log_return + 0.0030 * np.cos(x / 5.3),
        "C": (
            0.90 * market_log_return
            + 0.0020 * np.sin(x / 8.2)
            + 0.0010 * np.cos(x / 4.1)
        ),
    }
    closes = {
        sid: 50.0 * np.exp(np.cumsum(values))
        for sid, values in stock_log_returns.items()
    }
    prices = _long_prices(sessions, closes)
    benchmark = pd.DataFrame(
        {"date": sessions, "benchmark_tr_close": benchmark_close}
    )
    calendar = _calendar(sessions)
    membership = _membership(sessions, tuple(closes))
    signal = pd.Timestamp(
        calendar.loc[calendar["month_last_session"], "session_date"].iloc[-1]
    )
    return prices, benchmark, calendar, membership, signal


def _check_all_registered_factors_have_a_unique_auditable_long_key() -> None:
    prices, benchmark, calendar, membership, signal = _rich_fixture()

    result = materialize_cross_sectional_market_factors(
        prices,
        benchmark,
        calendar,
        membership,
        [signal],
        volume_qa_passed=True,
    )

    assert tuple(result.columns) == OUTPUT_COLUMNS
    assert set(result["factor_id"]) == set(FACTOR_IDS)
    assert len(result) == len(FACTOR_IDS) * 3
    assert not result.duplicated(["signal_date", "sid", "factor_id"]).any()
    assert result["eligible"].all()
    assert result["raw_value"].notna().all()
    assert result["score"].notna().all()
    assert result["missing_reason"].isna().all()

    reversal = result.loc[result["factor_id"] == "XS007_ST_REV_21"]
    np.testing.assert_allclose(reversal["score"], -reversal["raw_value"])
    maximum = result.loc[result["factor_id"] == "XS015_MAX_21"]
    np.testing.assert_allclose(maximum["score"], -maximum["raw_value"])
    beta = result.loc[result["factor_id"] == "XS013_LOW_BETA_FP"]
    np.testing.assert_allclose(beta["score"], -(0.6 * beta["raw_value"] + 0.4))
    beta_scores = beta.set_index("sid")["score"]
    assert beta_scores["A"] > beta_scores["B"]


def _check_future_prices_benchmark_volume_and_split_do_not_change_past_signal() -> None:
    prices, benchmark, calendar, membership, final_signal = _rich_fixture()
    month_ends = calendar.loc[calendar["month_last_session"], "session_date"]
    signal = pd.Timestamp(month_ends.iloc[-7])
    factor_ids = (
        "XS001_MOM_255_0",
        "XS004_HIGH_52W",
        "XS008_SAME_MONTH_5Y",
        "XS013_LOW_BETA_FP",
        "XS018_AMIHUD_252",
        "XS019_PRICE_DELAY_52W",
        "XS020_VOLUME_SHOCK_50D",
    )
    before = materialize_cross_sectional_market_factors(
        prices,
        benchmark,
        calendar,
        membership,
        [signal],
        factor_ids=factor_ids,
        volume_qa_passed=True,
    )

    changed_prices = prices.copy()
    future = changed_prices["date"] > signal
    changed_prices.loc[future, "tr_close"] *= 17.0
    changed_prices.loc[future, "raw_close"] *= 0.03
    changed_prices.loc[future, "volume"] *= 101.0
    split_row = changed_prices.index[future][0]
    changed_prices.loc[split_row, "stock_splits"] = 10.0
    changed_benchmark = benchmark.copy()
    changed_benchmark.loc[changed_benchmark["date"] > signal, "benchmark_tr_close"] *= 9.0

    after = materialize_cross_sectional_market_factors(
        changed_prices,
        changed_benchmark,
        calendar,
        membership,
        [signal],
        factor_ids=factor_ids,
        volume_qa_passed=True,
    )

    assert final_signal > signal
    pdt.assert_frame_equal(before, after)


def _check_high_52w_adjusts_splits_but_not_cash_dividends() -> None:
    sessions = pd.bdate_range("2020-01-02", periods=300)
    split_at = 270
    dividend_at = 280
    tr_closes = {
        "SPLIT": np.full(len(sessions), 100.0),
        "DIV": np.full(len(sessions), 100.0),
        "UNKNOWN": np.full(len(sessions), 100.0),
    }
    raw_closes = {
        "SPLIT": np.r_[
            np.full(split_at, 100.0), np.full(len(sessions) - split_at, 50.0)
        ],
        "DIV": np.r_[
            np.full(dividend_at, 100.0),
            np.full(len(sessions) - dividend_at, 95.0),
        ],
        "UNKNOWN": np.full(len(sessions), 100.0),
    }
    split_events = {
        "SPLIT": np.zeros(len(sessions)),
        "DIV": np.zeros(len(sessions)),
        "UNKNOWN": np.zeros(len(sessions)),
    }
    split_events["SPLIT"][split_at] = 2.0
    split_events["UNKNOWN"][split_at] = np.nan
    prices = _long_prices(
        sessions,
        tr_closes,
        raw_closes=raw_closes,
        splits=split_events,
    )
    calendar = _calendar(sessions)
    signal = pd.Timestamp(
        calendar.loc[calendar["month_last_session"], "session_date"].iloc[-1]
    )
    benchmark = pd.DataFrame(
        {"date": sessions, "benchmark_tr_close": np.full(len(sessions), 100.0)}
    )

    result = materialize_cross_sectional_market_factors(
        prices,
        benchmark,
        calendar,
        _membership(sessions, ("SPLIT", "DIV", "UNKNOWN")),
        [signal],
        factor_ids=["XS004_HIGH_52W"],
    ).set_index("sid")

    assert result.loc["SPLIT", "eligible"]
    assert result.loc["SPLIT", "raw_value"] == 1.0
    assert np.isclose(result.loc["DIV", "raw_value"], 0.95)
    assert not result.loc["UNKNOWN", "eligible"]
    assert pd.isna(result.loc["UNKNOWN", "raw_value"])


def _check_exact_minimum_history_and_factor_direction() -> None:
    sessions = pd.bdate_range("2021-01-04", periods=256)
    x = np.arange(len(sessions), dtype=float)
    closes = {
        "UP": 100.0 * np.power(1.001, x),
        "DOWN": 100.0 * np.power(0.999, x),
    }
    prices = _long_prices(sessions, closes)
    calendar = _calendar(sessions)
    calendar.loc[
        calendar["session_date"].isin([sessions[-2], sessions[-1]]),
        "month_last_session",
    ] = True
    benchmark = pd.DataFrame(
        {"date": sessions, "benchmark_tr_close": 100.0 * np.power(1.0002, x)}
    )

    result = materialize_cross_sectional_market_factors(
        prices,
        benchmark,
        calendar,
        _membership(sessions, ("UP", "DOWN")),
        [sessions[-2], sessions[-1]],
        factor_ids=["XS001_MOM_255_0", "XS007_ST_REV_21"],
    )

    momentum = result.loc[result["factor_id"] == "XS001_MOM_255_0"]
    early = momentum.loc[momentum["signal_date"] == sessions[-2]]
    exact = momentum.loc[momentum["signal_date"] == sessions[-1]].set_index("sid")
    assert not early["eligible"].any()
    assert early["raw_value"].isna().all()
    assert early["missing_reason"].notna().all()
    assert exact["eligible"].all()
    assert exact.loc["UP", "score"] > exact.loc["DOWN", "score"]

    reversal = result.loc[
        (result["factor_id"] == "XS007_ST_REV_21")
        & (result["signal_date"] == sessions[-1])
    ].set_index("sid")
    assert reversal.loc["DOWN", "score"] > reversal.loc["UP", "score"]


def _check_same_month_uses_the_coming_holding_month_without_lookahead() -> None:
    sessions = pd.date_range("2018-01-31", "2024-01-31", freq="BME")
    monthly_returns = np.zeros(len(sessions), dtype=float)
    february_returns = {
        2019: 0.10,
        2020: 0.20,
        2021: 0.30,
        2022: 0.40,
        2023: 0.50,
    }
    for year, value in february_returns.items():
        monthly_returns[
            (sessions.year == year) & (sessions.month == 2)
        ] = value
    close = 100.0 * np.cumprod(1.0 + monthly_returns)
    prices = _long_prices(sessions, {"A": close})
    calendar = pd.DataFrame(
        {
            "session_date": sessions,
            "month_last_session": True,
            "week_last_session": True,
        }
    )
    benchmark = pd.DataFrame(
        {"date": sessions, "benchmark_tr_close": np.full(len(sessions), 100.0)}
    )

    result = materialize_cross_sectional_market_factors(
        prices,
        benchmark,
        calendar,
        _membership(sessions, ("A",)),
        [sessions[-1]],
        factor_ids=["XS008_SAME_MONTH_5Y"],
    ).iloc[0]

    assert result["eligible"]
    assert np.isclose(result["raw_value"], 0.30)


def _check_same_month_weekly_carries_the_target_month_score() -> None:
    prices, benchmark, calendar, membership, _ = _rich_fixture()
    sessions = pd.DatetimeIndex(calendar["session_date"])
    session_months = sessions.to_period("M")
    next_session_by_date = calendar.set_index("session_date")["next_session"]
    target_month = None
    weekly_signals = pd.DatetimeIndex([])
    for candidate in reversed(session_months.unique()[:-1]):
        target_rows = calendar.loc[session_months == candidate]
        candidate_signals = pd.DatetimeIndex(
            target_rows.loc[target_rows["week_last_session"], "session_date"]
        )
        candidate_signals = candidate_signals[
            pd.to_datetime(next_session_by_date.reindex(candidate_signals)).dt.to_period("M")
            == candidate
        ]
        if len(candidate_signals) >= 2:
            target_month = candidate
            weekly_signals = candidate_signals
            break
    assert target_month is not None
    formation = pd.Timestamp(
        calendar.loc[
            (pd.DatetimeIndex(calendar["session_date"]).to_period("M")
             == target_month - 1)
            & calendar["month_last_session"],
            "session_date",
        ].iloc[0]
    )
    monthly = materialize_cross_sectional_market_factors(
        prices,
        benchmark,
        calendar,
        membership,
        [formation],
        factor_ids=["XS008_SAME_MONTH_5Y"],
    ).sort_values("sid")
    weekly = materialize_cross_sectional_market_factors(
        prices,
        benchmark,
        calendar,
        membership,
        weekly_signals,
        factor_ids=["XS008_SAME_MONTH_5Y"],
        allowed_signal_frequencies=("weekly", "monthly"),
    )
    expected = monthly.set_index("sid")["score"].sort_index()
    for _, group in weekly.groupby("signal_date", sort=True):
        pdt.assert_series_equal(
            group.set_index("sid")["score"].sort_index(),
            expected,
            check_names=False,
        )


def _check_volume_factors_require_explicit_qa_and_never_fill_missing_with_zero() -> None:
    sessions = pd.bdate_range("2022-01-03", periods=60)
    x = np.arange(len(sessions), dtype=float)
    prices = _long_prices(
        sessions,
        {"A": 100.0 * np.power(1.0005, x)},
        volumes={"A": 1_000.0 + x},
    )
    calendar = _calendar(sessions)
    signal = pd.Timestamp(
        calendar.loc[calendar["month_last_session"], "session_date"].iloc[-1]
    )
    benchmark = pd.DataFrame(
        {"date": sessions, "benchmark_tr_close": 100.0 * np.power(1.0002, x)}
    )
    membership = _membership(sessions, ("A",))

    blocked = materialize_cross_sectional_market_factors(
        prices,
        benchmark,
        calendar,
        membership,
        [signal],
        factor_ids=["XS020_VOLUME_SHOCK_50D"],
    ).iloc[0]
    assert not blocked["eligible"]
    assert pd.isna(blocked["raw_value"])
    assert pd.isna(blocked["score"])
    assert blocked["missing_reason"] == "volume_qa_not_passed"

    allowed = materialize_cross_sectional_market_factors(
        prices,
        benchmark,
        calendar,
        membership,
        [signal],
        factor_ids=["XS020_VOLUME_SHOCK_50D"],
        volume_qa_passed=True,
    ).iloc[0]
    assert allowed["eligible"]
    assert allowed["raw_value"] == 1.0
    assert pd.isna(allowed["missing_reason"])


def _check_volume_shock_is_invariant_to_a_pure_split_scale_change() -> None:
    sessions = pd.bdate_range("2022-01-03", periods=70)
    split_at = 45
    volume = np.full(len(sessions), 1_000.0)
    volume[split_at:] = 2_000.0
    close = np.full(len(sessions), 100.0)
    close[split_at:] = 50.0
    splits = np.zeros(len(sessions))
    splits[split_at] = 2.0
    prices = _long_prices(
        sessions,
        {"A": close},
        volumes={"A": volume},
        splits={"A": splits},
    )
    calendar = _calendar(sessions)
    signal = pd.Timestamp(
        calendar.loc[calendar["month_last_session"], "session_date"].iloc[-1]
    )
    benchmark = pd.DataFrame(
        {"date": sessions, "benchmark_tr_close": np.full(len(sessions), 100.0)}
    )
    result = materialize_cross_sectional_market_factors(
        prices,
        benchmark,
        calendar,
        _membership(sessions, ("A",)),
        [signal],
        factor_ids=["XS020_VOLUME_SHOCK_50D"],
        volume_qa_passed=True,
    ).iloc[0]
    assert result["eligible"]
    assert np.isclose(result["raw_value"], 0.51)


class CrossSectionalMarketTests(unittest.TestCase):
    def test_all_registered_factors_have_unique_auditable_keys(self) -> None:
        _check_all_registered_factors_have_a_unique_auditable_long_key()

    def test_future_inputs_do_not_change_a_past_signal(self) -> None:
        _check_future_prices_benchmark_volume_and_split_do_not_change_past_signal()

    def test_high_52w_split_and_dividend_treatment(self) -> None:
        _check_high_52w_adjusts_splits_but_not_cash_dividends()

    def test_exact_minimum_history_and_direction(self) -> None:
        _check_exact_minimum_history_and_factor_direction()

    def test_same_month_targets_the_coming_holding_month(self) -> None:
        _check_same_month_uses_the_coming_holding_month_without_lookahead()

    def test_same_month_weekly_carries_target_month_score(self) -> None:
        _check_same_month_weekly_carries_the_target_month_score()

    def test_volume_factors_require_explicit_qa(self) -> None:
        _check_volume_factors_require_explicit_qa_and_never_fill_missing_with_zero()

    def test_volume_shock_uses_causal_split_adjustment(self) -> None:
        _check_volume_shock_is_invariant_to_a_pure_split_scale_change()


if __name__ == "__main__":
    unittest.main()
