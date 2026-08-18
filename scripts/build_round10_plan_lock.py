"""Create or verify the Round 10 data-acquisition plan lock."""
from __future__ import annotations
import argparse, hashlib, json, tomllib
from pathlib import Path

MEMBERS = [
    "config/experiments/round10/plan.toml",
    "docs/20_experiments/R10A_rsp_lockbox_feature/design.md",
    "docs/40_round10_p00_mom255_mechanical_lockbox_plan_v1.md",
    "scripts/build_round10_plan_lock.py",
]

def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()

def payload(root: Path) -> dict:
    missing = [x for x in MEMBERS if not (root / x).is_file()]
    if missing: raise AssertionError(f"missing Round10 plan members: {missing}")
    plan = tomllib.loads((root / "config/experiments/round10/plan.toml").read_text(encoding="utf-8"))
    auth = plan["authorization"]
    if not auth["rsp_data_acquisition"] or not auth["overlap_identity_audit"]: raise AssertionError("Round10 data stage not authorized")
    if auth["prediction_target_phase"] or auth["outcome_reveal_phase"] or auth["strategy_nav"]: raise AssertionError("Round10 plan leaked later-stage authority")
    return {"schema_version": 1, "program_id": plan["program_id"], "lock_type": "r10a_data_acquisition_only", "frozen_at_local_date": "2026-08-18", "files": {x: sha(root / x) for x in sorted(MEMBERS)}, "authorization": auth, "firewall": plan["firewall"], "hard_stop": plan["hard_stop"]}

def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument("--project-root",default="."); g=ap.add_mutually_exclusive_group(required=True); g.add_argument("--write",action="store_true"); g.add_argument("--check",action="store_true"); a=ap.parse_args()
    root=Path(a.project_root).resolve(); data=(json.dumps(payload(root),indent=2,sort_keys=True)+"\n").encode(); path=root/"config/experiments/round10/PLAN_LOCK.json"
    if a.write: path.write_bytes(data)
    elif not path.exists() or path.read_bytes()!=data: raise AssertionError("Round10 PLAN_LOCK differs from canonical rebuild")
    print(json.dumps({"mode":"write" if a.write else "check","sha256":hashlib.sha256(data).hexdigest(),"members":len(MEMBERS)},sort_keys=True))

if __name__=="__main__": main()
