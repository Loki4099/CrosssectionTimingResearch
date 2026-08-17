"""Build compact Round 4 development-summary figures from immutable bundles."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--output", default="docs/figures/round4")
    args = parser.parse_args()
    runtime, output = Path(args.runtime_root), Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    base = runtime / "results/experiments/round4"
    r4b = base / "R4B_T2_SINGLE_FACTOR_REFERENCE/runs/r4b-t2-single-factor-20260817-v1"
    nav = pd.read_parquet(r4b / "nav_daily.parquet")
    nav = nav.loc[nav["cost_bps"].eq(10)]
    pivot = nav.pivot(index="date", columns=["arm_id", "path_type"], values="nav")
    fig, ax = plt.subplots(figsize=(13, 7))
    highlight = {
        "R4B__RSP_SPY63": ("#1b9e77", 3.0),
        "R4B__SMA_GAP": ("#7570b3", 2.2),
        "R4B__RV21": ("#d95f02", 2.2),
    }
    for arm in sorted(nav["arm_id"].unique()):
        active = pivot[(arm, "dynamic")] / pivot[(arm, "matched_static")] - 1
        color, width = highlight.get(arm, ("#bdbdbd", 0.8))
        ax.plot(active.index, active, color=color, linewidth=width, alpha=0.95 if arm in highlight else 0.55, label=arm if arm in highlight else None)
    ax.axhline(0, color="black", linestyle="--", linewidth=1)
    ax.set(title="Round 4 single-factor timing value | 10bp | dynamic / same-exposure static - 1", ylabel="active wealth", xlabel="date")
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(output / "r4b-active-wealth.png", dpi=180)
    plt.close(fig)

    r4c = base / "R4C_TARGET_SANITY/runs/r4c-target-sanity-20260817-v1"
    threshold = pd.read_csv(r4c / "threshold_sensitivity.csv")
    conflict = pd.read_csv(r4c / "horizon_conflicts.csv")
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    axes[0].plot(threshold["threshold_bps"], threshold["positive_rate"], marker="o")
    axes[0].axvline(0, color="black", linestyle="--", linewidth=1)
    axes[0].set(title="T2 positive rate vs return threshold", xlabel="threshold (bp)", ylabel="cash-wins label rate")
    axes[1].bar(conflict["horizon_weeks"] - 0.7, conflict["risk_on_mae_ge_10pct"], width=1.4, label="risk-on but MAE >=10%")
    axes[1].bar(conflict["horizon_weeks"] + 0.7, conflict["defense_mae_lt_2pct"], width=1.4, label="defense but MAE <2%")
    axes[1].set(title="One-week label vs future path conflict", xlabel="horizon (weeks)", ylabel="weeks")
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(output / "r4c-target-sanity.png", dpi=180)
    plt.close(fig)

    source = base / "R4D_SPY_DRAWDOWN_ATLAS/runs/r4d-spy-drawdown-atlas-20260817-v1/figures"
    destination = output / "event-atlas"
    destination.mkdir(exist_ok=True)
    for path in source.glob("*.png"):
        shutil.copyfile(path, destination / path.name)


if __name__ == "__main__":
    main()
