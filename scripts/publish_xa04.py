from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


RUNS = {
    "XA04A": "xa04a-core10-panel-20260821-v1",
    "XA04B": "xa04b-unified-models-20260821-v1",
    "XA04C": "xa04c-portfolios-inference-20260821-v1",
    "XA04D": "xa04d-audit-decision-20260821-v1",
}
ALLOW = {
    "XA04A": ("summary.json", "manifest.json", "factor_coverage_gate.csv", "complete_case_coverage.csv", "panel_audit.json"),
    "XA04B": ("summary.json", "manifest.json", "invalid_process_ledger.csv"),
    "XA04C": ("summary.json", "manifest.json", "absolute_assessment.csv", "parent_increment_assessment.csv",
              "rsp_ablation_assessment.csv", "raw_xs003_assessment.csv", "qualification_role_ledger.csv",
              "portfolio_accounting_identity.csv"),
    "XA04D": ("summary.json", "manifest.json", "decision.json", "qualified_tree_candidate_ledger.csv"),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap=argparse.ArgumentParser();ap.add_argument("--project-root",default=".");ap.add_argument("--runtime-root",required=True);args=ap.parse_args()
    project=Path(args.project_root).resolve();runtime=Path(args.runtime_root).resolve()
    dest=project/"results/published/cross_sectional_alpha/XA04"
    if dest.exists(): raise FileExistsError(dest)
    dest.mkdir(parents=True)
    files={}
    for batch,run in RUNS.items():
        source=runtime/"results/experiments/xa04"/batch/"runs"/run; target=dest/batch;target.mkdir()
        for name in ALLOW[batch]:
            src=source/name
            if not src.is_file():raise FileNotFoundError(src)
            shutil.copy2(src,target/name);rel=f"{batch}/{name}";files[rel]={"sha256":sha(target/name),"size_bytes":(target/name).stat().st_size}
    readme=("# XA04 compact publication\n\nStatus: `completed_hard_stop`; formal eligible: `false`; qualified trees: `0`. "
            "This package excludes predictions, holdings, daily NAV, and other runtime-only parquet files. "
            "See the experiment report in `docs/20_experiments/XA04_unified_core10_lightgbm/report.md`.\n")
    (dest/"README.md").write_text(readme,encoding="utf-8",newline="\n");files["README.md"]={"sha256":sha(dest/"README.md"),"size_bytes":(dest/"README.md").stat().st_size}
    payload={"schema_version":"xa04.publication.v1","status":"completed_hard_stop","formal_eligible":False,
             "qualified_tree_cells":0,"runtime_only_large_artifacts_excluded":True,"files":dict(sorted(files.items()))}
    (dest/"publication_manifest.json").write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n")

if __name__=="__main__":main()
