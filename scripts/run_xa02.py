from __future__ import annotations

import argparse
import json
from pathlib import Path

from momentum_reversal.pipelines.xa02_experiments import run_xa02


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", choices=("XA02A", "XA02B", "XA02C", "XA02D"))
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    print(json.dumps(run_xa02(Path(args.project_root), Path(args.runtime_root), args.batch),
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
