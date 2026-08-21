import argparse,json
from momentum_reversal.pipelines.xa05_experiments import audit_xa05
def main():
    a=argparse.ArgumentParser();a.add_argument("--project-root",default=".");a.add_argument("--runtime-root",required=True);x=a.parse_args();print(json.dumps(audit_xa05(x.project_root,x.runtime_root),indent=2,sort_keys=True))
if __name__=="__main__":main()
