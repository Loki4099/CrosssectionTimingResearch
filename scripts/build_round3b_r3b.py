from __future__ import annotations

import argparse
from pathlib import Path

from momentum_reversal.pipelines.round3b_persistence import build_r3b_development_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the R3B development bundle")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--r2a-candidate-dir", type=Path, required=True)
    parser.add_argument("--r2b-bundle-dir", type=Path, required=True)
    parser.add_argument("--r3a-bundle-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = build_r3b_development_bundle(
        project_root=args.project_root,
        r2a_candidate_dir=args.r2a_candidate_dir,
        r2b_bundle_dir=args.r2b_bundle_dir,
        r3a_bundle_dir=args.r3a_bundle_dir,
        output_root=args.output_root,
        run_id=args.run_id,
    )
    print({"bundle_dir": str(result.bundle_dir), "status": result.status, "prediction_rows": result.prediction_rows, "weekly_rows": result.weekly_rows})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

