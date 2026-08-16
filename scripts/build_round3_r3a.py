from __future__ import annotations

import argparse
from pathlib import Path

from momentum_reversal.pipelines.round3_reentry import build_r3a_development_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the R3A development bundle")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--r2a-candidate-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = build_r3a_development_bundle(
        project_root=args.project_root,
        r2a_candidate_dir=args.r2a_candidate_dir,
        output_root=args.output_root,
        run_id=args.run_id,
    )
    print(
        {
            "bundle_dir": str(result.bundle_dir),
            "status": result.status,
            "weekly_rows": result.weekly_rows,
            "nav_rows": result.nav_rows,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

