"""Build the preregistered all-qualified common-intersection R4B audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

from momentum_reversal.data.round2_market import sha256_file
from momentum_reversal.pipelines.round4_experiments import (
    _held_daily_target,
    _performance,
    replay_spy_cash,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--run-id", default="r4b-common-intersection-audit-20260817-v1")
    args = parser.parse_args()
    runtime = Path(args.runtime_root)
    r4a = runtime / "data/round4/staging/R4A_FREE_FACTOR_DATA/r4a-free-factor-data-20260817-v1"
    r4b = runtime / "results/experiments/round4/R4B_T2_SINGLE_FACTOR_REFERENCE/runs/r4b-t2-single-factor-20260817-v1"
    parent = runtime / "data/round2/staging/R2A_DATA/r2a-long-free-20260816-v1"
    output = r4b.parent.parent / "audits" / args.run_id
    output.mkdir(parents=True, exist_ok=False)
    common = pd.read_csv(r4a / "common_mask_candidate.csv", parse_dates=["signal_session"])
    common_dates = set(common.loc[common["all_qualified_available"], "signal_session"])
    signals = pd.read_parquet(r4b / "signals_weekly.parquet")
    targets = pd.read_parquet(r4b / "targets_weekly.parquet")
    market = pd.read_parquet(parent / "curated/market_daily.parquet")
    rf = pd.read_parquet(parent / "curated/risk_free_daily.parquet")
    rows = []
    navs = []
    first_common_by_arm = (
        signals.loc[
            signals["signal_session"].isin(common_dates) & signals["signal_valid"]
        ]
        .groupby("arm_id")["execution_session"]
        .min()
    )
    common_start = pd.Timestamp(first_common_by_arm.max())
    for arm_id, group in signals.groupby("arm_id", sort=True):
        group = group.sort_values("signal_session").copy()
        group["common"] = group["signal_session"].isin(common_dates)
        group = group.loc[pd.to_datetime(group["execution_session"]) >= common_start].copy()
        state = 1.0
        targets_common = []
        for row in group.itertuples(index=False):
            if row.common and row.signal_valid:
                state = 0.5 if row.defense_score > row.threshold_q75 else 1.0
                targets_common.append(
                    {"execution_session": row.execution_session, "target_spy_weight": state}
                )
        schedule = pd.DataFrame(targets_common)
        first = common_start
        daily_target = _held_daily_target(schedule, market, start=first, end=pd.Timestamp("2021-12-31"))
        static_weight = float(daily_target.mean())
        static_schedule = schedule.copy()
        static_schedule["target_spy_weight"] = static_weight
        dynamic = replay_spy_cash(market, rf, schedule, start=first, end=pd.Timestamp("2021-12-31"), cost_bps=10)
        static = replay_spy_cash(market, rf, static_schedule, start=first, end=pd.Timestamp("2021-12-31"), cost_bps=10)
        for kind, frame in (("dynamic", dynamic), ("matched_static", static)):
            part = frame.copy()
            part.insert(0, "arm_id", arm_id)
            part.insert(1, "path_type", kind)
            navs.append(part)
        diag = group.loc[group["common"] & group["signal_valid"]].merge(
            targets, on="signal_session", how="left", validate="one_to_one"
        )
        diag = diag.loc[diag["target_available"]]
        auc = roc_auc_score(diag["cash_wins_1w"], diag["defense_score"])
        rho = spearmanr(diag["defense_score"], diag["fwd_excess_logret_1w"]).statistic
        active_terminal = float(dynamic["nav"].iloc[-1] / static["nav"].iloc[-1] - 1)
        rows.append(
            {
                "arm_id": arm_id,
                "common_target_weeks": len(diag),
                "auc_t2": auc,
                "spearman_t1": rho,
                "dynamic_cagr": _performance(dynamic)["cagr"],
                "dynamic_mdd": _performance(dynamic)["mdd"],
                "mean_target_weight": static_weight,
                "active_terminal_wealth": active_terminal,
            }
        )
    pd.DataFrame(rows).to_csv(output / "common_summary.csv", index=False, lineterminator="\n")
    pd.concat(navs, ignore_index=True).to_parquet(output / "common_nav_daily.parquet", index=False, compression="zstd")
    files = []
    for path in sorted(output.iterdir()):
        if path.is_file():
            files.append({"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = {
        "schema_version": 1,
        "audit_id": "R4B_ALL_QUALIFIED_COMMON_INTERSECTION",
        "parent_r4b_manifest_sha256": sha256_file(r4b / "manifest.json"),
        "r4a_common_mask_sha256": sha256_file(r4a / "common_mask_candidate.csv"),
        "common_mask_weeks": len(common_dates),
        "common_evaluation_start": str(common_start.date()),
        "arms": len(rows),
        "lockbox_read": False,
        "files": files,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"output": str(output), "arms": len(rows), "common_weeks": len(common_dates)}))


if __name__ == "__main__":
    main()
