"""Run one preregistered Round 5 development batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from momentum_reversal.pipelines.round5_experiments import run_r5a, run_r5b, run_r5c, run_r5d


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", choices=["R5A", "R5B", "R5C", "R5D"])
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    runner = {"R5A": run_r5a, "R5B": run_r5b, "R5C": run_r5c, "R5D": run_r5d}[args.batch]
    result = runner(project_root=Path(args.project_root), runtime_root=Path(args.runtime_root), run_id=args.run_id)
    print(json.dumps({"output_dir": str(result.output_dir), "status": result.status}, sort_keys=True))


if __name__ == "__main__":
    main()
