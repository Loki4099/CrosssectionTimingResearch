"""Create or verify the canonical Round 7 preregistration lock."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tomllib

import pandas as pd


MEMBERS = [
    "config/experiments/round7/PARENT_ACCEPTANCE.json",
    "config/experiments/round7/attack_registry.csv",
    "config/experiments/round7/event_registry.csv",
    "config/experiments/round7/feature_bundles.csv",
    "config/experiments/round7/folds.json",
    "config/experiments/round7/model_recipes.csv",
    "config/experiments/round7/process_registry.csv",
    "config/experiments/round7/program.toml",
    "docs/20_experiments/R7A_dual_target_folds/design.md",
    "docs/20_experiments/R7B_risk_model_tournament/design.md",
    "docs/20_experiments/R7C_rsp_attack_comparator/design.md",
    "docs/20_experiments/R7D_head_qualification/design.md",
    "docs/33_round7_dual_head_model_program_draft_v1.md",
    "experiments/round7_groups.csv",
    "experiments/round7_registry.csv",
    "pyproject.toml",
    "scripts/build_round7_folds.py",
    "scripts/build_round7_prereg_lock.py",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    payload = build_payload(root)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path = root / "config/experiments/round7/PREREG_LOCK.json"
    if args.write:
        path.write_bytes(encoded)
    else:
        if not path.exists() or path.read_bytes() != encoded:
            raise AssertionError("Round7 PREREG_LOCK differs from canonical rebuild")
    print(json.dumps({"mode": "write" if args.write else "check", "path": path.relative_to(root).as_posix(),
                      "sha256": hashlib.sha256(encoded).hexdigest(), "members": len(MEMBERS)}, sort_keys=True))


def build_payload(root: Path) -> dict:
    missing = [relative for relative in MEMBERS if not (root / relative).is_file()]
    if missing:
        raise AssertionError(f"Round7 lock members missing: {missing}")
    program = tomllib.loads((root / "config/experiments/round7/program.toml").read_text(encoding="utf-8"))
    bundles = pd.read_csv(root / "config/experiments/round7/feature_bundles.csv")
    recipes = pd.read_csv(root / "config/experiments/round7/model_recipes.csv")
    processes = pd.read_csv(root / "config/experiments/round7/process_registry.csv")
    attacks = pd.read_csv(root / "config/experiments/round7/attack_registry.csv")
    events = pd.read_csv(root / "config/experiments/round7/event_registry.csv")
    folds = json.loads((root / "config/experiments/round7/folds.json").read_text(encoding="utf-8"))
    groups = pd.read_csv(root / "experiments/round7_groups.csv")
    registry = pd.read_csv(root / "experiments/round7_registry.csv")
    if len(bundles) != 9 or bundles.bundle_id.nunique() != 9:
        raise AssertionError("Round7 bundle count drifted")
    if len(recipes) != 12 or recipes.recipe_id.nunique() != 12:
        raise AssertionError("Round7 recipe count drifted")
    if len(processes) != 27 or processes.process_id.nunique() != 27:
        raise AssertionError("Round7 process count drifted")
    if len(attacks) != 3 or attacks.formal_hypothesis.astype(bool).sum() != 1:
        raise AssertionError("Round7 attack registry drifted")
    if len(events) != 6 or events.episode_id.tolist() != program["inference"]["major_event_ids"]:
        raise AssertionError("Round7 event registry drifted")
    if len(groups) != 4 or len(registry) != 4:
        raise AssertionError("Round7 batch registry drifted")
    if len(folds["outer_folds"]) != 8 or sum(x["test_weeks"] for x in folds["outer_folds"]) != 404:
        raise AssertionError("Round7 folds drifted")
    auth = program["authorization"]
    if not auth["risk_models"] or not auth["attack_isotonic"] or auth["strategy_nav"] or auth["final_state_machine"] or auth["lockbox"] or auth["mom255_transfer"]:
        raise AssertionError("Round7 authorization is not fail-closed")
    if program["dependencies"]["lightgbm"] != "4.6.0" or program["models"]["risk_trial_arms"] != 108:
        raise AssertionError("Round7 dependency/trial budget drifted")
    return {
        "schema_version": 1,
        "program_id": "dual_head_model_round7_v1",
        "lock_type": "development_batches_r7a_r7b_r7c_r7d",
        "frozen_at_local_date": "2026-08-18",
        "parent_r6_prereg_lock_sha256": program["parent"]["r6_prereg_lock_sha256"],
        "files": {relative: _sha(root / relative) for relative in sorted(MEMBERS)},
        "counts": {"feature_bundles": 9, "recipe_ids": 12, "risk_processes": 27, "risk_trial_arms": 108,
                   "attack_processes": 3, "formal_attack_hypotheses": 1, "major_events": 6,
                   "outer_folds": 8, "outer_test_weeks": 404,
                   "registered_batches": 4},
        "folds": {"sha256": _sha(root / "config/experiments/round7/folds.json"), "outer_years": list(range(2014, 2022)),
                  "purge_scheduled_weeks": 13, "embargo_scheduled_weeks": 1, "final_outer_last_signal": "2021-09-24"},
        "dependency": {"lightgbm": "4.6.0", "metadata_sha256": program["dependencies"]["lightgbm_metadata_sha256"],
                       "dll_sha256": program["dependencies"]["lightgbm_dll_sha256"],
                       "deterministic_prediction_sha256": program["dependencies"]["lightgbm_deterministic_prediction_sha256"],
                       "objective": "regression"},
        "authorization": auth,
        "firewall": program["firewall"],
        "hard_stop": program["hard_stop"],
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
