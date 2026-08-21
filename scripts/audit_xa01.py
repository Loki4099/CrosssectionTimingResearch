from __future__ import annotations

import argparse
import json
from pathlib import Path

from momentum_reversal.pipelines.xa01_experiments import audit_xa01


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    result = audit_xa01(Path(args.project_root), Path(args.runtime_root))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
