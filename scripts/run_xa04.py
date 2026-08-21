from __future__ import annotations
import argparse,json
from pathlib import Path
from momentum_reversal.pipelines.xa04_experiments import run_xa04
def main()->None:
    ap=argparse.ArgumentParser();ap.add_argument("batch",choices=("XA04A","XA04B","XA04C","XA04D"));ap.add_argument("--project-root",default=".");ap.add_argument("--runtime-root",required=True);a=ap.parse_args()
    print(json.dumps(run_xa04(Path(a.project_root),Path(a.runtime_root),a.batch),indent=2,sort_keys=True))
if __name__=="__main__":main()
