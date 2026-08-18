"""Run one preregistered Round 6 development batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from momentum_reversal.pipelines.round6_experiments import run_r6a, run_r6b, run_r6c, run_r6d


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", choices=["R6A", "R6B", "R6C", "R6D"])
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    runner = {"R6A": run_r6a, "R6B": run_r6b, "R6C": run_r6c, "R6D": run_r6d}[args.batch]
    result = runner(project_root=Path(args.project_root), runtime_root=Path(args.runtime_root), run_id=args.run_id)
    print(json.dumps({"output_dir": str(result.output_dir), "status": result.status}, sort_keys=True))


if __name__ == "__main__":
    main()
