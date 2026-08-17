"""Build the immutable Round 4 R4A factor-data candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from momentum_reversal.pipelines.round4_data import build_r4a_factor_data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default="config/data/round4/R4A_FACTOR_DATA.toml"
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--source-cache-dir", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = build_r4a_factor_data(
        config_path=Path(args.config),
        project_root=Path(args.project_root),
        runtime_root=Path(args.runtime_root),
        source_cache_dir=Path(args.source_cache_dir),
        run_id=args.run_id,
    )
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "manifest_path": str(result.manifest_path),
                "status": result.status,
                "reference_eligible_arms": result.reference_eligible_arms,
                "invalid_arms": result.invalid_arms,
                "common_weeks": result.common_weeks,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
