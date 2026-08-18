"""Create or verify the canonical Round 9 preregistration lock."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tomllib

import pandas as pd


MEMBERS = [
    "config/experiments/round9/PARENT_ACCEPTANCE.json",
    "config/experiments/round9/program.toml",
    "config/experiments/round9/transfer_registry.csv",
    "docs/20_experiments/R9A_mom255_union_ledger/design.md",
    "docs/20_experiments/R9B_mom255_transfer_economics/design.md",
    "docs/20_experiments/R9C_mom255_transfer_assessment/design.md",
    "docs/38_round9_p00_mom255_transfer_program_v1.md",
    "experiments/round9_groups.csv",
    "experiments/round9_registry.csv",
    "scripts/build_round9_prereg_lock.py",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payload(root: Path) -> dict[str, object]:
    missing = [member for member in MEMBERS if not (root / member).is_file()]
    if missing:
        raise AssertionError(f"missing Round9 lock members: {missing}")
    program = tomllib.loads(
        (root / "config/experiments/round9/program.toml").read_text(encoding="utf-8")
    )
    registry = pd.read_csv(root / "config/experiments/round9/transfer_registry.csv")
    if len(registry) != 6 or registry.transfer_id.nunique() != 6:
        raise AssertionError("Round9 transfer registry must contain six unique cells")
    if int(registry.primary.sum()) != 1:
        raise AssertionError("Round9 must have exactly one primary cell")
    if set(registry.frequency) != {"weekly", "monthly"}:
        raise AssertionError("Round9 frequency family drifted")
    if sorted(registry.top_k.unique().tolist()) != [10, 20, 50]:
        raise AssertionError("Round9 TopK family drifted")
    auth = program["authorization"]
    if not auth["union_event_ledger"] or not auth["development_mom255_nav"]:
        raise AssertionError("Round9 development transfer is not authorized")
    forbidden = ("lockbox", "model_search", "factor_search", "policy_search", "short_books", "wml")
    if any(auth[key] for key in forbidden):
        raise AssertionError("Round9 forbidden authorization drifted")
    return {
        "schema_version": 1,
        "program_id": program["program_id"],
        "lock_type": "development_batches_r9a_r9b_r9c",
        "frozen_at_local_date": "2026-08-18",
        "parent_round8_prereg_lock_sha256": program["parent"]["round8_prereg_lock_sha256"],
        "parent_g00_manifest_sha256": program["parent"]["g00_manifest_sha256"],
        "files": {member: sha(root / member) for member in sorted(MEMBERS)},
        "counts": {"transfer_cells": 6, "primary_cells": 1, "batches": 3, "cost_scenarios": 4},
        "authorization": auth,
        "firewall": program["firewall"],
        "hard_stop": program["hard_stop"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    data = (json.dumps(payload(root), indent=2, sort_keys=True) + "\n").encode("utf-8")
    path = root / "config/experiments/round9/PREREG_LOCK.json"
    if args.write:
        path.write_bytes(data)
    elif not path.exists() or path.read_bytes() != data:
        raise AssertionError("Round9 lock differs from canonical rebuild")
    print(json.dumps({"mode": "write" if args.write else "check", "sha256": hashlib.sha256(data).hexdigest(), "members": len(MEMBERS)}, sort_keys=True))


if __name__ == "__main__":
    main()
