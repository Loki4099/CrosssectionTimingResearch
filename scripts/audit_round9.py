from __future__ import annotations

import argparse
import json
from pathlib import Path
import tomllib

import pandas as pd

from momentum_reversal.data.round2_market import sha256_file
from momentum_reversal.pipelines.round9_experiments import _batch_root, _run_ids, _validate_bundle
from build_round9_prereg_lock import payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    root, runtime = Path(args.project_root).resolve(), Path(args.runtime_root).resolve()
    lock_path = root / "config/experiments/round9/PREREG_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock != payload(root):
        raise AssertionError("Round9 prereg lock is not canonical")
    program = tomllib.loads((root / "config/experiments/round9/program.toml").read_text(encoding="utf-8"))
    batches = ["R9A_MOM255_UNION_LEDGER", "R9B_MOM255_TRANSFER_ECONOMICS", "R9C_MOM255_TRANSFER_ASSESSMENT"]
    paths = []
    for batch, run_id in zip(batches, _run_ids(root), strict=True):
        path = _batch_root(runtime, batch, run_id)
        _validate_bundle(path, batch)
        paths.append(path)
    r9a, r9b, r9c = paths
    nav = pd.read_parquet(r9a / "nav_daily.parquet")
    events = pd.read_parquet(r9a / "event_ledger.parquet")
    identity = pd.read_csv(r9a / "g00_identity_audit.csv")
    static = pd.read_csv(r9a / "static_allocations.csv")
    comparisons = pd.read_csv(r9b / "transfer_comparisons.csv")
    decision = json.loads((r9c / "decision.json").read_text(encoding="utf-8"))
    nav["date"] = pd.to_datetime(nav.date)
    events["execution_date"] = pd.to_datetime(events.execution_date)
    if nav.date.max() > pd.Timestamp(program["firewall"]["maximum_strategy_nav_date"]):
        raise AssertionError("Round9 crossed the NAV firewall")
    expected = pd.MultiIndex.from_product([pd.read_csv(root / program["transfer"]["registry"]).transfer_id, ["naked", "p00_overlay", "matched_static"], [0.0, 5.0, 10.0, 20.0]])
    actual = pd.MultiIndex.from_frame(nav[["transfer_id", "path_type", "cost_bps"]].drop_duplicates())
    if set(expected) != set(actual):
        raise AssertionError("Round9 NAV scenario grid is incomplete")
    if len(identity) != 24 or not identity.identity_passed.all():
        raise AssertionError("Round9 G00 identity audit failed")
    if identity.maximum_nav_absolute_error.max() > float(program["identity"]["nav_tolerance"]):
        raise AssertionError("Round9 naked NAV identity tolerance failed")
    if len(static) != 6 or static.absolute_match_error.max() > float(program["static_control"]["tolerance"]):
        raise AssertionError("Round9 matched-static construction failed")
    monthly = events[(events.transfer_id.str.contains("MONTHLY")) & events.event_kind.eq("overlay")]
    if monthly.empty or monthly.base_reranked.any():
        raise AssertionError("Round9 monthly overlay secretly reranked")
    if len(comparisons) != 24 or comparisons.transfer_id.nunique() != 6:
        raise AssertionError("Round9 economic comparison grid drifted")
    if decision["lockbox_read"] is not False or decision["lockbox_authorized"] is not False:
        raise AssertionError("Round9 lockbox firewall failed")
    print(json.dumps({"status": "passed", "prereg_lock_sha256": sha256_file(lock_path), "batches": 3, "nav_rows": len(nav), "identity_checks": len(identity), "development_transfer_eligible": decision["development_transfer_eligible"]}, sort_keys=True))


if __name__ == "__main__":
    main()
