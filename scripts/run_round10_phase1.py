from __future__ import annotations
import argparse, json
from pathlib import Path
from momentum_reversal.pipelines.round10_experiments import run_r10b
def main():
    p=argparse.ArgumentParser(); p.add_argument("--project-root",default="."); p.add_argument("--runtime-root",required=True); p.add_argument("--run-id",required=True); a=p.parse_args(); r=run_r10b(project_root=Path(a.project_root),runtime_root=Path(a.runtime_root),run_id=a.run_id); print(json.dumps({"output_dir":str(r.output_dir),"status":r.status},sort_keys=True))
if __name__=="__main__": main()
