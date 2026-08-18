"""Round 10 mechanical outcome reveal over sealed target ledgers."""
from __future__ import annotations

import json
from pathlib import Path
import tomllib

import numpy as np
import pandas as pd

from momentum_reversal.backtest import BaselineBacktester
from momentum_reversal.data import CorporateActionLedger
from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.round2_market import sha256_file
from momentum_reversal.pipelines.round9_experiments import simulate_union_event_book
from momentum_reversal.pipelines.round10_experiments import PROGRAM_ID, Round10BatchResult, _file_records


def simulate_frozen_target_book(*,engine,target_ledger,risk_free_daily,start,end,cost_bps):
    ledger=target_ledger.copy(); ledger["execution_date"]=pd.to_datetime(ledger.execution_date).dt.normalize(); ledger=ledger[(ledger.execution_date>=start)&(ledger.execution_date<=end)]
    target_map={pd.Timestamp(d):g.set_index("sid").target_weight.astype(float).sort_index() for d,g in ledger.groupby("execution_date",sort=True)}
    sessions=engine.sessions[(engine.sessions>=start)&(engine.sessions<=end)]; rf=pd.to_numeric(risk_free_daily,errors="coerce").reindex(sessions)
    if rf.isna().any() or not target_map: raise DataQualityError("Round10 reveal calendar/RF incomplete")
    shares=pd.Series(dtype=float); cash=float(engine.initial_capital); previous=float(engine.initial_capital); rate=float(cost_bps)/10000; nav_rows=[]; event_rows=[]
    for date in sessions:
        date=pd.Timestamp(date); shares,cash,_=engine._apply_corporate_actions(date=date,shares=shares,cash=cash)
        if date in target_map:
            targets=target_map[date]; selected_open=engine._price_vector(date,pd.Index(targets.index),"tr_open",execution=True)
            if len(selected_open)!=len(targets): raise DataQualityError("Round10 sealed target open missing during reveal")
            existing=pd.Index(shares.index,dtype="object"); existing_open=engine._price_vector(date,existing,"tr_open",execution=False) if len(existing) else pd.Series(dtype=float); old=shares*existing_open if len(existing) else pd.Series(dtype=float); pre_nav=float(old.sum()+cash)
            union=existing.union(pd.Index(targets.index),sort=False); preweights=old.reindex(union,fill_value=0)/pre_nav; targetweights=targets.reindex(union,fill_value=0); l1=float((targetweights-preweights).abs().sum()); cost=pre_nav*rate*l1; post=pre_nav-cost; shares=(post*targets/selected_open).astype(float); cash=post*(1-float(targets.sum()))
            event_rows.append({"execution_date":date,"pretrade_nav":pre_nav,"target_long_exposure":float(targets.sum()),"l1_turnover":l1,"cost_bps":float(cost_bps),"cost_amount":cost,"selected_count":len(targets)})
        close=engine._price_vector(date,pd.Index(shares.index),"tr_close",execution=False); cash*=1+float(rf.loc[date]); long_value=float((shares*close).sum()); nav=long_value+cash; nav_rows.append({"date":date,"nav":nav,"daily_return":nav/previous-1,"long_value":long_value,"cash_value":cash,"long_exposure":long_value/nav,"cash_weight":cash/nav,"rf_return":float(rf.loc[date])}); previous=nav
    return {"nav":pd.DataFrame(nav_rows),"events":pd.DataFrame(event_rows)}


def run_r10c(*,project_root,runtime_root,run_id):
    root,runtime,program,paths=_load_inputs(project_root,runtime_root)
    if run_id!=program["run_id"]: raise DataQualityError("Round10 reveal run-id mismatch")
    output=runtime/"results/experiments/round10/R10C_OUTCOME_REVEAL/runs"/run_id; output.mkdir(parents=True,exist_ok=False)
    prices=pd.read_parquet(paths["dataset"]/"prices_daily.parquet"); corp=CorporateActionLedger(pd.read_parquet(paths["dataset"]/"corporate_actions.parquet")); calendar=pd.read_parquet(paths["dataset"]/"calendar.parquet"); sessions=pd.DatetimeIndex(pd.to_datetime(calendar.session_date)).normalize(); rf_table=pd.read_parquet(paths["dataset"]/"risk_free_daily.parquet"); rf_table["date"]=pd.to_datetime(rf_table.date).dt.normalize(); rf=rf_table.set_index("date").rf_return.astype(float)
    engine=BaselineBacktester(prices,object(),sessions=sessions,corporate_actions=corp,missing_valuation_policy="carry_last_close",missing_execution_policy="leave_cash")
    targets=pd.read_parquet(paths["r10b"]/"sealed_target_ledger.parquet"); states=pd.read_parquet(paths["r10b"]/"p00_states_weekly.parquet"); states["execution_session"]=pd.to_datetime(states.execution_session).dt.normalize(); overlay=states[states.execution_session.ge(pd.Timestamp(program["sample"]["first_execution"]))].set_index("execution_session").target_allocation.astype(float)
    holdings=pd.read_parquet(paths["g00"]/"artifacts/holdings.parquet"); g00_nav=pd.read_parquet(paths["g00"]/"artifacts/nav.parquet"); g00_nav["date"]=pd.to_datetime(g00_nav.date).dt.normalize(); registry=pd.read_csv(root/program["parents"]["transfer_registry"]); static=pd.read_csv(root/program["parents"]["round9_static_allocations_path"]).set_index("transfer_id"); start,end=pd.Timestamp(program["sample"]["first_execution"]),pd.Timestamp(program["sample"]["last_nav_date"])
    navs=[]; events=[]; identity=[]
    for spec in registry.itertuples(index=False):
        base=holdings[holdings.strategy_id.eq(spec.g00_strategy_id)][["execution_date","sid","target_weight"]]; static_allocation=float(static.loc[spec.transfer_id.replace("R10__","R9__"),"static_allocation"])
        for cost in program["economics"]["cost_bps"]:
            runs={"p00_overlay":simulate_frozen_target_book(engine=engine,target_ledger=targets[targets.transfer_id.eq(spec.transfer_id)],risk_free_daily=rf,start=start,end=end,cost_bps=cost),"naked":simulate_union_event_book(engine=engine,base_targets=base,overlay_schedule=overlay,risk_free_daily=rf,start=start,end=end,cost_bps=cost,path_type="naked",full_audit=False),"matched_static":simulate_union_event_book(engine=engine,base_targets=base,overlay_schedule=overlay,risk_free_daily=rf,start=start,end=end,cost_bps=cost,path_type="matched_static",static_allocation=static_allocation,full_audit=False)}
            for path_type,result in runs.items():
                n=result["nav"].copy(); n.insert(0,"transfer_id",spec.transfer_id); n.insert(1,"path_type",path_type); n.insert(2,"cost_bps",float(cost)); navs.append(n); e=result["events"].copy(); e.insert(0,"transfer_id",spec.transfer_id); e.insert(1,"path_type",path_type); e["cost_bps"]=float(cost); events.append(e)
            naked=runs["naked"]["nav"].sort_values("date"); frozen=g00_nav[(g00_nav.strategy_id.eq(spec.g00_strategy_id))&g00_nav.cost_bps.eq(float(cost))&(g00_nav.date>=start)&(g00_nav.date<=end)].sort_values("date")
            scaled=frozen.nav.to_numpy(float)/float(frozen.nav.iloc[0])*float(naked.nav.iloc[0]); nav_error=float(np.max(np.abs(naked.nav.to_numpy(float)-scaled))); ret_error=float(np.max(np.abs(naked.daily_return.to_numpy(float)[1:]-frozen.daily_return.to_numpy(float)[1:])))
            identity.append({"transfer_id":spec.transfer_id,"cost_bps":float(cost),"maximum_daily_return_error_after_first_day":ret_error,"normalized_nav_bridge_error":nav_error,"identity_passed":ret_error<=1e-12 and nav_error<=1e-11})
            if not identity[-1]["identity_passed"]: raise DataQualityError("Round10 naked/G00 bridge identity failed")
    nav=pd.concat(navs,ignore_index=True); event=pd.concat(events,ignore_index=True); nav.to_parquet(output/"nav_daily.parquet",index=False,compression="zstd"); event.to_parquet(output/"event_ledger.parquet",index=False,compression="zstd"); pd.DataFrame(identity).to_csv(output/"g00_identity_audit.csv",index=False,lineterminator="\n")
    metrics=[]
    for keys,part in nav.groupby(["transfer_id","path_type","cost_bps"],sort=True):
        tid,path_type,cost=keys; perf=_performance(part); ev=event[(event.transfer_id.eq(tid))&event.path_type.eq(path_type)&event.cost_bps.eq(cost)]; metrics.append({"transfer_id":tid,"path_type":path_type,"cost_bps":cost,**perf,"cumulative_l1_turnover":float(ev.l1_turnover.sum()),"mean_actual_long_exposure":float(part.long_exposure.mean())})
    metrics=pd.DataFrame(metrics); metrics.to_csv(output/"path_metrics.csv",index=False,lineterminator="\n"); comp=_comparisons(metrics).merge(registry[["transfer_id","frequency","primary"]],on="transfer_id",validate="many_to_one"); comp.to_csv(output/"transfer_comparisons.csv",index=False,lineterminator="\n")
    primary_id=program["economics"]["primary_transfer_id"]; primary_cost=float(program["economics"]["primary_cost_bps"]); active=_active(nav,primary_id,primary_cost); weekly=active.resample("W-FRI").sum(); lower,pvalue=_block_mean(weekly.to_numpy(float),int(program["inference"]["block_weeks"]),int(program["inference"]["bootstrap_repetitions"]),int(program["inference"]["seed"])); leave=[]
    for year in sorted(active.index.year.unique()):
        kept=active.loc[active.index.year.ne(year)]; leave.append({"removed_year":int(year),"removed_days":int(active.index.year.eq(year).sum()),"timing_value_without_year":float(np.exp(kept.sum())-1)})
    leave=pd.DataFrame(leave); leave.to_csv(output/"leave_one_year_out.csv",index=False,lineterminator="\n"); c10=comp[comp.cost_bps.eq(primary_cost)].copy(); p_row=c10[c10.transfer_id.eq(primary_id)].iloc[0]; passed=int(c10.four_metric_gate.sum()); weekly_passed=int(c10.loc[c10.frequency.eq("weekly"),"four_metric_gate"].sum()); monthly_passed=int(c10.loc[c10.frequency.eq("monthly"),"four_metric_gate"].sum()); medians={"overlay_to_naked_terminal_increment":float((c10.overlay_to_naked_terminal_ratio-1).median()),"timing_value_vs_static":float(c10.timing_value_vs_static.median()),"delta_sharpe_vs_naked":float(c10.delta_sharpe_vs_naked.median()),"delta_mdd_vs_naked":float(c10.delta_mdd_vs_naked.median())}; gates=program["gates"]
    primary_four=bool(p_row.four_metric_gate); inference=lower>float(gates["primary_block_lower_gt"]) and pvalue<=float(gates["primary_p_le"]); family=passed>=int(gates["family_minimum_passed"]) and weekly_passed>=int(gates["family_minimum_weekly_passed"]) and monthly_passed>=int(gates["family_minimum_monthly_passed"]) and all(v>float(gates["family_all_metric_medians_gt"]) for v in medians.values()); cost20=bool(comp[comp.cost_bps.eq(20)].timing_value_vs_static.gt(0).all()); leave_gate=bool(leave.timing_value_without_year.gt(float(gates["primary_minimum_leave_year_timing_gt"])).all()); passed_all=primary_four and inference and family and cost20 and leave_gate
    assessment={"primary_transfer_id":primary_id,"primary_four_metric_gate":primary_four,"primary_block13_95_lower":lower,"primary_one_sided_p":pvalue,"passed_cells":passed,"weekly_passed":weekly_passed,"monthly_passed":monthly_passed,"family_medians":medians,"family_gate":family,"cost20_direction_gate":cost20,"leave_one_year_gate":leave_gate,"mechanical_lockbox_passed":passed_all}; (output/"assessment.json").write_text(json.dumps(assessment,indent=2,sort_keys=True)+"\n",encoding="utf-8"); decision={"program_id":PROGRAM_ID,"status":"completed_mechanical_lockbox","mechanical_lockbox_passed":passed_all,"candidate":"P00_RSP_Y5_CLEAR__MOM255_TOP20_MONTHLY_LONG_ONLY","formal_eligible":False,"target_revision":False,"automatic_revision":False}; (output/"decision.json").write_text(json.dumps(decision,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    manifest={"schema_version":1,"program_id":PROGRAM_ID,"batch_id":"R10C_OUTCOME_REVEAL","run_id":run_id,"status":"completed_mechanical_lockbox","formal_eligible":False,"outcome_reveal_run":True,"strategy_nav_run":True,"sealed_target_sha256":program["sealed"]["targets_sha256"],"outcome_reveal_lock_sha256":sha256_file(root/"config/experiments/round10/OUTCOME_REVEAL_LOCK.json"),"counts":{"nav_rows":len(nav),"scenarios":72,"passed_cells":passed},"files":_file_records(output)}; (output/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8"); return Round10BatchResult(output,output/"manifest.json",manifest["status"])


def _performance(frame):
    x=frame.sort_values("date"); nav=x.nav.astype(float); ret=x.daily_return.astype(float); years=len(x)/252; std=float(ret.std(ddof=1)); return {"terminal":float(nav.iloc[-1]),"cagr":float(nav.iloc[-1]**(1/years)-1),"sharpe":float(ret.mean()/std*np.sqrt(252)) if std>0 else np.nan,"mdd":float((nav/nav.cummax()-1).min())}

def _comparisons(metrics):
    w=metrics.pivot(index=["transfer_id","cost_bps"],columns="path_type",values=["terminal","cagr","sharpe","mdd","cumulative_l1_turnover","mean_actual_long_exposure"]).reset_index(); w.columns=["_".join(filter(None,map(str,c))).rstrip("_") if isinstance(c,tuple) else c for c in w.columns]; out=pd.DataFrame({"transfer_id":w.transfer_id,"cost_bps":w.cost_bps}); out["overlay_to_naked_terminal_ratio"]=w.terminal_p00_overlay/w.terminal_naked; out["timing_value_vs_static"]=w.terminal_p00_overlay/w.terminal_matched_static-1; out["delta_sharpe_vs_naked"]=w.sharpe_p00_overlay-w.sharpe_naked; out["delta_mdd_vs_naked"]=w.mdd_p00_overlay-w.mdd_naked; out["overlay_terminal"]=w.terminal_p00_overlay; out["naked_terminal"]=w.terminal_naked; out["static_terminal"]=w.terminal_matched_static; out["overlay_cagr"]=w.cagr_p00_overlay; out["naked_cagr"]=w.cagr_naked; out["overlay_sharpe"]=w.sharpe_p00_overlay; out["naked_sharpe"]=w.sharpe_naked; out["overlay_mdd"]=w.mdd_p00_overlay; out["naked_mdd"]=w.mdd_naked; out["overlay_turnover"]=w.cumulative_l1_turnover_p00_overlay; out["naked_turnover"]=w.cumulative_l1_turnover_naked; out["overlay_mean_actual_long_exposure"]=w.mean_actual_long_exposure_p00_overlay; out["static_mean_actual_long_exposure"]=w.mean_actual_long_exposure_matched_static; out["four_metric_gate"]=out.overlay_to_naked_terminal_ratio.gt(1)&out.timing_value_vs_static.gt(0)&out.delta_sharpe_vs_naked.gt(0)&out.delta_mdd_vs_naked.gt(0); return out

def _active(nav,tid,cost):
    o=nav[(nav.transfer_id.eq(tid))&nav.path_type.eq("p00_overlay")&nav.cost_bps.eq(cost)].set_index("date").nav.sort_index(); s=nav[(nav.transfer_id.eq(tid))&nav.path_type.eq("matched_static")&nav.cost_bps.eq(cost)].set_index("date").nav.sort_index(); ratio=o/s; return np.log(ratio).diff().fillna(np.log(ratio.iloc[0]))

def _block_mean(x,block,reps,seed):
    rng=np.random.default_rng(seed); starts=np.arange(len(x)-block+1); est=np.empty(reps)
    for i in range(reps):
        ids=[]
        while len(ids)<len(x): s=int(rng.choice(starts)); ids.extend(range(s,s+block))
        est[i]=np.mean(x[np.asarray(ids[:len(x)])])
    return float(np.quantile(est,.05)),float(np.mean(est<=0))

def _load_inputs(project_root,runtime_root):
    root,runtime=Path(project_root).resolve(),Path(runtime_root).resolve(); lock=json.loads((root/"config/experiments/round10/OUTCOME_REVEAL_LOCK.json").read_text(encoding="utf-8"))
    for rel,expected in lock["files"].items():
        if sha256_file(root/rel)!=expected: raise DataQualityError(f"Round10 reveal lock member drift: {rel}")
    p=tomllib.loads((root/"config/experiments/round10/outcome_program.toml").read_text(encoding="utf-8")); a=p["authorization"]
    if not a["outcome_reveal_phase"] or not a["strategy_nav"] or a["model_or_policy_revision"] or a["target_revision"]: raise DataQualityError("Round10 reveal authorization failed")
    r10b=runtime/"results/experiments/round10/R10B_SEALED_TARGETS/runs"/p["sealed"]["r10b_run_id"]; g00=runtime/"results/experiments/G00/runs"/p["parents"]["g00_run_id"]; dataset=runtime/"data/curated"/p["parents"]["dataset_version"]
    checks={r10b/"manifest.json":p["sealed"]["r10b_manifest_sha256"],r10b/"p00_states_weekly.parquet":p["sealed"]["states_sha256"],r10b/"sealed_target_ledger.parquet":p["sealed"]["targets_sha256"],r10b/"target_event_audit.csv":p["sealed"]["event_audit_sha256"],r10b/"signal_identity.csv":p["sealed"]["signal_identity_sha256"],g00/"manifest.json":p["parents"]["g00_manifest_sha256"],g00/"artifacts/holdings.parquet":p["parents"]["g00_holdings_sha256"],g00/"artifacts/nav.parquet":p["parents"]["g00_nav_sha256"],dataset/"prices_daily.parquet":p["parents"]["prices_sha256"],dataset/"corporate_actions.parquet":p["parents"]["corporate_actions_sha256"],dataset/"risk_free_daily.parquet":p["parents"]["risk_free_sha256"],dataset/"calendar.parquet":p["parents"]["calendar_sha256"],root/p["parents"]["round9_static_allocations_path"]:p["parents"]["round9_static_allocations_sha256"],root/"src/momentum_reversal/pipelines/round9_experiments.py":p["parents"]["round9_union_runner_sha256"]}
    for path,expected in checks.items():
        if sha256_file(path)!=expected: raise DataQualityError(f"Round10 reveal parent drift: {path}")
    return root,runtime,p,{"r10b":r10b,"g00":g00,"dataset":dataset}
