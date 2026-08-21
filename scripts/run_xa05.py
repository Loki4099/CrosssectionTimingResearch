import argparse,json
from momentum_reversal.pipelines.xa05_experiments import run_xa05
def main():
    a=argparse.ArgumentParser();a.add_argument("batch",choices=("XA05A","XA05B","XA05C"));a.add_argument("--project-root",default=".");a.add_argument("--runtime-root",required=True);x=a.parse_args();print(json.dumps(run_xa05(x.project_root,x.runtime_root,x.batch),indent=2,sort_keys=True))
if __name__=="__main__":main()
