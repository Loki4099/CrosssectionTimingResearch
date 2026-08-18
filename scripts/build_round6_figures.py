"""Build deterministic Round 6 summary figures from immutable bundles."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


RUNS = {
    "A": "R6A_ATTACK4_TARGET/runs/r6a-attack4-target-identity-20260818-v1",
    "B": "R6B_ATTACK4_SINGLE_FACTOR/runs/r6b-attack4-single-factor-20260818-v1",
    "C": "R6C_ATTACK4_ROLE_PROXY/runs/r6c-attack4-role-proxy-20260818-v1",
    "D": "R6D_ATTACK4_ROBUSTNESS/runs/r6d-attack4-robustness-20260818-v1",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--output-root", default="docs/figures/round6")
    args = parser.parse_args()
    base = Path(args.runtime_root) / "results/experiments/round6"
    output = Path(args.output_root); output.mkdir(parents=True, exist_ok=True)
    _ranking(base / RUNS["D"], output / "r6-single-factor-rankic.png")
    _economic(base / RUNS["D"], output / "r6-economic-active-wealth.png")
    _qualification(base / RUNS["D"], output / "r6-qualification-gates.png")


def _labels(data: pd.DataFrame) -> pd.Series:
    return data.attack_arm_id.str.replace("A4__", "", regex=False).str.replace("_LVL", "", regex=False)


def _ranking(root: Path, path: Path) -> None:
    data = pd.read_csv(root / "final_assessment.csv").sort_values("spearman_a4")
    colors = ["#d95f02" if x == "A4__RSP_SPY63_LVL" else "#4c93c3" for x in data.attack_arm_id]
    fig, ax = plt.subplots(figsize=(12, 8))
    ax.barh(_labels(data), data.spearman_a4, color=colors)
    lower = (data.spearman_a4 - data.block4_95_lower_spearman).clip(lower=0).to_numpy()
    ax.errorbar(data.spearman_a4, range(len(data)), xerr=[lower, lower * 0], fmt="none", ecolor="#333333", alpha=.65, capsize=2)
    ax.axvline(0, color="black", lw=.8); ax.set_xlabel("Spearman(attack score, future 4-week SPY excess return)\nleft whisker: moving-block one-sided 95% lower bound")
    ax.set_title("Round 6 Attack4 single-factor ranking | 883 common development weeks")
    ax.grid(axis="x", alpha=.2); fig.tight_layout(); fig.savefig(path, dpi=180, bbox_inches="tight"); plt.close(fig)


def _economic(root: Path, path: Path) -> None:
    data = pd.read_csv(root / "final_assessment.csv").sort_values("active_terminal_wealth_10")
    colors = ["#d95f02" if x in ("A4__SKEW63_LVL", "A4__RSP_SPY63_LVL") else "#4c93c3" for x in data.attack_arm_id]
    fig, ax = plt.subplots(figsize=(12, 8))
    ax.barh(_labels(data), data.active_terminal_wealth_10 * 100, color=colors)
    ax.axvline(0, color="black", lw=.8); ax.set_xlabel("10bp dynamic wealth vs matched-exposure static (%)")
    ax.set_title("Round 6 fixed 100/50 SPY-cash attack proxy | common 2009-2021 path")
    ax.grid(axis="x", alpha=.2); fig.tight_layout(); fig.savefig(path, dpi=180, bbox_inches="tight"); plt.close(fig)


def _qualification(root: Path, path: Path) -> None:
    data = pd.read_csv(root / "final_assessment.csv")
    candidates = data[data.attack_arm_id.isin(["A4__RSP_SPY63_LVL", "A4__SKEW63_LVL", "A4__RSP_SPY63_D4"])].copy()
    labels = _labels(candidates)
    values = pd.DataFrame({"Direct": candidates.robust_direct_attack.astype(int), "Economic": candidates.economic_reference.astype(int), "Conditional": candidates.conditional_role_pass.astype(int)}, index=labels)
    fig, ax = plt.subplots(figsize=(8, 3.8)); ax.imshow(values.to_numpy(), cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(3), values.columns); ax.set_yticks(range(len(values)), values.index)
    for i in range(len(values)):
        for j in range(3): ax.text(j, i, "PASS" if values.iloc[i, j] else "FAIL", ha="center", va="center", color="white" if not values.iloc[i, j] else "black", weight="bold")
    ax.set_title("Pre-registered Round 6 qualification routes (no arm passed)")
    fig.tight_layout(); fig.savefig(path, dpi=180, bbox_inches="tight"); plt.close(fig)


if __name__ == "__main__":
    main()
