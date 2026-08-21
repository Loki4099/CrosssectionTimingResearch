"""XA05 final MOM12-7 and frozen-P00 system assessment."""
from __future__ import annotations
import hashlib,json,math,platform,subprocess,tomllib
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from momentum_reversal.backtest import BaselineBacktester
from momentum_reversal.data.corporate_actions import CorporateActionLedger
from momentum_reversal.data.membership import PITMembership
from momentum_reversal.pipelines.cross_sectional_database import DatabaseLayout
from momentum_reversal.pipelines.round9_experiments import simulate_union_event_book

PROGRAM=Path("config/experiments/xa05/program.toml");LOCK=Path("config/experiments/xa05/PREREG_LOCK.json")
RUN_IDS={"XA05A":"xa05a-frozen-targets-20260821-v1","XA05B":"xa05b-union-event-replay-20260821-v1","XA05C":"xa05c-drawdown-report-20260821-v1"}

def run_xa05(project_root:str|Path,runtime_root:str|Path,batch:str)->dict[str,Any]:
    project=Path(project_root).resolve();runtime=Path(runtime_root).resolve();batch=batch.upper()
    if batch not in RUN_IDS:raise ValueError(batch)
    p=_program(project);_verify_lock(project);commit=_clean_commit(project);deps=_dependencies(runtime,batch);root=_root(runtime,batch)
    if root.exists():raise FileExistsError(root)
    root.mkdir(parents=True)
    try:
        out={"XA05A":_run_a,"XA05B":_run_b,"XA05C":_run_c}[batch](project,runtime,root,p)
        _json(root/"summary.json",out);_manifest(project,root,batch,commit,deps);return out
    except Exception as exc:_json(root/"FAILED.json",{"type":type(exc).__name__,"message":str(exc)});raise

def _run_a(project:Path,runtime:Path,root:Path,p:dict)->dict:
    paths=_parents(project,runtime,p)
    checks={paths["holdings"]:p["parent"]["xa04_holdings_sha256"],paths["target"]:p["parent"]["xa04_target_sha256"],paths["decision"]:p["parent"]["xa04_decision_sha256"],paths["r8a"]:p["parent"]["r8a_states_sha256"],paths["r10b"]:p["parent"]["r10b_states_sha256"],project/"src/momentum_reversal/pipelines/round9_experiments.py":p["parent"]["union_engine_sha256"]}
    for path,expected in checks.items():
        if not path.is_file() or _sha(path)!=expected:raise ValueError(f"parent drift: {path}")
    decision=json.loads(paths["decision"].read_text(encoding="utf-8"))
    if decision.get("next_branch")!="RAW_XS003_ONLY" or decision.get("qualified_tree_cells")!=0:raise ValueError("XA04 branch drift")
    holdings=pd.read_parquet(paths["holdings"]);holdings=holdings.loc[holdings.process_id.eq(p["base"]["process_id"])].copy()
    target=pd.read_parquet(paths["target"]);mapping=target[["frequency","signal_date","execution_date"]].drop_duplicates()
    holdings=holdings.merge(mapping,on=["frequency","signal_date"],validate="many_to_one").rename(columns={"weight":"target_weight"})
    holdings["transfer_id"]=holdings.frequency.str.upper()+"_TOP"+holdings.top_k.astype(str)
    base=holdings[["transfer_id","frequency","top_k","signal_date","execution_date","sid","target_weight"]].sort_values(["transfer_id","execution_date","sid"])
    if base.duplicated(["transfer_id","execution_date","sid"]).any():raise ValueError("duplicate base target")
    a=pd.read_parquet(paths["r8a"]);a=a.loc[a.policy_id.eq(p["p00"]["policy_id"]),["week_id","signal_session","execution_session","risk_score","risk_threshold_q75","state","target_spy_weight"]].rename(columns={"risk_threshold_q75":"threshold_q75","target_spy_weight":"target_allocation"})
    b=pd.read_parquet(paths["r10b"])[["week_id","signal_session","execution_session","risk_score","threshold_q75","state","target_allocation"]]
    a["source"]="R8A";b["source"]="R10B";states=pd.concat([a,b],ignore_index=True)
    for col in ("signal_session","execution_session"):states[col]=pd.to_datetime(states[col]).dt.normalize()
    states=states.loc[states.execution_session.between(pd.Timestamp(p["sample"]["first_execution"]),pd.Timestamp(p["sample"]["last_valuation"]))].sort_values("execution_session",ignore_index=True)
    if states.execution_session.duplicated().any() or states.iloc[0].execution_session!=pd.Timestamp(p["sample"]["first_execution"]):raise ValueError("P00 stitch calendar failed")
    if not set(states.target_allocation.unique()).issubset({.5,1.0}):raise ValueError("P00 allocation drift")
    engine,rf=_engine(project,runtime,p);overlay=states.set_index("execution_session").target_allocation.astype(float);start=pd.Timestamp(p["sample"]["first_execution"]);end=pd.Timestamp(p["sample"]["last_valuation"])
    allocations=[]
    for transfer,part in base.groupby("transfer_id",sort=True):
        dynamic=simulate_union_event_book(engine=engine,base_targets=part,overlay_schedule=overlay,risk_free_daily=rf,start=start,end=end,cost_bps=0,path_type="p00_overlay",full_audit=False)
        wanted=float(dynamic["nav"].long_exposure.mean());lo,hi=0.0,1.0
        for _ in range(int(p["paths_under_test"]["matched_static_iterations"])):
            mid=(lo+hi)/2;trial=simulate_union_event_book(engine=engine,base_targets=part,overlay_schedule=overlay,risk_free_daily=rf,start=start,end=end,cost_bps=0,path_type="matched_static",static_allocation=mid,full_audit=False)
            if float(trial["nav"].long_exposure.mean())<wanted:lo=mid
            else:hi=mid
        allocation=(lo+hi)/2;check=simulate_union_event_book(engine=engine,base_targets=part,overlay_schedule=overlay,risk_free_daily=rf,start=start,end=end,cost_bps=0,path_type="matched_static",static_allocation=allocation,full_audit=False)
        actual=float(check["nav"].long_exposure.mean());err=abs(actual-wanted)
        if err>float(p["paths_under_test"]["matched_static_tolerance"]):raise ValueError("static exposure match failed")
        allocations.append({"transfer_id":transfer,"frequency":part.frequency.iloc[0],"top_k":int(part.top_k.iloc[0]),"p00_mean_actual_exposure_0bps":wanted,"static_allocation":allocation,"static_mean_actual_exposure_0bps":actual,"absolute_error":err})
    _parquet(root/"base_target_ledger.parquet",base);_parquet(root/"p00_state_spine.parquet",states);_csv(root/"matched_static_allocations.csv",pd.DataFrame(allocations))
    acceptance={"schema_version":"xa05.target_acceptance.v1","base_target_sha256":_sha(root/"base_target_ledger.parquet"),"p00_state_sha256":_sha(root/"p00_state_spine.parquet"),"static_allocations_sha256":_sha(root/"matched_static_allocations.csv"),"transfer_cells":8,"outcomes_read":False}
    _json(root/"TARGET_ACCEPTANCE.json",acceptance)
    return {"batch":"XA05A","status":"completed_targets_frozen","transfer_cells":8,"base_rows":len(base),"state_rows":len(states),"defense_weeks":int(states.state.eq("DEFENSE").sum()),"target_acceptance":acceptance}

def _run_b(project:Path,runtime:Path,root:Path,p:dict)->dict:
    a=_root(runtime,"XA05A");base=pd.read_parquet(a/"base_target_ledger.parquet");states=pd.read_parquet(a/"p00_state_spine.parquet");alloc=pd.read_csv(a/"matched_static_allocations.csv").set_index("transfer_id")
    overlay=states.set_index("execution_session").target_allocation.astype(float);engine,rf=_engine(project,runtime,p);start=pd.Timestamp(p["sample"]["first_execution"]);end=pd.Timestamp(p["sample"]["last_valuation"])
    navs=[];events=[]
    for transfer,part in base.groupby("transfer_id",sort=True):
        for cost in p["sample"]["cost_bps"]:
            for path_type in p["paths_under_test"]["path_types"]:
                result=simulate_union_event_book(engine=engine,base_targets=part,overlay_schedule=overlay,risk_free_daily=rf,start=start,end=end,cost_bps=float(cost),path_type=path_type,static_allocation=float(alloc.loc[transfer,"static_allocation"]) if path_type=="matched_static" else None,full_audit=False)
                for name,collection in (("nav",navs),("events",events)):
                    frame=result[name].copy();frame.insert(0,"transfer_id",transfer);frame.insert(1,"frequency",part.frequency.iloc[0]);frame.insert(2,"top_k",int(part.top_k.iloc[0]));frame.insert(3,"path_type",path_type)
                    if "cost_bps" not in frame:frame.insert(4,"cost_bps",float(cost))
                    else:frame["cost_bps"]=float(cost)
                    collection.append(frame)
    nav=pd.concat(navs,ignore_index=True);event=pd.concat(events,ignore_index=True)
    metrics=[];episodes=[];rolling=[];annual=[]
    for key,part in nav.groupby(["transfer_id","frequency","top_k","path_type","cost_bps"],sort=True):
        perf,eps,roll,year=_performance_bundle(part.sort_values("date"),p)
        ev=event.loc[(event.transfer_id==key[0])&(event.path_type==key[3])&(event.cost_bps==key[4])]
        metrics.append(dict(zip(["transfer_id","frequency","top_k","path_type","cost_bps"],key),**perf,cumulative_l1_turnover=float(ev.l1_turnover.sum()),event_count=len(ev)))
        for frame,collection in ((eps,episodes),(roll,rolling),(year,annual)):
            frame.insert(0,"cost_bps",key[4]);frame.insert(0,"path_type",key[3]);frame.insert(0,"top_k",key[2]);frame.insert(0,"frequency",key[1]);frame.insert(0,"transfer_id",key[0]);collection.append(frame)
    metrics=pd.DataFrame(metrics);comparisons=_comparisons(metrics);_parquet(root/"nav_daily.parquet",nav);_parquet(root/"event_ledger.parquet",event);_csv(root/"path_metrics.csv",metrics);_csv(root/"path_comparisons.csv",comparisons);_csv(root/"drawdown_episodes.csv",pd.concat(episodes,ignore_index=True));_parquet(root/"rolling_drawdown.parquet",pd.concat(rolling,ignore_index=True));_csv(root/"annual_returns.csv",pd.concat(annual,ignore_index=True))
    return {"batch":"XA05B","status":"completed","economic_paths":len(metrics),"nav_rows":len(nav),"event_rows":len(event),"drawdown_episodes":sum(len(x) for x in episodes),"primary_four_metric_gate":bool(comparisons.loc[(comparisons.frequency==p["sample"]["primary_frequency"])&(comparisons.top_k==p["sample"]["primary_top_k"])&(comparisons.cost_bps==p["sample"]["primary_cost_bps"]),"four_metric_gate"].iloc[0])}

def _performance_bundle(frame:pd.DataFrame,p:dict)->tuple[dict,pd.DataFrame,pd.DataFrame,pd.DataFrame]:
    nav=frame.set_index("date").nav.astype(float);ret=frame.set_index("date").daily_return.astype(float);dd=nav/nav.cummax()-1;years=len(nav)/252;cagr=float((nav.iloc[-1]/nav.iloc[0])**(1/years)-1);vol=float(ret.std(ddof=1)*np.sqrt(252));down=float(ret.clip(upper=0).std(ddof=1)*np.sqrt(252));mdd=float(dd.min());eps=_episodes(nav)
    maxdur=int(eps.duration_sessions.max()) if len(eps) else 0;maxrec=int(eps.recovery_sessions.max()) if len(eps) else 0;q=float(dd.quantile(.05));tail=dd[dd<=q]
    weekly=(1+ret).resample("W-FRI").prod()-1;monthly=(1+ret).resample("ME").prod()-1
    perf={"observations":len(nav),"terminal":float(nav.iloc[-1]),"cagr":cagr,"annualized_volatility":vol,"sharpe":float(ret.mean()/ret.std(ddof=1)*np.sqrt(252)),"sortino":float(ret.mean()*252/down) if down>0 else np.nan,"calmar":cagr/abs(mdd) if mdd<0 else np.nan,"maximum_drawdown":mdd,"maximum_drawdown_duration_sessions":maxdur,"maximum_recovery_sessions":maxrec,"underwater_fraction":float((dd<0).mean()),"average_drawdown":float(dd[dd<0].mean()),"pain_index":float(-dd.mean()),"ulcer_index":float(np.sqrt(np.mean(np.square(dd)))),"cdar_95":float(-tail.mean()),"daily_var_95":float(ret.quantile(.05)),"daily_expected_shortfall_95":float(ret[ret<=ret.quantile(.05)].mean()),"worst_day":float(ret.min()),"worst_week":float(weekly.min()),"worst_month":float(monthly.min()),"skew":float(ret.skew()),"excess_kurtosis":float(ret.kurt()),"mean_long_exposure":float(frame.long_exposure.mean()),"mdd_peak_date":str(eps.sort_values("depth").iloc[0].peak_date.date()) if len(eps) else "","mdd_trough_date":str(eps.sort_values("depth").iloc[0].trough_date.date()) if len(eps) else "","mdd_recovery_date":str(eps.sort_values("depth").iloc[0].recovery_date.date()) if len(eps) and pd.notna(eps.sort_values("depth").iloc[0].recovery_date) else ""}
    roll=pd.DataFrame({"date":nav.index,"underwater":dd.to_numpy()})
    for w in p["performance"]["rolling_windows_sessions"]:roll[f"drawdown_from_{w}d_peak"]=nav/nav.rolling(int(w),min_periods=1).max()-1
    annual=(1+ret).groupby(ret.index.year).prod().sub(1).rename("return").reset_index().rename(columns={"date":"year"});annual.columns=["year","return"]
    return perf,eps.sort_values("depth").head(int(p["performance"]["drawdown_episode_limit"])),roll,annual

def _episodes(nav:pd.Series)->pd.DataFrame:
    dd=nav/nav.cummax()-1;dates=list(nav.index);rows=[];i=0
    while i<len(dd):
        if dd.iloc[i]>=-1e-15:i+=1;continue
        peak_i=max(i-1,0);j=i
        while j<len(dd) and dd.iloc[j]<-1e-15:j+=1
        end_i=j if j<len(dd) else len(dd)-1;segment=dd.iloc[i:j if j<len(dd) else len(dd)];trough_label=segment.idxmin();trough_i=nav.index.get_loc(trough_label);recovered=j<len(dd)
        rows.append({"peak_date":dates[peak_i],"trough_date":dates[trough_i],"recovery_date":dates[j] if recovered else pd.NaT,"depth":float(segment.min()),"decline_sessions":trough_i-peak_i,"recovery_sessions":j-trough_i if recovered else len(dd)-1-trough_i,"duration_sessions":j-peak_i if recovered else len(dd)-1-peak_i,"recovered":recovered})
        i=j+1 if recovered else len(dd)
    return pd.DataFrame(rows,columns=["peak_date","trough_date","recovery_date","depth","decline_sessions","recovery_sessions","duration_sessions","recovered"])

def _comparisons(metrics:pd.DataFrame)->pd.DataFrame:
    cols=["terminal","cagr","annualized_volatility","sharpe","sortino","calmar","maximum_drawdown","ulcer_index","pain_index","cdar_95","maximum_drawdown_duration_sessions","maximum_recovery_sessions","cumulative_l1_turnover","mean_long_exposure"]
    w=metrics.pivot(index=["transfer_id","frequency","top_k","cost_bps"],columns="path_type",values=cols).reset_index();w.columns=["_".join(filter(None,map(str,c))) if isinstance(c,tuple) else c for c in w.columns]
    out=w[["transfer_id","frequency","top_k","cost_bps"]].copy();out["overlay_to_naked_terminal_ratio"]=w.terminal_p00_overlay/w.terminal_naked;out["timing_value_vs_static"]=w.terminal_p00_overlay/w.terminal_matched_static-1;out["delta_sharpe_vs_naked"]=w.sharpe_p00_overlay-w.sharpe_naked;out["delta_mdd_vs_naked"]=w.maximum_drawdown_p00_overlay-w.maximum_drawdown_naked;out["delta_ulcer_vs_naked"]=w.ulcer_index_p00_overlay-w.ulcer_index_naked;out["delta_pain_vs_naked"]=w.pain_index_p00_overlay-w.pain_index_naked;out["delta_cdar95_vs_naked"]=w.cdar_95_p00_overlay-w.cdar_95_naked;out["delta_max_duration_vs_naked"]=w.maximum_drawdown_duration_sessions_p00_overlay-w.maximum_drawdown_duration_sessions_naked;out["four_metric_gate"]=(out.overlay_to_naked_terminal_ratio>1)&(out.timing_value_vs_static>0)&(out.delta_sharpe_vs_naked>0)&(out.delta_mdd_vs_naked>0);return out

def _run_c(project:Path,runtime:Path,root:Path,p:dict)->dict:
    import matplotlib;matplotlib.use("Agg");import matplotlib.pyplot as plt
    b=_root(runtime,"XA05B");nav=pd.read_parquet(b/"nav_daily.parquet");metrics=pd.read_csv(b/"path_metrics.csv");comp=pd.read_csv(b/"path_comparisons.csv");episodes=pd.read_csv(b/"drawdown_episodes.csv",parse_dates=["peak_date","trough_date","recovery_date"]);rolling=pd.read_parquet(b/"rolling_drawdown.parquet");annual=pd.read_csv(b/"annual_returns.csv");states=pd.read_parquet(_root(runtime,"XA05A")/"p00_state_spine.parquet")
    figures=root/"figures";figures.mkdir();dpi=int(p["charts"]["dpi"])
    for frequency,cost in (("monthly",5),("weekly",10)):
        cell=nav[(nav.frequency==frequency)&(nav.top_k==20)&(nav.cost_bps==cost)].copy();_plot_nav(cell,figures/f"{frequency}_top20_nav.png",dpi);_plot_underwater(cell,figures/f"{frequency}_top20_underwater.png",dpi)
        rr=rolling[(rolling.frequency==frequency)&(rolling.top_k==20)&(rolling.cost_bps==cost)];_plot_rolling(rr,figures/f"{frequency}_top20_rolling_drawdown.png",dpi)
    _plot_exposure(nav,states,figures/"monthly_top20_p00_exposure_state.png",dpi);_plot_annual(annual,figures/"monthly_top20_annual_returns.png",dpi);_plot_episodes(episodes,figures/"monthly_top20_drawdown_episodes.png",dpi);_plot_heatmaps(comp,figures/"cross_cell_robustness_heatmaps.png",dpi)
    primary=comp[(comp.frequency==p["sample"]["primary_frequency"])&(comp.top_k==p["sample"]["primary_top_k"])&(comp.cost_bps==p["sample"]["primary_cost_bps"])].iloc[0]
    at_primary=comp[((comp.frequency=="monthly")&(comp.cost_bps==5))|((comp.frequency=="weekly")&(comp.cost_bps==10))]
    family_pass=int(at_primary.four_metric_gate.sum());weekly=int(at_primary.loc[at_primary.frequency=="weekly","four_metric_gate"].sum());monthly=int(at_primary.loc[at_primary.frequency=="monthly","four_metric_gate"].sum())
    cost20=comp[comp.cost_bps==20].timing_value_vs_static.gt(0).all();primary_pass=bool(primary.four_metric_gate);family=family_pass>=5 and weekly>=2 and monthly>=2;overall=primary_pass and family and bool(cost20)
    decision={"schema_version":"xa05.decision.v1","status":"completed_hard_stop","formal_eligible":False,"primary_four_metric_gate":primary_pass,"family_passed_cells":family_pass,"weekly_passed_cells":weekly,"monthly_passed_cells":monthly,"family_gate":family,"cost20_timing_gate":bool(cost20),"overall_historical_system_gate":overall,"automatic_deployment":False}
    _json(root/"decision.json",decision);_csv(root/"primary_comparison.csv",pd.DataFrame([primary]));_csv(root/"all_cell_comparisons.csv",comp);_csv(root/"all_path_metrics.csv",metrics)
    return {"batch":"XA05C","status":"completed_hard_stop","figures":len(list(figures.glob("*.png"))),**decision}

def _plot_nav(cell,out,dpi):
    import matplotlib.pyplot as plt;fig,ax=plt.subplots(figsize=(11,5));
    for name,g in cell.groupby("path_type"):ax.plot(g.date,g.nav/g.nav.iloc[0],label=name)
    ax.set_yscale("log");ax.set_title("MOM12-7: growth of $1");ax.grid(alpha=.25);ax.legend();fig.tight_layout();fig.savefig(out,dpi=dpi);plt.close(fig)
def _plot_underwater(cell,out,dpi):
    import matplotlib.pyplot as plt;fig,ax=plt.subplots(figsize=(11,5));
    for name,g in cell.groupby("path_type"):x=g.nav/g.nav.cummax()-1;ax.plot(g.date,x,label=name)
    ax.set_title("Underwater drawdown");ax.grid(alpha=.25);ax.legend();fig.tight_layout();fig.savefig(out,dpi=dpi);plt.close(fig)
def _plot_rolling(r,out,dpi):
    import matplotlib.pyplot as plt;fig,ax=plt.subplots(figsize=(11,5));
    for name,g in r.groupby("path_type"):ax.plot(g.date,g.drawdown_from_252d_peak,label=name)
    ax.set_title("Drawdown from rolling 252-session peak");ax.grid(alpha=.25);ax.legend();fig.tight_layout();fig.savefig(out,dpi=dpi);plt.close(fig)
def _plot_exposure(nav,states,out,dpi):
    import matplotlib.pyplot as plt;g=nav[(nav.frequency=="monthly")&(nav.top_k==20)&(nav.cost_bps==5)&(nav.path_type=="p00_overlay")];fig,ax=plt.subplots(figsize=(11,5));ax.plot(g.date,g.long_exposure,label="actual long exposure");ax.step(states.execution_session,states.target_allocation,where="post",alpha=.6,label="P00 target");ax.set_ylim(0,1.1);ax.set_title("P00 exposure and state");ax.grid(alpha=.25);ax.legend();fig.tight_layout();fig.savefig(out,dpi=dpi);plt.close(fig)
def _plot_annual(annual,out,dpi):
    import matplotlib.pyplot as plt;g=annual[(annual.frequency=="monthly")&(annual.top_k==20)&(annual.cost_bps==5)];w=g.pivot(index="year",columns="path_type",values="return");ax=w.plot.bar(figsize=(11,5));ax.set_title("Calendar-year returns");ax.grid(axis="y",alpha=.25);ax.figure.tight_layout();ax.figure.savefig(out,dpi=dpi);plt.close(ax.figure)
def _plot_episodes(eps,out,dpi):
    import matplotlib.pyplot as plt;g=eps[(eps.frequency=="monthly")&(eps.top_k==20)&(eps.cost_bps==5)];fig,ax=plt.subplots(figsize=(10,5));
    for name,x in g.groupby("path_type"):ax.scatter(x.duration_sessions,-x.depth,label=name,alpha=.75)
    ax.set_xlabel("duration sessions");ax.set_ylabel("drawdown depth");ax.set_title("Worst drawdown episodes");ax.grid(alpha=.25);ax.legend();fig.tight_layout();fig.savefig(out,dpi=dpi);plt.close(fig)
def _plot_heatmaps(comp,out,dpi):
    import matplotlib.pyplot as plt;metrics=["overlay_to_naked_terminal_ratio","timing_value_vs_static","delta_sharpe_vs_naked","delta_mdd_vs_naked"];fig,axes=plt.subplots(2,2,figsize=(12,8));
    for ax,m in zip(axes.flat,metrics):
        g=comp[((comp.frequency=="monthly")&(comp.cost_bps==5))|((comp.frequency=="weekly")&(comp.cost_bps==10))];w=g.pivot(index="frequency",columns="top_k",values=m);im=ax.imshow(w,cmap="RdYlGn",aspect="auto");ax.set_xticks(range(len(w.columns)),w.columns);ax.set_yticks(range(len(w.index)),w.index);ax.set_title(m);fig.colorbar(im,ax=ax,shrink=.75)
    fig.tight_layout();fig.savefig(out,dpi=dpi);plt.close(fig)

def _engine(project,runtime,p):
    layout=DatabaseLayout.load(project_root=project,runtime_root=runtime);m=layout.market_root;prices=pd.read_parquet(m/"prices_daily.parquet");membership=PITMembership.from_intervals(pd.read_parquet(m/"membership.parquet"));actions=CorporateActionLedger(pd.read_parquet(m/"corporate_actions.parquet"));sessions=pd.DatetimeIndex(pd.read_parquet(m/"calendar.parquet").session_date);rf=pd.read_parquet(m/"risk_free_daily.parquet").set_index("date").rf_return.astype(float)
    engine=BaselineBacktester(prices,membership,sessions=sessions,evaluation_start=pd.Timestamp(p["sample"]["first_execution"]),signal_end=pd.Timestamp(p["sample"]["last_valuation"]),corporate_actions=actions,missing_valuation_policy="carry_last_close",missing_execution_policy="leave_cash")
    return engine,rf
def _parents(project,runtime,p):
    q=p["paths"];return {"holdings":runtime/q["xa04_holdings"],"target":runtime/q["xa04_target"],"decision":runtime/q["xa04_decision"],"r8a":project/q["r8a_states"],"r10b":project/q["r10b_states"]}
def _program(project):
    with (project/PROGRAM).open("rb") as h:return tomllib.load(h)
def _root(runtime,batch):return runtime/"results"/"experiments"/"xa05"/batch/"runs"/RUN_IDS[batch]
def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _verify_lock(project):
    lock=json.loads((project/LOCK).read_text(encoding="utf-8"));
    for rel,item in lock["files"].items():
        path=project/rel
        if _sha(path)!=item["sha256"] or path.stat().st_size!=item["size_bytes"]:raise ValueError(f"lock drift {rel}")
def _clean_commit(project):
    if subprocess.run(["git","status","--porcelain"],cwd=project,capture_output=True,text=True,check=True).stdout.strip():raise ValueError("clean git required")
    return subprocess.run(["git","rev-parse","HEAD"],cwd=project,capture_output=True,text=True,check=True).stdout.strip()
def _dependencies(runtime,batch):
    deps={}
    for prior in list(RUN_IDS)[:list(RUN_IDS).index(batch)]:
        path=_root(runtime,prior)/"manifest.json";m=json.loads(path.read_text(encoding="utf-8"));_verify_manifest(path.parent,m);deps[prior]=_sha(path)
    return deps
def _json(path,payload):Path(path).write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n")
def _csv(path,frame):frame.to_csv(path,index=False,lineterminator="\n")
def _parquet(path,frame):frame.to_parquet(path,index=False)
def _manifest(project,root,batch,commit,deps):
    files={x.name:{"sha256":_sha(x),"size_bytes":x.stat().st_size} for x in sorted(root.iterdir()) if x.is_file() and x.name!="manifest.json"};_json(root/"manifest.json",{"schema_version":"xa05.runtime_manifest.v1","batch":batch,"run_id":RUN_IDS[batch],"git_commit":commit,"prereg_lock_sha256":_sha(project/LOCK),"dependencies":deps,"python":platform.python_version(),"files":files})
def _verify_manifest(root,m):
    actual={x.name for x in root.iterdir() if x.is_file() and x.name!="manifest.json"}
    if actual!=set(m["files"]):raise ValueError("manifest members")
    for name,item in m["files"].items():
        path=root/name
        if _sha(path)!=item["sha256"] or path.stat().st_size!=item["size_bytes"]:raise ValueError("manifest drift")
def audit_xa05(project_root,runtime_root):
    project=Path(project_root).resolve();runtime=Path(runtime_root).resolve();_verify_lock(project);man={}
    for b in RUN_IDS:
        r=_root(runtime,b);m=json.loads((r/"manifest.json").read_text(encoding="utf-8"));_verify_manifest(r,m);man[b]=_sha(r/"manifest.json")
    d=json.loads((_root(runtime,"XA05C")/"decision.json").read_text(encoding="utf-8"));
    if d["automatic_deployment"]:raise ValueError("unauthorized deployment")
    return {"status":"passed","manifests":man,"decision":d}
