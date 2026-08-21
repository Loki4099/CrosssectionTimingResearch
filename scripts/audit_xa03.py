from __future__ import annotations

import argparse
import json
from pathlib import Path

from momentum_reversal.pipelines.xa03_experiments import audit_xa03


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    print(json.dumps(audit_xa03(Path(args.project_root), Path(args.runtime_root)),
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
