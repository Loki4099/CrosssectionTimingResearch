"""Publish audited Round 7 bundles, registry, figures, and a compact README."""

from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from momentum_reversal.data.round2_market import sha256_file
from publish_compact import copy_compact


BATCHES = (
    ("R7A_DUAL_TARGET_FOLDS", "R7A", 0),
    ("R7B_RISK_MODEL_TOURNAMENT", "R7B", 1),
    ("R7C_RSP_ATTACK_COMPARATOR", "R7C", 2),
    ("R7D_HEAD_QUALIFICATION", "R7D", 3),
)

COMPACT_FILES = {
    "R7A": ("acceptance_summary.csv", "common_weekly.parquet", "fold_ledger.csv", "manifest.json"),
    "R7B": ("inner_selection.csv", "inner_trial_summary.csv", "manifest.json", "outer_predictions.parquet", "raw_rsp_sentinel.parquet", "risk_summary.csv", "risk_yearly.csv"),
    "R7C": ("attack_summary.csv", "attack_yearly.csv", "manifest.json", "outer_predictions.parquet"),
    "R7D": ("attack_final_assessment.csv", "attack_leave_one_event_out.csv", "decision.json", "head_role_ledger.csv", "manifest.json", "risk_equivalence_clusters.csv", "risk_final_assessment.csv", "risk_leave_one_event_out.csv"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    root, runtime = Path(args.project_root).resolve(), Path(args.runtime_root).resolve()
    subprocess.run([sys.executable, str(root / "scripts/audit_round7.py"), "--project-root", str(root), "--runtime-root", str(runtime)], check=True, capture_output=True)
    program = tomllib.loads((root / "config/experiments/round7/program.toml").read_text(encoding="utf-8"))
    published = root / "results/published/round7"
    figures = root / "docs/figures/round7"
    published.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    registry_rows = []
    for batch, short, index in BATCHES:
        source = runtime / "results/experiments/round7" / batch / "runs" / program["run_ids"][index]
        destination = published / short
        copy_compact(source, destination, COMPACT_FILES[short])
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        registry_rows.append({"batch_id": batch, "run_id": program["run_ids"][index], "status": manifest["status"],
                              "assessment": manifest["assessment"], "manifest_sha256": sha256_file(source / "manifest.json"),
                              "tree_sha256": _tree_sha(source), "formal_eligible": False, "lockbox_read": False})
    pd.DataFrame(registry_rows).to_csv(root / "experiments/round7_results.csv", index=False, lineterminator="\n")
    risk = pd.read_csv(published / "R7D/risk_final_assessment.csv").sort_values("spearman_y5")
    attack = pd.read_csv(published / "R7D/attack_final_assessment.csv")
    selection = pd.read_csv(published / "R7B/inner_selection.csv")
    _risk_figure(risk, figures / "r7-risk-outer-oos-rankic.png")
    _attack_figure(attack, figures / "r7-rsp-attack-comparison.png")
    _selection_figure(selection, figures / "r7-inner-recipe-selection.png")
    readme = """# Published Round 7 results

Audited immutable development bundles for the preregistered dual-head model experiment.

- `R7A`: common targets and absolute nested folds
- `R7B`: 27 risk-process outer-OOS predictions and inner selection ledgers
- `R7C`: raw-RSP and monotone-A4 outer-OOS attack predictions
- `R7D`: event leave-one-out, equivalence clusters, final qualification, and hard stop

No lockbox outcomes, state-machine paths, strategy NAV, or mom255 transfer were generated.
"""
    (published / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    print(json.dumps({"status": "published", "batches": 4, "figures": 3,
                      "risk_qualified": int(risk.risk_qualified.sum()),
                      "attack_qualified": int(attack.attack_qualified.sum())}, sort_keys=True))


def _tree_sha(path: Path) -> str:
    records = [{"path": p.relative_to(path).as_posix(), "sha256": sha256_file(p), "size": p.stat().st_size}
               for p in sorted(path.rglob("*")) if p.is_file()]
    return hashlib.sha256((json.dumps(records, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def _risk_figure(risk: pd.DataFrame, path: Path) -> None:
    colors = np.where(risk.risk_qualified, "#1b9e77", np.where(risk.block13_95_lower > 0, "#4c78a8", "#b9c2cc"))
    fig, ax = plt.subplots(figsize=(10, 9))
    ax.barh(risk.process_id, risk.spearman_y5, color=colors)
    ax.axvline(.3850387788219442, color="#d62728", linestyle="--", linewidth=1.5, label="Raw RSP sentinel (0.385)")
    ax.axvline(0, color="black", linewidth=.7)
    ax.set_xlabel("Outer-OOS Spearman with Y5")
    ax.set_title("Round 7 risk processes: no process passed all gates")
    ax.legend(loc="lower right")
    fig.tight_layout(); fig.savefig(path, dpi=180); plt.close(fig)


def _attack_figure(attack: pd.DataFrame, path: Path) -> None:
    labels = ["Raw -RSP", "Isotonic E[A4|-RSP]"]
    x = np.arange(2); width = .25
    skill = attack.mae_skill_vs_train_mean.to_numpy(float).copy()
    skill[attack.attack_process_id.eq("AX01_RAW_RSP_RECOVERY").to_numpy()] = np.nan
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(x - width, attack.spearman_a4, width, label="Spearman A4")
    ax.bar(x, attack.auc_b4 - .5, width, label="AUC B4 minus 0.5")
    ax.bar(x + width, skill, width, label="MAE skill (calibrated head only)")
    ax.axhline(0, color="black", linewidth=.8)
    ax.set_xticks(x, labels); ax.set_title("Round 7 RSP attack comparator")
    ax.legend(); fig.tight_layout(); fig.savefig(path, dpi=180); plt.close(fig)


def _selection_figure(selection: pd.DataFrame, path: Path) -> None:
    counts = selection.groupby(["family", "selected_recipe_id"]).size().unstack(fill_value=0)
    fig, ax = plt.subplots(figsize=(10, 5))
    counts.plot(kind="bar", stacked=True, ax=ax, colormap="tab20")
    ax.set_ylabel("Outer folds selected"); ax.set_xlabel("")
    ax.set_title("Inner one-SE recipe selections across 27 processes × 8 folds")
    ax.legend(title="Recipe", bbox_to_anchor=(1.02, 1), loc="upper left")
    fig.tight_layout(); fig.savefig(path, dpi=180); plt.close(fig)


if __name__ == "__main__": main()
