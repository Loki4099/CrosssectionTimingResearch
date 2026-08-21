from __future__ import annotations
import argparse,json
from momentum_reversal.pipelines.xa04_experiments import audit_xa04
def main()->None:
    ap=argparse.ArgumentParser();ap.add_argument("--project-root",default=".");ap.add_argument("--runtime-root",required=True);a=ap.parse_args();print(json.dumps(audit_xa04(a.project_root,a.runtime_root),indent=2,sort_keys=True))
if __name__=="__main__":main()
