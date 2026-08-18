"""Publish audited Round 9 bundles and figures."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from momentum_reversal.data.round2_market import sha256_file
from publish_compact import copy_compact


BATCHES = [
    ("R9A_MOM255_UNION_LEDGER", "R9A", 0),
    ("R9B_MOM255_TRANSFER_ECONOMICS", "R9B", 1),
    ("R9C_MOM255_TRANSFER_ASSESSMENT", "R9C", 2),
]

COMPACT_FILES = {
    "R9A": ("g00_identity_audit.csv", "manifest.json", "static_allocations.csv"),
    "R9B": ("manifest.json", "path_metrics.csv", "transfer_comparisons.csv", "yearly_timing.csv"),
    "R9C": ("cell_assessment.csv", "decision.json", "family_assessment.json", "family_medians.csv", "leave_one_event_out.csv", "manifest.json"),
}


def tree(path: Path) -> str:
    records = [{"path": item.relative_to(path).as_posix(), "sha": sha256_file(item), "size": item.stat().st_size} for item in sorted(path.rglob("*")) if item.is_file()]
    return hashlib.sha256((json.dumps(records, sort_keys=True) + "\n").encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    root, runtime = Path(args.project_root).resolve(), Path(args.runtime_root).resolve()
    subprocess.run([sys.executable, str(root / "scripts/audit_round9.py"), "--project-root", str(root), "--runtime-root", str(runtime)], check=True, capture_output=True)
    program = tomllib.loads((root / "config/experiments/round9/program.toml").read_text(encoding="utf-8"))
    published, figures = root / "results/published/round9", root / "docs/figures/round9"
    published.mkdir(parents=True, exist_ok=True); figures.mkdir(parents=True, exist_ok=True)
    rows = []
    sources = {}
    for batch, short, index in BATCHES:
        source = runtime / "results/experiments/round9" / batch / "runs" / program["run_ids"][index]
        sources[short] = source
        destination = published / short
        copy_compact(source, destination, COMPACT_FILES[short])
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        rows.append({"batch_id": batch, "run_id": program["run_ids"][index], "status": manifest["status"], "assessment": manifest["assessment"], "manifest_sha256": sha256_file(source / "manifest.json"), "tree_sha256": tree(source), "formal_eligible": False, "lockbox_read": False})
    pd.DataFrame(rows).to_csv(root / "experiments/round9_results.csv", index=False, lineterminator="\n")
    (published / "README.md").write_text("# Published Round 9 results\n\nAudited 2018–2021 development transfer of frozen P00 to six long-only mom_255_0 cells. Lockbox outcomes were not read.\n", encoding="utf-8")

    nav = pd.read_parquet(sources["R9A"] / "nav_daily.parquet")
    nav["date"] = pd.to_datetime(nav.date)
    primary = nav[(nav.transfer_id.eq("R9__MOM255__TOP20__MONTHLY")) & nav.cost_bps.eq(10)]
    fig, ax = plt.subplots(figsize=(9, 5.2))
    labels = {"naked": "Naked mom255", "p00_overlay": "P00 overlay", "matched_static": "Matched static"}
    colors = {"naked": "#6b7280", "p00_overlay": "#1677b8", "matched_static": "#d18f00"}
    for path_type, part in primary.groupby("path_type"):
        ax.plot(part.date, part.nav, label=labels[path_type], color=colors[path_type], linewidth=1.8)
    ax.set_title("Round 9 primary: mom_255_0 Top20 monthly (10bp)")
    ax.set_ylabel("NAV (start = 1)"); ax.grid(alpha=.2); ax.legend(frameon=False); fig.tight_layout()
    fig.savefig(figures / "r9-primary-nav.png", dpi=190); plt.close(fig)

    comparison = pd.read_csv(published / "R9C/cell_assessment.csv")
    order = ["T10 M", "T10 W", "T20 M", "T20 W", "T50 M", "T50 W"]
    comparison["label"] = comparison.transfer_id.str.extract(r"TOP(\d+)__(MONTHLY|WEEKLY)").apply(lambda row: f"T{row.iloc[0]} {'M' if row.iloc[1] == 'MONTHLY' else 'W'}", axis=1)
    comparison["label"] = pd.Categorical(comparison.label, order, ordered=True)
    comparison = comparison.sort_values("label")
    x = range(len(comparison)); width = .37
    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.bar([i - width/2 for i in x], (comparison.overlay_to_naked_terminal_ratio - 1) * 100, width, label="vs naked", color="#1677b8")
    ax.bar([i + width/2 for i in x], comparison.timing_value_vs_static * 100, width, label="vs matched static", color="#d18f00")
    ax.axhline(0, color="black", linewidth=.8); ax.set_xticks(list(x), comparison.label.astype(str)); ax.set_ylabel("Terminal improvement (%)"); ax.set_title("P00 transfer across six mom255 cells (10bp)"); ax.legend(frameon=False); fig.tight_layout()
    fig.savefig(figures / "r9-six-cell-terminal-improvement.png", dpi=190); plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.6))
    axes[0].bar(comparison.label.astype(str), comparison.delta_sharpe_vs_naked, color="#4c9f70"); axes[0].axhline(0, color="black", linewidth=.8); axes[0].set_title("Sharpe improvement"); axes[0].tick_params(axis="x", rotation=35)
    axes[1].bar(comparison.label.astype(str), comparison.delta_mdd_vs_naked * 100, color="#9b6fb6"); axes[1].axhline(0, color="black", linewidth=.8); axes[1].set_title("Max-drawdown improvement"); axes[1].set_ylabel("Percentage points"); axes[1].tick_params(axis="x", rotation=35)
    fig.tight_layout(); fig.savefig(figures / "r9-six-cell-risk-adjusted-deltas.png", dpi=190); plt.close(fig)
    decision = json.loads((published / "R9C/decision.json").read_text(encoding="utf-8"))
    print(json.dumps({"status": "published", "batches": 3, "figures": 3, "development_transfer_eligible": decision["development_transfer_eligible"]}, sort_keys=True))


if __name__ == "__main__":
    main()
