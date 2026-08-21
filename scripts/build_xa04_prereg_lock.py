"""Build or verify the XA04 unified CORE10 preregistration lock."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import tomllib


MEMBERS = (
    "config/experiments/xa04/comparison_registry.csv",
    "config/experiments/xa04/model_recipes.csv",
    "config/experiments/xa04/process_registry.csv",
    "config/experiments/xa04/program.toml",
    "docs/20_experiments/XA04_unified_core10_lightgbm/design.md",
    "docs/47_xa04_unified_core10_lightgbm_program_v1.md",
    "scripts/build_xa04_prereg_lock.py",
)
LOCK = "config/experiments/xa04/PREREG_LOCK.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def validate(root: Path) -> None:
    with (root / "config/experiments/xa04/program.toml").open("rb") as handle:
        p = tomllib.load(handle)
    if p["status"] != "preregistered_authorized" or p["formal_eligible"] is not False:
        raise ValueError("XA04 authorization/status drift")
    if len(p["factors"]["core10"]) != 10 or len(set(p["factors"]["core10"])) != 10:
        raise ValueError("CORE10 must contain ten unique factors")
    if p["sample"]["missing_value_policy"] != "forbidden" or p["sample"]["model_specific_universe"]:
        raise ValueError("unified complete-case contract drift")
    if p["sample"]["first_oos_signal_close"] != "2017-12-29":
        raise ValueError("OOS start drift")
    if p["models"]["fallback"] or p["walk_forward"]["annual_recipe_selection"]:
        raise ValueError("fallback/recipe selection is forbidden")
    if any(p["authorization"].values()):
        raise ValueError("closed authorization unexpectedly enabled")
    processes = _rows(root / "config/experiments/xa04/process_registry.csv")
    if len(processes) != 34 or len({r["process_id"] for r in processes}) != 34:
        raise ValueError("expected exactly 34 registered processes per frequency")
    layers = {name: sum(r["layer"] == name for r in processes) for name in {r["layer"] for r in processes}}
    if layers != {"static": 2, "factor_only": 8, "state": 24}:
        raise ValueError(f"process layer counts drift: {layers}")
    if sum(r["family"] == "lightgbm" for r in processes) != 12:
        raise ValueError("expected twelve LightGBM processes per frequency")
    if sum("S2" in r["process_id"] or "S6" in r["process_id"] for r in processes) != 12:
        raise ValueError("expected twelve with-RSP state paths")
    for r in processes:
        if r["layer"] == "state" and (not r["parent_process"] or not r["rsp_twin"]):
            raise ValueError(f"state comparison mapping missing: {r['process_id']}")
    comparisons = _rows(root / "config/experiments/xa04/comparison_registry.csv")
    counts = {r["comparison_family"]: int(r["count_per_frequency"]) for r in comparisons}
    if counts != {"absolute": 34, "learned_parent": 32, "rsp_ablation": 12, "raw_anchor": 34}:
        raise ValueError(f"comparison families drift: {counts}")


def build(root: Path) -> dict:
    validate(root)
    files = {}
    for rel in sorted(MEMBERS):
        path = root / rel
        files[rel] = {"sha256": _sha(path), "size_bytes": path.stat().st_size}
    return {
        "schema_version": "xa04.prereg_lock.v1",
        "program_id": "xa04_unified_core10_lightgbm_supplement_v1",
        "status": "locked_authorized",
        "formal_eligible": False,
        "member_count": len(files),
        "files": files,
    }


def canonical(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    payload = canonical(build(root))
    target = root / LOCK
    if args.write:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    elif not target.exists() or target.read_bytes() != payload:
        raise SystemExit("XA04 PREREG_LOCK mismatch")
    print(hashlib.sha256(payload).hexdigest())


if __name__ == "__main__":
    main()
