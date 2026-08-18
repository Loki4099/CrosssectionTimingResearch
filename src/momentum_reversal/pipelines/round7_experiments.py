"""Preregistered Round 7 dual-head model experiments."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tomllib
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import Ridge
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import SplineTransformer

from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.round2_market import sha256_file


PROGRAM_ID = "dual_head_model_round7_v1"
SEED = 20260818
BOOTSTRAP_REPETITIONS = 2000
FEATURE_IDS = ("R4B__RSP_SPY63", "R4B__RET126", "R4B__SMA_GAP", "R4B__RV126", "R4B__VIX_LEVEL")


@dataclass(frozen=True, slots=True)
class Round7BatchResult:
    output_dir: Path
    manifest_path: Path
    status: str


class TrainOnlyTransform:
    def fit(self, x: np.ndarray) -> "TrainOnlyTransform":
        x = np.asarray(x, float)
        self.low = np.nanquantile(x, .01, axis=0)
        self.high = np.nanquantile(x, .99, axis=0)
        clipped = np.clip(x, self.low, self.high)
        self.median = np.nanmedian(clipped, axis=0)
        filled = np.where(np.isfinite(clipped), clipped, self.median)
        self.mean = filled.mean(axis=0)
        self.std = filled.std(axis=0)
        self.std[self.std <= 1e-12] = 1.0
        return self

    def transform(self, x: np.ndarray) -> np.ndarray:
        clipped = np.clip(np.asarray(x, float), self.low, self.high)
        filled = np.where(np.isfinite(clipped), clipped, self.median)
        return (filled - self.mean) / self.std


@dataclass
class FittedRiskModel:
    transform: TrainOnlyTransform
    model: Any
    spline: SplineTransformer | None

    def predict(self, x: np.ndarray) -> np.ndarray:
        z = self.transform.transform(x)
        if self.spline is not None:
            z = self.spline.transform(z)
        if isinstance(self.model, lgb.LGBMRegressor):
            return np.asarray(self.model.booster_.predict(z), float)
        return np.asarray(self.model.predict(z), float)


def run_r7a(*, project_root: str | Path, runtime_root: str | Path, run_id: str) -> Round7BatchResult:
    root, runtime, lock, program, parents = _load_inputs(project_root, runtime_root)
    _require_run_id(root, 0, run_id)
    output = _batch_root(runtime, "R7A_DUAL_TARGET_FOLDS", run_id)
    output.mkdir(parents=True, exist_ok=False)
    common = _build_common(parents)
    if len(common) != 948 or common.signal_session.min() != pd.Timestamp("2003-08-01") or common.signal_session.max() != pd.Timestamp("2021-09-24"):
        raise DataQualityError("Round7 common calendar drifted")
    folds = json.loads((root / program["development"]["folds_path"]).read_text(encoding="utf-8"))
    _validate_fold_rows(common, folds)
    common.to_parquet(output / "common_weekly.parquet", index=False, compression="zstd")
    pd.DataFrame(_flatten_folds(folds)).to_csv(output / "fold_ledger.csv", index=False, lineterminator="\n")
    summary = pd.DataFrame([{
        "common_weeks": len(common), "first_signal": common.signal_session.min(), "last_signal": common.signal_session.max(),
        "outer_folds": len(folds["outer_folds"]), "outer_oos_weeks": sum(x["test_weeks"] for x in folds["outer_folds"]),
        "mean_y5": common.y5.mean(), "mean_a4": common.a4.mean(), "rsp_y5_spearman": _rho(common[FEATURE_IDS[0]], common.y5),
        "rsp_a4_spearman": _rho(-common[FEATURE_IDS[0]], common.a4),
        "lightgbm_version": lgb.__version__, "lightgbm_repeat_deterministic": _lightgbm_repeat_test(),
    }])
    summary.to_csv(output / "acceptance_summary.csv", index=False, lineterminator="\n")
    manifest = _write_manifest(output, root, lock, "R7A_DUAL_TARGET_FOLDS", run_id,
        counts={"common_weeks": len(common), "outer_folds": len(folds["outer_folds"]), "outer_oos_weeks": 404},
        parent_manifests={key: sha256_file(path / "manifest.json") for key, path in parents.items()}, models_run=False)
    return Round7BatchResult(output, output / "manifest.json", manifest["status"])


def run_r7b(*, project_root: str | Path, runtime_root: str | Path, run_id: str) -> Round7BatchResult:
    root, runtime, lock, _, _ = _load_inputs(project_root, runtime_root)
    _require_run_id(root, 1, run_id)
    r7a = _batch_root(runtime, "R7A_DUAL_TARGET_FOLDS", _run_ids(root)[0])
    _validate_bundle(r7a, "R7A_DUAL_TARGET_FOLDS")
    output = _batch_root(runtime, "R7B_RISK_MODEL_TOURNAMENT", run_id)
    output.mkdir(parents=True, exist_ok=False)
    data = pd.read_parquet(r7a / "common_weekly.parquet")
    _normalise_dates(data)
    folds = json.loads((root / "config/experiments/round7/folds.json").read_text(encoding="utf-8"))
    bundles = pd.read_csv(root / "config/experiments/round7/feature_bundles.csv").set_index("bundle_id")
    recipes = pd.read_csv(root / "config/experiments/round7/model_recipes.csv").set_index("recipe_id")
    processes = pd.read_csv(root / "config/experiments/round7/process_registry.csv")
    prediction_rows: list[pd.DataFrame] = []
    selection_rows: list[dict[str, Any]] = []
    trial_rows: list[dict[str, Any]] = []
    raw_rows: list[pd.DataFrame] = []
    for outer in folds["outer_folds"]:
        train = _date_slice(data, outer["train_start_signal"], outer["train_end_signal"])
        test = _date_slice(data, outer["test_start_signal"], outer["test_end_signal"])
        raw_threshold = float(train[FEATURE_IDS[0]].quantile(.75))
        raw = test[["week_id", "signal_session", "execution_session", "risk_terminal_execution", "y5", "raw_mae13"]].copy()
        raw["outer_year"] = outer["outer_year"]
        raw["predicted_risk"] = test[FEATURE_IDS[0]].to_numpy(float)
        raw["threshold_q75"] = raw_threshold
        raw["alert_high"] = raw.predicted_risk >= raw_threshold
        raw_rows.append(raw)
        for proc in processes.itertuples(index=False):
            features = bundles.loc[proc.bundle_id, "feature_arm_ids"].split("|")
            recipe_ids = proc.selector_recipe_ids.split("|")
            losses: dict[str, list[np.ndarray]] = {rid: [] for rid in recipe_ids}
            for inner in outer["inner_folds"]:
                inner_train = _date_slice(data, inner["train_start_signal"], inner["train_end_signal"])
                valid = _date_slice(data, inner["validation_start_signal"], inner["validation_end_signal"])
                for rid in recipe_ids:
                    model = _fit_risk_model(inner_train[features].to_numpy(), inner_train.y5.to_numpy(), recipes.loc[rid])
                    pred = model.predict(valid[features].to_numpy())
                    losses[rid].append(np.abs(valid.y5.to_numpy() - pred))
            selected, stats = _one_se_select(recipe_ids, losses, recipes)
            for rid, values in stats.items():
                trial_rows.append({"process_id": proc.process_id, "outer_year": outer["outer_year"], "recipe_id": rid, **values})
            fit = _fit_risk_model(train[features].to_numpy(), train.y5.to_numpy(), recipes.loc[selected])
            pred_test = fit.predict(test[features].to_numpy())
            pred_train = fit.predict(train[features].to_numpy())
            threshold = float(np.quantile(pred_train, .75))
            part = test[["week_id", "signal_session", "execution_session", "risk_terminal_execution", "y5", "raw_mae13"]].copy()
            part.insert(0, "process_id", proc.process_id)
            part["bundle_id"] = proc.bundle_id
            part["family"] = proc.family
            part["selected_recipe_id"] = selected
            part["outer_year"] = outer["outer_year"]
            part["predicted_risk"] = pred_test
            part["threshold_q75"] = threshold
            part["alert_high"] = pred_test >= threshold
            prediction_rows.append(part)
            selection_rows.append({"process_id": proc.process_id, "outer_year": outer["outer_year"], "bundle_id": proc.bundle_id,
                                   "family": proc.family, "selected_recipe_id": selected, "inner_oof_weeks": sum(len(x) for x in losses[selected]),
                                   "outer_train_weeks": len(train), "outer_test_weeks": len(test), "threshold_q75": threshold})
    predictions = pd.concat(prediction_rows, ignore_index=True)
    raw = pd.concat(raw_rows, ignore_index=True)
    summary, yearly = _risk_summaries(predictions, raw, processes, bundles)
    predictions.to_parquet(output / "outer_predictions.parquet", index=False, compression="zstd")
    raw.to_parquet(output / "raw_rsp_sentinel.parquet", index=False, compression="zstd")
    pd.DataFrame(selection_rows).to_csv(output / "inner_selection.csv", index=False, lineterminator="\n")
    pd.DataFrame(trial_rows).to_csv(output / "inner_trial_summary.csv", index=False, lineterminator="\n")
    summary.to_csv(output / "risk_summary.csv", index=False, lineterminator="\n")
    yearly.to_csv(output / "risk_yearly.csv", index=False, lineterminator="\n")
    manifest = _write_manifest(output, root, lock, "R7B_RISK_MODEL_TOURNAMENT", run_id,
        counts={"risk_processes": 27, "outer_predictions": len(predictions), "raw_rsp_predictions": len(raw),
                "inner_selections": len(selection_rows), "inner_trials": len(trial_rows)},
        parent_manifests={"r7a": sha256_file(r7a / "manifest.json")}, models_run=True)
    return Round7BatchResult(output, output / "manifest.json", manifest["status"])


def run_r7c(*, project_root: str | Path, runtime_root: str | Path, run_id: str) -> Round7BatchResult:
    root, runtime, lock, _, _ = _load_inputs(project_root, runtime_root)
    _require_run_id(root, 2, run_id)
    r7a = _batch_root(runtime, "R7A_DUAL_TARGET_FOLDS", _run_ids(root)[0])
    _validate_bundle(r7a, "R7A_DUAL_TARGET_FOLDS")
    output = _batch_root(runtime, "R7C_RSP_ATTACK_COMPARATOR", run_id)
    output.mkdir(parents=True, exist_ok=False)
    data = pd.read_parquet(r7a / "common_weekly.parquet")
    _normalise_dates(data)
    folds = json.loads((root / "config/experiments/round7/folds.json").read_text(encoding="utf-8"))
    rows: list[pd.DataFrame] = []
    for outer in folds["outer_folds"]:
        train = _date_slice(data, outer["train_start_signal"], outer["train_end_signal"])
        test = _date_slice(data, outer["test_start_signal"], outer["test_end_signal"])
        xtrain, xtest = -train[FEATURE_IDS[0]].to_numpy(float), -test[FEATURE_IDS[0]].to_numpy(float)
        iso = IsotonicRegression(increasing=True, out_of_bounds="clip").fit(xtrain, train.a4.to_numpy())
        pred_train, pred_test = iso.predict(xtrain), iso.predict(xtest)
        base = float(train.a4.mean())
        for process_id, predicted, train_scores in (("AX01_RAW_RSP_RECOVERY", xtest, xtrain),
                                                      ("AX02_RSP_A4_MONOTONE", pred_test, pred_train)):
            threshold = float(np.quantile(train_scores, .75))
            part = test[["week_id", "signal_session", "execution_session", "attack_terminal_execution", "a4", "b4", "w4", "severe_w4"]].copy()
            part.insert(0, "attack_process_id", process_id)
            part["outer_year"] = outer["outer_year"]
            part["predicted_attack"] = predicted
            part["threshold_q75"] = threshold
            part["attack_high"] = predicted >= threshold
            part["outer_train_mean_a4"] = base
            rows.append(part)
    predictions = pd.concat(rows, ignore_index=True)
    summary, yearly = _attack_summaries(predictions)
    predictions.to_parquet(output / "outer_predictions.parquet", index=False, compression="zstd")
    summary.to_csv(output / "attack_summary.csv", index=False, lineterminator="\n")
    yearly.to_csv(output / "attack_yearly.csv", index=False, lineterminator="\n")
    manifest = _write_manifest(output, root, lock, "R7C_RSP_ATTACK_COMPARATOR", run_id,
        counts={"attack_processes_run": 2, "outer_predictions": len(predictions), "outer_oos_weeks_per_process": 404},
        parent_manifests={"r7a": sha256_file(r7a / "manifest.json")}, models_run=True)
    return Round7BatchResult(output, output / "manifest.json", manifest["status"])


def run_r7d(*, project_root: str | Path, runtime_root: str | Path, run_id: str) -> Round7BatchResult:
    root, runtime, lock, _, _ = _load_inputs(project_root, runtime_root)
    _require_run_id(root, 3, run_id)
    rids = _run_ids(root)
    roots = {
        "r7a": _batch_root(runtime, "R7A_DUAL_TARGET_FOLDS", rids[0]),
        "r7b": _batch_root(runtime, "R7B_RISK_MODEL_TOURNAMENT", rids[1]),
        "r7c": _batch_root(runtime, "R7C_RSP_ATTACK_COMPARATOR", rids[2]),
    }
    for key, batch in (("r7a", "R7A_DUAL_TARGET_FOLDS"), ("r7b", "R7B_RISK_MODEL_TOURNAMENT"), ("r7c", "R7C_RSP_ATTACK_COMPARATOR")):
        _validate_bundle(roots[key], batch)
    output = _batch_root(runtime, "R7D_HEAD_QUALIFICATION", run_id)
    output.mkdir(parents=True, exist_ok=False)
    events = pd.read_csv(root / "config/experiments/round7/event_registry.csv", parse_dates=["peak_date", "trough_date", "recovery_date"])
    risk_pred = pd.read_parquet(roots["r7b"] / "outer_predictions.parquet")
    raw = pd.read_parquet(roots["r7b"] / "raw_rsp_sentinel.parquet")
    risk_summary = pd.read_csv(roots["r7b"] / "risk_summary.csv")
    attack_pred = pd.read_parquet(roots["r7c"] / "outer_predictions.parquet")
    attack_summary = pd.read_csv(roots["r7c"] / "attack_summary.csv")
    for frame in (risk_pred, raw, attack_pred): _normalise_dates(frame)
    risk_leave = _leave_one_event(risk_pred, events, "process_id", "predicted_risk", "y5", "risk_terminal_execution")
    attack_leave = _leave_one_event(attack_pred, events, "attack_process_id", "predicted_attack", "a4", "attack_terminal_execution")
    risk_min = risk_leave.groupby("process_id", as_index=False).spearman_without_event.min().rename(columns={"spearman_without_event": "minimum_leaveout_spearman"})
    attack_min = attack_leave.groupby("attack_process_id", as_index=False).spearman_without_event.min().rename(columns={"spearman_without_event": "minimum_leaveout_spearman"})
    risk_final = risk_summary.merge(risk_min, on="process_id", validate="one_to_one")
    risk_final["risk_qualified"] = (
        risk_final.outer_complete & risk_final.native_common_direction_match & risk_final.spearman_y5.gt(0)
        & risk_final.block13_95_lower.gt(0) & risk_final.bh_q_value.le(.10) & risk_final.y5_capture.ge(.35)
        & risk_final.mae10_lift.ge(1.25) & risk_final.positive_full_year_fraction.ge(.60)
        & risk_final.minimum_leaveout_spearman.gt(0) & risk_final.not_worse_than_rsp_one_se
        & (~risk_final.is_multifactor | risk_final.incremental_gate)
    )
    clusters = _equivalence_clusters(risk_pred, risk_final)
    risk_final = risk_final.merge(clusters[["process_id", "equivalence_cluster", "canonical_representative"]], on="process_id", validate="one_to_one")
    attack_final = attack_summary.merge(attack_min, on="attack_process_id", validate="one_to_one")
    ax01 = attack_final.loc[attack_final.attack_process_id.eq("AX01_RAW_RSP_RECOVERY")].iloc[0]
    attack_final["attack_qualified"] = False
    ax02mask = attack_final.attack_process_id.eq("AX02_RSP_A4_MONOTONE")
    attack_final.loc[ax02mask, "attack_qualified"] = (
        attack_final.loc[ax02mask, "spearman_a4"].gt(0) & attack_final.loc[ax02mask, "block4_95_lower"].gt(0)
        & attack_final.loc[ax02mask, "top_mean_a4"].gt(attack_final.loc[ax02mask, "rest_mean_a4"])
        & attack_final.loc[ax02mask, "auc_b4"].gt(.55) & attack_final.loc[ax02mask, "w4_median_not_worse"]
        & attack_final.loc[ax02mask, "w4_severe_not_worse"] & attack_final.loc[ax02mask, "minimum_leaveout_spearman"].gt(0)
        & attack_final.loc[ax02mask, "mae_skill_vs_train_mean"].gt(0)
        & attack_final.loc[ax02mask, "spearman_a4"].ge(float(ax01.spearman_a4) - attack_final.loc[ax02mask, "block4_se"])
    )
    role_rows = [{"head_type": "risk", "head_id": row.process_id, "qualified": bool(row.risk_qualified),
                  "canonical": bool(row.canonical_representative), "role": "risk_model"} for row in risk_final.itertuples(index=False)]
    role_rows += [
        {"head_type": "risk_control", "head_id": "RAW_RSP_SENTINEL", "qualified": True, "canonical": True, "role": "mandatory_round8_control"},
        {"head_type": "attack_control", "head_id": "AX00_Y5_CLEAR_ONLY", "qualified": True, "canonical": True, "role": "mandatory_round8_control"},
        {"head_type": "attack_control", "head_id": "AX01_RAW_RSP_RECOVERY", "qualified": True, "canonical": True, "role": "mandatory_round8_control"},
        {"head_type": "attack", "head_id": "AX02_RSP_A4_MONOTONE", "qualified": bool(attack_final.loc[ax02mask, "attack_qualified"].iloc[0]), "canonical": True, "role": "formal_a4_head"},
    ]
    risk_leave.to_csv(output / "risk_leave_one_event_out.csv", index=False, lineterminator="\n")
    attack_leave.to_csv(output / "attack_leave_one_event_out.csv", index=False, lineterminator="\n")
    risk_final.to_csv(output / "risk_final_assessment.csv", index=False, lineterminator="\n")
    attack_final.to_csv(output / "attack_final_assessment.csv", index=False, lineterminator="\n")
    clusters.to_csv(output / "risk_equivalence_clusters.csv", index=False, lineterminator="\n")
    pd.DataFrame(role_rows).to_csv(output / "head_role_ledger.csv", index=False, lineterminator="\n")
    assessment = "completed_pending_user_round8_freeze_decision"
    decision = {
        "program_id": PROGRAM_ID, "status": assessment, "risk_qualified_processes": int(risk_final.risk_qualified.sum()),
        "risk_qualified_canonical_processes": int((risk_final.risk_qualified & risk_final.canonical_representative).sum()),
        "attack_ax02_qualified": bool(attack_final.loc[ax02mask, "attack_qualified"].iloc[0]),
        "round8_authorized": False, "state_machine_run": False, "strategy_nav_run": False, "lockbox_read": False,
    }
    (output / "decision.json").write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = _write_manifest(output, root, lock, "R7D_HEAD_QUALIFICATION", run_id,
        counts={"risk_processes": len(risk_final), "risk_qualified": int(risk_final.risk_qualified.sum()),
                "risk_canonical_qualified": int((risk_final.risk_qualified & risk_final.canonical_representative).sum()),
                "attack_processes_assessed": len(attack_final), "attack_qualified": int(attack_final.attack_qualified.sum()),
                "major_events": len(events)},
        parent_manifests={key: sha256_file(path / "manifest.json") for key, path in roots.items()},
        models_run=False, assessment=assessment)
    return Round7BatchResult(output, output / "manifest.json", manifest["status"])


def _fit_risk_model(x: np.ndarray, y: np.ndarray, spec: pd.Series) -> FittedRiskModel:
    transform = TrainOnlyTransform().fit(x)
    z = transform.transform(x)
    spline = None
    family = str(spec.family)
    if family == "positive_ridge":
        model = Ridge(alpha=float(spec.alpha), positive=True, solver="lbfgs", max_iter=10000)
    elif family == "additive_spline_ridge":
        spline = SplineTransformer(n_knots=int(spec.n_knots), degree=int(spec.spline_degree), include_bias=False)
        z = spline.fit_transform(z)
        model = Ridge(alpha=float(spec.alpha))
    elif family == "monotone_lightgbm":
        model = lgb.LGBMRegressor(objective="regression", max_depth=int(spec.max_depth), num_leaves=int(spec.num_leaves),
            n_estimators=int(spec.n_estimators), learning_rate=float(spec.learning_rate), min_child_samples=int(spec.min_child_samples),
            subsample=1.0, colsample_bytree=1.0, reg_lambda=1.0, monotone_constraints=[1] * z.shape[1],
            random_state=SEED, n_jobs=1, deterministic=True, force_col_wise=True, verbosity=-1)
    else:
        raise DataQualityError(f"Unknown Round7 family: {family}")
    model.fit(z, y)
    return FittedRiskModel(transform, model, spline)


def _one_se_select(recipe_ids: list[str], losses: dict[str, list[np.ndarray]], recipes: pd.DataFrame) -> tuple[str, dict[str, dict[str, Any]]]:
    joined = {rid: np.concatenate(losses[rid]) for rid in recipe_ids}
    means = {rid: float(values.mean()) for rid, values in joined.items()}
    best = min(recipe_ids, key=lambda rid: (means[rid], int(recipes.loc[rid, "capacity_rank"]), rid))
    stats: dict[str, dict[str, Any]] = {}
    eligible: list[str] = []
    for rid in recipe_ids:
        diff = joined[rid] - joined[best]
        se = _moving_block_mean_se(diff, 13)
        ok = means[rid] <= means[best] + se + 1e-15
        if ok: eligible.append(rid)
        stats[rid] = {"mean_absolute_error": means[rid], "best_recipe_id": best, "paired_block_se": se, "within_one_se": ok}
    selected = min(eligible, key=lambda rid: (int(recipes.loc[rid, "capacity_rank"]), rid))
    for rid in stats: stats[rid]["selected"] = rid == selected
    return selected, stats


def _risk_summaries(predictions: pd.DataFrame, raw: pd.DataFrame, processes: pd.DataFrame, bundles: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw_metrics = _risk_metrics(raw)
    raw_rho = raw_metrics["spearman_y5"]
    rows, years = [], []
    for proc in processes.itertuples(index=False):
        part = predictions[predictions.process_id.eq(proc.process_id)].sort_values("signal_session")
        metrics = _risk_metrics(part)
        lower, p, se = _block_bootstrap_rho(part.predicted_risk, part.y5, 13)
        diff_se = _paired_rho_diff_se(part.predicted_risk.to_numpy(), raw.predicted_risk.to_numpy(), part.y5.to_numpy(), 13)
        yearly_values = []
        for year, annual in part.groupby("outer_year"):
            rho = _rho(annual.predicted_risk, annual.y5)
            years.append({"process_id": proc.process_id, "outer_year": int(year), "weeks": len(annual), "spearman_y5": rho})
            if int(year) <= 2020 and np.isfinite(rho): yearly_values.append(rho > 0)
        bundle_count = int(bundles.loc[proc.bundle_id, "feature_count"])
        rows.append({"process_id": proc.process_id, "bundle_id": proc.bundle_id, "family": proc.family, "outer_weeks": len(part),
                     "outer_complete": len(part) == 404, "native_common_direction_match": metrics["spearman_y5"] > 0,
                     **metrics, "block13_95_lower": lower, "block13_one_sided_p": p, "block13_se": se,
                     "positive_full_year_fraction": float(np.mean(yearly_values)), "raw_rsp_spearman_y5": raw_rho,
                     "rho_difference_vs_rsp": metrics["spearman_y5"] - raw_rho, "rho_difference_block_se": diff_se,
                     "not_worse_than_rsp_one_se": metrics["spearman_y5"] >= raw_rho - diff_se,
                     "is_multifactor": bundle_count > 1,
                     "incremental_gate": ((metrics["spearman_y5"] - raw_rho >= .02) or (metrics["y5_capture"] - raw_metrics["y5_capture"] >= .05)
                                          or (metrics["mae10_lift"] - raw_metrics["mae10_lift"] >= .10))})
    summary = pd.DataFrame(rows)
    summary["bh_q_value"] = _bh_adjust(summary.block13_one_sided_p.to_numpy())
    return summary, pd.DataFrame(years)


def _risk_metrics(frame: pd.DataFrame) -> dict[str, float]:
    high = frame.alert_high.astype(bool)
    total_y5 = frame.y5.sum()
    mae10 = frame.raw_mae13.ge(.10)
    base10 = mae10.mean()
    return {"spearman_y5": _rho(frame.predicted_risk, frame.y5),
            "y5_capture": float(frame.loc[high, "y5"].sum() / total_y5) if total_y5 > 0 else np.nan,
            "mae10_precision": float(mae10[high].mean()),
            "mae10_lift": float(mae10[high].mean() / base10) if base10 > 0 else np.nan,
            "alert_fraction": float(high.mean())}


def _attack_summaries(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, years = [], []
    for pid, part in predictions.groupby("attack_process_id", sort=True):
        part = part.sort_values("signal_session")
        high = part.attack_high.astype(bool)
        lower, p, se = _block_bootstrap_rho(part.predicted_attack, part.a4, 4)
        auc = roc_auc_score(part.b4, part.predicted_attack) if part.b4.nunique() == 2 else np.nan
        model_mae = np.abs(part.a4 - part.predicted_attack).mean()
        base_mae = np.abs(part.a4 - part.outer_train_mean_a4).mean()
        rows.append({"attack_process_id": pid, "outer_weeks": len(part), "spearman_a4": _rho(part.predicted_attack, part.a4),
                     "block4_95_lower": lower, "block4_one_sided_p": p, "block4_se": se,
                     "top_mean_a4": part.loc[high, "a4"].mean(), "rest_mean_a4": part.loc[~high, "a4"].mean(),
                     "auc_b4": auc, "top_b4_rate": part.loc[high, "b4"].mean(), "rest_b4_rate": part.loc[~high, "b4"].mean(),
                     "top_median_w4": part.loc[high, "w4"].median(), "rest_median_w4": part.loc[~high, "w4"].median(),
                     "top_severe_w4_rate": part.loc[high, "severe_w4"].mean(), "rest_severe_w4_rate": part.loc[~high, "severe_w4"].mean(),
                     "w4_median_not_worse": part.loc[high, "w4"].median() >= part.loc[~high, "w4"].median(),
                     "w4_severe_not_worse": part.loc[high, "severe_w4"].mean() <= part.loc[~high, "severe_w4"].mean(),
                     "model_mae": model_mae, "train_mean_baseline_mae": base_mae,
                     "mae_skill_vs_train_mean": (base_mae - model_mae) / base_mae})
        for year, annual in part.groupby("outer_year"):
            years.append({"attack_process_id": pid, "outer_year": int(year), "weeks": len(annual),
                          "spearman_a4": _rho(annual.predicted_attack, annual.a4), "mean_a4": annual.a4.mean()})
    return pd.DataFrame(rows), pd.DataFrame(years)


def _leave_one_event(predictions: pd.DataFrame, events: pd.DataFrame, id_col: str, score_col: str, target_col: str, terminal_col: str) -> pd.DataFrame:
    rows = []
    for identity, part in predictions.groupby(id_col, sort=True):
        for event in events.itertuples(index=False):
            remove = part.execution_session.le(event.recovery_date) & part[terminal_col].ge(event.peak_date)
            kept = part.loc[~remove]
            rows.append({id_col: identity, "episode_id": event.episode_id, "removed_weeks": int(remove.sum()),
                         "remaining_weeks": len(kept), "spearman_without_event": _rho(kept[score_col], kept[target_col])})
    return pd.DataFrame(rows)


def _equivalence_clusters(predictions: pd.DataFrame, final: pd.DataFrame) -> pd.DataFrame:
    pivot = predictions.pivot(index="week_id", columns="process_id", values="predicted_risk")
    ids = final.process_id.tolist()
    parent = {x: x for x in ids}
    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb: parent[max(ra, rb)] = min(ra, rb)
    corr = pivot.corr(method="spearman").abs()
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if final.loc[final.process_id.eq(a), "family"].item() == final.loc[final.process_id.eq(b), "family"].item() and corr.loc[a, b] >= .95:
                union(a, b)
    groups: dict[str, list[str]] = {}
    for identity in ids: groups.setdefault(find(identity), []).append(identity)
    complexity = {"positive_ridge": 1, "additive_spline_ridge": 2, "monotone_lightgbm": 3}
    rows = []
    for number, members in enumerate(sorted(groups.values(), key=lambda x: min(x)), 1):
        representative = min(members, key=lambda x: (complexity[final.loc[final.process_id.eq(x), "family"].item()],
            0 if "RB00" in x else (2 if any(f"RB0{i}" in x for i in range(1, 5)) else 3), x))
        for identity in members:
            rows.append({"process_id": identity, "equivalence_cluster": f"EQ{number:02d}",
                         "canonical_representative": identity == representative, "cluster_size": len(members)})
    return pd.DataFrame(rows)


def _block_bootstrap_rho(x: pd.Series | np.ndarray, y: pd.Series | np.ndarray, block: int) -> tuple[float, float, float]:
    x, y = np.asarray(x, float), np.asarray(y, float)
    rng = np.random.default_rng(SEED + block)
    starts = np.arange(len(x) - block + 1)
    values = np.empty(BOOTSTRAP_REPETITIONS)
    for i in range(BOOTSTRAP_REPETITIONS):
        ids: list[int] = []
        while len(ids) < len(x):
            start = int(rng.choice(starts)); ids.extend(range(start, start + block))
        pick = np.asarray(ids[:len(x)])
        values[i] = _rho(x[pick], y[pick])
    return float(np.nanquantile(values, .05)), float(np.nanmean(values <= 0)), float(np.nanstd(values, ddof=1))


def _paired_rho_diff_se(candidate: np.ndarray, control: np.ndarray, y: np.ndarray, block: int) -> float:
    rng = np.random.default_rng(SEED + 101 + block)
    starts, values = np.arange(len(y) - block + 1), np.empty(1000)
    for i in range(len(values)):
        ids: list[int] = []
        while len(ids) < len(y):
            start = int(rng.choice(starts)); ids.extend(range(start, start + block))
        pick = np.asarray(ids[:len(y)])
        values[i] = _rho(candidate[pick], y[pick]) - _rho(control[pick], y[pick])
    return float(np.nanstd(values, ddof=1))


def _moving_block_mean_se(values: np.ndarray, block: int) -> float:
    values = np.asarray(values, float)
    if len(values) < block: return float(values.std(ddof=1) / np.sqrt(max(len(values), 1)))
    block_means = np.convolve(values, np.ones(block) / block, mode="valid")
    effective = max(len(values) / block, 1.0)
    return float(block_means.std(ddof=1) / np.sqrt(effective))


def _build_common(parents: dict[str, Path]) -> pd.DataFrame:
    features = pd.read_parquet(parents["r4a"] / "feature_inputs_weekly.parquet")
    features["signal_session"] = pd.to_datetime(features.signal_session).dt.normalize()
    features = features[features.arm_id.isin(FEATURE_IDS)].pivot(index=["week_id", "signal_session"], columns="arm_id", values="defense_score").reset_index()
    risk = pd.read_parquet(parents["r5a"] / "targets_weekly.parquet").rename(columns={"terminal_execution": "risk_terminal_execution", "target_available_at": "risk_target_available_at", "excess_mae13_deadzone5": "y5"})
    attack = pd.read_parquet(parents["r6a"] / "targets_weekly.parquet").rename(columns={"terminal_execution": "attack_terminal_execution", "target_available_at": "attack_target_available_at", "fwd_excess_logret_4w": "a4", "sustainable_attack_4w": "b4", "fwd_worst_excess_4w": "w4"})
    risk = risk[["week_id", "signal_session", "execution_session", "risk_terminal_execution", "risk_target_available_at", "target_available", "y5", "raw_mae13"]].rename(columns={"target_available": "risk_available"})
    attack = attack[["week_id", "signal_session", "execution_session", "attack_terminal_execution", "attack_target_available_at", "target_available", "a4", "b4", "w4", "severe_w4"]].rename(columns={"target_available": "attack_available"})
    common = features.merge(risk, on=["week_id", "signal_session"], validate="one_to_one").merge(attack, on=["week_id", "signal_session", "execution_session"], validate="one_to_one")
    _normalise_dates(common)
    mask = common.risk_available & common.attack_available & common[list(FEATURE_IDS)].notna().all(axis=1) & common.signal_session.le(pd.Timestamp("2021-09-24"))
    return common.loc[mask].sort_values("signal_session").reset_index(drop=True)


def _validate_fold_rows(data: pd.DataFrame, folds: dict[str, Any]) -> None:
    for outer in folds["outer_folds"]:
        train = _date_slice(data, outer["train_start_signal"], outer["train_end_signal"])
        test = _date_slice(data, outer["test_start_signal"], outer["test_end_signal"])
        if len(train) != outer["train_weeks"] or len(test) != outer["test_weeks"]:
            raise DataQualityError("Round7 absolute fold count drifted")
        if not train.risk_target_available_at.lt(pd.Timestamp(outer["test_start_signal"])).all():
            raise DataQualityError("Round7 outer risk target maturity failed")
        for inner in outer["inner_folds"]:
            itr = _date_slice(data, inner["train_start_signal"], inner["train_end_signal"])
            iva = _date_slice(data, inner["validation_start_signal"], inner["validation_end_signal"])
            if len(itr) != inner["train_weeks"] or len(iva) != inner["validation_weeks"] or not itr.risk_target_available_at.lt(pd.Timestamp(inner["validation_start_signal"])).all():
                raise DataQualityError("Round7 inner fold maturity/count failed")


def _lightgbm_repeat_test() -> bool:
    rng = np.random.default_rng(SEED)
    x = rng.normal(size=(128, 2)); y = x[:, 0] + .3 * x[:, 1]
    params = dict(objective="regression", max_depth=2, num_leaves=4, n_estimators=50, learning_rate=.05,
                  min_child_samples=52, monotone_constraints=[1, 1], random_state=SEED, n_jobs=1,
                  deterministic=True, force_col_wise=True, verbosity=-1)
    a = lgb.LGBMRegressor(**params).fit(x, y).predict(x)
    b = lgb.LGBMRegressor(**params).fit(x, y).predict(x)
    return bool(np.array_equal(a, b))


def _flatten_folds(folds: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for outer in folds["outer_folds"]:
        rows.append({"fold_type": "outer", "outer_year": outer["outer_year"], "inner_fold": np.nan,
                     "train_start_signal": outer["train_start_signal"], "train_end_signal": outer["train_end_signal"],
                     "test_start_signal": outer["test_start_signal"], "test_end_signal": outer["test_end_signal"],
                     "train_weeks": outer["train_weeks"], "test_weeks": outer["test_weeks"]})
        for inner in outer["inner_folds"]:
            rows.append({"fold_type": "inner", "outer_year": outer["outer_year"], "inner_fold": inner["inner_fold"],
                         "train_start_signal": inner["train_start_signal"], "train_end_signal": inner["train_end_signal"],
                         "test_start_signal": inner["validation_start_signal"], "test_end_signal": inner["validation_end_signal"],
                         "train_weeks": inner["train_weeks"], "test_weeks": inner["validation_weeks"]})
    return rows


def _normalise_dates(frame: pd.DataFrame) -> None:
    for column in frame.columns:
        if column.endswith("session") or column.endswith("execution") or column.endswith("available_at"):
            try: frame[column] = pd.to_datetime(frame[column]).dt.normalize()
            except (TypeError, ValueError): pass


def _date_slice(data: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    return data[data.signal_session.between(pd.Timestamp(start), pd.Timestamp(end))].copy()


def _rho(x: pd.Series | np.ndarray, y: pd.Series | np.ndarray) -> float:
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    valid = np.isfinite(xa) & np.isfinite(ya)
    if valid.sum() < 3 or np.ptp(xa[valid]) <= 0 or np.ptp(ya[valid]) <= 0: return np.nan
    value = spearmanr(xa[valid], ya[valid]).statistic
    return float(value) if np.isfinite(value) else np.nan


def _bh_adjust(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, float); order = np.argsort(p); ranked = p[order]
    adjusted = np.minimum.accumulate((ranked * len(p) / np.arange(1, len(p) + 1))[::-1])[::-1]
    out = np.empty_like(adjusted); out[order] = np.minimum(adjusted, 1.0); return out


def _load_inputs(project_root: str | Path, runtime_root: str | Path) -> tuple[Path, Path, dict[str, Any], dict[str, Any], dict[str, Path]]:
    root, runtime = Path(project_root).resolve(), Path(runtime_root).resolve()
    lock_path = root / "config/experiments/round7/PREREG_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    for relative, expected in lock["files"].items():
        if sha256_file(root / relative) != expected: raise DataQualityError(f"Round7 prereg hash mismatch: {relative}")
    program = tomllib.loads((root / "config/experiments/round7/program.toml").read_text(encoding="utf-8"))
    auth = program["authorization"]
    if not auth["risk_models"] or not auth["attack_isotonic"] or auth["strategy_nav"] or auth["final_state_machine"] or auth["lockbox"] or auth["mom255_transfer"]:
        raise DataQualityError("Round7 authorization is not fail-closed")
    parents = {
        "r4a": runtime / "data/round4/staging/R4A_FREE_FACTOR_DATA" / program["parent"]["r4a_run_id"],
        "r5a": runtime / "results/experiments/round5/R5A_MAE13_TARGET/runs" / program["parent"]["r5a_run_id"],
        "r6a": runtime / "results/experiments/round6/R6A_ATTACK4_TARGET/runs" / program["parent"]["r6a_run_id"],
    }
    for key in parents:
        if sha256_file(parents[key] / "manifest.json") != program["parent"][f"{key}_manifest_sha256"]:
            raise DataQualityError(f"Round7 {key} parent manifest drifted")
    expected_files = {"r4a": ("feature_inputs_weekly.parquet", "r4a_features_sha256"),
                      "r5a": ("targets_weekly.parquet", "r5a_targets_sha256"), "r6a": ("targets_weekly.parquet", "r6a_targets_sha256")}
    for key, (name, field) in expected_files.items():
        if sha256_file(parents[key] / name) != program["parent"][field]: raise DataQualityError(f"Round7 {key} parent file drifted")
    if lgb.__version__ != program["dependencies"]["lightgbm"]: raise DataQualityError("Round7 LightGBM version drifted")
    return root, runtime, lock, program, parents


def _run_ids(root: Path) -> list[str]:
    values = tomllib.loads((root / "config/experiments/round7/program.toml").read_text(encoding="utf-8"))["run_ids"]
    if len(values) != 4: raise DataQualityError("Round7 run-id count drifted")
    return list(values)


def _require_run_id(root: Path, index: int, run_id: str) -> None:
    if _run_ids(root)[index] != run_id: raise DataQualityError("Round7 run-id differs from preregistration")


def _batch_root(runtime: Path, batch: str, run_id: str) -> Path:
    return runtime / "results/experiments/round7" / batch / "runs" / run_id


def _validate_bundle(path: Path, expected_batch: str) -> None:
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest["program_id"] != PROGRAM_ID or manifest["batch_id"] != expected_batch or manifest["lockbox_read"] is not False:
        raise DataQualityError(f"Round7 parent identity/firewall failed: {path}")
    for record in manifest["files"]:
        member = path / record["path"]
        if member.stat().st_size != record["size_bytes"] or sha256_file(member) != record["sha256"]:
            raise DataQualityError(f"Round7 immutable bundle mismatch: {member}")


def _write_manifest(output: Path, root: Path, lock: dict[str, Any], batch_id: str, run_id: str, *, counts: dict[str, int], parent_manifests: dict[str, str], models_run: bool, assessment: str = "completed_development") -> dict[str, Any]:
    files = [{"path": p.relative_to(output).as_posix(), "size_bytes": p.stat().st_size, "sha256": sha256_file(p)}
             for p in sorted((x for x in output.rglob("*") if x.is_file()), key=lambda x: x.relative_to(output).as_posix())]
    manifest = {"schema_version": 1, "program_id": PROGRAM_ID, "batch_id": batch_id, "run_id": run_id,
        "status": "completed_development", "assessment": assessment, "formal_eligible": False,
        "maximum_target_signal": "2021-09-24", "lockbox_read": False, "lockbox_predictions_generated": False,
        "models_run": models_run, "final_state_machine_run": False, "strategy_nav_run": False, "mom255_transfer_run": False,
        "factor_additions_run": False, "window_search_run": False, "position_search_run": False,
        "prereg_lock_sha256": sha256_file(root / "config/experiments/round7/PREREG_LOCK.json"),
        "parent_manifests": parent_manifests, "counts": counts, "files": files}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
