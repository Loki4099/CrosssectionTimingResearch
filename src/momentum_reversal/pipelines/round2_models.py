"""R2C simple-stage nested walk-forward models and SPY/T-bill measurement."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata as importlib_metadata
import json
from pathlib import Path
import subprocess
from typing import Any, Callable

import numpy as np
import pandas as pd

from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.pipelines.round2_protocol import sha256_file
from momentum_reversal.pipelines.round2_signals import CORE_FEATURES


SENTINELS = {
    "R2C__PROC_SENT_RV21": ("spy_rv21", 1.0),
    "R2C__PROC_SENT_SMA_GAP": ("sma50_over_sma200_minus_1", -1.0),
    "R2C__PROC_SENT_DRAWDOWN252": ("drawdown_from_252d_high", -1.0),
    "R2C__PROC_SENT_RET21": ("spy_total_return_21d", -1.0),
}
RIDGE_ARMS = {
    "R2C__RIDGE_L001": 0.01,
    "R2C__RIDGE_L01": 0.1,
    "R2C__RIDGE_L1": 1.0,
    "R2C__RIDGE_L10": 10.0,
}
GAM_ARMS = {
    "R2C__GAM_L01": 0.1,
    "R2C__GAM_L1": 1.0,
    "R2C__GAM_L10": 10.0,
}
PROCESS_ORDER = (*SENTINELS, "R2C__PROC_RIDGE", "R2C__PROC_GAM")


@dataclass(frozen=True, slots=True)
class R2CResult:
    bundle_dir: Path
    manifest_path: Path
    process_count: int
    prediction_rows: int
    complex_gate_open: bool


@dataclass(frozen=True, slots=True)
class _Transform:
    lower: np.ndarray
    upper: np.ndarray
    median: np.ndarray
    mean: np.ndarray
    std: np.ndarray

    def apply(self, values: np.ndarray) -> np.ndarray:
        x = np.asarray(values, dtype=float).copy()
        x = np.clip(x, self.lower, self.upper)
        missing = ~np.isfinite(x)
        if missing.any():
            x[missing] = np.take(self.median, np.where(missing)[1])
        return (x - self.mean) / self.std


def build_r2c_simple_bundle(
    *,
    project_root: str | Path,
    r2a_candidate_dir: str | Path,
    r2b_bundle_dir: str | Path,
    output_root: str | Path,
    run_id: str,
) -> R2CResult:
    root = Path(project_root).resolve()
    r2a = Path(r2a_candidate_dir).resolve()
    r2b = Path(r2b_bundle_dir).resolve()
    output = (
        Path(output_root).resolve()
        / "experiments"
        / "round2"
        / "R2C_SPY_TBILL"
        / "runs"
        / run_id
    )
    output.mkdir(parents=True, exist_ok=False)
    auth = _verify_authorization(root, r2a, r2b)
    folds = json.loads(
        (root / "config" / "experiments" / "round2" / "folds.json").read_text(
            encoding="utf-8"
        )
    )
    features = pd.read_parquet(r2b / "features_weekly.parquet")
    targets = pd.read_parquet(r2b / "targets_weekly.parquet")
    data = _join_development_data(features, targets)
    predictions, selectors, arms = run_simple_walk_forward(data, folds)
    if predictions["signal_session"].max() >= pd.Timestamp("2021-12-31"):
        raise DataQualityError("R2C development prediction crossed the lockbox firewall")
    market = pd.read_parquet(r2a / "curated" / "market_daily.parquet")
    risk_free = pd.read_parquet(r2a / "curated" / "risk_free_daily.parquet")
    metrics, annual, crisis, nav = evaluate_simple_processes(
        predictions, market, risk_free
    )
    simple_gate = bool(metrics["all_hard_gates_pass"].any())

    frames = (
        ("predictions_oos.parquet", predictions),
        ("selector_ledger.parquet", selectors),
        ("arm_ledger.parquet", arms),
        ("process_metrics.csv", metrics),
        ("annual_metrics.csv", annual),
        ("crisis_leaveout.csv", crisis),
        ("nav_daily.parquet", nav),
    )
    files: list[Path] = []
    for name, frame in frames:
        path = output / name
        if path.suffix == ".parquet":
            frame.to_parquet(path, index=False, compression="zstd")
        else:
            frame.to_csv(path, index=False, lineterminator="\n")
        files.append(path)
    config_path = output / "config_resolved.toml"
    config_path.write_bytes(
        (root / "config" / "experiments" / "round2" / "program.toml").read_bytes()
    )
    files.append(config_path)
    auth_path = output / "development_authorization.json"
    auth_path.write_text(
        json.dumps(auth, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    files.append(auth_path)

    manifest = {
        "schema_version": 1,
        "program_id": "defense_timing_round2_v1",
        "batch_id": "R2C_SPY_TBILL",
        "run_id": run_id,
        "stage": "simple_development",
        "status": (
            "completed_simple_gate_open"
            if simple_gate
            else "completed_no_simple_benchmark"
        ),
        "formal_eligible": False,
        "complex_gate_open": simple_gate,
        "lockbox_predictions_present": False,
        "lockbox_targets_present": False,
        "r2d_authorized": False,
        "counts": {
            "processes": int(predictions["process_id"].nunique()),
            "prediction_rows": len(predictions),
            "selector_rows": len(selectors),
            "arm_rows": len(arms),
            "nav_rows": len(nav),
        },
        "anchors": {
            "authorization_sha256": sha256_file(
                root
                / "config"
                / "experiments"
                / "round2"
                / "R2C_DEVELOPMENT_AUTH.json"
            ),
            "r2b_manifest_sha256": sha256_file(r2b / "manifest.json"),
            "fold_manifest_sha256": sha256_file(
                root / "config" / "experiments" / "round2" / "folds.json"
            ),
        },
        "build_provenance": _build_provenance(root),
        "files": [_file_record(path, output) for path in sorted(files)],
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return R2CResult(
        bundle_dir=output,
        manifest_path=manifest_path,
        process_count=int(predictions["process_id"].nunique()),
        prediction_rows=len(predictions),
        complex_gate_open=simple_gate,
    )


def run_simple_walk_forward(
    data: pd.DataFrame, folds: dict[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    predictions: list[pd.DataFrame] = []
    selector_rows: list[dict[str, Any]] = []
    arm_rows: list[dict[str, Any]] = []
    for outer in folds["development"]["outer_folds"]:
        outer_year = int(outer["outer_year"])
        train = _date_slice(
            data, outer["train_start_signal"], outer["train_end_signal"], target=True
        )
        test = _date_slice(
            data, outer["test_start_signal"], outer["test_end_signal"], target=False
        )
        if len(test) != int(outer["test_weeks"]):
            raise DataQualityError(f"outer test count mismatch: {outer_year}")

        for process_id, (column, direction) in SENTINELS.items():
            oof = _sentinel_inner_oof(data, outer, column, direction)
            fit_status = "valid"
            try:
                calibrated, calibrator = _prequential_calibration(oof)
                valid = calibrated["p_cash_wins"].notna()
                brier = float(
                    np.mean(
                        (
                            calibrated.loc[valid, "p_cash_wins"]
                            - calibrated.loc[valid, "y"]
                        )
                        ** 2
                    )
                )
            except DataQualityError as exc:
                calibrated = oof.assign(p_cash_wins=np.nan)
                calibrator = None
                brier = float("nan")
                fit_status = f"invalid:{exc}"
            raw_train = oof["raw_defense_score"].to_numpy(float)
            threshold = float(np.quantile(raw_train, 0.75))
            raw_test = direction * pd.to_numeric(test[column], errors="coerce").to_numpy(float)
            pred = _outer_prediction_frame(
                test,
                process_id=process_id,
                selected_arm_id=process_id.replace("PROC_", ""),
                outer_year=outer_year,
                raw=raw_test,
                calibrator=calibrator,
                threshold=threshold,
                base_rate=float(train["cash_wins_1w"].mean()),
                fit_status=fit_status,
            )
            predictions.append(pred)
            selector_rows.append(
                _selector_record(
                    outer_year,
                    process_id,
                    process_id.replace("PROC_", ""),
                    brier,
                    threshold,
                    calibrator,
                    len(oof),
                    fit_status,
                )
            )
            arm_rows.append(
                {
                    "outer_year": outer_year,
                    "process_id": process_id,
                    "arm_id": process_id.replace("PROC_", ""),
                    "inner_prequential_brier": brier,
                    "selected": True,
                    "one_se_eligible": True,
                    "fit_status": fit_status,
                }
            )

        for family, arm_spec, process_id in (
            ("ridge", RIDGE_ARMS, "R2C__PROC_RIDGE"),
            ("gam", GAM_ARMS, "R2C__PROC_GAM"),
        ):
            oof_by_arm: dict[str, pd.DataFrame] = {}
            raw_oof_by_arm: dict[str, pd.DataFrame] = {}
            invalid_arms: dict[str, str] = {}
            for arm_id, penalty in arm_spec.items():
                oof = _model_inner_oof(data, outer, family, penalty)
                raw_oof_by_arm[arm_id] = oof
                try:
                    calibrated, _ = _prequential_calibration(oof)
                    oof_by_arm[arm_id] = calibrated
                except DataQualityError as exc:
                    invalid_arms[arm_id] = str(exc)
            complexity = list(reversed(list(arm_spec)))
            if oof_by_arm:
                selected, arm_stats = _select_arm_one_se(
                    oof_by_arm,
                    complexity=complexity,
                    seed=20260816 + outer_year + (0 if family == "ridge" else 1000),
                )
                selected_oof = oof_by_arm[selected]
                _, calibrator = _prequential_calibration(selected_oof, refit_all=True)
                process_fit_status = "valid"
            else:
                selected = complexity[0]
                selected_oof = raw_oof_by_arm[selected].assign(p_cash_wins=np.nan)
                calibrator = None
                process_fit_status = "invalid:all registered arms have non-positive Platt slope"
                arm_stats = []
            for row in arm_stats:
                row.update({"outer_year": outer_year, "process_id": process_id})
                arm_rows.append(row)
            for arm_id, reason in invalid_arms.items():
                arm_rows.append(
                    {
                        "outer_year": outer_year,
                        "process_id": process_id,
                        "arm_id": arm_id,
                        "inner_prequential_brier": np.nan,
                        "paired_se_vs_best": np.nan,
                        "one_se_eligible": False,
                        "selected": False,
                        "fit_status": f"invalid:{reason}",
                    }
                )
            threshold = float(
                np.quantile(selected_oof["raw_defense_score"].to_numpy(float), 0.75)
            )
            penalty = arm_spec[selected]
            model = _fit_raw_model(train, family, penalty)
            raw_test = model(test)
            pred = _outer_prediction_frame(
                test,
                process_id=process_id,
                selected_arm_id=selected,
                outer_year=outer_year,
                raw=raw_test,
                calibrator=calibrator,
                threshold=threshold,
                base_rate=float(train["cash_wins_1w"].mean()),
                fit_status=process_fit_status,
            )
            predictions.append(pred)
            selected_brier = next(
                (
                    row["inner_prequential_brier"]
                    for row in arm_stats
                    if row["arm_id"] == selected
                ),
                float("nan"),
            )
            selector_rows.append(
                _selector_record(
                    outer_year,
                    process_id,
                    selected,
                    selected_brier,
                    threshold,
                    calibrator,
                    len(selected_oof),
                    process_fit_status,
                )
            )
    result = pd.concat(predictions, ignore_index=True).sort_values(
        ["process_id", "signal_session"], kind="mergesort"
    )
    if result.duplicated(["process_id", "signal_session"]).any():
        raise DataQualityError("duplicate R2C outer prediction key")
    expected = sum(
        int(fold["test_weeks"]) for fold in folds["development"]["outer_folds"]
    )
    counts = result.groupby("process_id").size()
    if len(counts) != 6 or not (counts == expected).all():
        raise DataQualityError("R2C outer prediction coverage mismatch")
    return (
        result.reset_index(drop=True),
        pd.DataFrame(selector_rows).sort_values(
            ["process_id", "outer_year"], kind="mergesort"
        ),
        pd.DataFrame(arm_rows).sort_values(
            ["process_id", "outer_year", "arm_id"], kind="mergesort"
        ),
    )


def evaluate_simple_processes(
    predictions: pd.DataFrame,
    market_daily: pd.DataFrame,
    risk_free_daily: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    metric_rows: list[dict[str, Any]] = []
    annual_rows: list[dict[str, Any]] = []
    crisis_rows: list[dict[str, Any]] = []
    nav_rows: list[pd.DataFrame] = []
    for process_id in PROCESS_ORDER:
        pred = predictions.loc[predictions["process_id"] == process_id].copy()
        eval_mask = pred["target_available"] & pred["t3_available"]
        ev = pred.loc[eval_mask].copy()
        y = ev["cash_wins_1w"].to_numpy(float)
        p = ev["p_cash_wins"].to_numpy(float)
        raw = ev["raw_defense_score"].to_numpy(float)
        base = ev["base_rate"].to_numpy(float)
        probability_valid = np.isfinite(p)
        complete_probability_path = bool(probability_valid.all())
        if probability_valid.any():
            brier = float(np.mean((p[probability_valid] - y[probability_valid]) ** 2))
            base_brier = float(
                np.mean((base[probability_valid] - y[probability_valid]) ** 2)
            )
            brier_skill = 1.0 - brier / base_brier
            auc = _roc_auc(y[probability_valid], p[probability_valid])
            calibration_intercept, calibration_slope = _calibration_diagnostic(
                p[probability_valid], y[probability_valid]
            )
            ece = _expected_calibration_error(
                p[probability_valid], y[probability_valid]
            )
        else:
            brier = base_brier = brier_skill = auc = float("nan")
            calibration_intercept = calibration_slope = ece = float("nan")
        spearman_t1 = _spearman(raw, ev["fwd_excess_logret_1w"].to_numpy(float))
        spearman_t3 = _spearman(raw, ev["fwd_worst_excess_4w"].to_numpy(float))
        quintile = pd.qcut(pd.Series(raw).rank(method="first"), 5, labels=False) + 1
        q1, q5 = quintile.to_numpy() == 1, quintile.to_numpy() == 5
        q_order = bool(
            y[q5].mean() > y[q1].mean()
            and ev["fwd_excess_logret_1w"].to_numpy(float)[q5].mean()
            < ev["fwd_excess_logret_1w"].to_numpy(float)[q1].mean()
            and ev["fwd_worst_excess_4w"].to_numpy(float)[q5].mean()
            < ev["fwd_worst_excess_4w"].to_numpy(float)[q1].mean()
        )
        alert_rate = float(pred["alert"].mean())
        annual_alert = pred.groupby("outer_year")["alert"].mean()
        alert_valid = bool(
            0.05 <= alert_rate <= 0.50
            and ((annual_alert > 0) & (annual_alert < 1)).all()
        )

        dynamic = replay_weekly_spy_cash(
            pred[["execution_session", "target_spy_weight"]],
            market_daily,
            risk_free_daily,
            cost_bps=10,
        )
        avg_control_weight = _solve_static_weight(
            dynamic,
            pred["execution_session"],
            market_daily,
            risk_free_daily,
            objective="average_exposure",
            cost_bps=10,
        )
        vol_control_weight = _solve_static_weight(
            dynamic,
            pred["execution_session"],
            market_daily,
            risk_free_daily,
            objective="excess_volatility",
            cost_bps=10,
        )
        avg_control = replay_weekly_spy_cash(
            _constant_schedule(pred["execution_session"], avg_control_weight),
            market_daily,
            risk_free_daily,
            cost_bps=10,
        )
        vol_control = replay_weekly_spy_cash(
            _constant_schedule(pred["execution_session"], vol_control_weight),
            market_daily,
            risk_free_daily,
            cost_bps=10,
        )
        timing_avg = float(dynamic["nav"].iloc[-1] / avg_control["nav"].iloc[-1] - 1)
        timing_vol = float(dynamic["nav"].iloc[-1] / vol_control["nav"].iloc[-1] - 1)
        active_daily = np.log1p(dynamic["daily_return"].to_numpy(float)) - np.log1p(
            avg_control["daily_return"].to_numpy(float)
        )
        years = dynamic["session_date"].dt.year.to_numpy()
        year_values = []
        year_log_contributions = []
        for year in sorted(np.unique(years)):
            log_contribution = float(active_daily[years == year].sum())
            value = float(np.expm1(log_contribution))
            year_values.append(value)
            year_log_contributions.append(log_contribution)
            annual_rows.append(
                {
                    "process_id": process_id,
                    "year": int(year),
                    "timing_value_vs_average": value,
                    "active_log_wealth_contribution": log_contribution,
                    "alert_rate": float(
                        pred.loc[pred["outer_year"] == year, "alert"].mean()
                    ),
                }
            )
        positive_year_fraction = float(np.mean(np.asarray(year_values) > 0))
        positive_contrib = np.maximum(np.asarray(year_log_contributions), 0)
        concentration = (
            float(positive_contrib.max() / positive_contrib.sum())
            if positive_contrib.sum() > 0
            else 1.0
        )
        leaveout_ok = True
        windows = {
            "dotcom": ("2000-03-24", "2002-10-09"),
            "gfc": ("2007-10-09", "2009-03-09"),
            "covid_selloff": ("2020-02-19", "2020-03-23"),
        }
        for name, (start, end) in windows.items():
            inside = dynamic["session_date"].between(start, end).to_numpy()
            value = float(np.expm1(active_daily[~inside].sum()))
            crisis_rows.append(
                {
                    "process_id": process_id,
                    "window": name,
                    "removed_sessions": int(inside.sum()),
                    "timing_value_excluding_window": value,
                }
            )
            leaveout_ok &= value > 0
        signal_gate = bool(
            complete_probability_path
            and brier_skill > 0
            and spearman_t1 < 0
            and spearman_t3 < 0
            and q_order
            and calibration_slope > 0
            and alert_valid
        )
        economic_gate = bool(
            timing_avg > 0
            and positive_year_fraction >= 0.60
            and timing_vol >= 0
            and concentration <= 0.50
            and leaveout_ok
        )
        metric_rows.append(
            {
                "process_id": process_id,
                "prediction_rows": len(pred),
                "evaluation_rows": len(ev),
                "brier": brier,
                "baseline_brier": base_brier,
                "brier_skill": brier_skill,
                "roc_auc": auc,
                "complete_probability_path": complete_probability_path,
                "spearman_t1": spearman_t1,
                "spearman_t3": spearman_t3,
                "cash_wins_q1": float(y[q1].mean()),
                "cash_wins_q5": float(y[q5].mean()),
                "mean_t1_q1": float(
                    ev["fwd_excess_logret_1w"].to_numpy(float)[q1].mean()
                ),
                "mean_t1_q5": float(
                    ev["fwd_excess_logret_1w"].to_numpy(float)[q5].mean()
                ),
                "mean_t3_q1": float(
                    ev["fwd_worst_excess_4w"].to_numpy(float)[q1].mean()
                ),
                "mean_t3_q5": float(
                    ev["fwd_worst_excess_4w"].to_numpy(float)[q5].mean()
                ),
                "calibration_intercept": calibration_intercept,
                "calibration_slope": calibration_slope,
                "ece": ece,
                "alert_rate": alert_rate,
                "annual_alert_non_degenerate": alert_valid,
                "matched_average_weight": avg_control_weight,
                "matched_vol_weight": vol_control_weight,
                "dynamic_terminal_nav": float(dynamic["nav"].iloc[-1]),
                "timing_value_vs_average": timing_avg,
                "timing_value_vs_same_vol": timing_vol,
                "positive_year_fraction": positive_year_fraction,
                "positive_year_concentration": concentration,
                "crisis_leaveout_all_positive": leaveout_ok,
                "signal_gate_pass": signal_gate,
                "economic_gate_pass": economic_gate,
                "all_hard_gates_pass": signal_gate and economic_gate,
            }
        )
        for label, frame in (
            ("dynamic", dynamic),
            ("matched_average", avg_control),
            ("matched_same_vol", vol_control),
        ):
            piece = frame.copy()
            piece.insert(0, "source", label)
            piece.insert(0, "process_id", process_id)
            nav_rows.append(piece)
    return (
        pd.DataFrame(metric_rows).sort_values("process_id", kind="mergesort"),
        pd.DataFrame(annual_rows),
        pd.DataFrame(crisis_rows),
        pd.concat(nav_rows, ignore_index=True),
    )


def replay_weekly_spy_cash(
    schedule: pd.DataFrame,
    market_daily: pd.DataFrame,
    risk_free_daily: pd.DataFrame,
    *,
    cost_bps: float,
) -> pd.DataFrame:
    market = market_daily.copy()
    market["session_date"] = pd.to_datetime(market["session_date"]).dt.normalize()
    market = market.sort_values("session_date", kind="mergesort")
    rf = risk_free_daily.copy()
    rf["session_date"] = pd.to_datetime(rf["session_date"]).dt.normalize()
    daily = market.merge(
        rf[["session_date", "rf_simple_decimal"]],
        on="session_date",
        how="left",
        validate="one_to_one",
    )
    sched = schedule.copy()
    sched["execution_session"] = pd.to_datetime(sched["execution_session"]).dt.normalize()
    sched = sched.sort_values("execution_session", kind="mergesort")
    if sched["execution_session"].duplicated().any():
        raise DataQualityError("duplicate R2C execution date")
    target_by_date = dict(
        zip(sched["execution_session"], sched["target_spy_weight"], strict=True)
    )
    start = sched["execution_session"].min()
    end = pd.Timestamp("2021-12-31")
    daily = daily.loc[daily["session_date"].between(start, end)].reset_index(drop=True)
    if daily.empty or daily["session_date"].iloc[0] != start:
        raise DataQualityError("R2C replay start is not an XNYS session")
    nav = 1.0
    spy_value = 0.0
    cash_value = 1.0
    prior_close = np.nan
    records = []
    for row in daily.itertuples(index=False):
        date = pd.Timestamp(row.session_date)
        open_price = float(row.tr_open)
        close_price = float(row.tr_close)
        rf_return = float(row.rf_simple_decimal)
        if np.isfinite(prior_close):
            spy_value *= open_price / prior_close
        pretrade = spy_value + cash_value
        turnover = 0.0
        cost = 0.0
        target = np.nan
        if date in target_by_date:
            target = float(target_by_date[date])
            pre_weight = spy_value / pretrade if pretrade > 0 else 0.0
            turnover = abs(target - pre_weight)
            cost = pretrade * (cost_bps / 10000.0) * turnover
            postcost = pretrade - cost
            spy_value = postcost * target
            cash_value = postcost - spy_value
        spy_value *= close_price / open_price
        cash_value *= 1.0 + rf_return
        close_nav = spy_value + cash_value
        daily_return = close_nav / nav - 1.0
        close_weight = spy_value / close_nav
        records.append(
            {
                "session_date": date,
                "nav": close_nav,
                "daily_return": daily_return,
                "rf_return": rf_return,
                "close_spy_weight": close_weight,
                "target_spy_weight": target,
                "turnover": turnover,
                "cost": cost,
            }
        )
        nav = close_nav
        prior_close = close_price
    result = pd.DataFrame(records)
    if not np.isfinite(result[["nav", "daily_return"]]).all().all():
        raise DataQualityError("non-finite R2C NAV")
    return result


def _constant_schedule(execution: pd.Series, weight: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "execution_session": pd.to_datetime(execution).to_numpy(),
            "target_spy_weight": float(weight),
        }
    )


def _solve_static_weight(
    dynamic: pd.DataFrame,
    execution: pd.Series,
    market: pd.DataFrame,
    rf: pd.DataFrame,
    *,
    objective: str,
    cost_bps: float,
) -> float:
    if objective == "average_exposure":
        target = float(dynamic["close_spy_weight"].mean())
        measure = lambda frame: float(frame["close_spy_weight"].mean())
    elif objective == "excess_volatility":
        target = float(
            np.std(dynamic["daily_return"] - dynamic["rf_return"], ddof=1)
        )
        measure = lambda frame: float(
            np.std(frame["daily_return"] - frame["rf_return"], ddof=1)
        )
    else:
        raise ValueError(objective)
    low, high = 0.0, 1.0
    low_value = measure(
        replay_weekly_spy_cash(
            _constant_schedule(execution, low), market, rf, cost_bps=cost_bps
        )
    )
    high_value = measure(
        replay_weekly_spy_cash(
            _constant_schedule(execution, high), market, rf, cost_bps=cost_bps
        )
    )
    if target < low_value - 1e-12 or target > high_value + 1e-12:
        raise DataQualityError(f"static control has no solution: {objective}")
    for _ in range(40):
        mid = (low + high) / 2.0
        value = measure(
            replay_weekly_spy_cash(
                _constant_schedule(execution, mid), market, rf, cost_bps=cost_bps
            )
        )
        if value < target:
            low = mid
        else:
            high = mid
    return float((low + high) / 2.0)


def _join_development_data(features: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    left = features.copy()
    right = targets.drop(
        columns=[
            column
            for column in ("week_id", "execution_session")
            if column in targets.columns
        ]
    )
    data = left.merge(right, on="signal_session", how="inner", validate="one_to_one")
    for column in (
        "signal_session",
        "execution_session",
        "next_1w_execution",
        "next_4w_execution",
    ):
        data[column] = pd.to_datetime(data[column]).dt.normalize()
    if data.loc[data["withheld_lockbox"], ["fwd_excess_logret_1w", "cash_wins_1w", "fwd_worst_excess_4w"]].notna().any().any():
        raise DataQualityError("R2B lockbox target leaked into R2C input")
    return data.sort_values("signal_session", kind="mergesort").reset_index(drop=True)


def _date_slice(
    data: pd.DataFrame, start: str, end: str, *, target: bool
) -> pd.DataFrame:
    result = data.loc[
        data["signal_session"].between(pd.Timestamp(start), pd.Timestamp(end))
        & data["feature_complete"]
    ].copy()
    if target:
        result = result.loc[result["target_available"]]
    return result.reset_index(drop=True)


def _sentinel_inner_oof(
    data: pd.DataFrame, outer: dict[str, Any], column: str, direction: float
) -> pd.DataFrame:
    pieces = []
    for inner in outer["inner_folds"]:
        validation = _date_slice(
            data,
            inner["validation_start_signal"],
            inner["validation_end_signal"],
            target=True,
        )
        pieces.append(
            pd.DataFrame(
                {
                    "signal_session": validation["signal_session"],
                    "inner_fold": int(inner["inner_fold"]),
                    "raw_defense_score": direction
                    * pd.to_numeric(validation[column], errors="coerce"),
                    "y": validation["cash_wins_1w"].astype(float),
                }
            )
        )
    return pd.concat(pieces, ignore_index=True)


def _model_inner_oof(
    data: pd.DataFrame,
    outer: dict[str, Any],
    family: str,
    penalty: float,
) -> pd.DataFrame:
    pieces = []
    for inner in outer["inner_folds"]:
        train = _date_slice(
            data, inner["train_start_signal"], inner["train_end_signal"], target=True
        )
        validation = _date_slice(
            data,
            inner["validation_start_signal"],
            inner["validation_end_signal"],
            target=True,
        )
        model = _fit_raw_model(train, family, penalty)
        pieces.append(
            pd.DataFrame(
                {
                    "signal_session": validation["signal_session"],
                    "inner_fold": int(inner["inner_fold"]),
                    "raw_defense_score": model(validation),
                    "y": validation["cash_wins_1w"].astype(float),
                }
            )
        )
    return pd.concat(pieces, ignore_index=True)


def _fit_raw_model(
    train: pd.DataFrame, family: str, penalty: float
) -> Callable[[pd.DataFrame], np.ndarray]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import SplineTransformer

    x = train[list(CORE_FEATURES)].to_numpy(float)
    y = train["cash_wins_1w"].to_numpy(int)
    transform = _fit_transform(x)
    x_scaled = transform.apply(x)
    spline = None
    if family == "gam":
        spline = SplineTransformer(
            n_knots=3,
            degree=2,
            knots="quantile",
            extrapolation="linear",
            include_bias=False,
        )
        x_scaled = spline.fit_transform(x_scaled)
    elif family != "ridge":
        raise ValueError(family)
    model = LogisticRegression(
        C=1.0 / penalty,
        l1_ratio=0.0,
        solver="lbfgs",
        fit_intercept=True,
        max_iter=2000,
        random_state=20260816,
    )
    model.fit(x_scaled, y)

    def predict(frame: pd.DataFrame) -> np.ndarray:
        values = transform.apply(frame[list(CORE_FEATURES)].to_numpy(float))
        if spline is not None:
            values = spline.transform(values)
        return np.asarray(model.decision_function(values), dtype=float)

    return predict


def _fit_transform(x: np.ndarray) -> _Transform:
    lower = np.nanquantile(x, 0.01, axis=0)
    upper = np.nanquantile(x, 0.99, axis=0)
    clipped = np.clip(x, lower, upper)
    median = np.nanmedian(clipped, axis=0)
    missing = ~np.isfinite(clipped)
    if missing.any():
        clipped[missing] = np.take(median, np.where(missing)[1])
    mean = clipped.mean(axis=0)
    std = clipped.std(axis=0, ddof=1)
    if not np.isfinite(std).all() or (std <= 0).any():
        raise DataQualityError("invalid R2C training standard deviation")
    return _Transform(lower, upper, median, mean, std)


def _prequential_calibration(
    oof: pd.DataFrame, *, refit_all: bool = False
) -> tuple[pd.DataFrame, tuple[float, float]]:
    result = oof.copy()
    result["p_cash_wins"] = np.nan
    folds = sorted(result["inner_fold"].unique())
    for fold in folds[1:]:
        prior = result["inner_fold"] < fold
        current = result["inner_fold"] == fold
        intercept, slope = _fit_platt(
            result.loc[prior, "raw_defense_score"].to_numpy(float),
            result.loc[prior, "y"].to_numpy(float),
        )
        result.loc[current, "p_cash_wins"] = _sigmoid(
            intercept
            + slope * result.loc[current, "raw_defense_score"].to_numpy(float)
        )
    calibrator = _fit_platt(
        result["raw_defense_score"].to_numpy(float), result["y"].to_numpy(float)
    )
    if refit_all:
        return result, calibrator
    return result, calibrator


def _fit_platt(
    raw: np.ndarray, y: np.ndarray, *, enforce_positive: bool = True
) -> tuple[float, float]:
    from sklearn.linear_model import LogisticRegression

    if len(np.unique(y)) != 2:
        raise DataQualityError("Platt training fold has one class")
    model = LogisticRegression(
        C=np.inf,
        l1_ratio=0.0,
        solver="lbfgs",
        fit_intercept=True,
        max_iter=1000,
        random_state=20260816,
    )
    model.fit(raw.reshape(-1, 1), y.astype(int))
    slope = float(model.coef_[0, 0])
    if not np.isfinite(slope) or (enforce_positive and slope <= 0):
        raise DataQualityError("Platt slope is non-positive")
    return float(model.intercept_[0]), slope


def _select_arm_one_se(
    calibrated: dict[str, pd.DataFrame], *, complexity: list[str], seed: int
) -> tuple[str, list[dict[str, Any]]]:
    usable = {}
    for arm, frame in calibrated.items():
        valid = frame["p_cash_wins"].notna()
        usable[arm] = frame.loc[valid].reset_index(drop=True)
    keys = list(usable)
    reference = usable[keys[0]][["signal_session", "y"]]
    for key in keys[1:]:
        pd.testing.assert_frame_equal(
            reference,
            usable[key][["signal_session", "y"]],
            check_dtype=False,
        )
    losses = {
        key: (frame["p_cash_wins"].to_numpy(float) - frame["y"].to_numpy(float))
        ** 2
        for key, frame in usable.items()
    }
    mean_loss = {key: float(value.mean()) for key, value in losses.items()}
    best = min(mean_loss, key=mean_loss.get)
    eligible = []
    se_by_arm = {}
    for offset, key in enumerate(keys):
        diff = losses[key] - losses[best]
        se = _moving_block_mean_se(diff, block=13, repetitions=2000, seed=seed + offset)
        se_by_arm[key] = se
        if float(diff.mean()) <= se + 1e-15:
            eligible.append(key)
    selected = next(key for key in complexity if key in eligible)
    rows = [
        {
            "arm_id": key,
            "inner_prequential_brier": mean_loss[key],
            "paired_se_vs_best": se_by_arm[key],
            "one_se_eligible": key in eligible,
            "selected": key == selected,
            "fit_status": "valid",
        }
        for key in keys
    ]
    return selected, rows


def _outer_prediction_frame(
    test: pd.DataFrame,
    *,
    process_id: str,
    selected_arm_id: str,
    outer_year: int,
    raw: np.ndarray,
    calibrator: tuple[float, float] | None,
    threshold: float,
    base_rate: float,
    fit_status: str,
) -> pd.DataFrame:
    if calibrator is None:
        intercept = slope = float("nan")
        p = np.full(len(raw), np.nan)
    else:
        intercept, slope = calibrator
        p = _sigmoid(intercept + slope * raw)
    alert = raw >= threshold
    return pd.DataFrame(
        {
            "process_id": process_id,
            "selected_arm_id": selected_arm_id,
            "outer_year": outer_year,
            "week_id": test["week_id"].to_numpy(),
            "signal_session": test["signal_session"].to_numpy(),
            "execution_session": test["execution_session"].to_numpy(),
            "raw_defense_score": raw,
            "p_cash_wins": p,
            "platt_intercept": intercept,
            "platt_slope": slope,
            "train_q75": threshold,
            "alert": alert,
            "target_spy_weight": np.where(alert, 0.5, 1.0),
            "fit_status": fit_status,
            "base_rate": base_rate,
            "target_available": test["target_available"].to_numpy(bool),
            "t3_available": test["t3_available"].to_numpy(bool),
            "fwd_excess_logret_1w": test["fwd_excess_logret_1w"].to_numpy(float),
            "cash_wins_1w": test["cash_wins_1w"].to_numpy(float),
            "fwd_worst_excess_4w": test["fwd_worst_excess_4w"].to_numpy(float),
        }
    )


def _selector_record(
    year: int,
    process_id: str,
    arm_id: str,
    brier: float,
    threshold: float,
    calibrator: tuple[float, float] | None,
    inner_rows: int,
    fit_status: str,
) -> dict[str, Any]:
    intercept, slope = (
        calibrator if calibrator is not None else (float("nan"), float("nan"))
    )
    return {
        "outer_year": year,
        "process_id": process_id,
        "selected_arm_id": arm_id,
        "inner_prequential_brier": brier,
        "inner_oof_rows": inner_rows,
        "train_q75": threshold,
        "platt_intercept": intercept,
        "platt_slope": slope,
        "fit_status": fit_status,
    }


def _moving_block_mean_se(
    values: np.ndarray, *, block: int, repetitions: int, seed: int
) -> float:
    values = np.asarray(values, dtype=float)
    n = len(values)
    rng = np.random.default_rng(seed)
    blocks = int(np.ceil(n / block))
    stats = np.empty(repetitions)
    offsets = np.arange(block)
    for index in range(repetitions):
        starts = rng.integers(0, n - block + 1, size=blocks)
        take = (starts[:, None] + offsets).reshape(-1)[:n]
        stats[index] = values[take].mean()
    return float(stats.std(ddof=1))


def _sigmoid(value: np.ndarray | float) -> np.ndarray:
    x = np.asarray(value, dtype=float)
    return np.where(x >= 0, 1 / (1 + np.exp(-x)), np.exp(x) / (1 + np.exp(x)))


def _spearman(left: np.ndarray, right: np.ndarray) -> float:
    return float(
        np.corrcoef(
            pd.Series(left).rank(method="average"),
            pd.Series(right).rank(method="average"),
        )[0, 1]
    )


def _roc_auc(y: np.ndarray, score: np.ndarray) -> float:
    ranks = pd.Series(score).rank(method="average").to_numpy(float)
    positive = y == 1
    n_pos = int(positive.sum())
    n_neg = len(y) - n_pos
    return float((ranks[positive].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def _calibration_diagnostic(p: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    clipped = np.clip(p, 1e-8, 1 - 1e-8)
    return _fit_platt(
        np.log(clipped / (1 - clipped)), y, enforce_positive=False
    )


def _expected_calibration_error(p: np.ndarray, y: np.ndarray) -> float:
    bins = np.minimum((p * 10).astype(int), 9)
    total = 0.0
    for value in range(10):
        mask = bins == value
        if mask.any():
            total += float(mask.mean()) * abs(float(p[mask].mean() - y[mask].mean()))
    return total


def _verify_authorization(root: Path, r2a: Path, r2b: Path) -> dict[str, Any]:
    path = root / "config" / "experiments" / "round2" / "R2C_DEVELOPMENT_AUTH.json"
    auth = json.loads(path.read_text(encoding="utf-8"))
    if auth["status"] != "authorized_simple_development_only":
        raise DataQualityError("R2C simple development is not authorized")
    if auth["authorization"]["r2c_lockbox"]:
        raise DataQualityError("R2C authorization unexpectedly opens lockbox")
    if sha256_file(r2b / "manifest.json") != auth["inputs"]["r2b_manifest_sha256"]:
        raise DataQualityError("R2B manifest differs from authorization")
    actual = sorted(p.relative_to(r2b).as_posix() for p in r2b.rglob("*") if p.is_file())
    members = []
    for rel in actual:
        p = r2b / rel
        members.append(f"{rel}|{p.stat().st_size}|{sha256_file(p)}")
    tree = hashlib.sha256("\n".join(members).encode()).hexdigest()
    if tree != auth["inputs"]["r2b_tree_sha256"]:
        raise DataQualityError("R2B tree differs from authorization")
    r2a_manifest = sha256_file(r2a / "manifest.json")
    if r2a_manifest != auth["inputs"]["r2a_manifest_sha256"]:
        raise DataQualityError("R2A manifest differs from authorization")
    return auth


def _build_provenance(root: Path) -> dict[str, Any]:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if status:
        raise DataQualityError("R2C bundle requires a clean workspace")
    return {
        "git_commit": commit,
        "workspace_dirty": False,
        "dependency_versions": {
            name: importlib_metadata.version(name)
            for name in ("numpy", "pandas", "pyarrow", "scikit-learn", "scipy")
        },
        "code_file_sha256": {
            rel: sha256_file(root / rel)
            for rel in (
                "scripts/build_round2_r2c.py",
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
