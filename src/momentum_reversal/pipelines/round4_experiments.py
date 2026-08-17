"""Round 4 development-only experiment runners.

All runners require the byte-locked Round 4 preregistration and reject the
2022+ mechanical lockbox.  The module contains no model fitting.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import average_precision_score, roc_auc_score

from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.round2_market import sha256_file


PROGRAM_ID = "defense_factor_audit_round4_v1"
LOCKBOX_START = pd.Timestamp("2022-01-03")
MAX_SIGNAL = pd.Timestamp("2021-12-23")


@dataclass(frozen=True, slots=True)
class Round4BatchResult:
    output_dir: Path
    manifest_path: Path
    status: str


def run_r4b(
    *, project_root: str | Path, runtime_root: str | Path, run_id: str
) -> Round4BatchResult:
    root, runtime, prereg, r4a, parent = _load_inputs(project_root, runtime_root)
    output = runtime / "results/experiments/round4/R4B_T2_SINGLE_FACTOR_REFERENCE/runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    features = pd.read_parquet(r4a / "feature_inputs_weekly.parquet")
    features["signal_session"] = pd.to_datetime(features["signal_session"]).dt.normalize()
    registry = pd.read_csv(root / "config/experiments/round4/factor_registry_resolved.csv")
    eligible = registry.loc[registry["eligibility_status"].eq("reference_eligible"), "arm_id"].tolist()
    market = pd.read_parquet(parent / "curated/market_daily.parquet")
    rf = pd.read_parquet(parent / "curated/risk_free_daily.parquet")
    calendar = pd.read_parquet(parent / "curated/decision_calendar.parquet")
    targets = build_t1_t2(market, rf, calendar)
    targets.to_parquet(output / "targets_weekly.parquet", index=False, compression="zstd")

    threshold_rows: list[dict[str, Any]] = []
    signal_parts: list[pd.DataFrame] = []
    for arm_id in eligible:
        arm = features.loc[features["arm_id"].eq(arm_id)].sort_values("signal_session").copy()
        arm = arm.merge(
            calendar[["signal_session", "execution_session"]],
            on="signal_session",
            how="left",
            validate="one_to_one",
        )
        arm["execution_session"] = pd.to_datetime(arm["execution_session"]).dt.normalize()
        arm["execution_year"] = arm["execution_session"].dt.year
        arm["target_spy_weight"] = np.nan
        arm["threshold_q75"] = np.nan
        arm["signal_valid"] = False
        state = 1.0
        opened = False
        for year in range(2005, 2022):
            test = arm["execution_year"].eq(year)
            history = pd.to_numeric(
                arm.loc[arm["execution_session"] < pd.Timestamp(year=year, month=1, day=1), "defense_score"],
                errors="coerce",
            ).dropna()
            valid = len(history) >= 260
            threshold = float(history.quantile(0.75, interpolation="linear")) if valid else np.nan
            threshold_rows.append(
                {
                    "arm_id": arm_id,
                    "execution_year": year,
                    "history_weeks": len(history),
                    "threshold_q75": threshold,
                    "year_valid": valid,
                }
            )
            for index in arm.index[test]:
                score = float(arm.at[index, "defense_score"])
                score_valid = valid and np.isfinite(score)
                if score_valid:
                    state = 0.5 if score > threshold else 1.0
                    opened = True
                    arm.at[index, "signal_valid"] = True
                arm.at[index, "target_spy_weight"] = state if opened else 1.0
                arm.at[index, "threshold_q75"] = threshold
        arm = arm.loc[arm["execution_year"].between(2005, 2021)].copy()
        arm["alert"] = arm["target_spy_weight"].eq(0.5) & arm["signal_valid"]
        signal_parts.append(
            arm[
                [
                    "arm_id",
                    "week_id",
                    "signal_session",
                    "execution_session",
                    "defense_score",
                    "threshold_q75",
                    "signal_valid",
                    "alert",
                    "target_spy_weight",
                ]
            ]
        )
    thresholds = pd.DataFrame(threshold_rows)
    signals = pd.concat(signal_parts, ignore_index=True)
    thresholds.to_parquet(output / "annual_thresholds.parquet", index=False, compression="zstd")
    signals.to_parquet(output / "signals_weekly.parquet", index=False, compression="zstd")

    nav_parts: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    yearly_parts: list[pd.DataFrame] = []
    quintile_parts: list[pd.DataFrame] = []
    active_weekly_by_arm: dict[str, pd.Series] = {}
    for arm_id in eligible:
        arm_signal = signals.loc[signals["arm_id"].eq(arm_id)].copy()
        first = arm_signal.loc[arm_signal["signal_valid"], "execution_session"].min()
        if pd.isna(first):
            continue
        arm_signal = arm_signal.loc[arm_signal["execution_session"] >= first]
        valid_targets = arm_signal.loc[arm_signal["signal_valid"], ["execution_session", "target_spy_weight"]]
        daily_target = _held_daily_target(valid_targets, market, start=first, end=pd.Timestamp("2021-12-31"))
        static_weight = float(daily_target.mean())
        static_schedule = valid_targets.copy()
        static_schedule["target_spy_weight"] = static_weight
        dynamic_main: pd.DataFrame | None = None
        static_main: pd.DataFrame | None = None
        for cost_bps in (0, 5, 10, 20):
            dynamic = replay_spy_cash(
                market, rf, valid_targets, start=first, end=pd.Timestamp("2021-12-31"), cost_bps=cost_bps
            )
            static = replay_spy_cash(
                market, rf, static_schedule, start=first, end=pd.Timestamp("2021-12-31"), cost_bps=cost_bps
            )
            for kind, frame in (("dynamic", dynamic), ("matched_static", static)):
                part = frame.copy()
                part.insert(0, "arm_id", arm_id)
                part.insert(1, "path_type", kind)
                part.insert(2, "cost_bps", cost_bps)
                nav_parts.append(part)
            if cost_bps == 10:
                dynamic_main, static_main = dynamic, static
        assert dynamic_main is not None and static_main is not None
        active = dynamic_main.set_index("date")["nav"] / static_main.set_index("date")["nav"]
        active_log = np.log(active).diff().fillna(np.log(active.iloc[0]))
        active_weekly_by_arm[arm_id] = active_log.resample("W-FRI").sum()
        joined = arm_signal.merge(targets, on="signal_session", how="left", validate="one_to_one")
        valid_diag = joined["signal_valid"] & joined["target_available"] & joined["defense_score"].notna()
        diag = joined.loc[valid_diag].copy()
        y = diag["cash_wins_1w"].astype(int)
        score = diag["defense_score"].astype(float)
        auc = float(roc_auc_score(y, score)) if y.nunique() == 2 else np.nan
        pr_auc = float(average_precision_score(y, score)) if y.nunique() == 2 else np.nan
        rho = float(spearmanr(score, diag["fwd_excess_logret_1w"]).statistic)
        alert = diag["alert"].astype(bool)
        precision = float(y[alert].mean()) if alert.any() else np.nan
        recall = float(alert[y.eq(1)].mean()) if y.eq(1).any() else np.nan
        losses = (-diag["fwd_excess_logret_1w"]).clip(lower=0)
        capture = float(losses[alert].sum() / losses.sum()) if losses.sum() > 0 else np.nan
        years = pd.DatetimeIndex(active.index).year
        yearly = pd.DataFrame({"date": active.index, "active_log_return": active_log.to_numpy()})
        yearly["execution_year"] = years
        yearly = yearly.groupby("execution_year", as_index=False)["active_log_return"].sum()
        yearly.insert(0, "arm_id", arm_id)
        yearly["positive"] = yearly["active_log_return"] > 0
        yearly_parts.append(yearly)
        positive_year_fraction = float(yearly["positive"].mean())
        ci_low, p_value = _block_bootstrap_lower(active_weekly_by_arm[arm_id], 13, 2000, 20260817)
        dyn_metrics = _performance(dynamic_main)
        stat_metrics = _performance(static_main)
        active_terminal = float(active.iloc[-1] - 1.0)
        reference = bool(auc > 0.5 and rho < 0 and active_terminal > 0 and positive_year_fraction >= 0.60)
        summaries.append(
            {
                "arm_id": arm_id,
                "native_start": first,
                "oos_target_weeks": len(diag),
                "auc_t2": auc,
                "pr_auc_t2": pr_auc,
                "spearman_t1": rho,
                "alert_precision": precision,
                "alert_recall": recall,
                "negative_amount_capture": capture,
                "mean_daily_target_weight": static_weight,
                "dynamic_cagr": dyn_metrics["cagr"],
                "dynamic_sharpe": dyn_metrics["sharpe"],
                "dynamic_mdd": dyn_metrics["mdd"],
                "dynamic_turnover": float(dynamic_main["turnover"].sum()),
                "static_cagr": stat_metrics["cagr"],
                "active_terminal_wealth": active_terminal,
                "positive_year_fraction": positive_year_fraction,
                "block90_lower_active_logwealth": ci_low,
                "one_sided_block_p": p_value,
                "reference_positive": reference,
            }
        )
        diag["quintile"] = pd.qcut(diag["defense_score"], 5, labels=False, duplicates="drop") + 1
        q = diag.groupby("quintile", as_index=False).agg(
            weeks=("cash_wins_1w", "size"),
            cash_win_rate=("cash_wins_1w", "mean"),
            mean_t1=("fwd_excess_logret_1w", "mean"),
        )
        q.insert(0, "arm_id", arm_id)
        quintile_parts.append(q)
    summary = pd.DataFrame(summaries)
    summary["bh_q_value"] = _bh_adjust(summary["one_sided_block_p"].to_numpy(float))
    summary["robust_reference_positive"] = (
        summary["reference_positive"]
        & (summary["block90_lower_active_logwealth"] > 0)
        & (summary["bh_q_value"] <= 0.10)
    )
    pd.concat(nav_parts, ignore_index=True).to_parquet(output / "nav_daily.parquet", index=False, compression="zstd")
    summary.to_csv(output / "arm_summary.csv", index=False, lineterminator="\n")
    pd.concat(yearly_parts, ignore_index=True).to_csv(output / "yearly_contributions.csv", index=False, lineterminator="\n")
    pd.concat(quintile_parts, ignore_index=True).to_csv(output / "quintiles.csv", index=False, lineterminator="\n")
    manifest = _write_batch_manifest(
        output, root, prereg, "R4B_T2_SINGLE_FACTOR_REFERENCE", run_id,
        counts={"eligible_arms": len(eligible), "summary_rows": len(summary), "target_rows": len(targets)},
    )
    return Round4BatchResult(output, output / "manifest.json", manifest["status"])


def build_t1_t2(
    market_daily: pd.DataFrame, risk_free_daily: pd.DataFrame, decision_calendar: pd.DataFrame
) -> pd.DataFrame:
    market = market_daily.copy()
    market["session_date"] = pd.to_datetime(market["session_date"]).dt.normalize()
    market = market.sort_values("session_date").set_index("session_date")
    rf = risk_free_daily.copy()
    rf["session_date"] = pd.to_datetime(rf["session_date"]).dt.normalize()
    rf = rf.sort_values("session_date").set_index("session_date")
    sessions = market.index
    position = {date: index for index, date in enumerate(sessions)}
    opens = market["tr_open"].to_numpy(float)
    rf_log = rf.reindex(sessions)["rf_log"].to_numpy(float)
    cumulative = np.r_[0.0, np.cumsum(rf_log)]
    calendar = decision_calendar.copy()
    for column in ("signal_session", "execution_session", "next_1w_execution"):
        calendar[column] = pd.to_datetime(calendar[column]).dt.normalize()
    rows: list[dict[str, Any]] = []
    for row in calendar.itertuples(index=False):
        signal = pd.Timestamp(row.signal_session)
        if signal > MAX_SIGNAL:
            continue
        e0, e1 = pd.Timestamp(row.execution_session), pd.Timestamp(row.next_1w_execution)
        available = e0 in position and e1 in position and e1 < LOCKBOX_START
        t1 = np.nan
        if available:
            i0, i1 = position[e0], position[e1]
            t1 = float(np.log(opens[i1] / opens[i0]) - (cumulative[i1] - cumulative[i0]))
        rows.append(
            {
                "week_id": row.week_id,
                "signal_session": signal,
                "execution_session": e0,
                "next_1w_execution": e1,
                "target_available": available,
                "fwd_excess_logret_1w": t1,
                "cash_wins_1w": float(t1 < 0) if available else np.nan,
            }
        )
    return pd.DataFrame(rows)


def replay_spy_cash(
    market_daily: pd.DataFrame,
    risk_free_daily: pd.DataFrame,
    schedule: pd.DataFrame,
    *, start: pd.Timestamp,
    end: pd.Timestamp,
    cost_bps: float,
) -> pd.DataFrame:
    market = market_daily.copy()
    market["session_date"] = pd.to_datetime(market["session_date"]).dt.normalize()
    market = market.set_index("session_date").sort_index().loc[start:end]
    rf = risk_free_daily.copy()
    rf["session_date"] = pd.to_datetime(rf["session_date"]).dt.normalize()
    rf = rf.set_index("session_date").sort_index().reindex(market.index)
    targets = {
        pd.Timestamp(row.execution_session): float(row.target_spy_weight)
        for row in schedule.itertuples(index=False)
    }
    risky, cash, previous_close = 0.0, 1.0, np.nan
    rows: list[dict[str, Any]] = []
    for date, row in market.iterrows():
        turnover = cost = 0.0
        if date in targets:
            if np.isfinite(previous_close):
                risky *= float(row.tr_open) / previous_close
            pre_nav = risky + cash
            pre_weight = risky / pre_nav if pre_nav > 0 else 0.0
            target = targets[date]
            turnover = abs(target - pre_weight)
            cost = pre_nav * cost_bps / 10000.0 * turnover
            post_nav = pre_nav - cost
            risky, cash = target * post_nav, (1.0 - target) * post_nav
            risky *= float(row.tr_close) / float(row.tr_open)
        else:
            if np.isfinite(previous_close):
                risky *= float(row.tr_close) / previous_close
        cash *= 1.0 + float(rf.at[date, "rf_simple_decimal"])
        nav = risky + cash
        daily_return = nav / rows[-1]["nav"] - 1.0 if rows else nav - 1.0
        rows.append(
            {
                "date": date,
                "nav": nav,
                "daily_return": daily_return,
                "spy_weight": risky / nav,
                "cash_weight": cash / nav,
                "turnover": turnover,
                "cost_amount": cost,
            }
        )
        previous_close = float(row.tr_close)
    return pd.DataFrame(rows)


def _held_daily_target(
    schedule: pd.DataFrame, market: pd.DataFrame, *, start: pd.Timestamp, end: pd.Timestamp
) -> pd.Series:
    sessions = pd.DatetimeIndex(pd.to_datetime(market["session_date"])).normalize()
    sessions = sessions[(sessions >= start) & (sessions <= end)]
    mapping = schedule.set_index("execution_session")["target_spy_weight"]
    return pd.Series(mapping.reindex(sessions).ffill().to_numpy(float), index=sessions)


def _performance(nav: pd.DataFrame) -> dict[str, float]:
    wealth = nav["nav"].to_numpy(float)
    years = len(nav) / 252.0
    cagr = wealth[-1] ** (1.0 / years) - 1.0
    returns = nav["daily_return"].to_numpy(float)
    std = np.std(returns, ddof=1)
    sharpe = np.mean(returns) / std * np.sqrt(252.0) if std > 0 else np.nan
    series = np.r_[1.0, wealth]
    mdd = float(np.min(series / np.maximum.accumulate(series) - 1.0))
    return {"cagr": float(cagr), "sharpe": float(sharpe), "mdd": mdd}


def _block_bootstrap_lower(
    values: pd.Series, block: int, repetitions: int, seed: int
) -> tuple[float, float]:
    x = values.dropna().to_numpy(float)
    rng = np.random.default_rng(seed)
    estimates = np.empty(repetitions)
    starts = np.arange(max(1, len(x) - block + 1))
    for index in range(repetitions):
        sampled: list[float] = []
        while len(sampled) < len(x):
            start = int(rng.choice(starts))
            sampled.extend(x[start : start + block])
        estimates[index] = np.sum(sampled[: len(x)])
    return float(np.quantile(estimates, 0.05)), float(np.mean(estimates <= 0.0))


def _bh_adjust(p: np.ndarray) -> np.ndarray:
    order = np.argsort(p)
    ranked = p[order]
    adjusted = np.minimum.accumulate((ranked * len(p) / np.arange(1, len(p) + 1))[::-1])[::-1]
    result = np.empty_like(adjusted)
    result[order] = np.minimum(adjusted, 1.0)
    return result


def _load_inputs(
    project_root: str | Path, runtime_root: str | Path
) -> tuple[Path, Path, dict[str, Any], Path, Path]:
    root, runtime = Path(project_root).resolve(), Path(runtime_root).resolve()
    lock_path = root / "config/experiments/round4/PREREG_LOCK.json"
    prereg = json.loads(lock_path.read_text(encoding="utf-8"))
    for relative, expected in prereg["files"].items():
        if sha256_file(root / relative) != expected:
            raise DataQualityError(f"Round4 prereg hash mismatch: {relative}")
    if prereg["authorization"]["lockbox"] is not False:
        raise DataQualityError("Round4 lockbox firewall is not closed")
    r4a = runtime / "data/round4/staging/R4A_FREE_FACTOR_DATA" / prereg["r4a"]["run_id"]
    if sha256_file(r4a / "manifest.json") != prereg["r4a"]["manifest_sha256"]:
        raise DataQualityError("R4A manifest anchor mismatch")
    parent = runtime / "data/round2/staging/R2A_DATA/r2a-long-free-20260816-v1"
    return root, runtime, prereg, r4a, parent


def _write_batch_manifest(
    output: Path,
    root: Path,
    prereg: dict[str, Any],
    batch_id: str,
    run_id: str,
    *, counts: dict[str, int],
) -> dict[str, Any]:
    files = []
    for path in sorted((p for p in output.rglob("*") if p.is_file()), key=lambda p: p.relative_to(output).as_posix()):
        files.append(
            {
                "path": path.relative_to(output).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    manifest = {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "batch_id": batch_id,
        "run_id": run_id,
        "status": "completed_development",
        "formal_eligible": False,
        "maximum_signal": str(MAX_SIGNAL.date()),
        "lockbox_read": False,
        "models_run": False,
        "position_search_run": False,
        "prereg_lock_sha256": sha256_file(root / "config/experiments/round4/PREREG_LOCK.json"),
        "r4a_manifest_sha256": prereg["r4a"]["manifest_sha256"],
        "counts": counts,
        "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip(),
        "files": files,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return manifest
