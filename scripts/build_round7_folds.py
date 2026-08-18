"""Build or verify the frozen Round 7 nested walk-forward folds."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tomllib

import pandas as pd


FEATURE_IDS = ["R4B__RSP_SPY63", "R4B__RET126", "R4B__SMA_GAP", "R4B__RV126", "R4B__VIX_LEVEL"]
OUTER_YEARS = list(range(2014, 2022))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root, runtime = Path(args.project_root).resolve(), Path(args.runtime_root).resolve()
    payload = build_folds(root, runtime)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path = root / "config/experiments/round7/folds.json"
    if args.write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
    else:
        if not path.exists() or path.read_bytes() != encoded:
            raise AssertionError("Round7 folds.json differs from deterministic rebuild")
    print(json.dumps({"mode": "write" if args.write else "check", "path": path.relative_to(root).as_posix(),
                      "sha256": hashlib.sha256(encoded).hexdigest(), "outer_folds": len(payload["outer_folds"]),
                      "outer_test_weeks": sum(x["test_weeks"] for x in payload["outer_folds"])}, sort_keys=True))


def build_folds(root: Path, runtime: Path) -> dict:
    program = tomllib.loads((root / "config/experiments/round7/program.toml").read_text(encoding="utf-8"))
    r4a = runtime / "data/round4/staging/R4A_FREE_FACTOR_DATA" / program["parent"]["r4a_run_id"]
    r5a = runtime / "results/experiments/round5/R5A_MAE13_TARGET/runs" / program["parent"]["r5a_run_id"]
    r6a = runtime / "results/experiments/round6/R6A_ATTACK4_TARGET/runs" / program["parent"]["r6a_run_id"]
    checks = [
        (r4a / "manifest.json", program["parent"]["r4a_manifest_sha256"]),
        (r4a / "feature_inputs_weekly.parquet", program["parent"]["r4a_features_sha256"]),
        (r5a / "manifest.json", program["parent"]["r5a_manifest_sha256"]),
        (r5a / "targets_weekly.parquet", program["parent"]["r5a_targets_sha256"]),
        (r6a / "manifest.json", program["parent"]["r6a_manifest_sha256"]),
        (r6a / "targets_weekly.parquet", program["parent"]["r6a_targets_sha256"]),
    ]
    for path, expected in checks:
        if _sha(path) != expected:
            raise AssertionError(f"Round7 fold parent mismatch: {path}")
    features = pd.read_parquet(r4a / "feature_inputs_weekly.parquet")
    features["signal_session"] = pd.to_datetime(features["signal_session"]).dt.normalize()
    pivot = features.loc[features["arm_id"].isin(FEATURE_IDS)].pivot(
        index=["week_id", "signal_session"], columns="arm_id", values="defense_score"
    ).dropna().reset_index()
    risk = pd.read_parquet(r5a / "targets_weekly.parquet")
    attack = pd.read_parquet(r6a / "targets_weekly.parquet")
    for frame in (risk, attack):
        for column in ("signal_session", "execution_session", "target_available_at"):
            if column in frame:
                frame[column] = pd.to_datetime(frame[column]).dt.normalize()
    risk = risk.loc[risk["target_available"], ["week_id", "signal_session", "execution_session", "target_available_at"]]
    attack = attack.loc[attack["target_available"], ["week_id", "signal_session"]]
    common = pivot.merge(risk, on=["week_id", "signal_session"], validate="one_to_one").merge(
        attack, on=["week_id", "signal_session"], validate="one_to_one"
    ).sort_values("signal_session", kind="mergesort").reset_index(drop=True)
    if len(common) != 948 or common["signal_session"].min() != pd.Timestamp("2003-08-01") or common["signal_session"].max() != pd.Timestamp("2021-09-24"):
        raise AssertionError("Round7 common sample identity drifted")
    outer_rows = []
    for outer_year in OUTER_YEARS:
        test = common.loc[common["execution_session"].dt.year.eq(outer_year)].copy()
        if test.empty:
            raise AssertionError(f"Round7 empty outer year: {outer_year}")
        first_index = int(test.index.min())
        train_end_exclusive = first_index - 13  # last included index is first_index-14: 13-week purge + 1-week embargo
        train = common.iloc[:train_end_exclusive].copy()
        if len(train) < 520:
            raise AssertionError(f"Round7 outer train too short: {outer_year}")
        test_start = pd.Timestamp(test["signal_session"].min())
        if not train["target_available_at"].lt(test_start).all():
            raise AssertionError(f"Round7 outer label maturity leak: {outer_year}")
        inner = _inner_folds(common, train, outer_year)
        if len(inner) < 3:
            raise AssertionError(f"Round7 insufficient inner folds: {outer_year}")
        outer_rows.append({
            "outer_year": outer_year,
            "train_start_signal": str(train["signal_session"].min().date()),
            "train_end_signal": str(train["signal_session"].max().date()),
            "train_weeks": len(train),
            "test_start_signal": str(test["signal_session"].min().date()),
            "test_end_signal": str(test["signal_session"].max().date()),
            "test_weeks": len(test),
            "maturity_truncated": bool(outer_year == 2021),
            "inner_folds": inner,
        })
    return {
        "schema_version": 1,
        "program_id": "dual_head_model_round7_v1",
        "calendar": "R4A complete scheduled weekly decision calendar intersect frozen mature R5A/R6A targets",
        "feature_ids": FEATURE_IDS,
        "common_start_signal": str(common["signal_session"].min().date()),
        "common_end_signal": str(common["signal_session"].max().date()),
        "common_weeks": len(common),
        "purge_scheduled_weeks": 13,
        "embargo_scheduled_weeks": 1,
        "minimum_outer_train_weeks": 520,
        "minimum_inner_train_weeks": 260,
        "inner_validation_weeks": 52,
        "outer_folds": outer_rows,
        "parent_hashes": {path.name + "__" + str(index): expected for index, (path, expected) in enumerate(checks)},
    }


def _inner_folds(common: pd.DataFrame, outer_train: pd.DataFrame, outer_year: int) -> list[dict]:
    result = []
    n = len(outer_train)
    first_candidate = max(0, n - 5 * 52)
    starts = list(range(first_candidate, n, 52))
    for start in starts:
        end = min(start + 52, n)
        if end - start != 52:
            continue
        train_end_exclusive = start - 13
        if train_end_exclusive < 260:
            continue
        train = common.iloc[:train_end_exclusive]
        validation = common.iloc[start:end]
        validation_start = pd.Timestamp(validation["signal_session"].min())
        if not train["target_available_at"].lt(validation_start).all():
            raise AssertionError(f"Round7 inner label maturity leak: {outer_year}/{start}")
        result.append({
            "inner_fold": len(result) + 1,
            "train_start_signal": str(train["signal_session"].min().date()),
            "train_end_signal": str(train["signal_session"].max().date()),
            "train_weeks": len(train),
            "validation_start_signal": str(validation["signal_session"].min().date()),
            "validation_end_signal": str(validation["signal_session"].max().date()),
            "validation_weeks": len(validation),
        })
    return result


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
