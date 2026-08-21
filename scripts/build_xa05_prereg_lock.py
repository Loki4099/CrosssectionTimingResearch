from __future__ import annotations
import argparse,csv,hashlib,json
from pathlib import Path
import tomllib

MEMBERS=("config/experiments/xa05/program.toml","docs/20_experiments/XA05_mom12_7_p00_final_transfer/design.md","docs/48_xa05_mom12_7_p00_final_transfer_program_v1.md","scripts/build_xa05_prereg_lock.py")
LOCK="config/experiments/xa05/PREREG_LOCK.json"
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def validate(root:Path)->None:
    with (root/"config/experiments/xa05/program.toml").open("rb") as h:p=tomllib.load(h)
    if p["status"]!="preregistered_authorized" or p["formal_eligible"] is not False:raise ValueError("status drift")
    if p["base"]["process_id"]!="RAW_XS003_CORE10":raise ValueError("candidate drift")
    if p["sample"]["top_k"]!=[5,10,20,50] or p["sample"]["cost_bps"]!=[0,5,10,20]:raise ValueError("path grid drift")
    if p["paths_under_test"]["path_types"]!=["naked","p00_overlay","matched_static"]:raise ValueError("path types drift")
    if any(p["authorization"].values()):raise ValueError("closed authorization enabled")
    if len(p["performance"]["metrics"])<20:raise ValueError("drawdown metric bundle incomplete")
def build(root:Path)->dict:
    validate(root);files={r:{"sha256":sha(root/r),"size_bytes":(root/r).stat().st_size} for r in sorted(MEMBERS)}
    return {"schema_version":"xa05.prereg_lock.v1","program_id":"xa05_mom12_7_p00_final_transfer_v1","status":"locked_authorized","formal_eligible":False,"member_count":len(files),"files":files}
def canonical(x:dict)->bytes:return (json.dumps(x,indent=2,sort_keys=True)+"\n").encode()
def main()->None:
    ap=argparse.ArgumentParser();ap.add_argument("--project-root",default=".");g=ap.add_mutually_exclusive_group(required=True);g.add_argument("--write",action="store_true");g.add_argument("--check",action="store_true");a=ap.parse_args();root=Path(a.project_root).resolve();raw=canonical(build(root));target=root/LOCK
    if a.write:target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    elif not target.exists() or target.read_bytes()!=raw:raise SystemExit("XA05 PREREG_LOCK mismatch")
    print(hashlib.sha256(raw).hexdigest())
if __name__=="__main__":main()
