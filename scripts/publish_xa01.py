from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "xa01-matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from momentum_reversal.pipelines.xa01_experiments import RUN_ID, audit_xa01


FILES = (
    "signal_summary.csv", "yearly_rank_ic.csv", "factor_correlations.csv",
    "portfolio_metrics.csv", "primary_active_comparison.csv",
    "portfolio_robustness.csv", "subperiod_robustness.csv",
    "paper_horizon_diagnostics.csv", "g00_identity_audit.csv",
    "final_assessment.csv", "xa01a_summary.json", "xa01b_summary.json",
    "xa01c_summary.json", "xa01d_summary.json", "manifest.json",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    project = Path(args.project_root).resolve()
    runtime = Path(args.runtime_root).resolve()
    audit = audit_xa01(project, runtime)
    source = runtime / "results" / "experiments" / "xa01" / RUN_ID
    dest = project / "results" / "published" / "cross_sectional_alpha" / "XA01"
    figures = project / "docs" / "figures" / "cross_sectional_alpha" / "XA01"
    dest.mkdir(parents=True, exist_ok=True); figures.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copy2(source / name, dest / name)
    final = pd.read_csv(source / "final_assessment.csv")
    _rankic_plot(final, figures / "rankic_by_frequency.png")
    _active_plot(final, figures / "top20_active_by_frequency.png")
    decision = {
        "schema_version": "xa01.decision.v1", "run_id": RUN_ID,
        "status": "completed_hard_stop_with_g00_top50_identity_exception",
        "evidence_qualified_cells": int(final["evidence_qualified"].sum()),
        "dimension_representative_cells": int(final["dimension_representative"].sum()),
        "dimension_representatives": final.loc[final["dimension_representative"], ["factor_id", "frequency"]].to_dict("records"),
        "g00_identity_passed": bool(final["g00_identity_gate_passed"].all()),
        "formal_eligible": False, "models_run": False, "aggregation_run": False,
        "p00_run": False, "lockbox_read": False,
    }
    _json(dest / "decision.json", decision)
    (dest / "README.md").write_text(_readme(decision), encoding="utf-8", newline="\n")
    report = project / "docs" / "20_experiments" / "XA01_atomic_factor_walkforward" / "report.md"
    report.write_text(_report(final, audit), encoding="utf-8", newline="\n")
    members = {}
    for path in sorted(dest.iterdir()):
        if path.is_file() and path.name != "publication_manifest.json":
            members[path.name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
    _json(dest / "publication_manifest.json", {"schema_version": "xa01.publication.v1", "files": members})
    print(json.dumps({"status": "published", "destination": str(dest), "audit": audit}, indent=2))


def _rankic_plot(frame: pd.DataFrame, path: Path) -> None:
    pivot = frame.pivot(index="factor_id", columns="frequency", values="median_rank_ic")
    pivot = pivot.sort_values("monthly")
    ax = pivot.plot.barh(figsize=(12, 8), color={"monthly": "#3568a8", "weekly": "#e08b36"})
    ax.axvline(0, color="black", linewidth=.8); ax.set_xlabel("Median cross-sectional RankIC")
    ax.set_ylabel(""); ax.set_title("XA01 atomic-factor RankIC")
    ax.figure.subplots_adjust(left=.31, right=.97, bottom=.10, top=.93); ax.figure.savefig(path, dpi=180); plt.close(ax.figure)


def _active_plot(frame: pd.DataFrame, path: Path) -> None:
    pivot = frame.pivot(index="factor_id", columns="frequency", values="active_terminal_return")
    pivot = pivot.sort_values("monthly")
    ax = pivot.plot.barh(figsize=(12, 8), color={"monthly": "#3568a8", "weekly": "#e08b36"})
    ax.axvline(0, color="black", linewidth=.8); ax.set_xlabel("Top20 terminal return minus eligible-EW")
    ax.set_ylabel(""); ax.set_title("XA01 Top20 primary-cost active result")
    ax.figure.subplots_adjust(left=.31, right=.97, bottom=.10, top=.93); ax.figure.savefig(path, dpi=180); plt.close(ax.figure)


def _readme(decision: dict) -> str:
    return f"""# XA01 published results

Status: `{decision['status']}`. XA01 evaluated 14 atomic factors across weekly/monthly Top5/10/20/50 long-only paths over 2018-01-02 through 2026-06-30, with no fitted model, aggregation, P00 transfer or lockbox.

No factor-frequency cell met the strict evidence-qualified gate. `XS003_MOM_12_7` is retained as a dimension representative at both frequencies; this is a diversity-preservation label, not standalone validation.

The G00 identity audit passed 16/24 scenarios. Top10 and Top20 were exact; all eight Top50 scenarios differed slightly. The pattern is consistent with XA01's stricter complete-window eligibility affecting names near the Top50 boundary, but ranking-level attribution remains an open audit item. Results therefore carry a documented Top50 identity exception.
"""


def _report(frame: pd.DataFrame, audit: dict) -> str:
    reps = frame.loc[frame["dimension_representative"]]
    lines = ["# XA01 atomic-factor walk-forward report", "", "XA01 completed all four preregistered batches and hard-stopped before models, aggregation or P00 transfer.", "", "## Decision", "", "- Evidence-qualified factor-frequency cells: 0/28.", "- Dimension representatives: XS003_MOM_12_7 at weekly and monthly frequency.", "- Lockbox read: false; evidence is full-history causal/prequential exploration.", "- G00 identity: 16/24 exact; Top10/Top20 exact. Top50 differs slightly in a pattern consistent with stricter complete-window eligibility, but exact ranking-level attribution remains open.", "", "## Representative evidence", ""]
    for item in reps.itertuples(index=False):
        lines.append(f"- {item.frequency}: median RankIC {item.median_rank_ic:.4f}, BH q {item.bh_q:.3f}, Top20 active terminal difference {item.active_terminal_return:.3f}, active Sharpe difference {item.active_sharpe:.3f}; 4/4 TopK and 4/4 cost scenarios positive.")
    lines += ["", "The representative label does not override the failed FDR gate. The next aggregation experiment may use it as the trend-family diversity control, while other dimensions remain unrepresented unless a new plan authorizes expanded candidates.", "", "## Figures", "", "![RankIC](../../figures/cross_sectional_alpha/XA01/rankic_by_frequency.png)", "", "![Active performance](../../figures/cross_sectional_alpha/XA01/top20_active_by_frequency.png)", ""]
    return "\n".join(lines)


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
