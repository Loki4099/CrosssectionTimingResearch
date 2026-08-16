"""Build immutable R2B features, development targets, and signal diagnostics."""

from __future__ import annotations

import argparse
from pathlib import Path

from momentum_reversal.pipelines.round2_signals import build_r2b_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--r2a-candidate-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = build_r2b_bundle(
        project_root=args.project_root,
        r2a_candidate_dir=args.r2a_candidate_dir,
        output_root=args.output_root,
        run_id=args.run_id,
    )
    print(f"bundle={result.bundle_dir}")
    print(f"manifest={result.manifest_path}")
    print(f"feature_rows={result.feature_rows}")
    print(f"development_target_rows={result.development_target_rows}")
    print(f"withheld_lockbox_rows={result.withheld_lockbox_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

