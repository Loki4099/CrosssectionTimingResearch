"""Run one preregistered Round 7 development batch."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
from momentum_reversal.pipelines.round7_experiments import run_r7a, run_r7b, run_r7c, run_r7d

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", choices=["R7A", "R7B", "R7C", "R7D"])
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    runner = {"R7A": run_r7a, "R7B": run_r7b, "R7C": run_r7c, "R7D": run_r7d}[args.batch]
    result = runner(project_root=Path(args.project_root), runtime_root=Path(args.runtime_root), run_id=args.run_id)
    print(json.dumps({"output_dir": str(result.output_dir), "status": result.status}, sort_keys=True))

if __name__ == "__main__": main()
