"""Build or check the XA01 preregistration lock."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


MEMBERS = (
    "config/experiments/xa01/factor_registry.csv",
    "config/experiments/xa01/program.toml",
    "docs/20_experiments/XA01_atomic_factor_walkforward/design.md",
    "docs/44_xa01_atomic_factor_walkforward_program_v1.md",
)
LOCK = "config/experiments/xa01/PREREG_LOCK.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(project_root: Path) -> dict[str, object]:
    files = []
    for name in sorted(MEMBERS):
        path = project_root / name
        if not path.is_file():
            raise FileNotFoundError(path)
        files.append({"path": name, "sha256": _sha(path), "size_bytes": path.stat().st_size})
    return {
        "schema_version": "xa01.prereg_lock.v1",
        "program_id": "xa01_atomic_factor_walkforward_v1",
        "status": "locked_authorized",
        "formal_eligible": False,
        "lockbox_authorized": False,
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    expected = build(root)
    path = root / LOCK
    encoded = (json.dumps(expected, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if args.write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
        print(_sha(path))
        return 0
    if not path.is_file() or path.read_bytes() != encoded:
        raise SystemExit("XA01 preregistration lock mismatch")
    print(_sha(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

