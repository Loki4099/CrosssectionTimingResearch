"""Round 3 asymmetric volatility-defense and price-recovery experiment."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata as importlib_metadata
import json
from pathlib import Path
import subprocess
import tomllib
from typing import Any

import numpy as np
import pandas as pd

from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.pipelines.round2_models import (
    _constant_schedule,
    _solve_static_weight,
    replay_weekly_spy_cash,
)
from momentum_reversal.pipelines.round2_protocol import sha256_file


FULL_ARMED = "FULL_ARMED"
DEFENSE = "DEFENSE"
RECOVERY_UNARMED = "RECOVERY_UNARMED"
LOCKBOX_SIGNAL = pd.Timestamp("2021-12-31")
DEVELOPMENT_END = pd.Timestamp("2021-12-31")
MAIN_COST_BPS = 10


@dataclass(frozen=True, slots=True)
class R3AResult:
    bundle_dir: Path
    manifest_path: Path
    status: str
    weekly_rows: int
    nav_rows: int


def compute_daily_reentry_indicators(market_daily: pd.DataFrame) -> pd.DataFrame:
    """Compute causal RV21/q75 and two-close SMA21 recovery state."""
    required = {"session_date", "tr_close"}
    if not required.issubset(market_daily.columns):
        raise DataQualityError(f"missing R3A market columns: {sorted(required - set(market_daily))}")
    frame = market_daily[["session_date", "tr_close"]].copy()
    frame["session_date"] = pd.to_datetime(frame["session_date"]).dt.normalize()
    frame = frame.sort_values("session_date", kind="mergesort").reset_index(drop=True)
    if frame["session_date"].duplicated().any():
        raise DataQualityError("duplicate R3A market session")
    close = pd.to_numeric(frame["tr_close"], errors="coerce")
    if not np.isfinite(close).all() or (close <= 0).any():
        raise DataQualityError("invalid R3A total-return close")
    returns = close.pct_change(fill_method=None)
    rv21 = returns.rolling(21, min_periods=21).std(ddof=1) * np.sqrt(252.0)
    q75 = rv21.shift(1).rolling(756, min_periods=756).quantile(
        0.75, interpolation="linear"
    )
    sma21 = close.rolling(21, min_periods=21).mean()
    above = close > sma21
    recovery = above & above.shift(1, fill_value=False)
    frame["spy_return"] = returns
    frame["spy_rv21"] = rv21
    frame["lagged_q75"] = q75
    frame["sma21"] = sma21
    frame["above_sma21"] = above
    frame["two_close_recovery"] = recovery
    frame["defense_entry_signal"] = rv21 > q75
    return frame


def build_weekly_reentry_states(
    decision_calendar: pd.DataFrame,
    daily_indicators: pd.DataFrame,
    *,
    first_execution_year: int = 2005,
    last_execution_year: int = 2021,
) -> pd.DataFrame:
    """Apply the single frozen FULL/DEFENSE/RECOVERY state machine."""
    calendar = decision_calendar.copy()
    for column in ("signal_session", "execution_session"):
        calendar[column] = pd.to_datetime(calendar[column]).dt.normalize()
    calendar = calendar.loc[
        calendar["execution_session"].dt.year.between(
            first_execution_year, last_execution_year
        )
        & (calendar["signal_session"] < LOCKBOX_SIGNAL)
    ].sort_values("signal_session", kind="mergesort")
    if calendar.empty or calendar["signal_session"].duplicated().any():
        raise DataQualityError("invalid R3A development calendar")

    indicators = daily_indicators.copy()
    indicators["session_date"] = pd.to_datetime(
        indicators["session_date"]
    ).dt.normalize()
    signal_view = indicators.rename(columns={"session_date": "signal_session"})
    fields = [
        "signal_session",
        "spy_rv21",
        "lagged_q75",
        "sma21",
        "above_sma21",
        "two_close_recovery",
        "defense_entry_signal",
    ]
    weekly = calendar.merge(
        signal_view[fields], on="signal_session", how="left", validate="one_to_one"
    )
    if weekly[["spy_rv21", "lagged_q75", "sma21"]].isna().any().any():
        raise DataQualityError("R3A development signal state is incomplete")
    if not np.isfinite(weekly[["spy_rv21", "lagged_q75", "sma21"]]).all().all():
        raise DataQualityError("R3A development signal state is non-finite")
    if (weekly[["spy_rv21", "lagged_q75", "sma21"]] <= 0).any().any():
        raise DataQualityError("R3A development signal state is non-positive")

    daily = indicators.set_index("session_date").sort_index()
    state = FULL_ARMED
    prior_signal: pd.Timestamp | None = None
    seen_below_sma = False
    defense_entry_date: pd.Timestamp | None = None
    records: list[dict[str, Any]] = []
    for row in weekly.itertuples(index=False):
        signal = pd.Timestamp(row.signal_session)
        pre_state = state
        event = "hold"
        false_alarm = False
        if state == DEFENSE:
            start = prior_signal + pd.Timedelta(days=1) if prior_signal is not None else signal
            episode_slice = daily.loc[start:signal]
            if (~episode_slice["above_sma21"].fillna(False)).any():
                seen_below_sma = True

        if state == FULL_ARMED:
            if bool(row.defense_entry_signal):
                state = DEFENSE
                event = "enter_defense"
                defense_entry_date = signal
                seen_below_sma = not bool(row.above_sma21)
                target = 0.5
            else:
                target = 1.0
        elif state == DEFENSE:
            if bool(row.two_close_recovery):
                state = RECOVERY_UNARMED
                event = "exit_to_recovery"
                false_alarm = not seen_below_sma
                target = 1.0
            else:
                target = 0.5
        elif state == RECOVERY_UNARMED:
            target = 1.0
            if float(row.spy_rv21) <= float(row.lagged_q75):
                state = FULL_ARMED
                event = "rearm"
                defense_entry_date = None
                seen_below_sma = False
        else:  # pragma: no cover - defensive invariant
            raise DataQualityError(f"unknown R3A state: {state}")

        records.append(
            {
                **row._asdict(),
                "pre_state": pre_state,
                "post_state": state,
                "state_event": event,
                "asymmetric_target_spy_weight": target,
                "symmetric_target_spy_weight": (
                    0.5 if bool(row.defense_entry_signal) else 1.0
                ),
                "seen_below_sma_in_episode": seen_below_sma,
                "defense_entry_signal_session": defense_entry_date,
                "vol_only_false_alarm_exit": false_alarm,
            }
        )
        prior_signal = signal

    result = pd.DataFrame(records)
    if result["signal_session"].max() >= LOCKBOX_SIGNAL:
        raise DataQualityError("R3A state crossed the lockbox firewall")
    if not result["asymmetric_target_spy_weight"].isin([0.5, 1.0]).all():
        raise DataQualityError("R3A emitted an unregistered allocation")
    return result


def build_r3a_development_bundle(
    *,
    project_root: str | Path,
    r2a_candidate_dir: str | Path,
    output_root: str | Path,
    run_id: str,
) -> R3AResult:
    root = Path(project_root).resolve()
    r2a = Path(r2a_candidate_dir).resolve()
    bundle = (
        Path(output_root).resolve()
        / "experiments"
        / "round3"
        / "R3A_ASYMMETRIC_REENTRY"
        / "runs"
        / run_id
    )
    if bundle.exists():
        raise FileExistsError(f"immutable R3A bundle already exists: {bundle}")
    prereg = _verify_preregistration(root, r2a)
    provenance = _build_provenance(root)

    curated = r2a / "curated"
    market = pd.read_parquet(curated / "market_daily.parquet")
    risk_free = pd.read_parquet(curated / "risk_free_daily.parquet")
    calendar = pd.read_parquet(curated / "decision_calendar.parquet")
    indicators = compute_daily_reentry_indicators(market)
    weekly = build_weekly_reentry_states(calendar, indicators)

    schedules = {
        "ALWAYS_SPY": weekly[["execution_session"]].assign(target_spy_weight=1.0),
        "SYMMETRIC_RV21": weekly[["execution_session"]].assign(
            target_spy_weight=weekly["symmetric_target_spy_weight"].to_numpy()
        ),
        "ASYMMETRIC_REENTRY": weekly[["execution_session"]].assign(
            target_spy_weight=weekly["asymmetric_target_spy_weight"].to_numpy()
        ),
    }
    nav_parts: list[pd.DataFrame] = []
    summary_parts: list[dict[str, Any]] = []
    main_nav: dict[str, pd.DataFrame] = {}
    for cost_bps in (0, 5, 10, 20):
        for strategy, schedule in schedules.items():
            frame = replay_weekly_spy_cash(
                schedule, market, risk_free, cost_bps=cost_bps
            )
            frame.insert(0, "cost_bps", cost_bps)
            frame.insert(0, "strategy", strategy)
            nav_parts.append(frame)
            summary_parts.append(_performance_summary(strategy, cost_bps, frame, market))
            if cost_bps == MAIN_COST_BPS:
                main_nav[strategy] = frame.drop(columns=["strategy", "cost_bps"])

    asymmetric = main_nav["ASYMMETRIC_REENTRY"]
    avg_weight = _solve_static_weight(
        asymmetric,
        weekly["execution_session"],
        market,
        risk_free,
        objective="average_exposure",
        cost_bps=MAIN_COST_BPS,
    )
    vol_weight = _solve_static_weight(
        asymmetric,
        weekly["execution_session"],
        market,
        risk_free,
        objective="excess_volatility",
        cost_bps=MAIN_COST_BPS,
    )
    controls_nav: dict[str, pd.DataFrame] = {}
    for name, weight in (
        ("MATCHED_AVERAGE", avg_weight),
        ("MATCHED_SAME_VOL", vol_weight),
    ):
        frame = replay_weekly_spy_cash(
            _constant_schedule(weekly["execution_session"], weight),
            market,
            risk_free,
            cost_bps=MAIN_COST_BPS,
        )
        controls_nav[name] = frame

    controls = _control_summary(main_nav, controls_nav, avg_weight, vol_weight)
    mechanism = _mechanism_summary(main_nav, controls_nav, market)
    gate = _evaluate_gate(main_nav, controls_nav, summary_parts, mechanism)
    status = (
        "completed_reentry_candidate"
        if gate["all_development_gates_pass"]
        else "completed_no_reentry_candidate"
    )

    nav = pd.concat(nav_parts, ignore_index=True)
    control_nav = pd.concat(
        [frame.assign(control=name) for name, frame in controls_nav.items()],
        ignore_index=True,
    )
    summary = pd.DataFrame(summary_parts).sort_values(
        ["strategy", "cost_bps"], kind="mergesort"
    )
    mechanism_frame = pd.DataFrame(mechanism["rows"])

    bundle.mkdir(parents=True, exist_ok=False)
    frames: tuple[tuple[str, pd.DataFrame], ...] = (
        ("weekly_states.parquet", weekly),
        ("nav.parquet", nav),
        ("control_nav.parquet", control_nav),
        ("summary.csv", summary),
        ("controls.csv", pd.DataFrame([controls])),
        ("mechanism.csv", mechanism_frame),
    )
    written: list[Path] = []
    for name, frame in frames:
        path = bundle / name
        if path.suffix == ".parquet":
            frame.to_parquet(path, index=False, compression="zstd")
        else:
            frame.to_csv(path, index=False, lineterminator="\n")
        written.append(path)
    gate_path = bundle / "gate.json"
    gate_path.write_text(
        json.dumps(gate, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    written.append(gate_path)
    config_path = bundle / "config_resolved.toml"
    config_path.write_bytes(
        (root / "config" / "experiments" / "round3" / "program.toml").read_bytes()
    )
    written.append(config_path)

    manifest = {
        "schema_version": 1,
        "program_id": "asymmetric_defense_reentry_round3_v1",
        "batch_id": "R3A_ASYMMETRIC_REENTRY",
        "run_id": run_id,
        "stage": "development",
        "status": status,
        "formal_eligible": False,
        "lockbox_materialized": False,
        "lockbox_authorized": False,
        "mom255_transfer_authorized": False,
        "counts": {
            "weekly_states": len(weekly),
            "nav_rows": len(nav),
            "control_nav_rows": len(control_nav),
            "summary_rows": len(summary),
            "mechanism_rows": len(mechanism_frame),
            "defense_entries": int((weekly["state_event"] == "enter_defense").sum()),
            "recovery_exits": int((weekly["state_event"] == "exit_to_recovery").sum()),
            "rearms": int((weekly["state_event"] == "rearm").sum()),
        },
        "range": {
            "first_signal": str(weekly["signal_session"].min().date()),
            "last_signal": str(weekly["signal_session"].max().date()),
            "first_execution": str(weekly["execution_session"].min().date()),
            "last_execution": str(weekly["execution_session"].max().date()),
            "nav_end": str(nav["session_date"].max().date()),
        },
        "anchors": {
            "prereg_lock_sha256": sha256_file(
                root / "config" / "experiments" / "round3" / "PREREG_LOCK.json"
            ),
            "r2a_manifest_sha256": sha256_file(r2a / "manifest.json"),
            "r2a_tree_sha256": _tree_sha256(r2a),
            "round2_folds_sha256": sha256_file(
                root / "config" / "experiments" / "round2" / "folds.json"
            ),
        },
        "gate": {k: v for k, v in gate.items() if k != "diagnostics"},
        "preregistration": prereg,
        "build_provenance": provenance,
        "files": [_file_record(path, bundle) for path in sorted(written)],
    }
    manifest_path = bundle / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return R3AResult(bundle, manifest_path, status, len(weekly), len(nav))


def _performance_summary(
    strategy: str, cost_bps: int, nav: pd.DataFrame, market: pd.DataFrame
) -> dict[str, Any]:
    days = max((nav["session_date"].iloc[-1] - nav["session_date"].iloc[0]).days, 1)
    years = days / 365.2425
    terminal = float(nav["nav"].iloc[-1])
    cagr = terminal ** (1.0 / years) - 1.0
    excess = nav["daily_return"].to_numpy(float) - nav["rf_return"].to_numpy(float)
    sharpe = float(np.mean(excess) / np.std(excess, ddof=1) * np.sqrt(252.0))
    wealth = np.r_[1.0, nav["nav"].to_numpy(float)]
    mdd = float(np.min(wealth / np.maximum.accumulate(wealth) - 1.0))
    vol = float(np.std(nav["daily_return"], ddof=1) * np.sqrt(252.0))
    spy = _spy_returns_for_nav(nav, market)
    beta = float(np.cov(nav["daily_return"], spy, ddof=1)[0, 1] / np.var(spy, ddof=1))
    return {
        "strategy": strategy,
        "cost_bps": cost_bps,
        "terminal_nav": terminal,
        "cagr": cagr,
        "sharpe_excess_rf": sharpe,
        "max_drawdown": mdd,
        "annualized_volatility": vol,
        "beta_spy": beta,
        "annualized_l1_turnover": float(nav["turnover"].sum() / years),
        "total_cost": float(nav["cost"].sum()),
        "average_close_spy_weight": float(nav["close_spy_weight"].mean()),
    }


def _control_summary(
    main_nav: dict[str, pd.DataFrame],
    controls_nav: dict[str, pd.DataFrame],
    avg_weight: float,
    vol_weight: float,
) -> dict[str, Any]:
    asym = main_nav["ASYMMETRIC_REENTRY"]
    sym = main_nav["SYMMETRIC_RV21"]
    avg = controls_nav["MATCHED_AVERAGE"]
    same_vol = controls_nav["MATCHED_SAME_VOL"]
    return {
        "matched_average_weight": avg_weight,
        "matched_same_vol_weight": vol_weight,
        "asymmetric_terminal_nav": float(asym["nav"].iloc[-1]),
        "symmetric_terminal_nav": float(sym["nav"].iloc[-1]),
        "matched_average_terminal_nav": float(avg["nav"].iloc[-1]),
        "matched_same_vol_terminal_nav": float(same_vol["nav"].iloc[-1]),
        "active_vs_symmetric": float(asym["nav"].iloc[-1] / sym["nav"].iloc[-1] - 1),
        "timing_value_vs_average": float(asym["nav"].iloc[-1] / avg["nav"].iloc[-1] - 1),
        "timing_value_vs_same_vol": float(
            asym["nav"].iloc[-1] / same_vol["nav"].iloc[-1] - 1
        ),
    }


def _mechanism_summary(
    main_nav: dict[str, pd.DataFrame],
    controls_nav: dict[str, pd.DataFrame],
    market: pd.DataFrame,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    spy = _spy_returns_for_nav(main_nav["ALWAYS_SPY"], market)
    always = main_nav["ALWAYS_SPY"]
    always_prior_nav = np.r_[1.0, always["nav"].to_numpy(float)[:-1]]
    always_cost_drag = float(
        np.sum(always["cost"].to_numpy(float) / always_prior_nav)
    )
    for strategy in ("SYMMETRIC_RV21", "ASYMMETRIC_REENTRY"):
        frame = main_nav[strategy]
        x = spy - frame["rf_return"].to_numpy(float)
        shortfall = 1.0 - frame["close_spy_weight"].to_numpy(float)
        benefit = float(np.sum(shortfall * np.maximum(-x, 0.0)))
        missed = float(np.sum(shortfall * np.maximum(x, 0.0)))
        prior_nav = np.r_[1.0, frame["nav"].to_numpy(float)[:-1]]
        total_cost_drag = float(np.sum(frame["cost"].to_numpy(float) / prior_nav))
        incremental_cost_drag = total_cost_drag - always_cost_drag
        net = benefit - missed - incremental_cost_drag
        rows.append(
            {
                "scope": "full_sample",
                "strategy": strategy,
                "window": "development",
                "defense_benefit": benefit,
                "missed_upside": missed,
                "gross_timing": benefit - missed,
                "cost_drag": total_cost_drag,
                "incremental_cost_drag_vs_always_spy": incremental_cost_drag,
                "net_timing": net,
                "benefit_to_missed_ratio": benefit / missed if missed > 0 else np.inf,
            }
        )
    asym = main_nav["ASYMMETRIC_REENTRY"]
    avg = controls_nav["MATCHED_AVERAGE"]
    active = np.log1p(asym["daily_return"].to_numpy(float)) - np.log1p(
        avg["daily_return"].to_numpy(float)
    )
    dates = asym["session_date"]
    for name, start, end in (
        ("gfc", "2007-10-09", "2009-03-09"),
        ("covid_selloff", "2020-02-19", "2020-03-23"),
        ("covid_rebound", "2020-03-24", "2020-08-18"),
    ):
        mask = dates.between(start, end).to_numpy()
        rows.append(
            {
                "scope": "active_window",
                "strategy": "ASYMMETRIC_REENTRY",
                "window": name,
                "defense_benefit": np.nan,
                "missed_upside": np.nan,
                "gross_timing": float(np.expm1(active[mask].sum())),
                "cost_drag": np.nan,
                "incremental_cost_drag_vs_always_spy": np.nan,
                "net_timing": np.nan,
                "benefit_to_missed_ratio": np.nan,
            }
        )
    return {"rows": rows}


def _evaluate_gate(
    main_nav: dict[str, pd.DataFrame],
    controls_nav: dict[str, pd.DataFrame],
    summaries: list[dict[str, Any]],
    mechanism: dict[str, Any],
) -> dict[str, Any]:
    summary = pd.DataFrame(summaries)
    main = summary.loc[summary["cost_bps"] == MAIN_COST_BPS].set_index("strategy")
    asym = main_nav["ASYMMETRIC_REENTRY"]
    sym = main_nav["SYMMETRIC_RV21"]
    always = main_nav["ALWAYS_SPY"]
    avg = controls_nav["MATCHED_AVERAGE"]
    same_vol = controls_nav["MATCHED_SAME_VOL"]
    active_sym = np.log1p(asym["daily_return"].to_numpy(float)) - np.log1p(
        sym["daily_return"].to_numpy(float)
    )
    active_avg = np.log1p(asym["daily_return"].to_numpy(float)) - np.log1p(
        avg["daily_return"].to_numpy(float)
    )
    years = asym["session_date"].dt.year.to_numpy()
    annual_sym = np.array([active_sym[years == y].sum() for y in np.unique(years)])
    annual_avg = np.array([active_avg[years == y].sum() for y in np.unique(years)])
    positive = np.maximum(annual_avg, 0.0)
    concentration = float(positive.max() / positive.sum()) if positive.sum() > 0 else 1.0
    full_rows = {
        row["strategy"]: row
        for row in mechanism["rows"]
        if row["scope"] == "full_sample"
    }
    asym_mech = full_rows["ASYMMETRIC_REENTRY"]
    sym_mech = full_rows["SYMMETRIC_RV21"]
    missed_reduction = (
        1.0 - asym_mech["missed_upside"] / sym_mech["missed_upside"]
        if sym_mech["missed_upside"] > 0
        else float("nan")
    )
    gain_asym = float(main.loc["ASYMMETRIC_REENTRY", "max_drawdown"] - main.loc["ALWAYS_SPY", "max_drawdown"])
    gain_sym = float(main.loc["SYMMETRIC_RV21", "max_drawdown"] - main.loc["ALWAYS_SPY", "max_drawdown"])
    retention = gain_asym / gain_sym if gain_sym > 0 else float("nan")
    active_vs_sym = float(asym["nav"].iloc[-1] / sym["nav"].iloc[-1] - 1)
    timing_avg = float(asym["nav"].iloc[-1] / avg["nav"].iloc[-1] - 1)
    timing_vol = float(asym["nav"].iloc[-1] / same_vol["nav"].iloc[-1] - 1)
    days = max((asym["session_date"].iloc[-1] - asym["session_date"].iloc[0]).days, 1)
    cagr_delta = float(
        asym["nav"].iloc[-1] ** (365.2425 / days)
        - sym["nav"].iloc[-1] ** (365.2425 / days)
    )
    leaveout: dict[str, float] = {}
    for name, start, end in (
        ("gfc", "2007-10-09", "2009-03-09"),
        ("covid_selloff", "2020-02-19", "2020-03-23"),
    ):
        inside = asym["session_date"].between(start, end).to_numpy()
        leaveout[name] = float(np.expm1(active_avg[~inside].sum()))
    endpoint = asym["session_date"] <= pd.Timestamp("2021-06-30")
    endpoint_value = float(np.expm1(active_avg[endpoint.to_numpy()].sum()))

    crisis_ok: dict[str, bool] = {}
    for name, start, end in (
        ("gfc", "2007-10-09", "2009-03-09"),
        ("covid_selloff", "2020-02-19", "2020-03-23"),
    ):
        mask = asym["session_date"].between(start, end).to_numpy()
        asym_ret = asym.loc[mask, "daily_return"].to_numpy(float)
        always_ret = always.loc[mask, "daily_return"].to_numpy(float)
        asym_mdd = _mdd_from_returns(asym_ret)
        always_mdd = _mdd_from_returns(always_ret)
        crisis_ok[name] = not (
            float(np.min(asym_ret)) < float(np.min(always_ret)) - 1e-12
            and asym_mdd < always_mdd - 1e-12
        )

    h1 = {
        "active_vs_symmetric_positive": active_vs_sym > 0,
        "cagr_delta_vs_symmetric_positive": cagr_delta > 0,
        "missed_upside_reduction_at_least_25pct": missed_reduction >= 0.25,
        "positive_year_fraction_at_least_60pct": float(np.mean(annual_sym > 0)) >= 0.60,
    }
    ratio_pass = bool(
        asym_mech["benefit_to_missed_ratio"] > 1
        if np.isfinite(asym_mech["benefit_to_missed_ratio"])
        else asym_mech["defense_benefit"] > 0
    )
    h2 = {
        "timing_value_vs_average_positive": timing_avg > 0,
        "timing_value_vs_same_vol_nonnegative": timing_vol >= 0,
        "net_timing_positive": asym_mech["net_timing"] > 0,
        "benefit_to_missed_ratio_above_one": ratio_pass,
    }
    h3 = {
        "mdd_gain_positive": gain_asym > 0,
        "symmetric_mdd_gain_retention_at_least_75pct": (
            retention >= 0.75 if gain_sym > 0 else True
        ),
        "gfc_not_jointly_worse": crisis_ok["gfc"],
        "covid_selloff_not_jointly_worse": crisis_ok["covid_selloff"],
    }
    h4 = {
        "positive_year_concentration_at_most_50pct": concentration <= 0.50,
        "gfc_leaveout_positive": leaveout["gfc"] > 0,
        "covid_selloff_leaveout_positive": leaveout["covid_selloff"] > 0,
        "endpoint_2021_06_30_positive": endpoint_value > 0,
    }
    return {
        "H1": h1,
        "H2": h2,
        "H3": h3,
        "H4": h4,
        "h1_pass": all(h1.values()),
        "h2_pass": all(h2.values()),
        "h3_pass": all(h3.values()),
        "h4_pass": all(h4.values()),
        "all_development_gates_pass": all(
            all(section.values()) for section in (h1, h2, h3, h4)
        ),
        "lockbox_authorized": False,
        "diagnostics": {
            "active_vs_symmetric": active_vs_sym,
            "cagr_delta_vs_symmetric": cagr_delta,
            "missed_upside_reduction": missed_reduction,
            "positive_year_fraction_vs_symmetric": float(np.mean(annual_sym > 0)),
            "timing_value_vs_average": timing_avg,
            "timing_value_vs_same_vol": timing_vol,
            "mdd_gain_asymmetric": gain_asym,
            "mdd_gain_symmetric": gain_sym,
            "mdd_gain_retention": retention,
            "positive_year_concentration": concentration,
            "leaveout": leaveout,
            "endpoint_2021_06_30": endpoint_value,
        },
    }


def _spy_returns_for_nav(nav: pd.DataFrame, market: pd.DataFrame) -> np.ndarray:
    prices = market[["session_date", "tr_open", "tr_close"]].copy()
    prices["session_date"] = pd.to_datetime(prices["session_date"]).dt.normalize()
    prices = nav[["session_date"]].merge(
        prices, on="session_date", how="left", validate="one_to_one"
    )
    returns = prices["tr_close"].pct_change(fill_method=None).to_numpy(float).copy()
    returns[0] = float(prices["tr_close"].iloc[0] / prices["tr_open"].iloc[0] - 1.0)
    if not np.isfinite(returns).all():
        raise DataQualityError("R3A SPY return path is incomplete")
    return returns


def _mdd_from_returns(returns: np.ndarray) -> float:
    wealth = np.r_[1.0, np.cumprod(1.0 + np.asarray(returns, dtype=float))]
    return float(np.min(wealth / np.maximum.accumulate(wealth) - 1.0))


def _verify_preregistration(root: Path, r2a: Path) -> dict[str, Any]:
    lock_path = root / "config" / "experiments" / "round3" / "PREREG_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    for rel, expected in lock["files"].items():
        if sha256_file(root / rel) != expected:
            raise DataQualityError(f"R3A preregistration hash mismatch: {rel}")
    config_path = root / "config" / "experiments" / "round3" / "program.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if not config["authorization"]["development"]:
        raise DataQualityError("R3A development is not authorized")
    if config["authorization"]["lockbox"] or lock["authorization"]["lockbox"]:
        raise DataQualityError("R3A lockbox unexpectedly authorized")
    if sha256_file(r2a / "manifest.json") != lock["inputs"]["r2a_manifest_sha256"]:
        raise DataQualityError("R3A R2A manifest mismatch")
    tree = _tree_sha256(r2a)
    if tree != lock["inputs"]["r2a_tree_sha256"]:
        raise DataQualityError("R3A R2A tree mismatch")
    manifest = json.loads((r2a / "manifest.json").read_text(encoding="utf-8"))
    for record in manifest["files"]:
        path = r2a / record["path"]
        if path.stat().st_size != record["size_bytes"] or sha256_file(path) != record["sha256"]:
            raise DataQualityError(f"R3A R2A member mismatch: {record['path']}")
    return lock


def _tree_sha256(root: Path) -> str:
    members = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        members.append(
            f"{path.relative_to(root).as_posix()}|{path.stat().st_size}|{sha256_file(path)}"
        )
    return hashlib.sha256("\n".join(members).encode()).hexdigest()


def _build_provenance(root: Path) -> dict[str, Any]:
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    if status:
        raise DataQualityError("R3A bundle requires a clean workspace")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    return {
        "git_commit": commit,
        "workspace_dirty": False,
        "dependency_versions": {
            name: importlib_metadata.version(name)
            for name in ("numpy", "pandas", "pyarrow", "scipy")
        },
        "code_file_sha256": {
            rel: sha256_file(root / rel)
            for rel in (
                "scripts/build_round3_r3a.py",
                "src/momentum_reversal/pipelines/round3_reentry.py",
                "src/momentum_reversal/pipelines/round2_models.py",
            )
        },
    }


def _file_record(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
