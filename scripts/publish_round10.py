"""Publish audited Round 10 bundles and decision figures."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from momentum_reversal.data.round2_market import sha256_file
from publish_compact import copy_compact


COMPACT_FILES = {
    "R10A": ("manifest.json", "overlap_audit.csv"),
    "R10B": ("annual_thresholds.csv", "manifest.json", "p00_states_weekly.parquet", "signal_identity.csv", "target_event_audit.csv"),
    "R10C": ("assessment.json", "decision.json", "g00_identity_audit.csv", "leave_one_year_out.csv", "manifest.json", "path_metrics.csv", "transfer_comparisons.csv"),
}


def tree(path: Path) -> str:
    records = [{"path": p.relative_to(path).as_posix(), "sha": sha256_file(p), "size": p.stat().st_size} for p in sorted(path.rglob("*")) if p.is_file()]
    return hashlib.sha256((json.dumps(records, sort_keys=True) + "\n").encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    root, runtime = Path(args.project_root).resolve(), Path(args.runtime_root).resolve()
    subprocess.run([sys.executable, str(root / "scripts/audit_round10.py"), "--project-root", str(root), "--runtime-root", str(runtime)], check=True)
    r10a = runtime / "data/round10/staging/R10A_RSP_LOCKBOX_FEATURE/r10a-rsp-lockbox-feature-20260818-v1"
    r10b = runtime / "results/experiments/round10/R10B_SEALED_TARGETS/runs/r10b-sealed-targets-20260818-v1"
    r10c = runtime / "results/experiments/round10/R10C_OUTCOME_REVEAL/runs/r10c-outcome-reveal-20260818-v1"
    published, figures = root / "results/published/round10", root / "docs/figures/round10"
    published.mkdir(parents=True, exist_ok=True); figures.mkdir(parents=True, exist_ok=True)
    rows = []
    for batch, source, short, status in (
        ("R10A_RSP_LOCKBOX_FEATURE", r10a, "R10A", "completed_data_extension"),
        ("R10B_SEALED_TARGETS", r10b, "R10B", "completed_sealed_targets"),
        ("R10C_OUTCOME_REVEAL", r10c, "R10C", "completed_mechanical_lockbox"),
    ):
        destination = published / short
        copy_compact(source, destination, COMPACT_FILES[short])
        rows.append({
            "batch_id": batch,
            "run_id": json.loads((source / "manifest.json").read_text(encoding="utf-8"))["run_id"],
            "status": status,
            "assessment": "mechanical_lockbox_failed" if short == "R10C" else status,
            "manifest_sha256": sha256_file(source / "manifest.json"),
            "tree_sha256": tree(source),
            "formal_eligible": False,
            "lockbox_read": short == "R10C",
        })
    pd.DataFrame(rows).to_csv(root / "experiments/round10_results.csv", index=False, lineterminator="\n")
    (published / "README.md").write_text(
        "# Published Round 10 results\n\n"
        "Audited 2022-01-03 through 2026-06-30 mechanical outcome reveal for frozen P00 on six long-only `mom_255_0` cells.\n\n"
        "- `status=completed_mechanical_lockbox`\n"
        "- `mechanical_lockbox_passed=false`\n"
        "- six-cell joint gate: `0/6`\n"
        "- `formal_eligible=false`\n\n"
        "The primary retained positive timing value versus matched static and improved Sharpe, but trailed naked terminal wealth and worsened maximum drawdown. See [`docs/41_round10_p00_mom255_mechanical_lockbox_decision_memo.md`](../../../docs/41_round10_p00_mom255_mechanical_lockbox_decision_memo.md).\n\n"
        "The original reveal wrote all NAV and economic paths before a pandas `Index.ne` compatibility error in the leave-one-year summary. Those partial outputs were hashed immediately; the original reveal code and lock were preserved. An independently frozen read-only repair wrote only the leave-year table, assessment, decision, and manifest. No target, state, holding, cost, NAV, threshold, or gate was revised.\n",
        encoding="utf-8",
    )

    nav = pd.read_parquet(r10c / "nav_daily.parquet")
    nav["date"] = pd.to_datetime(nav.date)
    primary = nav[(nav.transfer_id.eq("R10__MOM255__TOP20__MONTHLY")) & nav.cost_bps.eq(10)]
    fig, ax = plt.subplots(figsize=(9, 5.2))
    labels = {"naked": "Naked mom255", "p00_overlay": "P00 overlay", "matched_static": "Matched static"}
    colors = {"naked": "#6b7280", "p00_overlay": "#1677b8", "matched_static": "#d18f00"}
    for path_type, part in primary.groupby("path_type"):
        ax.plot(part.date, part.nav, label=labels[path_type], color=colors[path_type], linewidth=1.8)
    ax.set_title("Round 10 mechanical lockbox: Top20 monthly (10bp)")
    ax.set_ylabel("NAV (start = 1)"); ax.grid(alpha=.2); ax.legend(frameon=False); fig.tight_layout()
    fig.savefig(figures / "r10-primary-nav.png", dpi=190); plt.close(fig)

    comparison = pd.read_csv(r10c / "transfer_comparisons.csv")
    comparison = comparison[comparison.cost_bps.eq(10)].copy()
    order = ["T10 M", "T10 W", "T20 M", "T20 W", "T50 M", "T50 W"]
    comparison["label"] = comparison.transfer_id.str.extract(r"TOP(\d+)__(MONTHLY|WEEKLY)").apply(lambda row: f"T{row.iloc[0]} {'M' if row.iloc[1] == 'MONTHLY' else 'W'}", axis=1)
    comparison["label"] = pd.Categorical(comparison.label, order, ordered=True); comparison = comparison.sort_values("label")
    x = range(len(comparison)); width = .37
    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.bar([i - width/2 for i in x], (comparison.overlay_to_naked_terminal_ratio - 1) * 100, width, label="vs naked", color="#b84a4a")
    ax.bar([i + width/2 for i in x], comparison.timing_value_vs_static * 100, width, label="vs matched static", color="#d18f00")
    ax.axhline(0, color="black", linewidth=.8); ax.set_xticks(list(x), comparison.label.astype(str)); ax.set_ylabel("Terminal difference (%)"); ax.set_title("Round 10 six-cell terminal comparison (10bp)"); ax.legend(frameon=False); fig.tight_layout()
    fig.savefig(figures / "r10-six-cell-terminal-comparison.png", dpi=190); plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.6))
    axes[0].bar(comparison.label.astype(str), comparison.delta_sharpe_vs_naked, color="#4c9f70"); axes[0].axhline(0, color="black", linewidth=.8); axes[0].set_title("Sharpe change vs naked"); axes[0].tick_params(axis="x", rotation=35)
    axes[1].bar(comparison.label.astype(str), comparison.delta_mdd_vs_naked * 100, color="#9b6fb6"); axes[1].axhline(0, color="black", linewidth=.8); axes[1].set_title("Max-drawdown change vs naked"); axes[1].set_ylabel("Percentage points"); axes[1].tick_params(axis="x", rotation=35)
    fig.tight_layout(); fig.savefig(figures / "r10-six-cell-risk-adjusted-deltas.png", dpi=190); plt.close(fig)
    print(json.dumps({"status": "published", "batches": 3, "figures": 3, "mechanical_lockbox_passed": False}, sort_keys=True))


if __name__ == "__main__":
    main()
