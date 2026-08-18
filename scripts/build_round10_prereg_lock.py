"""Create or verify the Round 10 sealed-target preregistration lock."""
from __future__ import annotations
import argparse,hashlib,json,tomllib
from pathlib import Path
import pandas as pd
MEMBERS=["config/experiments/round10/R10A_ACCEPTANCE.json","config/experiments/round10/program.toml","config/experiments/round10/transfer_registry.csv","docs/20_experiments/R10B_sealed_targets/design.md","docs/40_round10_p00_mom255_mechanical_lockbox_plan_v1.md","experiments/round10_groups.csv","experiments/round10_registry.csv","scripts/build_round10_prereg_lock.py","scripts/run_round10_phase1.py","src/momentum_reversal/pipelines/round10_experiments.py"]
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def payload(root:Path)->dict:
    missing=[x for x in MEMBERS if not (root/x).is_file()]
    if missing:raise AssertionError(f"missing Round10 prereg members: {missing}")
    p=tomllib.loads((root/"config/experiments/round10/program.toml").read_text(encoding="utf-8")); r=pd.read_csv(root/p["transfer"]["registry"]); a=p["authorization"]
    if len(r)!=6 or int(r.primary.sum())!=1:raise AssertionError("Round10 registry drifted")
    if not a["prediction_target_phase"] or a["outcome_reveal_phase"] or a["strategy_nav"] or a["performance_assessment"]:raise AssertionError("Round10 phase1 auth drifted")
    return {"schema_version":1,"program_id":p["program_id"],"lock_type":"sealed_prediction_target_phase","frozen_at_local_date":"2026-08-18","parent_plan_lock_sha256":p["parent"]["plan_lock_sha256"],"files":{x:sha(root/x) for x in sorted(MEMBERS)},"counts":{"transfer_cells":6,"primary_cells":1},"authorization":a,"firewall":p["firewall"],"hard_stop":p["hard_stop"]}
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--project-root",default=".");g=ap.add_mutually_exclusive_group(required=True);g.add_argument("--write",action="store_true");g.add_argument("--check",action="store_true");a=ap.parse_args();root=Path(a.project_root).resolve();data=(json.dumps(payload(root),indent=2,sort_keys=True)+"\n").encode();path=root/"config/experiments/round10/PREREG_LOCK.json"
    if a.write:path.write_bytes(data)
    elif not path.exists() or path.read_bytes()!=data:raise AssertionError("Round10 PREREG_LOCK differs from rebuild")
    print(json.dumps({"mode":"write" if a.write else "check","sha256":hashlib.sha256(data).hexdigest(),"members":len(MEMBERS)},sort_keys=True))
if __name__=="__main__":main()
