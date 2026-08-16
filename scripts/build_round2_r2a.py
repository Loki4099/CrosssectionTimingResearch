"""Build the R2A long-market staging dataset; never computes research targets."""

from __future__ import annotations

import argparse
from pathlib import Path

from momentum_reversal.pipelines.round2_data import (
    build_r2a_long_staging,
    rebuild_r2a_long_canonical_hashes,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default="config/data/round2/R2A_DATA.toml", type=Path
    )
    parser.add_argument("--project-root", default=Path.cwd(), type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--snapshot-id")
    parser.add_argument(
        "--verify-existing",
        type=Path,
        help="rebuild canonical hashes from an existing staging raw snapshot; no network/write",
    )
    parser.add_argument(
        "--reuse-raw-from",
        type=Path,
        help="build a new immutable snapshot from a verified parent raw snapshot; no network",
    )
    parser.add_argument(
        "--license-review-status",
        choices=("pending_human_confirmation", "approved_for_local_research"),
        default="pending_human_confirmation",
    )
    args = parser.parse_args()
    if args.verify_existing is not None:
        hashes = rebuild_r2a_long_canonical_hashes(args.verify_existing)
        for name, value in sorted(hashes.items()):
            print(f"{name}={value}")
        return 0
    if args.data_root is None or args.snapshot_id is None:
        parser.error("--data-root and --snapshot-id are required for acquisition")
    result = build_r2a_long_staging(
        config_path=args.config,
        project_root=args.project_root,
        data_root=args.data_root,
        snapshot_id=args.snapshot_id,
        license_review_status=args.license_review_status,
        reuse_raw_from=args.reuse_raw_from,
    )
    print(f"status={result.status}")
    print(f"output_dir={result.output_dir}")
    print(f"manifest={result.manifest_path}")
    print(f"spy_rows={result.spy_rows}")
    print(f"risk_free_rows={result.risk_free_rows}")
    print(f"vix_rows={result.vix_rows}")
    print(f"vix_missing_sessions={result.vix_missing_sessions}")
    print(f"vix_missing_signal_sessions={result.vix_missing_signal_sessions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
