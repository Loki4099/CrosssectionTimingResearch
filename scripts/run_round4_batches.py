"""Run one preregistered Round 4 development batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from momentum_reversal.pipelines.round4_diagnostics import run_r4c, run_r4d
from momentum_reversal.pipelines.round4_experiments import run_r4b


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", choices=["R4B", "R4C", "R4D"])
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if args.batch == "R4B":
        result = run_r4b(
            project_root=Path(args.project_root),
            runtime_root=Path(args.runtime_root),
            run_id=args.run_id,
        )
    elif args.batch == "R4C":
        result = run_r4c(
            project_root=Path(args.project_root),
            runtime_root=Path(args.runtime_root),
            run_id=args.run_id,
        )
    else:
        result = run_r4d(
            project_root=Path(args.project_root),
            runtime_root=Path(args.runtime_root),
            run_id=args.run_id,
        )
    print(json.dumps({"output_dir": str(result.output_dir), "status": result.status}, sort_keys=True))


if __name__ == "__main__":
    main()
