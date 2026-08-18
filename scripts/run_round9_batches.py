from __future__ import annotations

import argparse
import json
from pathlib import Path

from momentum_reversal.pipelines.round9_experiments import run_r9a, run_r9b, run_r9c


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", choices=["R9A", "R9B", "R9C"])
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = {"R9A": run_r9a, "R9B": run_r9b, "R9C": run_r9c}[args.batch](project_root=Path(args.project_root), runtime_root=Path(args.runtime_root), run_id=args.run_id)
    print(json.dumps({"output_dir": str(result.output_dir), "status": result.status}, sort_keys=True))


if __name__ == "__main__":
    main()
