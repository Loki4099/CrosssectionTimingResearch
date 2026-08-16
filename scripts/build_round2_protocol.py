"""Generate the target-free Round 2 fold/lockbox manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from momentum_reversal.pipelines.round2_protocol import build_round2_fold_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", required=True, type=Path)
    parser.add_argument(
        "--output",
        default=Path("config/experiments/round2/folds.json"),
        type=Path,
    )
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"immutable fold manifest already exists: {args.output}")
    payload = build_round2_fold_manifest(args.candidate_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"output={args.output.resolve()}")
    print(
        "development="
        f"{payload['development']['first_outer_year']}-"
        f"{payload['development']['last_outer_year']}"
    )
    print(
        "lockbox="
        f"{payload['mechanical_lockbox']['start_signal']}.."
        f"{payload['mechanical_lockbox']['end_signal']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

