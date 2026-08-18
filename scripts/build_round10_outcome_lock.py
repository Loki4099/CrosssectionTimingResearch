"""Create or verify the Round 10 outcome-reveal lock."""
from __future__ import annotations
import argparse,hashlib,json,tomllib
from pathlib import Path
MEMBERS=["config/experiments/round10/R10B_TARGET_ACCEPTANCE.json","config/experiments/round10/outcome_program.toml","config/experiments/round10/transfer_registry.csv","docs/20_experiments/R10C_outcome_reveal/design.md","scripts/build_round10_outcome_lock.py","scripts/run_round10_reveal.py","src/momentum_reversal/pipelines/round9_experiments.py","src/momentum_reversal/pipelines/round10_experiments.py","src/momentum_reversal/pipelines/round10_reveal.py"]
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def payload(root:Path)->dict:
 missing=[x for x in MEMBERS if not (root/x).is_file()]
 if missing:raise AssertionError(f"missing Round10 reveal members: {missing}")
 p=tomllib.loads((root/"config/experiments/round10/outcome_program.toml").read_text(encoding="utf-8"));a=p["authorization"]
 if not a["outcome_reveal_phase"] or not a["strategy_nav"] or a["target_revision"] or a["model_or_policy_revision"]:raise AssertionError("Round10 reveal auth drifted")
 return {"schema_version":1,"program_id":p["program_id"],"lock_type":"sealed_outcome_reveal","frozen_at_local_date":"2026-08-18","sealed_target_sha256":p["sealed"]["targets_sha256"],"sealed_state_sha256":p["sealed"]["states_sha256"],"files":{x:sha(root/x) for x in sorted(MEMBERS)},"authorization":a,"gates":p["gates"],"hard_stop":p["hard_stop"]}
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--project-root",default=".");g=ap.add_mutually_exclusive_group(required=True);g.add_argument("--write",action="store_true");g.add_argument("--check",action="store_true");a=ap.parse_args();root=Path(a.project_root).resolve();data=(json.dumps(payload(root),indent=2,sort_keys=True)+"\n").encode();path=root/"config/experiments/round10/OUTCOME_REVEAL_LOCK.json"
 if a.write:path.write_bytes(data)
 elif not path.exists() or path.read_bytes()!=data:raise AssertionError("Round10 outcome lock differs from rebuild")
 print(json.dumps({"mode":"write" if a.write else "check","sha256":hashlib.sha256(data).hexdigest(),"members":len(MEMBERS)},sort_keys=True))
if __name__=="__main__":main()
