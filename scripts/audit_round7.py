"""Read-only integrity and firewall audit for completed Round 7 bundles."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import numpy as np
import pandas as pd

from momentum_reversal.data.round2_market import sha256_file
from momentum_reversal.pipelines.round7_experiments import PROGRAM_ID, _bh_adjust, _rho, _validate_bundle


BATCHES = (
    ("R7A_DUAL_TARGET_FOLDS", 0),
    ("R7B_RISK_MODEL_TOURNAMENT", 1),
    ("R7C_RSP_ATTACK_COMPARATOR", 2),
    ("R7D_HEAD_QUALIFICATION", 3),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    root, runtime = Path(args.project_root).resolve(), Path(args.runtime_root).resolve()
    program = tomllib.loads((root / "config/experiments/round7/program.toml").read_text(encoding="utf-8"))
    lock_sha = sha256_file(root / "config/experiments/round7/PREREG_LOCK.json")
    subprocess.run([sys.executable, str(root / "scripts/build_round7_prereg_lock.py"), "--project-root", str(root), "--check"], check=True, capture_output=True)
    roots = {}
    manifests = {}
    for batch, index in BATCHES:
        path = runtime / "results/experiments/round7" / batch / "runs" / program["run_ids"][index]
        _validate_bundle(path, batch)
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["prereg_lock_sha256"] == lock_sha and manifest["lockbox_read"] is False
        assert manifest["final_state_machine_run"] is False and manifest["strategy_nav_run"] is False and manifest["mom255_transfer_run"] is False
        forbidden = ("nav", "state", "policy", "mom255", "lockbox_prediction")
        assert not any(any(token in record["path"].lower() for token in forbidden) for record in manifest["files"])
        roots[batch], manifests[batch] = path, manifest
    a = roots["R7A_DUAL_TARGET_FOLDS"]
    common = pd.read_parquet(a / "common_weekly.parquet")
    assert len(common) == 948 and pd.to_datetime(common.signal_session).max() == pd.Timestamp("2021-09-24")
    b = roots["R7B_RISK_MODEL_TOURNAMENT"]
    pred = pd.read_parquet(b / "outer_predictions.parquet")
    raw = pd.read_parquet(b / "raw_rsp_sentinel.parquet")
    selection = pd.read_csv(b / "inner_selection.csv")
    trials = pd.read_csv(b / "inner_trial_summary.csv")
    summary = pd.read_csv(b / "risk_summary.csv")
    assert len(pred) == 27 * 404 and pred.groupby("process_id").week_id.nunique().eq(404).all()
    assert not pred.duplicated(["process_id", "week_id"]).any() and len(raw) == 404
    assert len(selection) == 27 * 8 and len(trials) == 27 * 8 * 4 and trials.groupby(["process_id", "outer_year"]).selected.sum().eq(1).all()
    recomputed_rho = pred.groupby("process_id").apply(lambda x: _rho(x.predicted_risk, x.y5), include_groups=False).sort_index()
    recorded_rho = summary.set_index("process_id").spearman_y5.sort_index()
    assert np.allclose(recomputed_rho, recorded_rho, atol=1e-12)
    assert np.allclose(_bh_adjust(summary.block13_one_sided_p.to_numpy()), summary.bh_q_value.to_numpy(), atol=1e-12)
    c = roots["R7C_RSP_ATTACK_COMPARATOR"]
    attack = pd.read_parquet(c / "outer_predictions.parquet")
    assert len(attack) == 2 * 404 and set(attack.attack_process_id) == {"AX01_RAW_RSP_RECOVERY", "AX02_RSP_A4_MONOTONE"}
    assert attack.groupby("attack_process_id").week_id.nunique().eq(404).all()
    d = roots["R7D_HEAD_QUALIFICATION"]
    risk_final = pd.read_csv(d / "risk_final_assessment.csv")
    attack_final = pd.read_csv(d / "attack_final_assessment.csv")
    risk_leave = pd.read_csv(d / "risk_leave_one_event_out.csv")
    attack_leave = pd.read_csv(d / "attack_leave_one_event_out.csv")
    decision = json.loads((d / "decision.json").read_text(encoding="utf-8"))
    assert len(risk_final) == 27 and len(attack_final) == 2 and len(risk_leave) == 27 * 6 and len(attack_leave) == 2 * 6
    assert int(risk_final.risk_qualified.sum()) == decision["risk_qualified_processes"]
    assert bool(attack_final.loc[attack_final.attack_process_id.eq("AX02_RSP_A4_MONOTONE"), "attack_qualified"].iloc[0]) == decision["attack_ax02_qualified"]
    assert decision["round8_authorized"] is False and decision["state_machine_run"] is False and decision["lockbox_read"] is False
    result = {"status": "passed", "program_id": PROGRAM_ID, "prereg_lock_sha256": lock_sha,
              "bundles": len(BATCHES), "common_weeks": len(common), "risk_processes": len(risk_final),
              "risk_qualified": int(risk_final.risk_qualified.sum()), "attack_ax02_qualified": decision["attack_ax02_qualified"],
              "lockbox_read": False, "state_machine_run": False, "strategy_nav_run": False, "mom255_transfer_run": False}
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__": main()
