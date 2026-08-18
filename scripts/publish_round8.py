"""Publish audited Round 8 bundles and figures."""
from __future__ import annotations
import argparse,hashlib,json,subprocess,sys,tomllib
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from momentum_reversal.data.round2_market import sha256_file
from publish_compact import copy_compact
BATCHES=[("R8A_RSP_POLICY_SIGNALS","R8A",0),("R8B_RSP_SPYCASH_REPLAY","R8B",1),("R8C_RSP_POLICY_ASSESSMENT","R8C",2)]
COMPACT_FILES={
 "R8A":("manifest.json","policy_states_weekly.parquet","state_summary.csv"),
 "R8B":("economic_summary.csv","manifest.json","yearly_active.csv"),
 "R8C":("decision.json","final_assessment.csv","incremental_comparisons.csv","leave_one_event_out.csv","manifest.json"),
}
def tree(path):
 rec=[{"path":p.relative_to(path).as_posix(),"sha":sha256_file(p),"size":p.stat().st_size} for p in sorted(path.rglob("*")) if p.is_file()]; return hashlib.sha256((json.dumps(rec,sort_keys=True)+"\n").encode()).hexdigest()
def main():
 ap=argparse.ArgumentParser(); ap.add_argument("--project-root",default="."); ap.add_argument("--runtime-root",required=True); a=ap.parse_args(); root=Path(a.project_root).resolve(); runtime=Path(a.runtime_root).resolve(); subprocess.run([sys.executable,str(root/"scripts/audit_round8.py"),"--project-root",str(root),"--runtime-root",str(runtime)],check=True,capture_output=True); p=tomllib.loads((root/"config/experiments/round8/program.toml").read_text(encoding="utf-8")); pub=root/"results/published/round8"; figs=root/"docs/figures/round8"; pub.mkdir(parents=True,exist_ok=True); figs.mkdir(parents=True,exist_ok=True); rows=[]
 for batch,short,i in BATCHES:
  src=runtime/"results/experiments/round8"/batch/"runs"/p["run_ids"][i]; dst=pub/short
  copy_compact(src,dst,COMPACT_FILES[short])
  m=json.loads((src/"manifest.json").read_text(encoding="utf-8")); rows.append({"batch_id":batch,"run_id":p["run_ids"][i],"status":m["status"],"assessment":m["assessment"],"manifest_sha256":sha256_file(src/"manifest.json"),"tree_sha256":tree(src),"formal_eligible":False,"lockbox_read":False})
 pd.DataFrame(rows).to_csv(root/"experiments/round8_results.csv",index=False,lineterminator="\n"); (pub/"README.md").write_text("# Published Round 8 results\n\nAudited RSP-only development state, SPY/cash replay, and assessment bundles. No lockbox or mom255 outputs were generated.\n",encoding="utf-8")
 final=pd.read_csv(pub/"R8C/final_assessment.csv"); states=pd.read_csv(pub/"R8A/state_summary.csv")
 fig,ax=plt.subplots(figsize=(8,5)); ax.bar(final.policy_id,final.active_terminal_wealth,color=["#2ca02c","#4c78a8","#f2a541"]); ax.axhline(0,color="black",lw=.8); ax.set_ylabel("10bp active terminal wealth vs matched static"); ax.tick_params(axis="x",rotation=18); fig.tight_layout(); fig.savefig(figs/"r8-policy-active-wealth.png",dpi=180); plt.close(fig)
 fig,ax=plt.subplots(figsize=(8,5)); ax.bar(states.policy_id,states.defense_fraction,color=["#2ca02c","#4c78a8","#f2a541"]); ax.set_ylabel("Fraction of OOS weeks in DEFENSE"); ax.tick_params(axis="x",rotation=18); fig.tight_layout(); fig.savefig(figs/"r8-policy-defense-fraction.png",dpi=180); plt.close(fig)
 print(json.dumps({"status":"published","batches":3,"figures":2,"eligible":final.loc[final.development_policy_eligible,"policy_id"].tolist()},sort_keys=True))
if __name__=="__main__": main()
