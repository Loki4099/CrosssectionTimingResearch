from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "xa03-matplotlib"))
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from momentum_reversal.pipelines.xa03_experiments import RUN_IDS, audit_xa03


FILES = {
    "XA03A": (
        "causality_and_identity_audit.json", "common_universe_coverage.csv",
        "summary.json", "manifest.json",
    ),
    "XA03B": ("invalid_process_ledger.csv", "prediction_audit.csv", "summary.json", "manifest.json"),
    "XA03C": ("invalid_process_ledger.csv", "prediction_audit.csv", "summary.json", "manifest.json"),
    "XA03D": ("invalid_process_ledger.csv", "prediction_audit.csv", "summary.json", "manifest.json"),
    "XA03E": (
        "absolute_assessment.csv", "parent_child_incremental_assessment.csv",
        "rsp_incremental_assessment.csv", "path_summary.csv", "path_cost_summary.csv",
        "subperiod_and_mature_slice.csv", "calendar_and_rolling_performance.csv",
        "coefficient_and_importance_stability.csv", "coverage_and_concentration_audit.csv",
        "portfolio_accounting_identity.csv", "process_registry_resolved.csv",
        "qualification_role_ledger.csv", "decision.json", "summary.json", "manifest.json",
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    project = Path(args.project_root).resolve()
    runtime = Path(args.runtime_root).resolve()
    audit = audit_xa03(project, runtime)
    destination = project / "results/published/cross_sectional_alpha/XA03"
    figures = project / "docs/figures/cross_sectional_alpha/XA03"
    report = project / "docs/20_experiments/XA03_cross_sectional_aggregation/report.md"
    if destination.exists() or figures.exists() or report.exists():
        raise FileExistsError("XA03 compact publication destination already exists")
    destination.mkdir(parents=True)
    figures.mkdir(parents=True)
    for batch, names in FILES.items():
        source = runtime / "results/experiments/xa03" / batch / "runs" / RUN_IDS[batch]
        target = destination / batch
        target.mkdir()
        for name in names:
            shutil.copy2(source / name, target / name)

    paths = pd.read_csv(destination / "XA03E/path_summary.csv")
    paired = pd.read_csv(destination / "XA03E/parent_child_incremental_assessment.csv")
    rsp = pd.read_csv(destination / "XA03E/rsp_incremental_assessment.csv")
    roles = pd.read_csv(destination / "XA03E/qualification_role_ledger.csv")
    decision = json.loads((destination / "XA03E/decision.json").read_text(encoding="utf-8"))
    _path_plot(paths, figures / "top20_relative_wealth.png")
    _paired_plot(paired, figures / "paired_increment.png")
    _rsp_plot(rsp, figures / "rsp_ablation.png")
    facts = _facts(paths, rsp, roles, decision)
    (destination / "README.md").write_text(_readme(facts), encoding="utf-8", newline="\n")
    report.write_text(_report(facts), encoding="utf-8", newline="\n")
    members = {}
    for path in sorted(destination.rglob("*")):
        if path.is_file() and path.name != "publication_manifest.json":
            relative = path.relative_to(destination).as_posix()
            members[relative] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
    payload = {
        "schema_version": "xa03.publication.v1", "files": members,
        "audit": audit, "large_runtime_ledgers_excluded": True,
    }
    (destination / "publication_manifest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({"status": "published", "destination": str(destination), "audit": audit}, indent=2))


def _primary(paths: pd.DataFrame) -> pd.DataFrame:
    return paths.loc[
        paths["top_k"].eq(20)
        & (
            (paths["frequency"].eq("weekly") & paths["cost_bps"].eq(10))
            | (paths["frequency"].eq("monthly") & paths["cost_bps"].eq(5))
        )
        & ~paths["invalid"].astype(bool)
    ].copy()


def _path_plot(paths: pd.DataFrame, output: Path) -> None:
    frame = _primary(paths)
    chosen = []
    for frequency, group in frame.groupby("frequency"):
        one = group.nlargest(10, "terminal_relative_wealth").copy()
        one["label"] = str(frequency)[:1].upper() + ": " + one["process_id"]
        chosen.append(one)
    plot = pd.concat(chosen).sort_values("terminal_relative_wealth")
    colors = ["#3568a8" if value >= 0 else "#b34d4d" for value in plot["terminal_relative_wealth"]]
    ax = plot.plot.barh(x="label", y="terminal_relative_wealth", figsize=(11, 8), color=colors, legend=False)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_xlabel("terminal wealth minus matched common-EW wealth")
    ax.set_ylabel("")
    ax.set_title("XA03 Top20 primary-cost paths")
    ax.figure.tight_layout(); ax.figure.savefig(output, dpi=180); plt.close(ax.figure)


def _paired_plot(frame: pd.DataFrame, output: Path) -> None:
    plot = frame.dropna(subset=["economic_mean_increment"]).nlargest(15, "economic_mean_increment").copy()
    plot["label"] = plot["frequency"].str[:1].str.upper() + ": " + plot["candidate_process_id"]
    plot = plot.sort_values("economic_mean_increment")
    ax = plot.plot.barh(x="label", y="economic_mean_increment", figsize=(11, 7), color="#e08b36", legend=False)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_xlabel("mean active-return increment vs registered parent")
    ax.set_ylabel(""); ax.set_title("XA03 strongest parent-child point estimates")
    ax.figure.tight_layout(); ax.figure.savefig(output, dpi=180); plt.close(ax.figure)


def _rsp_plot(frame: pd.DataFrame, output: Path) -> None:
    plot = frame.copy()
    plot["label"] = plot["frequency"].str[:1].str.upper() + ": " + plot["candidate_process_id"]
    plot["economic_mean_increment"] = plot["economic_mean_increment"].fillna(0.0)
    plot = plot.sort_values("economic_mean_increment")
    colors = ["#4a9b62" if value > 0 else "#888888" for value in plot["economic_mean_increment"]]
    ax = plot.plot.barh(x="label", y="economic_mean_increment", figsize=(10, 5), color=colors, legend=False)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_xlabel("with-RSP minus no-RSP mean active return")
    ax.set_ylabel(""); ax.set_title("XA03 mandatory RSP ablations")
    ax.figure.tight_layout(); ax.figure.savefig(output, dpi=180); plt.close(ax.figure)


def _facts(paths: pd.DataFrame, rsp: pd.DataFrame, roles: pd.DataFrame, decision: dict) -> dict:
    primary = _primary(paths)
    best = {
        frequency: group.sort_values("terminal_relative_wealth", ascending=False).iloc[0].to_dict()
        for frequency, group in primary.groupby("frequency")
    }
    supported = rsp.loc[(rsp["economic_q"] <= 0.10) & (rsp["economic_mean_increment"] > 0)]
    return {
        "qualified": int(decision["qualified_process_frequency_cells"]),
        "rsp_supported": int(decision["rsp_incremental_supported_cells"]),
        "invalid": int(roles["primary_status"].eq("invalid").sum()),
        "best": best,
        "supported_rsp_rows": supported.to_dict(orient="records"),
    }


def _readme(facts: dict) -> str:
    weekly = facts["best"]["weekly"]; monthly = facts["best"]["monthly"]
    return f"""# XA03 published results

Status: `completed_hard_stop`. XA03 completed the registered direct-factor, single-factor model, transparent aggregation, factor-only, factor-plus-state, and RSP-ablation comparisons. It did not run P00, bagging, stacking, a lockbox, or automatic champion selection.

- Mechanically qualified process-frequency cells: {facts['qualified']}.
- RSP-increment-supported cells: {facts['rsp_supported']}.
- Invalid registered cells retained in the ledger: {facts['invalid']}.
- Best weekly Top20 primary-cost path by terminal relative wealth: `{weekly['process_id']}` ({weekly['terminal_relative_wealth']:.3f}).
- Best monthly Top20 primary-cost path by terminal relative wealth: `{monthly['process_id']}` ({monthly['terminal_relative_wealth']:.3f}).

Large prediction, holdings, period-return, and 3.23-million-row daily-NAV Parquet ledgers remain in the immutable runtime. This directory contains compact tables, manifests, and decision evidence only.
"""


def _report(facts: dict) -> str:
    weekly = facts["best"]["weekly"]; monthly = facts["best"]["monthly"]
    supported = facts["supported_rsp_rows"][0] if facts["supported_rsp_rows"] else None
    rsp_text = (
        f"The sole supported RSP ablation was `{supported['candidate_process_id']}` at "
        f"{supported['frequency']} frequency: mean economic increment {supported['economic_mean_increment']:.6f}, "
        f"economic q={supported['economic_q']:.4f}."
        if supported else "No RSP ablation passed its registered economic evidence gate."
    )
    return f"""# XA03 rolling cross-sectional aggregation report

XA03A through XA03E completed and the program hard-stopped before P00, bagging, stacking, lockbox use, or automatic model selection.

## Result boundary

- Registered process-frequency cells: 114; TopK paths: 456; cost paths: 1,824.
- Mechanically qualified cells: {facts['qualified']}.
- Invalid cells retained rather than replaced: {facts['invalid']}.
- RSP-increment-supported cells: {facts['rsp_supported']}.
- Evidence remains exploratory full-history prequential evidence (`formal_eligible=false`).

## Main findings

The raw intermediate-horizon momentum factor remained the strongest economic path. Weekly Top20 at 10 bps was `{weekly['process_id']}` with CAGR {weekly['cagr']:.2%}, daily MDD {weekly['max_drawdown']:.2%}, active IR {weekly['active_ir']:.3f}, and terminal wealth advantage {weekly['terminal_relative_wealth']:.3f} versus matched common-EW. Monthly Top20 at 5 bps was `{monthly['process_id']}` with CAGR {monthly['cagr']:.2%}, daily MDD {monthly['max_drawdown']:.2%}, active IR {monthly['active_ir']:.3f}, and terminal wealth advantage {monthly['terminal_relative_wealth']:.3f}.

No fitted single-factor or aggregate process passed the complete absolute and parent-increment qualification stack after registered multiplicity and stability gates. Restricted aggregate LightGBM cells were invalid because the frozen independent-date/calendar-year leaf-support constraint could not be met; they were not silently replaced by Ridge or a looser tree.

{rsp_text} This supports an RSP-dependent interaction in that one model comparison, but the model itself did not qualify absolutely or incrementally against its registered factor-only parent. It is mechanism evidence, not a deployable winner.

The event-driven repair replayed the unchanged frozen holdings through PIT membership, corporate actions, missing-price rules, and exact proportional transaction costs. The final runtime contains 3,226,608 daily NAV rows. Earlier period-proxy and missing-control-metadata attempts remain quarantined for provenance and are not published as results.

## Figures

![Top20 relative wealth](../../figures/cross_sectional_alpha/XA03/top20_relative_wealth.png)

![Parent-child increments](../../figures/cross_sectional_alpha/XA03/paired_increment.png)

![RSP ablations](../../figures/cross_sectional_alpha/XA03/rsp_ablation.png)

## Decision

Do not advance an automatic XA03 champion. Preserve raw `XS003_MOM_12_7` as the primary single-factor benchmark, keep the monthly ALL14 Ridge plus state/RSP result as a narrowly scoped interaction hypothesis, and review model-capacity design before authorizing any XA04 or P00 transfer.
"""


if __name__ == "__main__":
    main()
