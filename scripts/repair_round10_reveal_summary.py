"""Finish the frozen Round 10 reveal after a summary-only pandas API error.

This script never reruns targets, holdings, costs, or NAV.  It is deliberately
limited to the already-hashed partial reveal outputs listed in the acceptance
record.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tomllib

import numpy as np
import pandas as pd

from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.round2_market import sha256_file
from momentum_reversal.pipelines.round10_experiments import PROGRAM_ID, _file_records
from momentum_reversal.pipelines.round10_reveal import _active, _block_mean


ACCEPTANCE = Path("config/experiments/round10/REVEAL_REPAIR_ACCEPTANCE.json")
OUTCOME_LOCK = Path("config/experiments/round10/OUTCOME_REVEAL_LOCK.json")
PROGRAM = Path("config/experiments/round10/outcome_program.toml")


def repair_summary(*, project_root: Path, runtime_root: Path, run_id: str) -> Path:
    root = project_root.resolve()
    runtime = runtime_root.resolve()
    acceptance = json.loads((root / ACCEPTANCE).read_text(encoding="utf-8"))
    program = tomllib.loads((root / PROGRAM).read_text(encoding="utf-8"))
    if run_id != acceptance["run_id"] or run_id != program["run_id"]:
        raise DataQualityError("Round10 repair run-id mismatch")
    if sha256_file(root / OUTCOME_LOCK) != acceptance["outcome_reveal_lock_sha256"]:
        raise DataQualityError("Round10 original outcome lock drift")
    if sha256_file(root / "src/momentum_reversal/pipelines/round10_reveal.py") != acceptance["frozen_reveal_code_sha256"]:
        raise DataQualityError("Round10 frozen reveal code drift")

    output = runtime / "results/experiments/round10/R10C_OUTCOME_REVEAL/runs" / run_id
    for name, expected in acceptance["partial_outputs"].items():
        path = output / name
        if not path.is_file() or sha256_file(path) != expected:
            raise DataQualityError(f"Round10 partial reveal output drift: {name}")
    forbidden = ["leave_one_year_out.csv", "assessment.json", "decision.json", "manifest.json"]
    if any((output / name).exists() for name in forbidden):
        raise DataQualityError("Round10 repair refuses to overwrite summary outputs")

    nav = pd.read_parquet(output / "nav_daily.parquet")
    nav["date"] = pd.to_datetime(nav["date"]).dt.normalize()
    comp = pd.read_csv(output / "transfer_comparisons.csv")
    primary_id = program["economics"]["primary_transfer_id"]
    primary_cost = float(program["economics"]["primary_cost_bps"])
    active = _active(nav, primary_id, primary_cost)
    weekly = active.resample("W-FRI").sum()
    lower, pvalue = _block_mean(
        weekly.to_numpy(float),
        int(program["inference"]["block_weeks"]),
        int(program["inference"]["bootstrap_repetitions"]),
        int(program["inference"]["seed"]),
    )
    active_year = active.index.year.to_numpy()
    leave_rows = []
    for year in sorted(np.unique(active_year)):
        keep = active_year != year
        leave_rows.append({
            "removed_year": int(year),
            "removed_days": int((active_year == year).sum()),
            "timing_value_without_year": float(np.exp(active.iloc[np.flatnonzero(keep)].sum()) - 1),
        })
    leave = pd.DataFrame(leave_rows)
    leave.to_csv(output / "leave_one_year_out.csv", index=False, lineterminator="\n")

    c10 = comp[comp.cost_bps.eq(primary_cost)].copy()
    p_row = c10[c10.transfer_id.eq(primary_id)].iloc[0]
    passed = int(c10.four_metric_gate.sum())
    weekly_passed = int(c10.loc[c10.frequency.eq("weekly"), "four_metric_gate"].sum())
    monthly_passed = int(c10.loc[c10.frequency.eq("monthly"), "four_metric_gate"].sum())
    medians = {
        "overlay_to_naked_terminal_increment": float((c10.overlay_to_naked_terminal_ratio - 1).median()),
        "timing_value_vs_static": float(c10.timing_value_vs_static.median()),
        "delta_sharpe_vs_naked": float(c10.delta_sharpe_vs_naked.median()),
        "delta_mdd_vs_naked": float(c10.delta_mdd_vs_naked.median()),
    }
    gates = program["gates"]
    primary_four = bool(p_row.four_metric_gate)
    inference = lower > float(gates["primary_block_lower_gt"]) and pvalue <= float(gates["primary_p_le"])
    family = (
        passed >= int(gates["family_minimum_passed"])
        and weekly_passed >= int(gates["family_minimum_weekly_passed"])
        and monthly_passed >= int(gates["family_minimum_monthly_passed"])
        and all(value > float(gates["family_all_metric_medians_gt"]) for value in medians.values())
    )
    cost20 = bool(comp[comp.cost_bps.eq(20)].timing_value_vs_static.gt(0).all())
    leave_gate = bool(leave.timing_value_without_year.gt(float(gates["primary_minimum_leave_year_timing_gt"])).all())
    passed_all = primary_four and inference and family and cost20 and leave_gate
    assessment = {
        "primary_transfer_id": primary_id,
        "primary_four_metric_gate": primary_four,
        "primary_block13_95_lower": lower,
        "primary_one_sided_p": pvalue,
        "passed_cells": passed,
        "weekly_passed": weekly_passed,
        "monthly_passed": monthly_passed,
        "family_medians": medians,
        "family_gate": family,
        "cost20_direction_gate": cost20,
        "leave_one_year_gate": leave_gate,
        "mechanical_lockbox_passed": passed_all,
        "summary_repair_applied": True,
    }
    (output / "assessment.json").write_text(json.dumps(assessment, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    decision = {
        "program_id": PROGRAM_ID,
        "status": "completed_mechanical_lockbox",
        "mechanical_lockbox_passed": passed_all,
        "candidate": "P00_RSP_Y5_CLEAR__MOM255_TOP20_MONTHLY_LONG_ONLY",
        "formal_eligible": False,
        "target_revision": False,
        "automatic_revision": False,
        "summary_repair_applied": True,
    }
    (output / "decision.json").write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "batch_id": "R10C_OUTCOME_REVEAL",
        "run_id": run_id,
        "status": "completed_mechanical_lockbox",
        "formal_eligible": False,
        "outcome_reveal_run": True,
        "strategy_nav_run": True,
        "sealed_target_sha256": program["sealed"]["targets_sha256"],
        "outcome_reveal_lock_sha256": sha256_file(root / OUTCOME_LOCK),
        "repair_acceptance_sha256": sha256_file(root / ACCEPTANCE),
        "summary_repair_applied": True,
        "counts": {"nav_rows": len(nav), "scenarios": 72, "passed_cells": passed},
        "files": _file_records(output),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    output = repair_summary(project_root=Path(args.project_root), runtime_root=Path(args.runtime_root), run_id=args.run_id)
    print(json.dumps({"output_dir": str(output), "status": "completed_mechanical_lockbox"}, sort_keys=True))


if __name__ == "__main__":
    main()
