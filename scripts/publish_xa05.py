from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


RUNS = {
    "XA05A": "xa05a-frozen-targets-20260821-v1",
    "XA05B": "xa05b-union-event-replay-20260821-v1",
    "XA05C": "xa05c-drawdown-report-20260821-v1",
}
ALLOW = {
    "XA05A": (
        "summary.json",
        "manifest.json",
        "TARGET_ACCEPTANCE.json",
        "matched_static_allocations.csv",
    ),
    "XA05B": (
        "summary.json",
        "manifest.json",
        "path_metrics.csv",
        "path_comparisons.csv",
        "drawdown_episodes.csv",
        "annual_returns.csv",
    ),
    "XA05C": (
        "summary.json",
        "manifest.json",
        "decision.json",
        "primary_comparison.csv",
        "all_cell_comparisons.csv",
        "all_path_metrics.csv",
    ),
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    project = Path(args.project_root).resolve()
    runtime = Path(args.runtime_root).resolve()
    destination = project / "results/published/cross_sectional_alpha/XA05"
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)

    files: dict[str, dict[str, int | str]] = {}
    for batch, run_id in RUNS.items():
        source = runtime / "results/experiments/xa05" / batch / "runs" / run_id
        target = destination / batch
        target.mkdir()
        for name in ALLOW[batch]:
            src = source / name
            if not src.is_file():
                raise FileNotFoundError(src)
            shutil.copy2(src, target / name)
            rel = f"{batch}/{name}"
            files[rel] = {"sha256": _sha(target / name), "size_bytes": (target / name).stat().st_size}

    figure_source = runtime / "results/experiments/xa05/XA05C/runs" / RUNS["XA05C"] / "figures"
    figure_target = destination / "figures"
    figure_target.mkdir()
    for src in sorted(figure_source.glob("*.png")):
        shutil.copy2(src, figure_target / src.name)
        rel = f"figures/{src.name}"
        files[rel] = {"sha256": _sha(figure_target / src.name), "size_bytes": (figure_target / src.name).stat().st_size}
    if len(list(figure_target.glob("*.png"))) != 10:
        raise ValueError("XA05 publication requires exactly ten registered figures")

    readme = (
        "# XA05 compact publication\n\n"
        "Status: `completed_hard_stop`; formal eligible: `false`; automatic deployment: `false`. "
        "The primary monthly Top20 P00 path improves Sharpe and drawdown diagnostics but trails naked terminal wealth, "
        "so the registered family gate fails. This package excludes daily NAV, event ledgers, holdings, and rolling Parquet files. "
        "See the experiment report in `docs/20_experiments/XA05_mom12_7_p00_final_transfer/report.md`.\n"
    )
    (destination / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    files["README.md"] = {"sha256": _sha(destination / "README.md"), "size_bytes": (destination / "README.md").stat().st_size}

    payload = {
        "schema_version": "xa05.publication.v1",
        "status": "completed_hard_stop",
        "formal_eligible": False,
        "automatic_deployment": False,
        "primary_four_metric_gate_passed": False,
        "family_gate_passed": False,
        "registered_figures": 10,
        "runtime_only_large_artifacts_excluded": True,
        "files": dict(sorted(files.items())),
    }
    (destination / "publication_manifest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
