"""Candidate-independent R4C target audit and R4D drawdown atlas."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from momentum_reversal.pipelines.round4_experiments import (
    LOCKBOX_START,
    MAX_SIGNAL,
    Round4BatchResult,
    _load_inputs,
    _write_batch_manifest,
    build_t1_t2,
)


def run_r4c(
    *, project_root: str | Path, runtime_root: str | Path, run_id: str
) -> Round4BatchResult:
    root, runtime, prereg, _, parent = _load_inputs(project_root, runtime_root)
    output = runtime / "results/experiments/round4/R4C_TARGET_SANITY/runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    market = pd.read_parquet(parent / "curated/market_daily.parquet")
    rf = pd.read_parquet(parent / "curated/risk_free_daily.parquet")
    calendar = pd.read_parquet(parent / "curated/decision_calendar.parquet")
    outcomes = build_horizon_outcomes(market, rf, calendar)
    t1 = outcomes.loc[outcomes["h1_available"], "excess_logret_1w"].dropna()
    thresholds = []
    for bps in (-40, -20, -10, 0, 10, 20, 40):
        label = t1 < bps / 10000.0
        thresholds.append(
            {
                "threshold_bps": bps,
                "weeks": len(label),
                "positive_rate": float(label.mean()),
                "flip_rate_vs_zero": float((label != (t1 < 0)).mean()),
            }
        )
    threshold_frame = pd.DataFrame(thresholds)
    threshold_frame.to_csv(output / "threshold_sensitivity.csv", index=False, lineterminator="\n")

    states = (t1 < 0).astype(int)
    runs = _state_runs(states)
    runs.to_csv(output / "state_runs.csv", index=False, lineterminator="\n")
    valid = outcomes.loc[outcomes["h1_available"]].copy()
    valid["t2"] = valid["excess_logret_1w"] < 0
    valid["mismatch_risk_on_deep_mae13"] = (
        valid["t2"].eq(False) & valid["mae13"].ge(0.10)
    )
    valid["mismatch_defense_shallow_mae13"] = (
        valid["t2"].eq(True) & valid["mae13"].lt(0.02)
    )
    conflict_rows = []
    for horizon in (4, 13, 26):
        available = valid[f"h{horizon}_available"]
        sample = valid.loc[available]
        conflict_rows.append(
            {
                "horizon_weeks": horizon,
                "weeks": len(sample),
                "t2_risk_on_terminal_negative": int(
                    ((~sample["t2"]) & sample[f"excess_logret_{horizon}w"].lt(0)).sum()
                ),
                "t2_defense_terminal_positive": int(
                    (sample["t2"] & sample[f"excess_logret_{horizon}w"].ge(0)).sum()
                ),
                "risk_on_mae_ge_10pct": int(
                    ((~sample["t2"]) & sample[f"mae{horizon}"].ge(0.10)).sum()
                ),
                "defense_mae_lt_2pct": int(
                    (sample["t2"] & sample[f"mae{horizon}"].lt(0.02)).sum()
                ),
            }
        )
    pd.DataFrame(conflict_rows).to_csv(output / "horizon_conflicts.csv", index=False, lineterminator="\n")

    oracle = _oracle_paths(outcomes, seed=20260817)
    oracle.to_csv(output / "oracle_paths.csv", index=False, lineterminator="\n")
    summary = pd.DataFrame(
        [
            ("h1_weeks", len(t1)),
            ("t2_positive_rate", float((t1 < 0).mean())),
            ("near_zero_20bp_fraction", float((t1.abs() <= 0.002).mean())),
            ("negative_amount_top_quartile_share", _negative_concentration(t1, 0.25)),
            ("t2_lag1_autocorrelation", float(states.autocorr(1))),
            ("risk_on_deep_mae13_weeks", int(valid["mismatch_risk_on_deep_mae13"].sum())),
            ("defense_shallow_mae13_weeks", int(valid["mismatch_defense_shallow_mae13"].sum())),
            ("lockbox_read", False),
            ("factors_rerun", False),
        ],
        columns=["metric", "value"],
    )
    summary.to_csv(output / "target_sanity_summary.csv", index=False, lineterminator="\n")
    outcomes.to_parquet(output / "horizon_outcomes.parquet", index=False, compression="zstd")
    manifest = _write_batch_manifest(
        output, root, prereg, "R4C_TARGET_SANITY", run_id,
        counts={"h1_weeks": len(t1), "outcome_rows": len(outcomes), "thresholds": len(threshold_frame)},
    )
    return Round4BatchResult(output, output / "manifest.json", manifest["status"])


def build_horizon_outcomes(
    market_daily: pd.DataFrame, risk_free_daily: pd.DataFrame, decision_calendar: pd.DataFrame
) -> pd.DataFrame:
    market = market_daily.copy()
    market["session_date"] = pd.to_datetime(market["session_date"]).dt.normalize()
    market = market.sort_values("session_date").set_index("session_date")
    rf = risk_free_daily.copy()
    rf["session_date"] = pd.to_datetime(rf["session_date"]).dt.normalize()
    rf = rf.sort_values("session_date").set_index("session_date").reindex(market.index)
    sessions = market.index
    pos = {date: index for index, date in enumerate(sessions)}
    opens = market["tr_open"].to_numpy(float)
    closes = market["tr_close"].to_numpy(float)
    rf_log = rf["rf_log"].to_numpy(float)
    csum = np.r_[0.0, np.cumsum(rf_log)]
    calendar = decision_calendar.copy().sort_values("signal_session").reset_index(drop=True)
    for column in ("signal_session", "execution_session"):
        calendar[column] = pd.to_datetime(calendar[column]).dt.normalize()
    executions = pd.DatetimeIndex(calendar["execution_session"])
    rows: list[dict[str, Any]] = []
    for index, row in calendar.iterrows():
        signal, e0 = pd.Timestamp(row.signal_session), pd.Timestamp(row.execution_session)
        if signal > MAX_SIGNAL or e0 not in pos:
            continue
        record: dict[str, Any] = {
            "week_id": row.week_id,
            "signal_session": signal,
            "execution_session": e0,
        }
        i0 = pos[e0]
        for horizon in (1, 4, 13, 26):
            endpoint = executions[index + horizon] if index + horizon < len(executions) else pd.NaT
            available = pd.notna(endpoint) and pd.Timestamp(endpoint) < LOCKBOX_START and endpoint in pos
            record[f"h{horizon}_available"] = bool(available)
            record[f"endpoint_{horizon}w"] = endpoint
            record[f"excess_logret_{horizon}w"] = np.nan
            record[f"mae{horizon}"] = np.nan
            if available:
                ih = pos[pd.Timestamp(endpoint)]
                terminal = np.log(opens[ih] / opens[i0]) - (csum[ih] - csum[i0])
                close_path = np.log(closes[i0:ih] / opens[i0]) - (
                    csum[i0 + 1 : ih + 1] - csum[i0]
                )
                worst = min(0.0, float(np.min(close_path)), float(terminal))
                record[f"excess_logret_{horizon}w"] = float(terminal)
                record[f"mae{horizon}"] = -worst
        rows.append(record)
    return pd.DataFrame(rows)

def run_r4d(
    *, project_root: str | Path, runtime_root: str | Path, run_id: str
) -> Round4BatchResult:
    root, runtime, prereg, r4a, parent = _load_inputs(project_root, runtime_root)
    output = runtime / "results/experiments/round4/R4D_SPY_DRAWDOWN_ATLAS/runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    figures = output / "figures"
    figures.mkdir()
    market = pd.read_parquet(parent / "curated/market_daily.parquet")
    market["session_date"] = pd.to_datetime(market["session_date"]).dt.normalize()
    market = market.loc[market["session_date"] <= pd.Timestamp("2021-12-31")]
    episodes = build_drawdown_episodes(market)
    episodes.to_csv(output / "episodes.csv", index=False, lineterminator="\n")
    calendar = pd.read_parquet(parent / "curated/decision_calendar.parquet")
    calendar["signal_session"] = pd.to_datetime(calendar["signal_session"]).dt.normalize()
    features = pd.read_parquet(r4a / "feature_inputs_weekly.parquet")
    features["signal_session"] = pd.to_datetime(features["signal_session"]).dt.normalize()
    eligible = pd.read_csv(root / "config/experiments/round4/factor_registry_resolved.csv")
    eligible = eligible.loc[eligible["eligibility_status"].eq("reference_eligible"), "arm_id"].tolist()
    anchors = _event_anchors(episodes, calendar)
    anchors.to_csv(output / "event_anchors.csv", index=False, lineterminator="\n")
    paths, summary = _factor_event_paths(features, anchors, eligible)
    paths.to_parquet(output / "factor_event_paths.parquet", index=False, compression="zstd")
    summary.to_csv(output / "event_summary.csv", index=False, lineterminator="\n")
    _plot_event_atlas(paths, eligible, figures)
    manifest = _write_batch_manifest(
        output, root, prereg, "R4D_SPY_DRAWDOWN_ATLAS", run_id,
        counts={
            "episodes": len(episodes),
            "main_10pct_episodes": int(episodes["severity_10"].sum()),
            "eligible_arms": len(eligible),
            "figures": len(list(figures.glob("*.png"))),
        },
    )
    return Round4BatchResult(output, output / "manifest.json", manifest["status"])


def build_drawdown_episodes(market_daily: pd.DataFrame) -> pd.DataFrame:
    frame = market_daily.sort_values("session_date").reset_index(drop=True)
    dates = pd.DatetimeIndex(frame["session_date"])
    price = frame["tr_close"].to_numpy(float)
    episodes: list[dict[str, Any]] = []
    peak_index = 0
    in_episode = False
    breach: dict[int, pd.Timestamp | pd.NaT] = {}
    trough_index = 0
    for index in range(1, len(price)):
        if not in_episode:
            if price[index] >= price[peak_index]:
                peak_index = index
                continue
            in_episode = True
            trough_index = index
            breach = {5: pd.NaT, 10: pd.NaT, 15: pd.NaT, 20: pd.NaT}
        drawdown = price[index] / price[peak_index] - 1.0
        if price[index] < price[trough_index]:
            trough_index = index
        for level in breach:
            if pd.isna(breach[level]) and drawdown <= -level / 100.0:
                breach[level] = dates[index]
        recovered = price[index] >= price[peak_index]
        terminal = index == len(price) - 1
        if recovered or terminal:
            max_dd = price[trough_index] / price[peak_index] - 1.0
            if max_dd <= -0.05:
                episodes.append(
                    {
                        "episode_id": f"E{len(episodes)+1:03d}",
                        "peak_date": dates[peak_index],
                        "first_5_date": breach[5],
                        "first_10_date": breach[10],
                        "first_15_date": breach[15],
                        "first_20_date": breach[20],
                        "trough_date": dates[trough_index],
                        "recovery_date": dates[index] if recovered else pd.NaT,
                        "right_censored": not recovered,
                        "max_drawdown": max_dd,
                        "severity_10": max_dd <= -0.10,
                        "severity_15": max_dd <= -0.15,
                        "severity_20": max_dd <= -0.20,
                        "shallow_5_10": -0.10 < max_dd <= -0.05,
                    }
                )
            if recovered:
                peak_index = index
                in_episode = False
    return pd.DataFrame(episodes)


def _event_anchors(episodes: pd.DataFrame, calendar: pd.DataFrame) -> pd.DataFrame:
    signals = pd.DatetimeIndex(calendar.loc[calendar["signal_session"] <= MAX_SIGNAL, "signal_session"])
    rows = []
    for episode in episodes.itertuples(index=False):
        for kind in ("peak", "first_10", "trough", "recovery"):
            date = getattr(episode, f"{kind}_date")
            if pd.isna(date):
                continue
            position = signals.searchsorted(pd.Timestamp(date), side="right") - 1
            if position >= 0:
                rows.append(
                    {
                        "episode_id": episode.episode_id,
                        "severity_10": episode.severity_10,
                        "shallow_5_10": episode.shallow_5_10,
                        "anchor_kind": kind,
                        "event_date": date,
                        "signal_session": signals[position],
                    }
                )
    return pd.DataFrame(rows)


def _factor_event_paths(
    features: pd.DataFrame, anchors: pd.DataFrame, eligible: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    offsets = (-52, -26, -13, -8, -4, -1, 0)
    rows: list[dict[str, Any]] = []
    summary_rows = []
    main = anchors.loc[anchors["severity_10"] & anchors["anchor_kind"].eq("first_10")]
    for arm_id in eligible:
        arm = features.loc[features["arm_id"].eq(arm_id)].sort_values("signal_session").reset_index(drop=True)
        scores = arm["defense_score"].to_numpy(float)
        percentiles = np.full(len(arm), np.nan)
        zscores = np.full(len(arm), np.nan)
        alerts = np.zeros(len(arm), dtype=bool)
        for index in range(260, len(arm)):
            history = scores[:index]
            history = history[np.isfinite(history)]
            if len(history) < 260 or not np.isfinite(scores[index]):
                continue
            percentiles[index] = float(np.mean(history < scores[index]))
            std = np.std(history, ddof=1)
            zscores[index] = (scores[index] - np.mean(history)) / std if std > 0 else np.nan
            alerts[index] = scores[index] > np.quantile(history, 0.75, method="linear")
        index_by_date = {pd.Timestamp(date): index for index, date in enumerate(arm["signal_session"])}
        covered = 0
        leads: list[int] = []
        for event in main.itertuples(index=False):
            anchor_index = index_by_date.get(pd.Timestamp(event.signal_session))
            if anchor_index is None:
                continue
            before = alerts[max(0, anchor_index - 13) : anchor_index + 1]
            if before.any():
                covered += 1
                leads.append(int(np.flatnonzero(before)[0] - len(before) + 1))
            for offset in offsets:
                point = anchor_index + offset
                if 0 <= point < len(arm):
                    rows.append(
                        {
                            "arm_id": arm_id,
                            "episode_id": event.episode_id,
                            "anchor_kind": "first_10",
                            "offset_weeks": offset,
                            "signal_session": arm.at[point, "signal_session"],
                            "defense_score": scores[point],
                            "expanding_percentile": percentiles[point],
                            "expanding_zscore": zscores[point],
                            "alert": alerts[point],
                        }
                    )
        summary_rows.append(
            {
                "arm_id": arm_id,
                "main_events": len(main),
                "events_alerted_prior_13w": covered,
                "event_coverage": covered / len(main) if len(main) else np.nan,
                "median_first_alert_lead_weeks": float(np.median(leads)) if leads else np.nan,
                "total_alert_weeks": int(alerts.sum()),
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(summary_rows)


def _plot_event_atlas(paths: pd.DataFrame, eligible: list[str], directory: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for arm_id in eligible:
        frame = paths.loc[paths["arm_id"].eq(arm_id)]
        pivot = frame.pivot(index="offset_weeks", columns="episode_id", values="expanding_percentile")
        fig, ax = plt.subplots(figsize=(8, 5))
        for column in pivot:
            ax.plot(pivot.index, pivot[column], color="#9ecae1", alpha=0.45, linewidth=1)
        if len(pivot.columns):
            ax.plot(pivot.index, pivot.median(axis=1), color="#08519c", linewidth=2.5, label="median")
        ax.axhline(0.75, color="#d95f0e", linestyle="--", linewidth=1, label="causal q75")
        ax.axvline(0, color="black", linestyle=":", linewidth=1)
        ax.set(title=f"{arm_id} | causal percentile before first -10% breach", xlabel="weeks from first -10% breach", ylabel="expanding percentile", ylim=(0, 1))
        ax.legend(loc="best")
        fig.tight_layout()
        fig.savefig(directory / f"{arm_id.lower()}.png", dpi=180)
        plt.close(fig)


def _state_runs(states: pd.Series) -> pd.DataFrame:
    rows = []
    start = 0
    values = states.to_numpy(int)
    for index in range(1, len(values) + 1):
        if index == len(values) or values[index] != values[start]:
            rows.append(
                {
                    "state": int(values[start]),
                    "start_index": start,
                    "end_index": index - 1,
                    "weeks": index - start,
                }
            )
            start = index
    return pd.DataFrame(rows)


def _negative_concentration(t1: pd.Series, fraction: float) -> float:
    losses = (-t1).clip(lower=0).sort_values(ascending=False)
    count = max(1, int(np.ceil(len(losses) * fraction)))
    return float(losses.iloc[:count].sum() / losses.sum())


def _oracle_paths(outcomes: pd.DataFrame, seed: int) -> pd.DataFrame:
    sample = outcomes.loc[outcomes["h1_available"]].copy().reset_index(drop=True)
    t1 = sample["excess_logret_1w"].to_numpy(float)
    rng = np.random.default_rng(seed)
    budget_count = int(np.floor(0.25 * len(sample)))
    budget_alert = np.zeros(len(sample), dtype=bool)
    budget_alert[np.argsort(t1)[:budget_count]] = True
    random_alert = np.zeros(len(sample), dtype=bool)
    random_alert[rng.choice(len(sample), size=budget_count, replace=False)] = True
    policies = {
        "sign_oracle": t1 < 0,
        "worst_25pct_oracle": budget_alert,
        "random_25pct": random_alert,
    }
    rows = []
    for name, alerts in policies.items():
        for cost_bps in (0, 5, 10, 20):
            weight = np.where(alerts, 0.5, 1.0)
            previous = 0.0
            wealth = 1.0
            for value, target in zip(t1, weight, strict=True):
                wealth *= max(1e-12, 1.0 - abs(target - previous) * cost_bps / 10000.0)
                # Relative to cash; exact gross market/RF levels cancel only
                # approximately, which is sufficient for this oracle bound.
                wealth *= target * np.exp(value) + (1.0 - target)
                previous = target
            rows.append(
                {
                    "policy": name,
                    "cost_bps": cost_bps,
                    "alert_weeks": int(alerts.sum()),
                    "relative_to_cash_terminal": wealth,
                }
            )
    return pd.DataFrame(rows)
