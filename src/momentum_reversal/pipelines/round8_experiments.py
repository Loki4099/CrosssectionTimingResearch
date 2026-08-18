"""Preregistered Round 8 RSP-only risk-veto policy experiments."""
from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
import tomllib
from typing import Any
import numpy as np
import pandas as pd

from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.round2_market import sha256_file
from momentum_reversal.pipelines.round4_experiments import _performance, replay_spy_cash

PROGRAM_ID="rsp_only_state_machine_round8_v1"
SEED=20260818

@dataclass(frozen=True,slots=True)
class Round8BatchResult:
    output_dir:Path; manifest_path:Path; status:str

def build_policy_states(raw:pd.DataFrame,attack:pd.DataFrame,registry:pd.DataFrame)->pd.DataFrame:
    ax1=attack[attack.attack_process_id.eq("AX01_RAW_RSP_RECOVERY")][["week_id","predicted_attack","attack_high"]].rename(columns={"predicted_attack":"raw_attack_score","attack_high":"raw_attack_high"})
    ax2=attack[attack.attack_process_id.eq("AX02_RSP_A4_MONOTONE")][["week_id","predicted_attack"]].rename(columns={"predicted_attack":"expected_a4"})
    base=raw.merge(ax1,on="week_id",validate="one_to_one").merge(ax2,on="week_id",validate="one_to_one").sort_values("signal_session")
    if len(base)!=raw.week_id.nunique() or not base.week_id.is_unique: raise DataQualityError("Round8 common prediction calendar drifted")
    rows=[]
    for spec in registry.itertuples(index=False):
        state="NORMAL"; defense_age=0
        for row in base.itertuples(index=False):
            prior=state; risk=bool(row.alert_high) if pd.notna(row.alert_high) else False
            if risk:
                state="DEFENSE"; defense_age=1 if prior=="NORMAL" else defense_age+1
                reason="risk_veto"
            elif state=="DEFENSE":
                if spec.policy_id=="P00_RSP_Y5_CLEAR": exit_ok=True
                elif spec.policy_id=="P01_RSP_RAW_RECOVERY": exit_ok=bool(row.raw_attack_high) if pd.notna(row.raw_attack_high) else False
                elif spec.policy_id=="P02_RSP_A4_ISOTONIC": exit_ok=bool(row.expected_a4>0) if pd.notna(row.expected_a4) else False
                else: raise DataQualityError(f"unknown Round8 policy: {spec.policy_id}")
                if defense_age>=1 and exit_ok: state="NORMAL"; defense_age=0; reason="exit_confirmed"
                else: defense_age+=1; reason="exit_not_confirmed"
            else:
                reason="normal_hold"; defense_age=0
            rows.append({"policy_id":spec.policy_id,"week_id":row.week_id,"signal_session":row.signal_session,
                "execution_session":row.execution_session,"outer_year":row.outer_year,"risk_score":row.predicted_risk,
                "risk_threshold_q75":row.threshold_q75,"risk_high":risk,"raw_attack_score":row.raw_attack_score,
                "raw_attack_high":row.raw_attack_high,"expected_a4":row.expected_a4,"prior_state":prior,"state":state,
                "transition":f"{prior}_TO_{state}","transition_reason":reason,"defense_age":defense_age,
                "target_spy_weight":.5 if state=="DEFENSE" else 1.0,"y5":row.y5,"raw_mae13":row.raw_mae13})
    out=pd.DataFrame(rows)
    if not out.groupby("policy_id").week_id.nunique().eq(len(base)).all(): raise DataQualityError("Round8 state coverage drifted")
    return out

def run_r8a(*,project_root:str|Path,runtime_root:str|Path,run_id:str)->Round8BatchResult:
    root,runtime,lock,program,parents=_load_inputs(project_root,runtime_root); _require_run_id(root,0,run_id)
    output=_batch_root(runtime,"R8A_RSP_POLICY_SIGNALS",run_id); output.mkdir(parents=True,exist_ok=False)
    raw=pd.read_parquet(parents["r7b"]/"raw_rsp_sentinel.parquet"); attack=pd.read_parquet(parents["r7c"]/"outer_predictions.parquet")
    for f in (raw,attack): _dates(f)
    registry=pd.read_csv(root/program["policies"]["registry"])
    if len(raw)!=404 or raw.week_id.nunique()!=404: raise DataQualityError("Round8 frozen outer-OOS calendar drifted")
    states=build_policy_states(raw,attack,registry)
    states.to_parquet(output/"policy_states_weekly.parquet",index=False,compression="zstd")
    summary=states.groupby("policy_id",as_index=False).agg(weeks=("week_id","size"),defense_fraction=("state",lambda x:float(x.eq("DEFENSE").mean())),
      transitions_to_defense=("transition",lambda x:int(x.eq("NORMAL_TO_DEFENSE").sum())),transitions_to_normal=("transition",lambda x:int(x.eq("DEFENSE_TO_NORMAL").sum())),
      mean_target_weight=("target_spy_weight","mean"),maximum_defense_age=("defense_age","max"))
    summary.to_csv(output/"state_summary.csv",index=False,lineterminator="\n")
    manifest=_manifest(output,root,lock,"R8A_RSP_POLICY_SIGNALS",run_id,{"policies":3,"state_rows":len(states),"weeks_per_policy":404},
      {"r7b":sha256_file(parents["r7b"]/"manifest.json"),"r7c":sha256_file(parents["r7c"]/"manifest.json")},False)
    return Round8BatchResult(output,output/"manifest.json",manifest["status"])

def run_r8b(*,project_root:str|Path,runtime_root:str|Path,run_id:str)->Round8BatchResult:
    root,runtime,lock,program,parents=_load_inputs(project_root,runtime_root); _require_run_id(root,1,run_id)
    r8a=_batch_root(runtime,"R8A_RSP_POLICY_SIGNALS",_run_ids(root)[0]); _validate_bundle(r8a,"R8A_RSP_POLICY_SIGNALS")
    output=_batch_root(runtime,"R8B_RSP_SPYCASH_REPLAY",run_id); output.mkdir(parents=True,exist_ok=False)
    states=pd.read_parquet(r8a/"policy_states_weekly.parquet"); _dates(states)
    market=pd.read_parquet(parents["r2a"]/"curated/market_daily.parquet"); rf=pd.read_parquet(parents["r2a"]/"curated/risk_free_daily.parquet")
    start=pd.Timestamp(program["sample"]["first_execution"]); end=pd.Timestamp(program["sample"]["nav_end"])
    navs=[]; summaries=[]; yearly=[]
    for pid,part in states.groupby("policy_id",sort=True):
        schedule=part[["execution_session","target_spy_weight"]]
        daily_sessions=pd.DatetimeIndex(pd.to_datetime(market.session_date)).normalize(); daily_sessions=daily_sessions[(daily_sessions>=start)&(daily_sessions<=end)]
        held=pd.Series(schedule.set_index("execution_session").target_spy_weight.reindex(daily_sessions).ffill().to_numpy(float),index=daily_sessions)
        static_weight=float(held.mean()); static_schedule=schedule.copy(); static_schedule["target_spy_weight"]=static_weight
        for cost in program["policies"]["cost_bps"]:
            dynamic=replay_spy_cash(market,rf,schedule,start=start,end=end,cost_bps=cost)
            static=replay_spy_cash(market,rf,static_schedule,start=start,end=end,cost_bps=cost)
            for kind,frame in (("dynamic",dynamic),("matched_static",static)):
                saved=frame.copy(); saved.insert(0,"policy_id",pid); saved.insert(1,"path_type",kind); saved.insert(2,"cost_bps",cost); navs.append(saved)
            active=dynamic.set_index("date").nav/static.set_index("date").nav
            dm,sm=_performance(dynamic),_performance(static)
            summaries.append({"policy_id":pid,"cost_bps":cost,"mean_target_weight":static_weight,"dynamic_terminal":dynamic.nav.iloc[-1],
              "static_terminal":static.nav.iloc[-1],"active_terminal_wealth":active.iloc[-1]-1,"dynamic_cagr":dm["cagr"],"dynamic_sharpe":dm["sharpe"],"dynamic_mdd":dm["mdd"],
              "static_cagr":sm["cagr"],"static_sharpe":sm["sharpe"],"static_mdd":sm["mdd"],"dynamic_turnover":dynamic.turnover.sum()})
            if cost==10:
                alog=np.log(active).diff().fillna(np.log(active.iloc[0])); y=pd.DataFrame({"date":active.index,"active_log_return":alog.to_numpy()}); y["execution_year"]=y.date.dt.year
                y=y.groupby("execution_year",as_index=False).active_log_return.sum(); y.insert(0,"policy_id",pid); y["positive"]=y.active_log_return>0; yearly.append(y)
    pd.concat(navs,ignore_index=True).to_parquet(output/"nav_daily.parquet",index=False,compression="zstd")
    pd.DataFrame(summaries).to_csv(output/"economic_summary.csv",index=False,lineterminator="\n")
    pd.concat(yearly,ignore_index=True).to_csv(output/"yearly_active.csv",index=False,lineterminator="\n")
    manifest=_manifest(output,root,lock,"R8B_RSP_SPYCASH_REPLAY",run_id,{"policies":3,"nav_paths":24,"nav_rows":sum(len(x) for x in navs)},
      {"r8a":sha256_file(r8a/"manifest.json"),"r2a":sha256_file(parents["r2a"]/"manifest.json")},False)
    return Round8BatchResult(output,output/"manifest.json",manifest["status"])

def run_r8c(*,project_root:str|Path,runtime_root:str|Path,run_id:str)->Round8BatchResult:
    root,runtime,lock,program,_=_load_inputs(project_root,runtime_root); _require_run_id(root,2,run_id)
    r8a=_batch_root(runtime,"R8A_RSP_POLICY_SIGNALS",_run_ids(root)[0]); r8b=_batch_root(runtime,"R8B_RSP_SPYCASH_REPLAY",_run_ids(root)[1])
    _validate_bundle(r8a,"R8A_RSP_POLICY_SIGNALS"); _validate_bundle(r8b,"R8B_RSP_SPYCASH_REPLAY")
    output=_batch_root(runtime,"R8C_RSP_POLICY_ASSESSMENT",run_id); output.mkdir(parents=True,exist_ok=False)
    states=pd.read_parquet(r8a/"policy_states_weekly.parquet"); nav=pd.read_parquet(r8b/"nav_daily.parquet"); econ=pd.read_csv(r8b/"economic_summary.csv"); yearly=pd.read_csv(r8b/"yearly_active.csv")
    _dates(states); _dates(nav)
    events=pd.read_csv(root/program["inference"]["event_registry"],parse_dates=["peak_date","recovery_date"])
    e10=econ[econ.cost_bps.eq(10)].copy(); e20=econ[econ.cost_bps.eq(20)][["policy_id","active_terminal_wealth"]].rename(columns={"active_terminal_wealth":"active_terminal_wealth_20"})
    py=yearly.groupby("policy_id",as_index=False).positive.mean().rename(columns={"positive":"positive_year_fraction"})
    final=e10.merge(e20,on="policy_id").merge(py,on="policy_id")
    event_rows=[]; weekly_active={}
    for pid in final.policy_id:
        dyn=nav[(nav.policy_id.eq(pid))&nav.path_type.eq("dynamic")&nav.cost_bps.eq(10)].set_index("date").nav
        sta=nav[(nav.policy_id.eq(pid))&nav.path_type.eq("matched_static")&nav.cost_bps.eq(10)].set_index("date").nav
        alog=np.log(dyn/sta).diff().fillna(np.log((dyn/sta).iloc[0])); weekly_active[pid]=alog.resample("W-FRI").sum()
        for ev in events.itertuples(index=False):
            keep=~((alog.index>=ev.peak_date)&(alog.index<=ev.recovery_date)); event_rows.append({"policy_id":pid,"episode_id":ev.episode_id,
              "removed_days":int((~keep).sum()),"active_terminal_without_event":float(np.exp(alog[keep].sum())-1)})
    leave=pd.DataFrame(event_rows); minleave=leave.groupby("policy_id",as_index=False).active_terminal_without_event.min().rename(columns={"active_terminal_without_event":"minimum_leaveout_active_terminal"})
    final=final.merge(minleave,on="policy_id"); final["common_economic_gate"]=(final.active_terminal_wealth.gt(0)&final.active_terminal_wealth_20.gt(0)&final.positive_year_fraction.ge(.60)&final.dynamic_mdd.ge(final.static_mdd)&final.minimum_leaveout_active_terminal.gt(0))
    p00states=states[states.policy_id.eq("P00_RSP_Y5_CLEAR")]; p00mdd=float(final.loc[final.policy_id.eq("P00_RSP_Y5_CLEAR"),"dynamic_mdd"].iloc[0]); p00prem=int((p00states.transition.eq("DEFENSE_TO_NORMAL")&p00states.raw_mae13.ge(.10)).sum()); p00exp=float(p00states.loc[p00states.raw_mae13.ge(.10),"target_spy_weight"].mean())
    increment=[]
    for pid in ("P01_RSP_RAW_RECOVERY","P02_RSP_A4_ISOTONIC"):
        diff=weekly_active[pid].subtract(weekly_active["P00_RSP_Y5_CLEAR"],fill_value=np.nan).dropna(); lower,p=_block_mean(diff.to_numpy(),13,5000)
        st=states[states.policy_id.eq(pid)]; prem=int((st.transition.eq("DEFENSE_TO_NORMAL")&st.raw_mae13.ge(.10)).sum()); exposure=float(st.loc[st.raw_mae13.ge(.10),"target_spy_weight"].mean()); mdd=float(final.loc[final.policy_id.eq(pid),"dynamic_mdd"].iloc[0])
        increment.append({"policy_id":pid,"control_policy_id":"P00_RSP_Y5_CLEAR","weekly_observations":len(diff),"mean_weekly_increment":diff.mean(),"block13_95_lower":lower,"one_sided_p":p,"mdd_not_worse":mdd>=p00mdd,"premature_reentry":prem,"control_premature_reentry":p00prem,"premature_not_increased":prem<=p00prem,"mae10_mean_exposure":exposure,"control_mae10_mean_exposure":p00exp,"mae10_exposure_not_increased":exposure<=p00exp})
    inc=pd.DataFrame(increment); inc["holm_p_value"]=_holm(inc.one_sided_p.to_numpy()); inc["incremental_gate"]=(inc.block13_95_lower.gt(0)&inc.holm_p_value.le(.05)&inc.mdd_not_worse&inc.premature_not_increased&inc.mae10_exposure_not_increased)
    final=final.merge(inc[["policy_id","holm_p_value","incremental_gate"]],on="policy_id",how="left"); final["parent_head_qualified"]=final.policy_id.ne("P02_RSP_A4_ISOTONIC")
    final["development_policy_eligible"]=final.common_economic_gate & final.parent_head_qualified & (final.policy_id.eq("P00_RSP_Y5_CLEAR")|final.incremental_gate.fillna(False))
    final["exploratory_dual_label_mechanism_positive"]=final.policy_id.eq("P02_RSP_A4_ISOTONIC")&final.common_economic_gate&final.incremental_gate.fillna(False)
    leave.to_csv(output/"leave_one_event_out.csv",index=False,lineterminator="\n"); inc.to_csv(output/"incremental_comparisons.csv",index=False,lineterminator="\n"); final.to_csv(output/"final_assessment.csv",index=False,lineterminator="\n")
    decision={"program_id":PROGRAM_ID,"status":"completed_pending_user_round9_freeze_decision","development_policy_eligible":final.loc[final.development_policy_eligible,"policy_id"].tolist(),"exploratory_dual_label_positive":bool(final.exploratory_dual_label_mechanism_positive.any()),"round9_authorized":False,"lockbox_read":False,"mom255_transfer_run":False}
    (output/"decision.json").write_text(json.dumps(decision,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    manifest=_manifest(output,root,lock,"R8C_RSP_POLICY_ASSESSMENT",run_id,{"policies":3,"eligible":int(final.development_policy_eligible.sum()),"events":6,"incremental_hypotheses":2},{"r8a":sha256_file(r8a/"manifest.json"),"r8b":sha256_file(r8b/"manifest.json")},False,"completed_pending_user_round9_freeze_decision")
    return Round8BatchResult(output,output/"manifest.json",manifest["status"])

def _block_mean(x:np.ndarray,block:int,reps:int)->tuple[float,float]:
    rng=np.random.default_rng(SEED); starts=np.arange(len(x)-block+1); est=np.empty(reps)
    for i in range(reps):
        ids=[]
        while len(ids)<len(x): s=int(rng.choice(starts)); ids.extend(range(s,s+block))
        est[i]=np.mean(x[np.asarray(ids[:len(x)])])
    return float(np.quantile(est,.05)),float(np.mean(est<=0))
def _holm(p:np.ndarray)->np.ndarray:
    p=np.asarray(p,float); order=np.argsort(p); out=np.empty_like(p); running=0.
    for rank,idx in enumerate(order): running=max(running,(len(p)-rank)*p[idx]); out[idx]=min(running,1.)
    return out
def _dates(frame:pd.DataFrame)->None:
    for c in frame.columns:
        if c in ("date",) or c.endswith("session") or c.endswith("execution"):
            try: frame[c]=pd.to_datetime(frame[c]).dt.normalize()
            except: pass
def _load_inputs(project_root,runtime_root):
    root,runtime=Path(project_root).resolve(),Path(runtime_root).resolve(); lock=json.loads((root/"config/experiments/round8/PREREG_LOCK.json").read_text(encoding="utf-8"))
    for rel,val in lock["files"].items():
        if sha256_file(root/rel)!=val: raise DataQualityError(f"Round8 prereg mismatch: {rel}")
    p=tomllib.loads((root/"config/experiments/round8/program.toml").read_text(encoding="utf-8")); a=p["authorization"]
    if a["lockbox"] or a["mom255_transfer"] or a["model_search"] or not a["spy_cash_development_nav"]: raise DataQualityError("Round8 auth failed")
    parents={"r7a":runtime/"results/experiments/round7/R7A_DUAL_TARGET_FOLDS/runs"/p["parent"]["r7a_run_id"],"r7b":runtime/"results/experiments/round7/R7B_RISK_MODEL_TOURNAMENT/runs"/p["parent"]["r7b_run_id"],"r7c":runtime/"results/experiments/round7/R7C_RSP_ATTACK_COMPARATOR/runs"/p["parent"]["r7c_run_id"],"r7d":runtime/"results/experiments/round7/R7D_HEAD_QUALIFICATION/runs"/p["parent"]["r7d_run_id"],"r2a":runtime/"data/round2/staging/R2A_DATA"/p["parent"]["r2a_run_id"]}
    for k,path in parents.items():
        if sha256_file(path/"manifest.json")!=p["parent"][f"{k}_manifest_sha256"]: raise DataQualityError(f"Round8 parent drift: {k}")
    return root,runtime,lock,p,parents
def _run_ids(root): return list(tomllib.loads((root/"config/experiments/round8/program.toml").read_text(encoding="utf-8"))["run_ids"])
def _require_run_id(root,index,run_id):
    if _run_ids(root)[index]!=run_id: raise DataQualityError("Round8 run-id mismatch")
def _batch_root(runtime,batch,run_id): return runtime/"results/experiments/round8"/batch/"runs"/run_id
def _validate_bundle(path,batch):
    m=json.loads((path/"manifest.json").read_text(encoding="utf-8"))
    if m["program_id"]!=PROGRAM_ID or m["batch_id"]!=batch or m["lockbox_read"] is not False: raise DataQualityError("Round8 bundle identity failed")
    for r in m["files"]:
        f=path/r["path"]
        if f.stat().st_size!=r["size_bytes"] or sha256_file(f)!=r["sha256"]: raise DataQualityError("Round8 bundle mutated")
def _manifest(output,root,lock,batch,run_id,counts,parents,models,assessment="completed_development"):
    files=[{"path":p.relative_to(output).as_posix(),"size_bytes":p.stat().st_size,"sha256":sha256_file(p)} for p in sorted(output.rglob("*")) if p.is_file()]
    m={"schema_version":1,"program_id":PROGRAM_ID,"batch_id":batch,"run_id":run_id,"status":"completed_development","assessment":assessment,"formal_eligible":False,"lockbox_read":False,"lockbox_predictions_generated":False,"models_run":models,"state_machine_run":batch!="R8A_RSP_POLICY_SIGNALS" or True,"strategy_nav_run":batch=="R8B_RSP_SPYCASH_REPLAY","mom255_transfer_run":False,"prereg_lock_sha256":sha256_file(root/"config/experiments/round8/PREREG_LOCK.json"),"parent_manifests":parents,"counts":counts,"files":files}
    (output/"manifest.json").write_text(json.dumps(m,indent=2,sort_keys=True)+"\n",encoding="utf-8"); return m
