import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, RefreshCw, ShieldCheck, UsersRound } from "lucide-react";
import { api, type Auth } from "./api";

type Assignment={
  team_id:string;team_name:string;monitor_person_id:string|null;monitor_name:string|null
};
type Monitor={id:string;name:string;email:string;assigned_count:number};
type Overview={teams:Assignment[];monitors:Monitor[];total_teams:number;
  assigned_teams:number;unassigned_teams:number};
type Proposal={team_id:string;team_name:string;previous_monitor_id:string|null;
  new_monitor_id:string;new_monitor_name:string};
type Plan={plan_id:string;mode:"fill_unassigned"|"rebalance_all";total_teams:number;
  monitors:number;changes:number;proposals:Proposal[];expires_at:number};

export function MonitorAssignmentsPanel({auth,onApplied}:{
  auth:Auth;onApplied:()=>void
}) {
  const [open,setOpen]=useState(false);
  const [overview,setOverview]=useState<Overview|null>(null);
  const [mode,setMode]=useState<"fill_unassigned"|"rebalance_all">("fill_unassigned");
  const [plan,setPlan]=useState<Plan|null>(null);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [busy,setBusy]=useState(false);
  const refresh=useCallback(async()=>{
    const updated=await api<Overview>("/monitoring/assignments");
    setOverview(updated);
  },[]);
  useEffect(()=>{
    if(!open)return;
    void refresh().catch(e=>setError((e as Error).message));
    const timer=window.setInterval(()=>{
      if(!document.hidden)void refresh().catch(()=>{});
    },5000);
    return()=>window.clearInterval(timer);
  },[open,refresh]);
  async function manual(team:Assignment, monitorId:string) {
    setBusy(true);setError("");setNotice("");setPlan(null);
    try{
      await api("/monitoring/teams/"+team.team_id,"PUT",{
        monitor_person_id:monitorId||null
      },auth.csrf_token);
      await refresh();onApplied();
      setNotice("Monitor assignment updated. No Supervisely changes were made.");
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function preview() {
    setBusy(true);setError("");setNotice("");
    try{
      setPlan(await api<Plan>(
        "/monitoring/assignments/preview","POST",{mode},auth.csrf_token
      ));
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function apply() {
    if(!plan||!window.confirm(
      "Apply "+plan.changes+" local team-monitor assignment(s)? "+
      "This does not assign or change any native Supervisely labeling job."
    ))return;
    setBusy(true);setError("");
    try{
      await api("/monitoring/assignments/plans/"+plan.plan_id+"/apply",
        "POST",undefined,auth.csrf_token);
      setPlan(null);await refresh();onApplied();
      setNotice("Team responsibilities updated and audited.");
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  return <section className="panel monitor-assignment-panel">
    <div className="panel-heading"><div><span className="eyebrow">MONITOR COORDINATION</span>
      <h3>Monitor assignments</h3>
      <p className="muted">Assign each competition team to an event monitor, or preview a balanced allocation.</p></div>
      <UsersRound size={23}/></div>
    <div className="monitor-panel-actions"><button className="btn secondary"
      onClick={()=>{setOpen(x=>!x);setPlan(null);}}>
      {open?"Close assignments":"Manage monitor assignments"}</button>
      {open&&<button className="btn secondary" disabled={busy}
        onClick={()=>void refresh()}><RefreshCw size={15}/> Refresh</button>}
    </div>
    {error&&<div role="alert" className="alert danger">{error}</div>}
    {notice&&<div role="status" className="alert success">{notice}</div>}
    {open&&overview&&<>
      <div className="monitor-stats">
        <span>{overview.total_teams} competition teams</span>
        <span>{overview.assigned_teams} assigned</span>
        <span>{overview.unassigned_teams} unassigned</span>
        <span>{overview.monitors.length} monitors</span>
      </div>
      <div className="monitor-staff-summary">{overview.monitors.map(mon=>
        <div key={mon.id}><ShieldCheck size={14}/>
          <strong>{mon.name}</strong><span>{mon.assigned_count} teams</span>
        </div>)}</div>
      <div className="monitor-balance-tools">
        <label>Allocation policy<select value={mode}
          onChange={e=>{setMode(e.target.value as typeof mode);setPlan(null);}}>
          <option value="fill_unassigned">Fill only unassigned teams</option>
          <option value="rebalance_all">Rebalance all teams evenly</option>
        </select></label>
        <button className="btn primary" onClick={()=>void preview()}
          disabled={busy||!overview.monitors.length||!overview.total_teams}>
          Preview balanced allocation</button>
      </div>
      {plan&&<div className="monitor-plan">
        <span className="eyebrow">ALLOCATION PREVIEW</span>
        <p><strong>{plan.changes}</strong> team assignment changes ·
          {plan.mode==="fill_unassigned"?" Existing assignments preserved":" Entire event rebalanced"}</p>
        <div className="monitor-plan-rows">{plan.proposals.map(item=>
          <div key={item.team_id}><strong>{item.team_name}</strong>
            <span>{item.new_monitor_name}</span></div>)}</div>
        <button className="btn primary" disabled={busy||!plan.changes}
          onClick={()=>void apply()}><CheckCircle2 size={15}/> Confirm assignment</button>
      </div>}
      <div className="monitor-team-editor">
        <h4>Manual team responsibilities</h4>
        <div className="monitor-team-rows">{overview.teams.map(team=>
          <label key={team.team_id}><span>{team.team_name}</span>
            <select value={team.monitor_person_id||""} disabled={busy}
              onChange={e=>void manual(team,e.target.value)}>
              <option value="">Unassigned</option>
              {overview.monitors.map(mon=><option value={mon.id} key={mon.id}>
                {mon.name} ({mon.assigned_count})</option>)}
            </select>
          </label>)}</div>
      </div>
      <p className="muted small" style={{marginTop:15}}>
        These assignments only organise Nightjar monitoring responsibilities.
        Native Supervisely team roles and labeling jobs are unaffected.
      </p>
    </>}
  </section>;
}
