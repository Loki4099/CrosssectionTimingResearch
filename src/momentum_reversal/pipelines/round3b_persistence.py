"""Round 3B fixed recovery-persistence confirmation experiment."""

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
    _fit_transform,
    _roc_auc,
    _solve_static_weight,
    _spearman,
    replay_weekly_spy_cash,
)
from momentum_reversal.pipelines.round2_protocol import sha256_file
from momentum_reversal.pipelines.round3_reentry import (
    DEFENSE,
    FULL_ARMED,
    RECOVERY_UNARMED,
    _mdd_from_returns,
    _performance_summary,
    _spy_returns_for_nav,
    build_weekly_reentry_states,
    compute_daily_reentry_indicators,
)


FEATURES = (
    "spy_total_return_21d",
    "sma50_over_sma200_minus_1",
    "drawdown_from_252d_high",
    "log_rv21_over_rv126",
)
LOCKBOX_SIGNAL = pd.Timestamp("2021-12-31")
MAIN_COST_BPS = 10


@dataclass(frozen=True, slots=True)
class R3BResult:
    bundle_dir: Path
    manifest_path: Path
    status: str
    prediction_rows: int
    weekly_rows: int


def build_four_week_attack_targets(
    decision_calendar: pd.DataFrame,
    market_daily: pd.DataFrame,
    risk_free_daily: pd.DataFrame,
) -> pd.DataFrame:
    """Build execution-open to fourth-execution-open labels, withholding lockbox."""
    calendar = decision_calendar.copy()
    for column in (
        "signal_session",
        "execution_session",
        "next_4w_execution",
    ):
        calendar[column] = pd.to_datetime(calendar[column]).dt.normalize()
    market = market_daily[["session_date", "tr_open"]].copy()
    market["session_date"] = pd.to_datetime(market["session_date"]).dt.normalize()
    opens = market.set_index("session_date")["tr_open"].astype(float)
    rf = risk_free_daily[["session_date", "rf_log"]].copy()
    rf["session_date"] = pd.to_datetime(rf["session_date"]).dt.normalize()
    rf = rf.set_index("session_date")["rf_log"].astype(float).sort_index()
    records: list[dict[str, Any]] = []
    for row in calendar.itertuples(index=False):
        signal = pd.Timestamp(row.signal_session)
        execution = pd.Timestamp(row.execution_session)
        terminal = pd.Timestamp(row.next_4w_execution)
        withheld = signal >= LOCKBOX_SIGNAL or terminal >= pd.Timestamp("2022-01-03")
        value = np.nan
        attack = np.nan
        available = False
        if not withheld and execution in opens.index and terminal in opens.index:
            rf_path = rf.loc[(rf.index >= execution) & (rf.index < terminal)]
            expected_sessions = market.loc[
                market["session_date"].between(execution, terminal, inclusive="left"),
                "session_date",
            ]
            if len(rf_path) != len(expected_sessions) or not np.isfinite(rf_path).all():
                raise DataQualityError("R3B four-week RF path is incomplete")
            value = float(
                np.log(float(opens.loc[terminal]) / float(opens.loc[execution]))
                - rf_path.sum()
            )
            attack = float(value > 0.0)
            available = True
        records.append(
            {
                "week_id": row.week_id,
                "signal_session": signal,
                "execution_session": execution,
                "next_4w_execution": terminal,
                "target_available_at": terminal,
                "withheld_lockbox": withheld,
                "target_available": available,
                "fwd_excess_logret_4w": value,
                "sustainable_attack_4w": attack,
            }
        )
    result = pd.DataFrame(records)
    if result.loc[result["withheld_lockbox"], ["fwd_excess_logret_4w", "sustainable_attack_4w"]].notna().any().any():
        raise DataQualityError("R3B lockbox target was materialized")
    return result


def run_persistence_walk_forward(
    features: pd.DataFrame,
    targets: pd.DataFrame,
    folds: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    from sklearn.linear_model import LogisticRegression

    data = features.merge(
        targets.drop(columns=["week_id", "execution_session"]),
        on="signal_session",
        how="inner",
        validate="one_to_one",
    )
    for column in ("signal_session", "execution_session"):
        data[column] = pd.to_datetime(data[column]).dt.normalize()
    prediction_parts: list[pd.DataFrame] = []
    ledger_rows: list[dict[str, Any]] = []
    for fold in folds["development"]["outer_folds"]:
        year = int(fold["outer_year"])
        train = data.loc[
            data["signal_session"].between(
                pd.Timestamp(fold["train_start_signal"]),
                pd.Timestamp(fold["train_end_signal"]),
            )
            & data["feature_complete"]
            & data["target_available"]
        ].copy()
        test = data.loc[
            data["signal_session"].between(
                pd.Timestamp(fold["test_start_signal"]),
                pd.Timestamp(fold["test_end_signal"]),
            )
            & data["feature_complete"]
        ].copy()
        if len(train) < 520 or len(test) != int(fold["test_weeks"]):
            raise DataQualityError(f"R3B fold coverage invalid: {year}")
        if train["sustainable_attack_4w"].nunique() != 2:
            raise DataQualityError(f"R3B training label is single-class: {year}")
        transform = _fit_transform(train.loc[:, FEATURES].to_numpy(float))
        x_train = transform.apply(train.loc[:, FEATURES].to_numpy(float))
        x_test = transform.apply(test.loc[:, FEATURES].to_numpy(float))
        y_train = train["sustainable_attack_4w"].to_numpy(int)
        model = LogisticRegression(
            C=1.0,
            solver="lbfgs",
            max_iter=2000,
            fit_intercept=True,
            class_weight=None,
            l1_ratio=0.0,
        )
        model.fit(x_train, y_train)
        if int(model.n_iter_[0]) >= 2000:
            raise DataQualityError(f"R3B Ridge failed to converge: {year}")
        probability = model.predict_proba(x_test)[:, 1]
        base_rate = float(y_train.mean())
        piece = test[
            ["week_id", "signal_session", "execution_session", "fwd_excess_logret_4w", "sustainable_attack_4w", "target_available"]
        ].copy()
        piece.insert(0, "outer_year", year)
        piece["p_sustainable_attack_4w"] = probability
        piece["train_base_rate"] = base_rate
        piece["model_recovery"] = probability > base_rate
        piece["train_rows"] = len(train)
        prediction_parts.append(piece)
        valid = piece["target_available"]
        y = piece.loc[valid, "sustainable_attack_4w"].to_numpy(float)
        p = piece.loc[valid, "p_sustainable_attack_4w"].to_numpy(float)
        ledger_rows.append(
            {
                "outer_year": year,
                "train_start_signal": fold["train_start_signal"],
                "train_end_signal": fold["train_end_signal"],
                "test_start_signal": fold["test_start_signal"],
                "test_end_signal": fold["test_end_signal"],
                "train_rows": len(train),
                "test_rows": len(test),
                "evaluated_test_rows": int(valid.sum()),
                "train_base_rate": base_rate,
                "test_attack_rate": float(piece["model_recovery"].mean()),
                "brier": float(np.mean((p - y) ** 2)),
                "baseline_brier": float(np.mean((base_rate - y) ** 2)),
                "brier_improvement": float(
                    np.mean((base_rate - y) ** 2) - np.mean((p - y) ** 2)
                ),
                "roc_auc": _roc_auc(y, p),
                "n_iter": int(model.n_iter_[0]),
                "intercept": float(model.intercept_[0]),
                **{f"coef_{name}": float(value) for name, value in zip(FEATURES, model.coef_[0], strict=True)},
            }
        )
    predictions = pd.concat(prediction_parts, ignore_index=True).sort_values(
        "signal_session", kind="mergesort"
    )
    if len(predictions) != 887 or predictions["signal_session"].max() >= LOCKBOX_SIGNAL:
        raise DataQualityError("R3B prediction coverage crossed firewall")
    return predictions, pd.DataFrame(ledger_rows)


def build_persistence_confirmed_states(
    price_only_states: pd.DataFrame,
    predictions: pd.DataFrame,
    daily_indicators: pd.DataFrame,
) -> pd.DataFrame:
    base = price_only_states.merge(
        predictions[
            ["signal_session", "p_sustainable_attack_4w", "train_base_rate", "model_recovery"]
        ],
        on="signal_session",
        how="left",
        validate="one_to_one",
    ).sort_values("signal_session", kind="mergesort")
    if base[["p_sustainable_attack_4w", "train_base_rate"]].isna().any().any():
        raise DataQualityError("R3B outer prediction is incomplete")
    state = FULL_ARMED
    records: list[dict[str, Any]] = []
    for row in base.itertuples(index=False):
        signal = pd.Timestamp(row.signal_session)
        pre_state = state
        event = "hold"
        if state == FULL_ARMED:
            if bool(row.defense_entry_signal):
                state = DEFENSE
                event = "enter_defense"
                target = 0.5
            else:
                target = 1.0
        elif state == DEFENSE:
            if bool(row.two_close_recovery) and bool(row.model_recovery):
                state = RECOVERY_UNARMED
                event = "exit_to_confirmed_recovery"
                target = 1.0
            else:
                target = 0.5
        elif state == RECOVERY_UNARMED:
            target = 1.0
            if float(row.spy_rv21) <= float(row.lagged_q75):
                state = FULL_ARMED
                event = "rearm"
        else:  # pragma: no cover
            raise DataQualityError(f"unknown R3B state: {state}")
        records.append(
            {
                **row._asdict(),
                "r3b_pre_state": pre_state,
                "r3b_post_state": state,
                "r3b_state_event": event,
                "r3b_target_spy_weight": target,
                "joint_recovery_signal": bool(row.two_close_recovery) and bool(row.model_recovery),
            }
        )
    result = pd.DataFrame(records)
    if result["signal_session"].max() >= LOCKBOX_SIGNAL:
        raise DataQualityError("R3B state crossed lockbox firewall")
    return result


def build_r3b_development_bundle(
    *,
    project_root: str | Path,
    r2a_candidate_dir: str | Path,
    r2b_bundle_dir: str | Path,
    r3a_bundle_dir: str | Path,
    output_root: str | Path,
    run_id: str,
) -> R3BResult:
    root = Path(project_root).resolve()
    r2a = Path(r2a_candidate_dir).resolve()
    r2b = Path(r2b_bundle_dir).resolve()
    r3a = Path(r3a_bundle_dir).resolve()
    bundle = (
        Path(output_root).resolve()
        / "experiments"
        / "round3"
        / "R3B_RECOVERY_PERSISTENCE"
        / "runs"
        / run_id
    )
    if bundle.exists():
        raise FileExistsError(f"immutable R3B bundle already exists: {bundle}")
    lock = _verify_inputs(root, r2a, r2b, r3a)
    provenance = _build_provenance(root)
    folds = json.loads(
        (root / "config" / "experiments" / "round2" / "folds.json").read_text(encoding="utf-8")
    )
    market = pd.read_parquet(r2a / "curated" / "market_daily.parquet")
    rf = pd.read_parquet(r2a / "curated" / "risk_free_daily.parquet")
    calendar = pd.read_parquet(r2a / "curated" / "decision_calendar.parquet")
    features = pd.read_parquet(r2b / "features_weekly.parquet")
    old_targets = pd.read_parquet(r2b / "targets_weekly.parquet")
    targets = build_four_week_attack_targets(calendar, market, rf)
    predictions, ledger = run_persistence_walk_forward(features, targets, folds)

    indicators = compute_daily_reentry_indicators(market)
    price_only = build_weekly_reentry_states(calendar, indicators)
    frozen_price_only = pd.read_parquet(r3a / "weekly_states.parquet")
    identity_columns = [
        "week_id", "signal_session", "execution_session", "spy_rv21", "lagged_q75",
        "sma21", "above_sma21", "two_close_recovery", "defense_entry_signal",
        "pre_state", "post_state", "state_event", "asymmetric_target_spy_weight",
        "symmetric_target_spy_weight",
    ]
    pd.testing.assert_frame_equal(
        price_only[identity_columns].reset_index(drop=True),
        frozen_price_only[identity_columns].reset_index(drop=True),
        check_dtype=False,
        check_exact=True,
    )
    states = build_persistence_confirmed_states(price_only, predictions, indicators)

    schedules = {
        "ALWAYS_SPY": states[["execution_session"]].assign(target_spy_weight=1.0),
        "SYMMETRIC_RV21": states[["execution_session"]].assign(
            target_spy_weight=states["symmetric_target_spy_weight"].to_numpy()
        ),
        "R3A_PRICE_ONLY": states[["execution_session"]].assign(
            target_spy_weight=states["asymmetric_target_spy_weight"].to_numpy()
        ),
        "R3B_PERSISTENCE_CONFIRMED": states[["execution_session"]].assign(
            target_spy_weight=states["r3b_target_spy_weight"].to_numpy()
        ),
    }
    nav_parts: list[pd.DataFrame] = []
    summary_rows: list[dict[str, Any]] = []
    main_nav: dict[str, pd.DataFrame] = {}
    for cost in (0, 5, 10, 20):
        for strategy, schedule in schedules.items():
            frame = replay_weekly_spy_cash(schedule, market, rf, cost_bps=cost)
            nav_parts.append(frame.assign(strategy=strategy, cost_bps=cost))
            summary_rows.append(_performance_summary(strategy, cost, frame, market))
            if cost == MAIN_COST_BPS:
                main_nav[strategy] = frame
    frozen_nav = pd.read_parquet(r3a / "nav.parquet")
    frozen_r3a = frozen_nav.loc[
        (frozen_nav["strategy"] == "ASYMMETRIC_REENTRY")
        & (frozen_nav["cost_bps"] == MAIN_COST_BPS)
    ].reset_index(drop=True)
    replayed_r3a = main_nav["R3A_PRICE_ONLY"].reset_index(drop=True)
    identity_nav_columns = [
        "session_date", "nav", "daily_return", "rf_return", "close_spy_weight",
        "target_spy_weight", "turnover", "cost",
    ]
    try:
        pd.testing.assert_frame_equal(
            frozen_r3a[identity_nav_columns],
            replayed_r3a[identity_nav_columns],
            check_dtype=False,
            check_exact=True,
        )
    except AssertionError as exc:
        raise DataQualityError("R3B failed exact R3A NAV identity") from exc

    r3b_nav = main_nav["R3B_PERSISTENCE_CONFIRMED"]
    avg_weight = _solve_static_weight(
        r3b_nav, states["execution_session"], market, rf,
        objective="average_exposure", cost_bps=MAIN_COST_BPS,
    )
    vol_weight = _solve_static_weight(
        r3b_nav, states["execution_session"], market, rf,
        objective="excess_volatility", cost_bps=MAIN_COST_BPS,
    )
    control_nav = {
        "MATCHED_AVERAGE": replay_weekly_spy_cash(
            _constant_schedule(states["execution_session"], avg_weight), market, rf, cost_bps=MAIN_COST_BPS
        ),
        "MATCHED_SAME_VOL": replay_weekly_spy_cash(
            _constant_schedule(states["execution_session"], vol_weight), market, rf, cost_bps=MAIN_COST_BPS
        ),
    }
    model_metrics = _model_metrics(predictions, ledger, old_targets)
    mechanism = _mechanism(main_nav, control_nav, market)
    controls = _controls(main_nav, control_nav, avg_weight, vol_weight)
    gate = _jsonable(_gate(model_metrics, main_nav, control_nav, summary_rows, mechanism))
    status = (
        "completed_persistence_candidate"
        if gate["all_development_gates_pass"]
        else "completed_no_persistence_candidate"
    )

    nav = pd.concat(nav_parts, ignore_index=True)
    controls_daily = pd.concat(
        [frame.assign(control=name) for name, frame in control_nav.items()], ignore_index=True
    )
    files_and_frames = (
        ("targets_weekly.parquet", targets),
        ("model_ledger.csv", ledger),
        ("predictions_oos.parquet", predictions),
        ("weekly_states.parquet", states),
        ("nav.parquet", nav),
        ("control_nav.parquet", controls_daily),
        ("summary.csv", pd.DataFrame(summary_rows)),
        ("controls.csv", pd.DataFrame([controls])),
        ("mechanism.csv", pd.DataFrame(mechanism["rows"])),
        ("model_metrics.csv", pd.DataFrame([model_metrics])),
    )
    bundle.mkdir(parents=True, exist_ok=False)
    written: list[Path] = []
    for name, frame in files_and_frames:
        path = bundle / name
        if path.suffix == ".parquet":
            frame.to_parquet(path, index=False, compression="zstd")
        else:
            frame.to_csv(path, index=False, lineterminator="\n")
        written.append(path)
    gate_path = bundle / "gate.json"
    gate_path.write_text(json.dumps(gate, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    written.append(gate_path)
    config_path = bundle / "config_resolved.toml"
    config_path.write_bytes((root / "config" / "experiments" / "round3b" / "program.toml").read_bytes())
    written.append(config_path)
    manifest = {
        "schema_version": 1,
        "program_id": "recovery_persistence_round3b_v1",
        "batch_id": "R3B_RECOVERY_PERSISTENCE",
        "run_id": run_id,
        "stage": "development",
        "status": status,
        "formal_eligible": False,
        "lockbox_materialized": False,
        "lockbox_authorized": False,
        "mom255_transfer_authorized": False,
        "counts": {
            "targets": len(targets), "predictions": len(predictions), "model_years": len(ledger),
            "weekly_states": len(states), "nav_rows": len(nav), "control_nav_rows": len(controls_daily),
            "defense_entries": int((states["r3b_state_event"] == "enter_defense").sum()),
            "confirmed_exits": int((states["r3b_state_event"] == "exit_to_confirmed_recovery").sum()),
            "rearms": int((states["r3b_state_event"] == "rearm").sum()),
        },
        "range": {
            "first_signal": str(states["signal_session"].min().date()),
            "last_signal": str(states["signal_session"].max().date()),
            "nav_end": str(nav["session_date"].max().date()),
        },
        "gate": {k: v for k, v in gate.items() if k != "diagnostics"},
        "anchors": {
            "prereg_lock_sha256": sha256_file(root / "config" / "experiments" / "round3b" / "PREREG_LOCK.json"),
            "r2a_manifest_sha256": sha256_file(r2a / "manifest.json"),
            "r2b_manifest_sha256": sha256_file(r2b / "manifest.json"),
            "r3a_manifest_sha256": sha256_file(r3a / "manifest.json"),
            "folds_sha256": sha256_file(root / "config" / "experiments" / "round2" / "folds.json"),
        },
        "preregistration": lock,
        "build_provenance": provenance,
        "files": [_file_record(path, bundle) for path in sorted(written)],
    }
    manifest_path = bundle / "manifest.json"
    manifest_path.write_text(
        json.dumps(_jsonable(manifest), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return R3BResult(bundle, manifest_path, status, len(predictions), len(states))


def _model_metrics(predictions: pd.DataFrame, ledger: pd.DataFrame, old_targets: pd.DataFrame) -> dict[str, Any]:
    valid = predictions["target_available"]
    ev = predictions.loc[valid]
    y = ev["sustainable_attack_4w"].to_numpy(float)
    p = ev["p_sustainable_attack_4w"].to_numpy(float)
    base = ev["train_base_rate"].to_numpy(float)
    brier = float(np.mean((p - y) ** 2))
    base_brier = float(np.mean((base - y) ** 2))
    tail = old_targets[["signal_session", "fwd_worst_excess_4w", "t3_available"]].copy()
    tail["signal_session"] = pd.to_datetime(tail["signal_session"]).dt.normalize()
    tail_eval = ev[["signal_session", "p_sustainable_attack_4w"]].merge(
        tail,
        on="signal_session",
        how="left",
        validate="one_to_one",
    )
    tail_valid = tail_eval["t3_available"].fillna(False) & tail_eval["fwd_worst_excess_4w"].notna()
    if int(tail_valid.sum()) != len(ev):
        raise DataQualityError("R3B T3 diagnostic coverage is incomplete")
    return {
        "evaluation_rows": len(ev),
        "brier": brier,
        "baseline_brier": base_brier,
        "brier_skill": 1.0 - brier / base_brier,
        "roc_auc": _roc_auc(y, p),
        "spearman_fwd_excess_logret_4w": _spearman(p, ev["fwd_excess_logret_4w"].to_numpy(float)),
        "spearman_fwd_worst_excess_4w": _spearman(
            tail_eval.loc[tail_valid, "p_sustainable_attack_4w"].to_numpy(float),
            tail_eval.loc[tail_valid, "fwd_worst_excess_4w"].to_numpy(float),
        ),
        "positive_brier_year_fraction": float((ledger["brier_improvement"] > 0).mean()),
        "annual_attack_rate_non_degenerate": bool(ledger["test_attack_rate"].between(0.05, 0.95).all()),
    }


def _mechanism(
    main_nav: dict[str, pd.DataFrame], control_nav: dict[str, pd.DataFrame], market: pd.DataFrame
) -> dict[str, Any]:
    spy = _spy_returns_for_nav(main_nav["ALWAYS_SPY"], market)
    always = main_nav["ALWAYS_SPY"]
    always_prior = np.r_[1.0, always["nav"].to_numpy(float)[:-1]]
    always_cost = float(np.sum(always["cost"].to_numpy(float) / always_prior))
    rows = []
    for strategy in ("SYMMETRIC_RV21", "R3A_PRICE_ONLY", "R3B_PERSISTENCE_CONFIRMED"):
        frame = main_nav[strategy]
        x = spy - frame["rf_return"].to_numpy(float)
        shortfall = 1.0 - frame["close_spy_weight"].to_numpy(float)
        benefit = float(np.sum(shortfall * np.maximum(-x, 0)))
        missed = float(np.sum(shortfall * np.maximum(x, 0)))
        prior = np.r_[1.0, frame["nav"].to_numpy(float)[:-1]]
        total_cost = float(np.sum(frame["cost"].to_numpy(float) / prior))
        incremental = total_cost - always_cost
        rows.append({
            "strategy": strategy, "defense_benefit": benefit, "missed_upside": missed,
            "incremental_cost_drag_vs_always_spy": incremental,
            "net_timing": benefit - missed - incremental,
            "benefit_to_missed_ratio": benefit / missed if missed > 0 else np.inf,
        })
    return {"rows": rows}


def _controls(main_nav: dict[str, pd.DataFrame], control_nav: dict[str, pd.DataFrame], avg_weight: float, vol_weight: float) -> dict[str, Any]:
    r3b = main_nav["R3B_PERSISTENCE_CONFIRMED"]
    return {
        "matched_average_weight": avg_weight,
        "matched_same_vol_weight": vol_weight,
        "active_vs_symmetric": float(r3b["nav"].iloc[-1] / main_nav["SYMMETRIC_RV21"]["nav"].iloc[-1] - 1),
        "active_vs_r3a": float(r3b["nav"].iloc[-1] / main_nav["R3A_PRICE_ONLY"]["nav"].iloc[-1] - 1),
        "timing_value_vs_average": float(r3b["nav"].iloc[-1] / control_nav["MATCHED_AVERAGE"]["nav"].iloc[-1] - 1),
        "timing_value_vs_same_vol": float(r3b["nav"].iloc[-1] / control_nav["MATCHED_SAME_VOL"]["nav"].iloc[-1] - 1),
    }


def _gate(model: dict[str, Any], main_nav: dict[str, pd.DataFrame], control_nav: dict[str, pd.DataFrame], summaries: list[dict[str, Any]], mechanism: dict[str, Any]) -> dict[str, Any]:
    summary = pd.DataFrame(summaries).loc[lambda x: x.cost_bps == 10].set_index("strategy")
    r3b = main_nav["R3B_PERSISTENCE_CONFIRMED"]
    sym = main_nav["SYMMETRIC_RV21"]
    r3a = main_nav["R3A_PRICE_ONLY"]
    always = main_nav["ALWAYS_SPY"]
    avg = control_nav["MATCHED_AVERAGE"]
    same = control_nav["MATCHED_SAME_VOL"]
    mech = {r["strategy"]: r for r in mechanism["rows"]}
    missed_reduction = 1 - mech["R3B_PERSISTENCE_CONFIRMED"]["missed_upside"] / mech["SYMMETRIC_RV21"]["missed_upside"]
    benefit_retention = mech["R3B_PERSISTENCE_CONFIRMED"]["defense_benefit"] / mech["SYMMETRIC_RV21"]["defense_benefit"]
    active_sym = np.log1p(r3b.daily_return.to_numpy(float)) - np.log1p(sym.daily_return.to_numpy(float))
    active_avg = np.log1p(r3b.daily_return.to_numpy(float)) - np.log1p(avg.daily_return.to_numpy(float))
    years = r3b.session_date.dt.year.to_numpy()
    annual_sym = np.array([active_sym[years == y].sum() for y in np.unique(years)])
    annual_avg = np.array([active_avg[years == y].sum() for y in np.unique(years)])
    pos = np.maximum(annual_avg, 0)
    concentration = float(pos.max() / pos.sum()) if pos.sum() > 0 else 1.0
    gain_r3b = float(summary.loc["R3B_PERSISTENCE_CONFIRMED", "max_drawdown"] - summary.loc["ALWAYS_SPY", "max_drawdown"])
    gain_sym = float(summary.loc["SYMMETRIC_RV21", "max_drawdown"] - summary.loc["ALWAYS_SPY", "max_drawdown"])
    retention = gain_r3b / gain_sym if gain_sym > 0 else np.nan
    leaveout = {}
    for name, start, end in (("gfc", "2007-10-09", "2009-03-09"), ("covid_selloff", "2020-02-19", "2020-03-23")):
        inside = r3b.session_date.between(start, end).to_numpy()
        leaveout[name] = float(np.expm1(active_avg[~inside].sum()))
    endpoint = float(np.expm1(active_avg[(r3b.session_date <= "2021-06-30").to_numpy()].sum()))
    stress_direction = True
    full_summary = pd.DataFrame(summaries)
    for cost in (0, 5, 20):
        table = full_summary.loc[full_summary.cost_bps == cost].set_index("strategy")
        stress_direction &= table.loc["R3B_PERSISTENCE_CONFIRMED", "terminal_nav"] > table.loc["SYMMETRIC_RV21", "terminal_nav"]
    h1 = {
        "brier_skill_positive": model["brier_skill"] > 0,
        "roc_auc_above_half": model["roc_auc"] > 0.5,
        "return_spearman_positive": model["spearman_fwd_excess_logret_4w"] > 0,
        "positive_brier_year_fraction_at_least_60pct": model["positive_brier_year_fraction"] >= 0.60,
        "annual_attack_rate_non_degenerate": model["annual_attack_rate_non_degenerate"],
    }
    h2 = {
        "active_vs_symmetric_positive": r3b.nav.iloc[-1] / sym.nav.iloc[-1] - 1 > 0,
        "active_vs_r3a_positive": r3b.nav.iloc[-1] / r3a.nav.iloc[-1] - 1 > 0,
        "cagr_vs_symmetric_positive": summary.loc["R3B_PERSISTENCE_CONFIRMED", "cagr"] > summary.loc["SYMMETRIC_RV21", "cagr"],
        "missed_upside_reduction_at_least_25pct": missed_reduction >= 0.25,
        "defense_benefit_retention_at_least_75pct": benefit_retention >= 0.75,
        "positive_year_fraction_at_least_60pct": float((annual_sym > 0).mean()) >= 0.60,
    }
    r3b_mech = mech["R3B_PERSISTENCE_CONFIRMED"]
    h3 = {
        "timing_value_vs_average_positive": r3b.nav.iloc[-1] / avg.nav.iloc[-1] - 1 > 0,
        "timing_value_vs_same_vol_nonnegative": r3b.nav.iloc[-1] / same.nav.iloc[-1] - 1 >= 0,
        "net_timing_positive": r3b_mech["net_timing"] > 0,
        "benefit_to_missed_above_one": r3b_mech["benefit_to_missed_ratio"] > 1,
        "mdd_gain_positive": gain_r3b > 0,
        "mdd_gain_retention_at_least_75pct": retention >= 0.75,
    }
    h4 = {
        "positive_year_concentration_at_most_50pct": concentration <= 0.50,
        "gfc_leaveout_positive": leaveout["gfc"] > 0,
        "covid_selloff_leaveout_positive": leaveout["covid_selloff"] > 0,
        "endpoint_2021_06_30_positive": endpoint > 0,
        "stress_cost_direction_positive": bool(stress_direction),
    }
    return {
        "H1": h1, "H2": h2, "H3": h3, "H4": h4,
        "h1_pass": all(h1.values()), "h2_pass": all(h2.values()),
        "h3_pass": all(h3.values()), "h4_pass": all(h4.values()),
        "all_development_gates_pass": all(all(x.values()) for x in (h1, h2, h3, h4)),
        "lockbox_authorized": False,
        "diagnostics": {
            **model,
            "missed_upside_reduction": missed_reduction,
            "defense_benefit_retention": benefit_retention,
            "mdd_gain_retention": retention,
            "positive_year_fraction_vs_symmetric": float((annual_sym > 0).mean()),
            "positive_year_concentration": concentration,
            "leaveout": leaveout,
            "endpoint_2021_06_30": endpoint,
        },
    }


def _verify_inputs(root: Path, r2a: Path, r2b: Path, r3a: Path) -> dict[str, Any]:
    lock = json.loads((root / "config" / "experiments" / "round3b" / "PREREG_LOCK.json").read_text())
    for rel, expected in lock["files"].items():
        if sha256_file(root / rel) != expected:
            raise DataQualityError(f"R3B prereg hash mismatch: {rel}")
    config = tomllib.loads((root / "config" / "experiments" / "round3b" / "program.toml").read_text())
    if not config["authorization"]["development"] or config["authorization"]["lockbox"]:
        raise DataQualityError("R3B authorization invalid")
    for name, path in (("r2a", r2a), ("r2b", r2b), ("r3a", r3a)):
        if sha256_file(path / "manifest.json") != lock["inputs"][f"{name}_manifest_sha256"]:
            raise DataQualityError(f"R3B {name} manifest mismatch")
        if _tree_sha256(path) != lock["inputs"][f"{name}_tree_sha256"]:
            raise DataQualityError(f"R3B {name} tree mismatch")
    return lock


def _tree_sha256(root: Path) -> str:
    members = [
        f"{p.relative_to(root).as_posix()}|{p.stat().st_size}|{sha256_file(p)}"
        for p in sorted(x for x in root.rglob("*") if x.is_file())
    ]
    return hashlib.sha256("\n".join(members).encode()).hexdigest()


def _build_provenance(root: Path) -> dict[str, Any]:
    status = subprocess.run(["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    if status:
        raise DataQualityError("R3B bundle requires a clean workspace")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    return {
        "git_commit": commit,
        "workspace_dirty": False,
        "dependency_versions": {name: importlib_metadata.version(name) for name in ("numpy", "pandas", "pyarrow", "scikit-learn", "scipy")},
        "code_file_sha256": {
            rel: sha256_file(root / rel)
            for rel in ("scripts/build_round3b_r3b.py", "src/momentum_reversal/pipelines/round3b_persistence.py", "src/momentum_reversal/pipelines/round3_reentry.py")
        },
    }


def _file_record(path: Path, root: Path) -> dict[str, Any]:
    return {"path": path.relative_to(root).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _jsonable(value: Any) -> Any:
    """Convert NumPy scalars recursively without changing numeric values."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
