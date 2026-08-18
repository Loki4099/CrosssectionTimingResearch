"""Acquire and freeze the outcome-free Round 10 RSP feature extension."""
from __future__ import annotations
import argparse, hashlib, json, tomllib
from pathlib import Path
import numpy as np
import pandas as pd
from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.round2_market import download_tiingo_eod_json, sha256_file
from momentum_reversal.data.round4_factors import normalize_rsp_tiingo
from build_round10_plan_lock import payload

def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument("--project-root",default="."); ap.add_argument("--runtime-root",required=True); ap.add_argument("--run-id",required=True); a=ap.parse_args()
    root,runtime=Path(a.project_root).resolve(),Path(a.runtime_root).resolve(); plan=tomllib.loads((root/"config/experiments/round10/plan.toml").read_text(encoding="utf-8")); lock=json.loads((root/"config/experiments/round10/PLAN_LOCK.json").read_text(encoding="utf-8"))
    if lock!=payload(root): raise DataQualityError("Round10 PLAN_LOCK drifted")
    if a.run_id!="r10a-rsp-lockbox-feature-20260818-v1": raise DataQualityError("Round10 R10A run-id mismatch")
    out=runtime/"data/round10/staging/R10A_RSP_LOCKBOX_FEATURE"/a.run_id; out.mkdir(parents=True,exist_ok=False)
    raw=download_tiingo_eod_json(symbol="RSP",start=plan["acquisition"]["start"],end=plan["acquisition"]["end"],project_root=root)
    raw_path=out/plan["acquisition"]["raw_filename"]; raw_path.write_bytes(raw)
    rsp=normalize_rsp_tiingo(raw,a.run_id)
    old_path=runtime/"data/round4/staging/R4A_FREE_FACTOR_DATA"/plan["parent"]["r4a_run_id"]/"raw/r4a_tiingo_rsp_2003_2021.json"
    if sha256_file(old_path)!=plan["parent"]["r4a_rsp_raw_sha256"]: raise DataQualityError("Round10 R4A RSP parent drifted")
    old=normalize_rsp_tiingo(old_path.read_bytes(),plan["parent"]["r4a_run_id"])
    overlap=rsp[rsp.session_date.le(pd.Timestamp(plan["acquisition"]["overlap_end"]))].merge(old,on="session_date",suffixes=("_new","_old"),validate="one_to_one")
    if len(overlap)!=int(plan["acquisition"]["required_overlap_rows"]) or len(overlap)!=len(old): raise DataQualityError("Round10 RSP overlap row/date identity failed")
    error=float(np.max(np.abs(overlap.tr_close_new.to_numpy()-overlap.tr_close_old.to_numpy())))
    if error>float(plan["acquisition"]["overlap_tr_close_absolute_tolerance"]): raise DataQualityError("Round10 RSP overlap value identity failed")
    if rsp.session_date.max()!=pd.Timestamp(plan["acquisition"]["end"]): raise DataQualityError("Round10 RSP extension does not reach required end")
    rsp.to_parquet(out/plan["acquisition"]["normalized_filename"],index=False,compression="zstd")
    pd.DataFrame([{"overlap_rows":len(overlap),"overlap_start":overlap.session_date.min(),"overlap_end":overlap.session_date.max(),"maximum_tr_close_absolute_error":error,"identity_passed":True}]).to_csv(out/"overlap_audit.csv",index=False,lineterminator="\n")
    files=[{"path":p.relative_to(out).as_posix(),"size_bytes":p.stat().st_size,"sha256":sha256_file(p)} for p in sorted(out.rglob("*")) if p.is_file()]
    manifest={"schema_version":1,"program_id":plan["program_id"],"batch_id":"R10A_RSP_LOCKBOX_FEATURE","run_id":a.run_id,"status":"completed_candidate","assessment":"completed_pending_rsp_extension_acceptance","formal_eligible":False,"strategy_nav_run":False,"forward_returns_run":False,"performance_summary_run":False,"g00_outcomes_read":False,"plan_lock_sha256":sha256_file(root/"config/experiments/round10/PLAN_LOCK.json"),"counts":{"daily_rows":len(rsp),"overlap_rows":len(overlap)},"date_range":{"first":str(rsp.session_date.min().date()),"last":str(rsp.session_date.max().date())},"maximum_overlap_error":error,"files":files}
    (out/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"output_dir":str(out),"status":manifest["status"],"rows":len(rsp),"overlap_error":error},sort_keys=True))

if __name__=="__main__": main()
