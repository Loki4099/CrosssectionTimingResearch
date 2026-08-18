"""Read-only audit of Round 6 preregistration, immutable bundles, and firewalls."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


RUNS = {
    "R6A_ATTACK4_TARGET": "r6a-attack4-target-identity-20260818-v1",
    "R6B_ATTACK4_SINGLE_FACTOR": "r6b-attack4-single-factor-20260818-v1",
    "R6C_ATTACK4_ROLE_PROXY": "r6c-attack4-role-proxy-20260818-v1",
    "R6D_ATTACK4_ROBUSTNESS": "r6d-attack4-robustness-20260818-v1",
}
LOCK_SHA256 = "af00cccc159c3763671fded835a28eb3afffb3f0eac00a291ac7415a672d9a23"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    project, runtime = Path(args.project_root).resolve(), Path(args.runtime_root).resolve()
    lock_path = project / "config/experiments/round6/PREREG_LOCK.json"
    if _sha(lock_path) != LOCK_SHA256:
        raise AssertionError("Round6 prereg lock self-hash drifted")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    for relative, expected in lock["files"].items():
        if _sha(project / relative) != expected:
            raise AssertionError(f"Round6 lock member drifted: {relative}")

    base = runtime / "results/experiments/round6"
    trees: dict[str, str] = {}
    for batch, run_id in RUNS.items():
        root = base / batch / "runs" / run_id
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        for key in ("lockbox_read", "lockbox_predictions_generated", "models_run", "final_state_machine_run", "mom255_transfer_run"):
            if manifest[key] is not False:
                raise AssertionError(f"Round6 forbidden activity: {batch}/{key}")
        if manifest["prereg_lock_sha256"] != LOCK_SHA256:
            raise AssertionError(f"Round6 manifest lock mismatch: {batch}")
        for record in manifest["files"]:
            member = root / record["path"]
            if member.stat().st_size != record["size_bytes"] or _sha(member) != record["sha256"]:
                raise AssertionError(f"Round6 bundle mismatch: {member}")
        members = [f"{p.relative_to(root).as_posix()}\0{p.stat().st_size}\0{_sha(p)}" for p in sorted(root.rglob("*")) if p.is_file()]
        trees[batch] = hashlib.sha256("\n".join(members).encode()).hexdigest()

    a = pd.read_parquet(base / "R6A_ATTACK4_TARGET/runs" / RUNS["R6A_ATTACK4_TARGET"] / "targets_weekly.parquet")
    outcome = ["fwd_excess_logret_4w", "sustainable_attack_4w", "fwd_worst_excess_4w", "severe_w4"]
    if a.loc[pd.to_datetime(a.execution_session) >= pd.Timestamp("2022-01-03"), outcome].notna().any().any():
        raise AssertionError("Round6 lockbox target materialized")
    valid = a[a.target_available]
    if pd.to_datetime(valid.signal_session).max() != pd.Timestamp("2021-11-26"):
        raise AssertionError("Round6 target firewall drifted")
    if not np.array_equal(valid.sustainable_attack_4w.to_numpy(float), valid.fwd_excess_logret_4w.gt(0).astype(float).to_numpy()):
        raise AssertionError("Round6 A4/B4 identity drifted")

    b = base / "R6B_ATTACK4_SINGLE_FACTOR/runs" / RUNS["R6B_ATTACK4_SINGLE_FACTOR"]
    scores = pd.read_parquet(b / "scores_weekly.parquet")
    if scores.attack_arm_id.nunique() != 20 or pd.to_datetime(scores.signal_session).max() > pd.Timestamp("2021-12-23"):
        raise AssertionError("Round6 score registry/firewall drifted")
    if len(pd.read_csv(b / "signal_summary.csv")) != 20:
        raise AssertionError("Round6 signal summary incomplete")
    for arm_id in ("A4__SMA_GAP_D4", "A4__RV_RATIO_D4", "A4__RSP_SPY63_D4"):
        arm = scores[scores.attack_arm_id.eq(arm_id)].sort_values("signal_session")
        expected = arm.source_defense_score.shift(4) - arm.source_defense_score
        np.testing.assert_allclose(arm.attack_score.to_numpy(float), expected.to_numpy(float), equal_nan=True, rtol=0, atol=0)

    c = base / "R6C_ATTACK4_ROLE_PROXY/runs" / RUNS["R6C_ATTACK4_ROLE_PROXY"]
    states = pd.read_parquet(c / "states_weekly.parquet")
    nav = pd.read_parquet(c / "nav_daily.parquet")
    if pd.to_datetime(states.signal_session).max() > pd.Timestamp("2021-12-23") or pd.to_datetime(nav.date).max() > pd.Timestamp("2021-12-31"):
        raise AssertionError("Round6 proxy/NAV firewall drifted")
    if nav.nav.min() <= 0 or nav.spy_weight.max() > 1 + 1e-12 or nav.cash_weight.min() < -1e-12:
        raise AssertionError("Round6 invalid long-only proxy path")
    if set(states.loc[states.signal_valid, "target_spy_weight"].unique()) - {0.5, 1.0}:
        raise AssertionError("Round6 unauthorized proxy weight")

    d = base / "R6D_ATTACK4_ROBUSTNESS/runs" / RUNS["R6D_ATTACK4_ROBUSTNESS"]
    final = pd.read_csv(d / "final_assessment.csv")
    if len(final) != 20 or final.model_input_eligible.sum() != 0:
        raise AssertionError("Round6 frozen qualification result drifted")
    manifest_d = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    if manifest_d["assessment"] != "completed_no_attack_role_candidate":
        raise AssertionError("Round6 hard-stop assessment drifted")
    print(json.dumps({"status": "passed", "lock_sha256": LOCK_SHA256, "trees": trees,
                      "qualified": [], "assessment": manifest_d["assessment"]}, sort_keys=True))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
