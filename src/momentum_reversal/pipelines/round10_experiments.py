"""Round 10 sealed target generation and mechanical outcome reveal."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tomllib
from typing import Any

import numpy as np
import pandas as pd

from momentum_reversal.backtest import BaselineBacktester
from momentum_reversal.data import CorporateActionLedger
from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.round2_market import sha256_file
from momentum_reversal.data.round4_factors import build_rsp_spy_score


PROGRAM_ID = "p00_mom255_mechanical_lockbox_round10_v1"


@dataclass(frozen=True, slots=True)
class Round10BatchResult:
    output_dir: Path
    manifest_path: Path
    status: str


def build_lockbox_states(
    weekly_scores: pd.DataFrame,
    *,
    parent_state: str,
    parent_defense_age: int,
    threshold_2021: float,
    bridge_first_signal: pd.Timestamp,
    last_signal: pd.Timestamp,
    purge_weeks: int = 13,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = weekly_scores.copy().sort_values("signal_session", kind="mergesort").reset_index(drop=True)
    for column in ("signal_session", "execution_session"):
        frame[column] = pd.to_datetime(frame[column]).dt.normalize()
    frame["risk_score"] = pd.to_numeric(frame.risk_score, errors="coerce")
    frame = frame.loc[frame.risk_score.notna()].reset_index(drop=True)
    thresholds: dict[int, float] = {2021: float(threshold_2021)}
    rows = frame.loc[(frame.signal_session >= bridge_first_signal) & (frame.signal_session <= last_signal)]
    for year in sorted(set(rows.execution_session.dt.year) - {2021}):
        first_index = int(frame.index[frame.execution_session.dt.year.eq(year)].min())
        train_end_exclusive = first_index - purge_weeks
        if train_end_exclusive <= 0:
            raise DataQualityError(f"Round10 insufficient threshold history: {year}")
        thresholds[year] = float(frame.iloc[:train_end_exclusive].risk_score.quantile(0.75, interpolation="linear"))
    state, defense_age = parent_state, int(parent_defense_age)
    output = []
    for row in rows.itertuples(index=False):
        year = pd.Timestamp(row.execution_session).year
        threshold = thresholds[year]
        risk = bool(float(row.risk_score) >= threshold)
        prior = state
        if risk:
            state = "DEFENSE"; defense_age = 1 if prior == "NORMAL" else defense_age + 1; reason = "risk_veto"
        elif state == "DEFENSE":
            if defense_age < 1: raise DataQualityError("Round10 minimum defense age violated")
            state = "NORMAL"; defense_age = 0; reason = "risk_clear"
        else:
            state = "NORMAL"; defense_age = 0; reason = "normal_hold"
        output.append({"week_id": row.week_id, "signal_session": row.signal_session, "execution_session": row.execution_session, "execution_year": year, "risk_score": float(row.risk_score), "threshold_q75": threshold, "risk_high": risk, "prior_state": prior, "state": state, "transition": f"{prior}_TO_{state}", "transition_reason": reason, "defense_age": defense_age, "target_allocation": 0.5 if state == "DEFENSE" else 1.0})
    threshold_frame = pd.DataFrame([{"execution_year": year, "threshold_q75": value} for year, value in sorted(thresholds.items())])
    return pd.DataFrame(output), threshold_frame


def build_sealed_target_ledger(
    *,
    engine: BaselineBacktester,
    base_targets: pd.DataFrame,
    states: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = base_targets.copy(); base["execution_date"] = pd.to_datetime(base.execution_date).dt.normalize()
    base = base[(base.execution_date >= start) & (base.execution_date <= end)]
    base_map = {pd.Timestamp(date): group.set_index("sid").target_weight.astype(float).sort_index() for date, group in base.groupby("execution_date", sort=True)}
    schedule = states.copy(); schedule["execution_session"] = pd.to_datetime(schedule.execution_session).dt.normalize()
    schedule = schedule[(schedule.execution_session >= start) & (schedule.execution_session <= end)].set_index("execution_session")
    allocation = schedule.target_allocation.astype(float)
    if allocation.empty or allocation.index.duplicated().any(): raise DataQualityError("Round10 sealed state schedule invalid")
    events = pd.DatetimeIndex(sorted(base_map)).union(allocation.index).sort_values()
    sessions = engine.sessions[(engine.sessions >= start) & (engine.sessions <= end)]
    if events.difference(sessions).size: raise DataQualityError("Round10 sealed event calendar drifted")
    held = allocation.reindex(sessions).ffill()
    if held.isna().any(): raise DataQualityError("Round10 first lockbox allocation missing")
    shares = pd.Series(dtype=float); action_cash = 0.0; ledger, event_rows = [], []
    for date in sessions:
        date = pd.Timestamp(date)
        shares, action_cash, _ = engine._apply_corporate_actions(date=date, shares=shares, cash=action_cash)
        if date not in events: continue
        is_base, is_overlay = date in base_map, date in allocation.index
        target_allocation = float(held.loc[date])
        if is_base:
            targets = base_map[date] * target_allocation
            opens = engine._price_vector(date, pd.Index(targets.index), "tr_open", execution=True)
            if len(opens) != len(targets): raise DataQualityError("Round10 frozen G00 target open missing")
        else:
            existing = pd.Index(shares.index, dtype="object")
            opens = engine._price_vector(date, existing, "tr_open", execution=False)
            values = shares * opens; risky = float(values.sum())
            if risky <= 0: raise DataQualityError("Round10 overlay-only event has no risky book")
            targets = values / risky * target_allocation
        shares = (targets / opens).astype(float)
        event_kind = "base_and_overlay" if is_base and is_overlay else ("base" if is_base else "overlay")
        expected_filled_allocation = float(targets.sum())
        event_rows.append({"execution_date": date, "event_kind": event_kind, "base_reranked": bool(is_base), "overlay_event": bool(is_overlay), "target_allocation": target_allocation, "filled_target_allocation": expected_filled_allocation, "selected_count": len(targets)})
        for sid, weight in targets.items():
            ledger.append({"execution_date": date, "event_kind": event_kind, "base_reranked": bool(is_base), "overlay_event": bool(is_overlay), "target_allocation": target_allocation, "sid": sid, "target_weight": float(weight)})
    result, audit = pd.DataFrame(ledger), pd.DataFrame(event_rows)
    totals = result.groupby("execution_date").target_weight.sum()
    # Frozen G00 base targets preserve its leave-cash execution semantics: a
    # base event may sum below the requested allocation when a selected open
    # was unavailable in the parent run. Overlay-only events normalize the
    # currently held relative stock book back to the requested allocation.
    expected = audit.set_index("execution_date").filled_target_allocation
    if not np.allclose(totals.reindex(expected.index), expected, rtol=0, atol=1e-12): raise DataQualityError("Round10 sealed targets do not sum to allocation")
    return result, audit


def run_r10b(*, project_root: str | Path, runtime_root: str | Path, run_id: str) -> Round10BatchResult:
    root, runtime, lock, program, paths = _load_phase1_inputs(project_root, runtime_root)
    if run_id != program["run_ids"][0]: raise DataQualityError("Round10 R10B run-id mismatch")
    output = runtime / "results/experiments/round10/R10B_SEALED_TARGETS/runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    rsp = pd.read_parquet(paths["r10a"] / "rsp_daily.parquet")
    market = pd.read_parquet(paths["r2a"] / "curated/market_daily.parquet")
    decision = pd.read_parquet(paths["r2a"] / "curated/decision_calendar.parquet")
    for frame in (rsp, market, decision):
        for column in ("session_date", "signal_session", "execution_session"):
            if column in frame: frame[column] = pd.to_datetime(frame[column]).dt.normalize()
    scores = build_rsp_spy_score(rsp, market).rename(columns={"rsp_spy_score63": "risk_score"})
    weekly = decision[["week_id", "signal_session", "execution_session"]].merge(scores, left_on="signal_session", right_on="session_date", how="left", validate="one_to_one").drop(columns="session_date")
    old = pd.read_parquet(paths["r4a"] / "feature_inputs_weekly.parquet")
    old = old[old.arm_id.eq("R4B__RSP_SPY63")][["signal_session", "defense_score"]]; old["signal_session"] = pd.to_datetime(old.signal_session).dt.normalize()
    identity = old.merge(weekly[["signal_session", "risk_score"]], on="signal_session", validate="one_to_one")
    error = float(np.max(np.abs(identity.defense_score.to_numpy(float) - identity.risk_score.to_numpy(float))))
    if error > 1e-12 or len(identity) != len(old): raise DataQualityError("Round10 weekly RSP score identity failed")
    parent_states = pd.read_parquet(paths["r8a"] / "policy_states_weekly.parquet")
    parent = parent_states[parent_states.policy_id.eq("P00_RSP_Y5_CLEAR")].sort_values("signal_session").iloc[-1]
    raw = pd.read_parquet(paths["r7b"] / "raw_rsp_sentinel.parquet")
    threshold_2021 = float(raw.loc[raw.outer_year.eq(2021), "threshold_q75"].iloc[0])
    states, thresholds = build_lockbox_states(weekly, parent_state=str(parent.state), parent_defense_age=int(parent.defense_age), threshold_2021=threshold_2021, bridge_first_signal=pd.Timestamp(program["sample"]["bridge_first_signal"]), last_signal=pd.Timestamp(program["sample"]["lockbox_last_signal"]), purge_weeks=int(program["policy"]["purge_scheduled_weeks"]))
    lockbox_states = states[states.execution_session.ge(pd.Timestamp(program["sample"]["lockbox_first_execution"]))].copy()
    prices = pd.read_parquet(paths["dataset"] / "prices_daily.parquet")
    corp = CorporateActionLedger(pd.read_parquet(paths["dataset"] / "corporate_actions.parquet"))
    calendar = pd.read_parquet(paths["dataset"] / "calendar.parquet"); sessions = pd.DatetimeIndex(pd.to_datetime(calendar.session_date)).normalize()
    engine = BaselineBacktester(prices, object(), sessions=sessions, corporate_actions=corp, missing_valuation_policy="carry_last_close", missing_execution_policy="leave_cash")
    holdings = pd.read_parquet(paths["g00"] / "artifacts/holdings.parquet")
    registry = pd.read_csv(root / program["transfer"]["registry"])
    all_targets, all_events = [], []
    start, end = pd.Timestamp(program["sample"]["lockbox_first_execution"]), pd.Timestamp(program["sample"]["lockbox_nav_end"])
    for spec in registry.itertuples(index=False):
        base = holdings[holdings.strategy_id.eq(spec.g00_strategy_id)][["execution_date", "sid", "target_weight"]]
        target, audit = build_sealed_target_ledger(engine=engine, base_targets=base, states=lockbox_states, start=start, end=end)
        for frame in (target, audit): frame.insert(0, "transfer_id", spec.transfer_id)
        all_targets.append(target); all_events.append(audit)
    target_ledger = pd.concat(all_targets, ignore_index=True); event_audit = pd.concat(all_events, ignore_index=True)
    states.to_parquet(output / "p00_states_weekly.parquet", index=False, compression="zstd")
    thresholds.to_csv(output / "annual_thresholds.csv", index=False, lineterminator="\n")
    target_ledger.to_parquet(output / "sealed_target_ledger.parquet", index=False, compression="zstd")
    event_audit.to_csv(output / "target_event_audit.csv", index=False, lineterminator="\n")
    pd.DataFrame([{"overlap_weeks":len(identity),"maximum_score_absolute_error":error,"identity_passed":True}]).to_csv(output / "signal_identity.csv", index=False, lineterminator="\n")
    files = _file_records(output)
    manifest={"schema_version":1,"program_id":PROGRAM_ID,"batch_id":"R10B_SEALED_TARGETS","run_id":run_id,"status":"completed_sealed_targets","assessment":"completed_pending_outcome_reveal_lock","formal_eligible":False,"lockbox_target_generation":True,"outcome_reveal_run":False,"strategy_nav_run":False,"forward_returns_run":False,"performance_metrics_run":False,"cost_scenarios_run":False,"g00_nav_read":False,"prereg_lock_sha256":sha256_file(root/"config/experiments/round10/PREREG_LOCK.json"),"counts":{"state_rows":len(states),"lockbox_state_rows":len(lockbox_states),"transfer_cells":6,"target_rows":len(target_ledger),"target_events":len(event_audit)},"files":files}
    (output/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return Round10BatchResult(output,output/"manifest.json",manifest["status"])


def _load_phase1_inputs(project_root, runtime_root):
    root,runtime=Path(project_root).resolve(),Path(runtime_root).resolve(); lock=json.loads((root/"config/experiments/round10/PREREG_LOCK.json").read_text(encoding="utf-8"))
    for rel,expected in lock["files"].items():
        if sha256_file(root/rel)!=expected: raise DataQualityError(f"Round10 prereg mismatch: {rel}")
    program=tomllib.loads((root/"config/experiments/round10/program.toml").read_text(encoding="utf-8")); auth=program["authorization"]
    if not auth["prediction_target_phase"] or auth["outcome_reveal_phase"] or auth["strategy_nav"] or auth["performance_assessment"]: raise DataQualityError("Round10 phase1 authorization failed")
    p=program["parent"]
    paths={"r10a":runtime/"data/round10/staging/R10A_RSP_LOCKBOX_FEATURE"/p["r10a_run_id"],"r2a":runtime/"data/round2/staging/R2A_DATA"/p["r2a_run_id"],"r4a":runtime/"data/round4/staging/R4A_FREE_FACTOR_DATA"/p["r4a_run_id"],"r7b":runtime/"results/experiments/round7/R7B_RISK_MODEL_TOURNAMENT/runs"/p["r7b_run_id"],"r8a":runtime/"results/experiments/round8/R8A_RSP_POLICY_SIGNALS/runs"/p["r8a_run_id"],"g00":runtime/"results/experiments/G00/runs"/p["g00_run_id"],"dataset":runtime/"data/curated"/p["dataset_version"]}
    checks={paths["r10a"]/"manifest.json":p["r10a_manifest_sha256"],paths["r10a"]/"rsp_daily.parquet":p["r10a_rsp_daily_sha256"],paths["r2a"]/"manifest.json":p["r2a_manifest_sha256"],paths["r2a"]/"curated/market_daily.parquet":p["r2a_market_sha256"],paths["r2a"]/"curated/decision_calendar.parquet":p["r2a_calendar_sha256"],paths["r4a"]/"feature_inputs_weekly.parquet":p["r4a_features_sha256"],paths["r7b"]/"manifest.json":p["r7b_manifest_sha256"],paths["r7b"]/"raw_rsp_sentinel.parquet":p["r7b_raw_rsp_sha256"],paths["r8a"]/"manifest.json":p["r8a_manifest_sha256"],paths["r8a"]/"policy_states_weekly.parquet":p["r8a_states_sha256"],paths["g00"]/"manifest.json":p["g00_manifest_sha256"],paths["g00"]/"artifacts/holdings.parquet":p["g00_holdings_sha256"],paths["dataset"]/"prices_daily.parquet":p["prices_sha256"],paths["dataset"]/"corporate_actions.parquet":p["corporate_actions_sha256"],paths["dataset"]/"calendar.parquet":p["calendar_sha256"]}
    for path,expected in checks.items():
        if sha256_file(path)!=expected: raise DataQualityError(f"Round10 phase1 parent drift: {path}")
    return root,runtime,lock,program,paths


def _file_records(output: Path) -> list[dict[str, Any]]:
    return [{"path":p.relative_to(output).as_posix(),"size_bytes":p.stat().st_size,"sha256":sha256_file(p)} for p in sorted(output.rglob("*")) if p.is_file()]
