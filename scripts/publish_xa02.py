from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "xa02-matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from momentum_reversal.pipelines.xa02_experiments import RUN_IDS, audit_xa02


FILES = {
    "XA02A": ("path_summary.csv", "xa01_path_identity.csv", "ranking_audit.csv", "xs056_twelve_month_repair.csv", "summary.json", "manifest.json"),
    "XA02B": ("state_bin_coverage.csv", "causality_audit.json", "summary.json", "manifest.json"),
    "XA02C": ("atlas_1d_conditional_summary.csv", "atlas_1d_tests.csv", "state_episode_contributions.csv", "atlas_2d_grid.csv", "atlas_2d_tests.csv", "summary.json", "manifest.json"),
    "XA02D": ("factor_similarity.csv", "empirical_clusters.csv", "factor_state_relationship_assessment.csv", "factor_state_role_assessment.csv", "atlas_2d_role_assessment.csv", "rolling_performance.csv", "calendar_performance.csv", "decision.json", "summary.json", "manifest.json"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    project = Path(args.project_root).resolve(); runtime = Path(args.runtime_root).resolve()
    audit = audit_xa02(project, runtime)
    dest = project / "results/published/cross_sectional_alpha/XA02"
    figures = project / "docs/figures/cross_sectional_alpha/XA02"
    report = project / "docs/20_experiments/XA02_factor_market_state_atlas/report.md"
    if dest.exists() or figures.exists() or report.exists():
        raise FileExistsError("XA02 compact publication destination already exists")
    dest.mkdir(parents=True); figures.mkdir(parents=True)
    for batch, names in FILES.items():
        source = runtime / "results/experiments/xa02" / batch / "runs" / RUN_IDS[batch]
        target = dest / batch; target.mkdir()
        for name in names: shutil.copy2(source / name, target / name)
    roles = pd.read_csv(dest / "XA02D/factor_state_role_assessment.csv")
    relationships = pd.read_csv(dest / "XA02D/factor_state_relationship_assessment.csv")
    _role_plot(roles, figures / "role_counts.png")
    _relationship_plot(relationships, figures / "conditional_relationships.png")
    decision = json.loads((dest / "XA02D/decision.json").read_text(encoding="utf-8"))
    (dest / "README.md").write_text(_readme(decision), encoding="utf-8", newline="\n")
    report.write_text(_report(decision), encoding="utf-8", newline="\n")
    members = {}
    for path in sorted(dest.rglob("*")):
        if path.is_file() and path.name != "publication_manifest.json":
            rel = path.relative_to(dest).as_posix()
            members[rel] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
    (dest / "publication_manifest.json").write_text(
        json.dumps({"schema_version": "xa02.publication.v1", "files": members,
                    "audit": audit}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": "published", "destination": str(dest), "audit": audit}, indent=2))


def _role_plot(frame: pd.DataFrame, path: Path) -> None:
    counts = frame["primary_role"].value_counts().sort_values()
    ax = counts.plot.barh(figsize=(9, 5), color="#3568a8")
    ax.set_xlabel("factor-frequency cells"); ax.set_ylabel(""); ax.set_title("XA02 advisory primary roles")
    ax.figure.tight_layout(); ax.figure.savefig(path, dpi=180); plt.close(ax.figure)


def _relationship_plot(frame: pd.DataFrame, path: Path) -> None:
    one = frame.loc[frame["qualifies"].astype(bool)].groupby("state_id").size().sort_values()
    if one.empty: one = frame.loc[frame["relationship_role"].eq("exploratory_state_candidate")].groupby("state_id").size().sort_values()
    ax = one.plot.barh(figsize=(9, 5), color="#e08b36")
    ax.set_xlabel("qualifying cells (or exploratory when none qualify)"); ax.set_ylabel("")
    ax.set_title("XA02 state relationships"); ax.figure.tight_layout(); ax.figure.savefig(path, dpi=180); plt.close(ax.figure)


def _readme(decision: dict) -> str:
    return f"""# XA02 published results

Status: `{decision['status']}`. XA02 reconstructed the complete XA01 factor paths and produced a causal market-state atlas. It did not train a model, aggregate factors, run P00, read a lockbox, or authorize XA03.

Qualifying one-dimensional relationships: {decision['qualifying_1d_relationships']}. Robust two-dimensional contexts: {decision['robust_2d_contexts']}. Exploratory two-dimensional contexts: {decision['exploratory_2d_contexts']}.

Daily NAV, holdings and large Parquet ledgers remain in the immutable runtime bundle. This directory contains compact summaries, role ledgers and manifests only.
"""


def _report(decision: dict) -> str:
    return f"""# XA02 factor performance and market-state atlas report

XA02 completed XA02A through XA02D and hard-stopped before model design.

## Result boundary

- One-dimensional qualifying relationships: {decision['qualifying_1d_relationships']}.
- Robust two-dimensional contexts: {decision['robust_2d_contexts']}.
- Exploratory two-dimensional contexts: {decision['exploratory_2d_contexts']}.
- Models, aggregation, strategy selection, market-state classifier, P00 and lockbox: not run.

## Figures

![Role counts](../../figures/cross_sectional_alpha/XA02/role_counts.png)

![Conditional relationships](../../figures/cross_sectional_alpha/XA02/conditional_relationships.png)

The advisory ledger requires user review before any XA03 model inputs or targets are frozen.
"""


if __name__ == "__main__":
    main()
