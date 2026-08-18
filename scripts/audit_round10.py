"""Read-only end-to-end audit for Round 10."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pandas as pd

from momentum_reversal.data.round2_market import sha256_file


def verify_manifest(bundle: Path) -> dict:
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    for record in manifest["files"]:
        path = bundle / record["path"]
        if path.stat().st_size != record["size_bytes"] or sha256_file(path) != record["sha256"]:
            raise AssertionError(f"Round10 manifest drift: {path}")
    return manifest


def run_check(root: Path, script: str) -> None:
    subprocess.run(
        [sys.executable, str(root / "scripts" / script), "--project-root", str(root), "--check"],
        check=True,
        capture_output=True,
        text=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    root, runtime = Path(args.project_root).resolve(), Path(args.runtime_root).resolve()
    for script in ("build_round10_plan_lock.py", "build_round10_prereg_lock.py", "build_round10_outcome_lock.py", "build_round10_repair_lock.py"):
        run_check(root, script)
    program = tomllib.loads((root / "config/experiments/round10/outcome_program.toml").read_text(encoding="utf-8"))
    r10a = runtime / "data/round10/staging/R10A_RSP_LOCKBOX_FEATURE/r10a-rsp-lockbox-feature-20260818-v1"
    r10b = runtime / "results/experiments/round10/R10B_SEALED_TARGETS/runs" / program["sealed"]["r10b_run_id"]
    r10c = runtime / "results/experiments/round10/R10C_OUTCOME_REVEAL/runs" / program["run_id"]
    ma, mb, mc = verify_manifest(r10a), verify_manifest(r10b), verify_manifest(r10c)
    if ma.get("strategy_nav_run", False) or ma.get("outcome_reveal_run", False):
        raise AssertionError("Round10 R10A firewall failed")
    if mb["strategy_nav_run"] or mb["performance_metrics_run"] or mb["g00_nav_read"]:
        raise AssertionError("Round10 sealed-target firewall failed")
    if mc["sealed_target_sha256"] != sha256_file(r10b / "sealed_target_ledger.parquet"):
        raise AssertionError("Round10 sealed target changed at reveal")

    states = pd.read_parquet(r10b / "p00_states_weekly.parquet")
    targets = pd.read_parquet(r10b / "sealed_target_ledger.parquet")
    nav = pd.read_parquet(r10c / "nav_daily.parquet")
    comparisons = pd.read_csv(r10c / "transfer_comparisons.csv")
    identity = pd.read_csv(r10c / "g00_identity_audit.csv")
    leave = pd.read_csv(r10c / "leave_one_year_out.csv")
    assessment = json.loads((r10c / "assessment.json").read_text(encoding="utf-8"))
    decision = json.loads((r10c / "decision.json").read_text(encoding="utf-8"))
    states["execution_session"] = pd.to_datetime(states.execution_session)
    nav["date"] = pd.to_datetime(nav.date)
    if states.execution_session.max() > pd.Timestamp("2026-06-29") or nav.date.max() > pd.Timestamp(program["sample"]["last_nav_date"]):
        raise AssertionError("Round10 crossed its lockbox end")
    if len(targets) != 39991 or targets.transfer_id.nunique() != 6:
        raise AssertionError("Round10 sealed target ledger drifted")
    scenarios = nav[["transfer_id", "path_type", "cost_bps"]].drop_duplicates()
    if len(scenarios) != 72 or nav.transfer_id.nunique() != 6:
        raise AssertionError("Round10 reveal scenario grid incomplete")
    if len(comparisons) != 24 or comparisons.transfer_id.nunique() != 6:
        raise AssertionError("Round10 comparison grid incomplete")
    if len(identity) != 24 or not identity.identity_passed.all():
        raise AssertionError("Round10 G00 identity audit failed")
    if set(leave.removed_year) != {2022, 2023, 2024, 2025, 2026}:
        raise AssertionError("Round10 leave-one-year grid incomplete")
    if decision["mechanical_lockbox_passed"] or assessment["mechanical_lockbox_passed"]:
        raise AssertionError("Round10 frozen decision must remain a failure")
    if assessment["passed_cells"] != int(comparisons[comparisons.cost_bps.eq(10)].four_metric_gate.sum()):
        raise AssertionError("Round10 family decision mismatch")
    if not assessment["summary_repair_applied"] or not mc["summary_repair_applied"]:
        raise AssertionError("Round10 repair provenance missing")
    print(json.dumps({
        "status": "passed",
        "plan_lock_sha256": sha256_file(root / "config/experiments/round10/PLAN_LOCK.json"),
        "prereg_lock_sha256": sha256_file(root / "config/experiments/round10/PREREG_LOCK.json"),
        "outcome_reveal_lock_sha256": sha256_file(root / "config/experiments/round10/OUTCOME_REVEAL_LOCK.json"),
        "repair_lock_sha256": sha256_file(root / "config/experiments/round10/REVEAL_REPAIR_LOCK.json"),
        "nav_rows": len(nav),
        "identity_checks": len(identity),
        "mechanical_lockbox_passed": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
