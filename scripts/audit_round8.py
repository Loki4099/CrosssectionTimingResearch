"""Read-only audit for completed Round 8 bundles."""
from __future__ import annotations
import argparse,json,subprocess,sys,tomllib
from pathlib import Path
import pandas as pd
from momentum_reversal.data.round2_market import sha256_file
from momentum_reversal.pipelines.round8_experiments import _validate_bundle
def main():
 p=argparse.ArgumentParser(); p.add_argument("--project-root",default="."); p.add_argument("--runtime-root",required=True); a=p.parse_args(); root=Path(a.project_root).resolve(); runtime=Path(a.runtime_root).resolve(); program=tomllib.loads((root/"config/experiments/round8/program.toml").read_text(encoding="utf-8")); subprocess.run([sys.executable,str(root/"scripts/build_round8_prereg_lock.py"),"--project-root",str(root),"--check"],check=True,capture_output=True)
 batches=[("R8A_RSP_POLICY_SIGNALS",0),("R8B_RSP_SPYCASH_REPLAY",1),("R8C_RSP_POLICY_ASSESSMENT",2)]; roots={}
 for batch,i in batches:
  path=runtime/"results/experiments/round8"/batch/"runs"/program["run_ids"][i]; _validate_bundle(path,batch); m=json.loads((path/"manifest.json").read_text(encoding="utf-8")); assert m["lockbox_read"] is False and m["mom255_transfer_run"] is False; roots[batch]=path
 states=pd.read_parquet(roots["R8A_RSP_POLICY_SIGNALS"]/"policy_states_weekly.parquet"); nav=pd.read_parquet(roots["R8B_RSP_SPYCASH_REPLAY"]/"nav_daily.parquet"); final=pd.read_csv(roots["R8C_RSP_POLICY_ASSESSMENT"]/"final_assessment.csv"); inc=pd.read_csv(roots["R8C_RSP_POLICY_ASSESSMENT"]/"incremental_comparisons.csv"); decision=json.loads((roots["R8C_RSP_POLICY_ASSESSMENT"]/"decision.json").read_text(encoding="utf-8"))
 assert len(states)==1212 and states.groupby("policy_id").week_id.nunique().eq(404).all(); assert set(states.target_spy_weight)=={.5,1.}; assert not ((states.risk_high)&states.state.ne("DEFENSE")).any(); assert len(final)==3 and len(inc)==2; assert set(decision["development_policy_eligible"])==set(final.loc[final.development_policy_eligible,"policy_id"]); assert decision["round9_authorized"] is False and decision["lockbox_read"] is False
 p00=states[states.policy_id.eq("P00_RSP_Y5_CLEAR")].state.reset_index(drop=True); p02=states[states.policy_id.eq("P02_RSP_A4_ISOTONIC")].state.reset_index(drop=True); assert p00.equals(p02)
 print(json.dumps({"status":"passed","program_id":program["program_id"],"prereg_lock_sha256":sha256_file(root/"config/experiments/round8/PREREG_LOCK.json"),"bundles":3,"policies":3,"weeks_per_policy":404,"eligible":decision["development_policy_eligible"],"p02_state_identity_with_p00":True,"lockbox_read":False,"mom255_transfer_run":False},indent=2,sort_keys=True))
if __name__=="__main__": main()
