"""Build or verify the immutable Round 6 preregistration lock.

The lock hashes raw file bytes.  It deliberately performs no newline, Unicode,
CSV, TOML, or Markdown normalization.  ``--write`` refuses to overwrite a
different existing lock; ``--check`` is read-only and verifies both the member
hashes and the canonical JSON serialization.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
import tomllib
from typing import Any


PROGRAM_ID = "attack4_single_factor_round6_v1"
FROZEN_AT_LOCAL_DATE = "2026-08-18"
LOCK_RELATIVE_PATH = "config/experiments/round6/PREREG_LOCK.json"
PROGRAM_RELATIVE_PATH = "config/experiments/round6/program.toml"
PARENT_ACCEPTANCE_RELATIVE_PATH = (
    "config/experiments/round6/PARENT_ACCEPTANCE.json"
)
CURRENT_R5_LOCK_RELATIVE_PATH = "config/experiments/round5/PREREG_LOCK.json"
EXPECTED_CURRENT_R5_LOCK_SHA256 = (
    "cc85d9a99b08bc8773096ec8c36b41cbd2e67c2ac844dae20fbce8a23bd9522d"
)
EXPECTED_RUNTIME_R5_LOCK_SHA256 = (
    "0d007b6c093f86a8eb93448531e1145c42d275d02b86116c984494d7485b607f"
)

EXPECTED_BATCH_IDS = (
    "R6A_ATTACK4_TARGET",
    "R6B_ATTACK4_SINGLE_FACTOR",
    "R6C_ATTACK4_ROLE_PROXY",
    "R6D_ATTACK4_ROBUSTNESS",
)

# Keep this allow-list explicit.  Adding a member is a preregistration change,
# not an automatic directory-discovery operation.
ALLOWED_MEMBERS = tuple(
    sorted(
        (
            "config/experiments/round6/PARENT_ACCEPTANCE.json",
            "config/experiments/round6/factor_registry.csv",
            "config/experiments/round6/program.toml",
            "config/experiments/round6/target_registry.csv",
            "docs/20_experiments/R6A_attack4_target/design.md",
            "docs/20_experiments/R6B_attack4_single_factor/design.md",
            "docs/20_experiments/R6C_attack4_role_proxy/design.md",
            "docs/20_experiments/R6D_attack4_robustness/design.md",
            "docs/30_defense_attack_dual_head_route_v1.md",
            "docs/31_round6_attack4_single_factor_program_v1.md",
            "experiments/round6_groups.csv",
            "experiments/round6_registry.csv",
            "scripts/build_round6_prereg_lock.py",
        )
    )
)

DELTA4_DEFINITION = (
    "source_defense_score[t-4_scheduled_weeks]-source_defense_score[t]; "
    "join endpoints on the full decision calendar before missing-value filtering"
)


class PreregistrationError(RuntimeError):
    """Raised when a Round 6 freeze invariant is not satisfied."""


def sha256_file(path: Path) -> str:
    """Return SHA256 of the file's exact bytes."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PreregistrationError(f"expected a JSON object: {path}")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _as_bool(value: str, *, field: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise PreregistrationError(f"{field} must be true/false, got {value!r}")


def _require_member_files(project_root: Path) -> None:
    missing = [relative for relative in ALLOWED_MEMBERS if not (project_root / relative).is_file()]
    if missing:
        joined = "\n  ".join(missing)
        raise PreregistrationError(f"Round 6 lock members are missing:\n  {joined}")


def _validate_parent(
    project_root: Path,
    program: dict[str, Any],
    acceptance: dict[str, Any],
) -> str:
    parent_path = project_root / CURRENT_R5_LOCK_RELATIVE_PATH
    if not parent_path.is_file():
        raise PreregistrationError(f"missing Round 5 parent lock: {parent_path}")
    actual = sha256_file(parent_path)
    if actual != EXPECTED_CURRENT_R5_LOCK_SHA256:
        raise PreregistrationError(
            "current Round 5 prereg lock drifted: "
            f"expected={EXPECTED_CURRENT_R5_LOCK_SHA256} actual={actual}"
        )

    parent = program.get("parent")
    if not isinstance(parent, dict):
        raise PreregistrationError("program.toml is missing [parent]")
    if parent.get("r5_current_prereg_lock_sha256") != actual:
        raise PreregistrationError(
            "program [parent].r5_current_prereg_lock_sha256 does not match "
            "the current Round 5 lock"
        )
    if (
        parent.get("r5_runtime_recorded_prereg_lock_sha256")
        != EXPECTED_RUNTIME_R5_LOCK_SHA256
    ):
        raise PreregistrationError("program does not preserve the runtime-recorded R5 lock")
    if parent.get("parent_acceptance_path") != PARENT_ACCEPTANCE_RELATIVE_PATH:
        raise PreregistrationError("program parent_acceptance_path drifted")

    attestation = acceptance.get("round5_attestation")
    if not isinstance(attestation, dict):
        raise PreregistrationError("PARENT_ACCEPTANCE lacks round5_attestation")
    if attestation.get("current_repository_prereg_lock_sha256") != actual:
        raise PreregistrationError("PARENT_ACCEPTANCE current R5 lock hash drifted")
    if (
        attestation.get("runtime_recorded_prereg_lock_sha256")
        != EXPECTED_RUNTIME_R5_LOCK_SHA256
    ):
        raise PreregistrationError("PARENT_ACCEPTANCE runtime R5 lock hash drifted")
    if attestation.get("experimental_semantics_changed") is not False:
        raise PreregistrationError("R5 parent acceptance must deny semantic drift")
    if attestation.get("data_or_result_bytes_changed") is not False:
        raise PreregistrationError("R5 parent acceptance must deny result-byte drift")
    audit = acceptance.get("round5_audit")
    if not isinstance(audit, dict) or audit.get("status") != "passed":
        raise PreregistrationError("PARENT_ACCEPTANCE does not record a passed R5 audit")
    if audit.get("all_lockbox_read_false") is not True:
        raise PreregistrationError("PARENT_ACCEPTANCE does not preserve the R5 firewall")
    return actual


def _validate_program(program: dict[str, Any]) -> None:
    if program.get("schema_version") != 1 or program.get("program_id") != PROGRAM_ID:
        raise PreregistrationError("unexpected Round 6 program identity")
    if program.get("formal_eligible") is not False:
        raise PreregistrationError("Round 6 must remain formal_eligible=false")
    if program.get("status") != "preregistered_development_authorized":
        raise PreregistrationError("Round 6 program status is not frozen/authorized")

    target = program.get("target")
    if not isinstance(target, dict):
        raise PreregistrationError("program.toml is missing [target]")
    expected_target = {
        "primary_name": "fwd_excess_logret_4w",
        "binary_name": "sustainable_attack_4w",
        "binary_diagnostic_only": True,
        "binary_can_promote": False,
        "worst_path_name": "fwd_worst_excess_4w",
        "worst_path_guardrail_only": True,
        "worst_path_can_promote": False,
        "alternative_target_selection": False,
    }
    for key, expected in expected_target.items():
        if target.get(key) != expected:
            raise PreregistrationError(f"target.{key} drifted from {expected!r}")

    factors = program.get("factors")
    if not isinstance(factors, dict):
        raise PreregistrationError("program.toml is missing [factors]")
    if (
        factors.get("registered_arms"),
        factors.get("level_arms"),
        factors.get("delta4_arms"),
    ) != (20, 17, 3):
        raise PreregistrationError("Round 6 factor count must remain 20=17+3")
    if (
        factors.get("delta4_attack_transform")
        != "defense_score_at_t_minus_4_scheduled_weeks_minus_defense_score_at_t"
    ):
        raise PreregistrationError("Round 6 delta4 attack transform drifted")
    if factors.get("delta4_calendar") != "complete_frozen_decision_calendar_before_missing_filter":
        raise PreregistrationError("Round 6 delta4 calendar rule drifted")
    if factors.get("delta4_missing_rule") != "either_endpoint_missing_means_missing_no_backfill":
        raise PreregistrationError("Round 6 delta4 missing rule drifted")

    inference = program.get("inference")
    if not isinstance(inference, dict):
        raise PreregistrationError("program.toml is missing [inference]")
    if (inference.get("block_weeks"), inference.get("veto_sensitivity_block_weeks")) != (4, 8):
        raise PreregistrationError("Round 6 inference must remain primary block=4/veto block=8")
    if inference.get("fdr_level") != 0.10 or inference.get("alert_budget") != 0.25:
        raise PreregistrationError("Round 6 multiplicity or alert budget drifted")

    model_input = program.get("qualification", {}).get("model_input")
    if not isinstance(model_input, dict):
        raise PreregistrationError("program.toml is missing qualification.model_input")
    if model_input.get("formula") != "robust_direct_attack OR economic_reference OR conditional_eligible":
        raise PreregistrationError("Round 6 role-route union drifted")
    if model_input.get("no_top_k") is not True or model_input.get("all_qualified_continue") is not True:
        raise PreregistrationError("Round 6 must retain every qualified arm without top-k")

    authorization = program.get("authorization")
    if not isinstance(authorization, dict):
        raise PreregistrationError("program.toml is missing [authorization]")
    forbidden = (
        "models",
        "model_selection",
        "bagging",
        "stacking",
        "final_state_machine",
        "state_machine_search",
        "lockbox",
        "mom255_transfer",
        "alternative_target",
        "unregistered_factor_additions",
        "window_search",
        "position_search",
    )
    for key in forbidden:
        if authorization.get(key) is not False:
            raise PreregistrationError(f"authorization.{key} must fail closed")


def _validate_registries(project_root: Path) -> tuple[int, int, int, int, int]:
    factors = _read_csv(project_root / "config/experiments/round6/factor_registry.csv")
    if len(factors) != 20 or len({row.get("attack_arm_id") for row in factors}) != 20:
        raise PreregistrationError("factor registry must contain 20 unique attack arms")
    levels = [row for row in factors if row.get("transform_kind") == "negate_level"]
    deltas = [row for row in factors if row.get("transform_kind") == "calendar_delta"]
    if (len(levels), len(deltas)) != (17, 3):
        raise PreregistrationError("factor registry must partition as 17 levels + 3 deltas")
    for row in levels:
        if row.get("lag_scheduled_weeks") != "0" or row.get("attack_score_definition") != "-source_defense_score":
            raise PreregistrationError(f"invalid level definition: {row.get('attack_arm_id')}")
    for row in deltas:
        if row.get("lag_scheduled_weeks") != "4" or row.get("attack_score_definition") != DELTA4_DEFINITION:
            raise PreregistrationError(f"invalid delta4 definition: {row.get('attack_arm_id')}")
    for row in factors:
        if not _as_bool(row.get("high_means_attack", ""), field="high_means_attack"):
            raise PreregistrationError("every registered score must be directed high-means-attack")
        if _as_bool(row.get("replacement_allowed", ""), field="replacement_allowed"):
            raise PreregistrationError("factor replacement must remain forbidden")
    direct = [row for row in factors if _as_bool(row.get("direct_eligible", ""), field="direct_eligible")]
    conditional = [row for row in factors if _as_bool(row.get("conditional_eligible", ""), field="conditional_eligible")]
    context = [row for row in factors if _as_bool(row.get("context_only", ""), field="context_only")]
    if (len(direct), len(conditional), len(context)) != (12, 6, 2):
        raise PreregistrationError("factor role permissions must remain direct=12 conditional=6 context=2")
    if any(row in direct or row in conditional for row in context):
        raise PreregistrationError("context-only arms cannot receive direct/conditional permission")

    targets = _read_csv(project_root / "config/experiments/round6/target_registry.csv")
    if len(targets) != 3 or len({row.get("target_id") for row in targets}) != 3:
        raise PreregistrationError("target registry must contain three unique targets")
    by_id = {row["target_id"]: row for row in targets}
    if set(by_id) != {"A4_CONTINUOUS", "A4_POSITIVE", "W4_VETO"}:
        raise PreregistrationError("target registry identities drifted")
    if not _as_bool(by_id["A4_CONTINUOUS"]["primary"], field="primary"):
        raise PreregistrationError("A4_CONTINUOUS must be the sole primary target")
    if not _as_bool(
        by_id["A4_POSITIVE"]["diagnostic_guardrail"],
        field="diagnostic_guardrail",
    ):
        raise PreregistrationError("A4_POSITIVE must remain diagnostic-only")
    if not _as_bool(by_id["W4_VETO"]["veto_only"], field="veto_only"):
        raise PreregistrationError("W4_VETO must remain veto-only")
    for target_id in ("A4_POSITIVE", "W4_VETO"):
        if _as_bool(by_id[target_id]["selection_authority"], field="selection_authority"):
            raise PreregistrationError(f"{target_id} cannot select or promote")

    groups = _read_csv(project_root / "experiments/round6_groups.csv")
    group_ids = tuple(row.get("batch_id") for row in groups)
    if group_ids != EXPECTED_BATCH_IDS:
        raise PreregistrationError(f"Round 6 batch registry drifted: {group_ids!r}")
    registry = _read_csv(project_root / "experiments/round6_registry.csv")
    if len(registry) != 4 or tuple(row.get("batch_id") for row in registry) != EXPECTED_BATCH_IDS:
        raise PreregistrationError("Round 6 experiment registry must map one row per batch")
    return len(factors), len(levels), len(deltas), len(targets), len(groups)


def build_lock(project_root: Path) -> dict[str, Any]:
    """Return the canonical Round 6 lock payload without writing it."""

    project_root = project_root.resolve()
    _require_member_files(project_root)
    program = tomllib.loads(
        (project_root / PROGRAM_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    acceptance = _read_json(project_root / PARENT_ACCEPTANCE_RELATIVE_PATH)
    _validate_program(program)
    parent_hash = _validate_parent(project_root, program, acceptance)
    factor_count, level_count, delta_count, target_count, batch_count = (
        _validate_registries(project_root)
    )

    files = {
        relative: sha256_file(project_root / relative)
        for relative in ALLOWED_MEMBERS
    }
    return {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "lock_type": "development_batches_r6a_r6b_r6c_r6d",
        "frozen_at_local_date": FROZEN_AT_LOCAL_DATE,
        "parent_r5_prereg_lock_sha256": parent_hash,
        "files": files,
        "parent": {
            "acceptance_path": PARENT_ACCEPTANCE_RELATIVE_PATH,
            "acceptance_sha256": files[PARENT_ACCEPTANCE_RELATIVE_PATH],
            "runtime_recorded_r5_prereg_lock_sha256": (
                EXPECTED_RUNTIME_R5_LOCK_SHA256
            ),
        },
        "counts": {
            "registered_factor_arms": factor_count,
            "level_arms": level_count,
            "delta4_arms": delta_count,
            "registered_targets": target_count,
            "registered_batches": batch_count,
            "direct_eligible_arms": 12,
            "conditional_eligible_arms": 6,
            "context_only_arms": 2,
        },
        "target": {
            "primary_name": program["target"]["primary_name"],
            "binary_name": program["target"]["binary_name"],
            "binary_diagnostic_only": program["target"]["binary_diagnostic_only"],
            "worst_path_name": program["target"]["worst_path_name"],
            "worst_path_guardrail_only": program["target"]["worst_path_guardrail_only"],
            "alternative_target_selection": program["target"]["alternative_target_selection"],
        },
        "authorization": program["authorization"],
        "firewall": program["firewall"],
        "hard_stop": program["hard_stop"],
    }


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    """Serialize with two-space indentation, UTF-8, and one terminal LF."""

    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")


def write_lock(project_root: Path) -> Path:
    destination = project_root.resolve() / LOCK_RELATIVE_PATH
    expected = canonical_json_bytes(build_lock(project_root))
    if destination.exists():
        if destination.read_bytes() != expected:
            raise PreregistrationError(
                f"refusing to overwrite a different existing lock: {destination}"
            )
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(expected)
    return destination


def check_lock(project_root: Path) -> Path:
    destination = project_root.resolve() / LOCK_RELATIVE_PATH
    if not destination.is_file():
        raise PreregistrationError(f"Round 6 lock does not exist: {destination}")
    expected = canonical_json_bytes(build_lock(project_root))
    actual = destination.read_bytes()
    if actual != expected:
        raise PreregistrationError(
            "Round 6 prereg lock is non-canonical or one of its members drifted"
        )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root)
    try:
        destination = write_lock(root) if args.write else check_lock(root)
    except (OSError, ValueError, KeyError, PreregistrationError) as error:
        print(f"round6-prereg-lock: failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(
        json.dumps(
            {
                "mode": "write" if args.write else "check",
                "path": destination.relative_to(root.resolve()).as_posix(),
                "sha256": sha256_file(destination),
                "status": "passed",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
