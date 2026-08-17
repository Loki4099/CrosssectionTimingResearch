"""Build deterministic Round 5 summary figures from immutable bundles."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--output-root", default="docs/figures/round5")
    args = parser.parse_args()
    runtime = Path(args.runtime_root)
    output = Path(args.output_root)
    output.mkdir(parents=True, exist_ok=True)
    base = runtime / "results/experiments/round5"
    a = base / "R5A_MAE13_TARGET/runs/r5a-mae13-target-20260817-v1"
    b = base / "R5B_MAE13_SINGLE_FACTOR/runs/r5b-mae13-single-factor-20260817-v1"
    c = base / "R5C_SPY_CASH_PROXY/runs/r5c-spy-cash-proxy-20260817-v1"
    _target_figure(a, output / "r5a-mae13-target.png")
    _ranking_figure(b, output / "r5b-single-factor-ranking.png")
    _rsp_figure(a, b, c, output / "r5c-rsp-spy63.png")


def _target_figure(root: Path, path: Path) -> None:
    data = pd.read_parquet(root / "targets_weekly.parquet")
    data = data[data.target_available].copy()
    data["signal_session"] = pd.to_datetime(data.signal_session)
    data = data[pd.to_datetime(data.execution_session).dt.year.between(2005, 2021)]
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [2, 1]})
    axes[0].plot(data.signal_session, data.raw_mae13 * 100, color="#2369a1", lw=1.2, label="Raw MAE13")
    axes[0].fill_between(data.signal_session, 5, data.raw_mae13 * 100, where=data.raw_mae13.ge(.05), color="#d95f02", alpha=.28, label="Loss beyond 5%")
    axes[0].axhline(5, color="#d95f02", ls="--", lw=1)
    axes[0].set_ylabel("Entry-anchored adverse excursion (%)")
    axes[0].legend(loc="upper right")
    axes[0].grid(alpha=.2)
    axes[1].hist(data.raw_mae13 * 100, bins=45, color="#4c93c3", alpha=.85)
    axes[1].axvline(5, color="#d95f02", ls="--", lw=1.2, label="5% dead-zone")
    axes[1].set_xlabel("Raw MAE13 (%)")
    axes[1].set_ylabel("Weekly labels")
    axes[1].legend()
    axes[1].grid(alpha=.15)
    fig.suptitle("Round 5 target | Future 13-week SPY-vs-cash adverse excursion")
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _ranking_figure(root: Path, path: Path) -> None:
    data = pd.read_csv(root / "signal_summary.csv").sort_values("spearman_y5")
    labels = data.arm_id.str.replace("R4B__", "", regex=False)
    colors = ["#d95f02" if arm == "R4B__RSP_SPY63" else "#4c93c3" for arm in data.arm_id]
    fig, axes = plt.subplots(1, 2, figsize=(15, 8), sharey=True)
    axes[0].barh(labels, data.spearman_y5, color=colors)
    axes[0].axvline(0, color="black", lw=.8)
    axes[0].set_xlabel("Spearman(score, excess-MAE13-5%)")
    axes[0].grid(axis="x", alpha=.2)
    axes[1].barh(labels, data.y5_loss_capture * 100, color=colors)
    axes[1].axvline(25, color="black", ls="--", lw=.9, label="Random 25% budget")
    axes[1].set_xlabel("Top-score quartile loss capture (%)")
    axes[1].legend(loc="lower right")
    axes[1].grid(axis="x", alpha=.2)
    fig.suptitle("Round 5 single-factor MAE13 ranking | 2005-2021 development")
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _rsp_figure(a: Path, b: Path, c: Path, path: Path) -> None:
    nav = pd.read_parquet(c / "nav_daily.parquet")
    nav = nav[(nav.arm_id == "R4B__RSP_SPY63") & nav.cost_bps.eq(10)].copy()
    nav["date"] = pd.to_datetime(nav.date)
    paths = {name: part.set_index("date").nav for name, part in nav.groupby("path_type")}
    active = paths["dynamic"] / paths["matched_static"] - 1
    targets = pd.read_parquet(a / "targets_weekly.parquet")
    targets = targets[targets.target_available].copy()
    targets["signal_session"] = pd.to_datetime(targets.signal_session)
    quintiles = pd.read_csv(b / "quintiles.csv")
    q = quintiles[quintiles.arm_id.eq("R4B__RSP_SPY63")]
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [2, 1, 1]})
    for name, color, style in (("dynamic", "#d95f02", "-"), ("matched_static", "#555555", "--"), ("always_spy", "#2369a1", ":")):
        axes[0].plot(paths[name].index, paths[name], color=color, ls=style, lw=1.5, label=name)
    axes[0].set_yscale("log")
    axes[0].set_ylabel("Wealth (log scale)")
    axes[0].legend()
    axes[0].grid(alpha=.2)
    axes[1].plot(active.index, active * 100, color="#d95f02", lw=1.3)
    axes[1].axhline(0, color="black", lw=.8)
    axes[1].set_ylabel("Dynamic / matched static - 1 (%)")
    axes[1].grid(alpha=.2)
    axes[2].bar(q.quintile, q.mean_y5 * 100, color="#4c93c3")
    axes[2].set_xlabel("RSP/SPY63 defense-score quintile")
    axes[2].set_ylabel("Mean excess MAE13 beyond 5% (%)")
    axes[2].grid(axis="y", alpha=.2)
    fig.suptitle("RSP/SPY63 participation proxy | Signal severity and 100/50 SPY-cash proxy")
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
