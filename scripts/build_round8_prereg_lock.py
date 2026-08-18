"""Create or verify the canonical Round 8 preregistration lock."""
from __future__ import annotations
import argparse, hashlib, json, tomllib
from pathlib import Path
import pandas as pd

MEMBERS = [
 "config/experiments/round8/PARENT_ACCEPTANCE.json", "config/experiments/round8/policy_registry.csv",
 "config/experiments/round8/program.toml", "docs/20_experiments/R8A_rsp_policy_signals/design.md",
 "docs/20_experiments/R8B_rsp_spycash_replay/design.md", "docs/20_experiments/R8C_rsp_policy_assessment/design.md",
 "docs/34_round8_risk_veto_state_machine_program_draft_v1.md", "experiments/round8_groups.csv",
 "experiments/round8_registry.csv", "scripts/build_round8_prereg_lock.py"]

def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def payload(root: Path) -> dict:
    missing=[x for x in MEMBERS if not (root/x).is_file()]
    if missing: raise AssertionError(f"missing Round8 lock members: {missing}")
    p=tomllib.loads((root/"config/experiments/round8/program.toml").read_text(encoding="utf-8"))
    r=pd.read_csv(root/"config/experiments/round8/policy_registry.csv")
    if len(r)!=3 or r.policy_id.nunique()!=3 or r.formal_incremental_hypothesis.sum()!=2: raise AssertionError("Round8 policy registry drifted")
    a=p["authorization"]
    if not a["state_signal_materialization"] or not a["spy_cash_development_nav"] or a["lockbox"] or a["mom255_transfer"] or a["model_search"]: raise AssertionError("Round8 auth drifted")
    return {"schema_version":1,"program_id":p["program_id"],"lock_type":"development_batches_r8a_r8b_r8c",
      "frozen_at_local_date":"2026-08-18","parent_r7_prereg_lock_sha256":p["parent"]["r7_prereg_lock_sha256"],
      "files":{x:sha(root/x) for x in sorted(MEMBERS)},"counts":{"policies":3,"formal_incremental_hypotheses":2,"batches":3,"outer_oos_weeks":404},
      "authorization":a,"firewall":p["firewall"],"hard_stop":p["hard_stop"]}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--project-root",default="."); g=ap.add_mutually_exclusive_group(required=True); g.add_argument("--write",action="store_true"); g.add_argument("--check",action="store_true"); x=ap.parse_args()
    root=Path(x.project_root).resolve(); data=(json.dumps(payload(root),indent=2,sort_keys=True)+"\n").encode(); path=root/"config/experiments/round8/PREREG_LOCK.json"
    if x.write: path.write_bytes(data)
    elif not path.exists() or path.read_bytes()!=data: raise AssertionError("Round8 lock differs from canonical rebuild")
    print(json.dumps({"mode":"write" if x.write else "check","sha256":hashlib.sha256(data).hexdigest(),"members":len(MEMBERS)},sort_keys=True))
if __name__=="__main__": main()
