from __future__ import annotations

import argparse
from pathlib import Path

from momentum_reversal.pipelines.round2_models import build_r2c_simple_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the R2C simple development bundle")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--r2a-candidate-dir", type=Path, required=True)
    parser.add_argument("--r2b-bundle-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = build_r2c_simple_bundle(
        project_root=args.project_root,
        r2a_candidate_dir=args.r2a_candidate_dir,
        r2b_bundle_dir=args.r2b_bundle_dir,
        output_root=args.output_root,
        run_id=args.run_id,
    )
    print(
        {
            "bundle_dir": str(result.bundle_dir),
            "process_count": result.process_count,
            "prediction_rows": result.prediction_rows,
            "complex_gate_open": result.complex_gate_open,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

