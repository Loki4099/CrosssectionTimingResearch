"""Read-only Round 5 immutable-bundle and Round 4 identity audit."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


RUNS = {
    "R5A_MAE13_TARGET": "r5a-mae13-target-20260817-v1",
    "R5B_MAE13_SINGLE_FACTOR": "r5b-mae13-single-factor-20260817-v1",
    "R5C_SPY_CASH_PROXY": "r5c-spy-cash-proxy-20260817-v1",
    "R5D_MAE13_ROBUSTNESS": "r5d-mae13-robustness-20260817-v1",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    runtime = Path(args.runtime_root)
    base = runtime / "results/experiments/round5"
    trees: dict[str, str] = {}
    for batch, run_id in RUNS.items():
        root = base / batch / "runs" / run_id
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if manifest["lockbox_read"] is not False:
            raise AssertionError(f"lockbox read: {batch}")
        for record in manifest["files"]:
            path = root / record["path"]
            if path.stat().st_size != record["size_bytes"] or _sha(path) != record["sha256"]:
                raise AssertionError(f"bundle mismatch: {path}")
        members = [
            f"{path.relative_to(root).as_posix()}\0{path.stat().st_size}\0{_sha(path)}"
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ]
        trees[batch] = hashlib.sha256("\n".join(members).encode()).hexdigest()
    a = pd.read_parquet(base / "R5A_MAE13_TARGET/runs" / RUNS["R5A_MAE13_TARGET"] / "targets_weekly.parquet")
    if a.loc[pd.to_datetime(a.execution_session) >= pd.Timestamp("2022-01-03"), "raw_mae13"].notna().any():
        raise AssertionError("Round5 lockbox target materialized")
    c = base / "R5C_SPY_CASH_PROXY/runs" / RUNS["R5C_SPY_CASH_PROXY"]
    signals = pd.read_parquet(c / "signals_weekly.parquet")
    nav = pd.read_parquet(c / "nav_daily.parquet")
    if pd.to_datetime(signals.signal_session).max() > pd.Timestamp("2021-12-23"):
        raise AssertionError("Round5 policy crossed firewall")
    if pd.to_datetime(nav.date).max() > pd.Timestamp("2021-12-31"):
        raise AssertionError("Round5 NAV crossed firewall")
    if nav.nav.min() <= 0 or nav.spy_weight.max() > 1 + 1e-12:
        raise AssertionError("Round5 invalid NAV or exposure")
    r4 = runtime / "results/experiments/round4/R4B_T2_SINGLE_FACTOR_REFERENCE/runs/r4b-t2-single-factor-20260817-v1"
    s4 = pd.read_parquet(r4 / "signals_weekly.parquet").sort_values(["arm_id", "signal_session"]).reset_index(drop=True)
    s5 = signals.sort_values(["arm_id", "signal_session"]).reset_index(drop=True)
    signal_columns = ["arm_id", "week_id", "signal_session", "execution_session", "defense_score", "threshold_q75", "signal_valid", "alert", "target_spy_weight"]
    pd.testing.assert_frame_equal(s4[signal_columns], s5[signal_columns], check_exact=True)
    n4 = pd.read_parquet(r4 / "nav_daily.parquet").sort_values(["arm_id", "path_type", "cost_bps", "date"]).reset_index(drop=True)
    n5 = nav[nav.path_type.isin(["dynamic", "matched_static"])].sort_values(["arm_id", "path_type", "cost_bps", "date"]).reset_index(drop=True)
    columns = ["arm_id", "path_type", "cost_bps", "date", "nav", "daily_return", "spy_weight", "cash_weight", "turnover", "cost_amount"]
    pd.testing.assert_frame_equal(n4[columns], n5[columns], check_exact=True)
    final = pd.read_csv(base / "R5D_MAE13_ROBUSTNESS/runs" / RUNS["R5D_MAE13_ROBUSTNESS"] / "final_assessment.csv")
    robust = final.loc[final.robust_reference_positive, "arm_id"].tolist()
    if robust != ["R4B__RSP_SPY63"]:
        raise AssertionError(f"Round5 robust result drifted: {robust}")
    print(json.dumps({"status": "passed", "trees": trees, "robust": robust}, sort_keys=True))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
