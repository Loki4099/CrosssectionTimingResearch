"""Build/check the narrow Round 10 summary-repair lock."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


MEMBERS = (
    "config/experiments/round10/REVEAL_REPAIR_ACCEPTANCE.json",
    "scripts/repair_round10_reveal_summary.py",
)
LOCK = "config/experiments/round10/REVEAL_REPAIR_LOCK.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payload(root: Path) -> dict:
    acceptance = json.loads((root / MEMBERS[0]).read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "program_id": acceptance["program_id"],
        "status": "frozen_deterministic_summary_repair",
        "outcome_reveal_lock_sha256": acceptance["outcome_reveal_lock_sha256"],
        "files": {name: digest(root / name) for name in sorted(MEMBERS)},
    }


def encoded(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    lock = root / LOCK
    expected = encoded(payload(root))
    if args.write:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_bytes(expected)
    elif not lock.is_file() or lock.read_bytes() != expected:
        raise SystemExit("Round10 repair lock missing or drifted")
    print(json.dumps({"mode": "write" if args.write else "check", "sha256": digest(lock)}, sort_keys=True))


if __name__ == "__main__":
    main()
