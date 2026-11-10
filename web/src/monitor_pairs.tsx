import { useCallback, useEffect, useState } from "react";
import { Plus, RefreshCw, ShieldCheck, UsersRound, X } from "lucide-react";
import { api, type Auth, type TeamResponse } from "./api";

type MonitorAvailable={
  id:string;name:string;email:string;available:boolean;
  current_pair_team_id:string|null;supervisely_role:string|null;
  membership_status:string;
};

export function MonitorPairPanel({auth,onChanged}:{auth:Auth;onChanged:()=>void}) {
  const [open,setOpen]=useState(false);
  const [monitors,setMonitors]=useState<MonitorAvailable[]>([]);
  const [teams,setTeams]=useState<TeamResponse["teams"]>([]);
  const [name,setName]=useState("");
  const [first,setFirst]=useState("");
  const [second,setSecond]=useState("");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const refresh=useCallback(async()=>{
    const [available,grouped]=await Promise.all([
      api<{items:MonitorAvailable[]}>("/teams/monitor-pair-availability"),
      api<TeamResponse>("/teams"),
    ]);
    setMonitors(available.items);
    setTeams(grouped.teams.filter(t=>
      t.type==="competition"&&t.member_source==="monitor_pair"));
  },[]);
  useEffect(()=>{
    if(!open)return;
    void refresh().catch(e=>setError((e as Error).message));
  },[open,refresh]);
  async function create(e:React.FormEvent) {
    e.preventDefault();
    if(!name.trim()||!first||!second||first===second)return;
    if(!window.confirm(
      "Create a two-monitor competition pair? Both remain monitoring staff. "+
      "No native Supervisely memberships or roles will be changed."
    ))return;
    setBusy(true);setError("");setNotice("");
    try {
      await api("/teams/monitor-pairs","POST",{
        name:name.trim(),monitor_person_ids:[first,second],
      },auth.csrf_token);
      setFirst("");setSecond("");setName("");
      setNotice("Two-person monitor pair created. Use Dataset and Competition to plan jobs.");
      await refresh();onChanged();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function disband(teamId:string,teamName:string){
    if(!window.confirm("Disband "+teamName+"? This is allowed only before "+
      "any annotation job is planned. Monitor staff and native accounts are retained."))return;
    setBusy(true);setError("");setNotice("");
    try{
      await api("/teams/monitor-pairs/"+teamId+"/disband","POST",undefined,auth.csrf_token);
      setNotice("Pair disbanded. Monitors are available for another trial pair.");
      await refresh();onChanged();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  const free=monitors.filter(p=>p.available);
  return <section className="panel monitor-pair-panel">
    <div className="panel-heading"><div>
      <span className="eyebrow">TWO-PERSON COMPETITION TESTING</span>
      <h3>Monitor test pairs</h3>
      <p className="muted">Form and disband a temporary competition team of exactly
        two registered monitors. Their staff roles and Supervisely memberships stay unchanged.</p>
    </div><UsersRound size={23}/></div>
    <div className="monitor-panel-actions">
      <button className="btn secondary" onClick={()=>setOpen(v=>!v)}>
        {open?"Close pair manager":"Manage monitor test pairs"}</button>
      {open&&<button className="btn secondary" disabled={busy}
        onClick={()=>void refresh()}><RefreshCw size={15}/> Refresh</button>}
    </div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}
    {open&&<div className="pair-manager">
      <form onSubmit={e=>void create(e)} className="form-stack">
        <h4>Build a pair</h4>
        <label>Team name<input value={name} maxLength={200} required
          placeholder="Monitor Test Pair 01"
          onChange={e=>setName(e.target.value)}/></label>
        <div className="form-grid">
          <label>First monitor<select value={first} required
            onChange={e=>setFirst(e.target.value)}>
            <option value="">Choose an available monitor</option>
            {free.map(m=><option key={m.id} value={m.id}>
              {m.name} ({m.supervisely_role||"Native role not verified"})</option>)}
          </select></label>
          <label>Second monitor<select value={second} required
            onChange={e=>setSecond(e.target.value)}>
            <option value="">Choose an available monitor</option>
            {free.filter(m=>m.id!==first).map(m=><option key={m.id} value={m.id}>
              {m.name} ({m.supervisely_role||"Native role not verified"})</option>)}
          </select></label>
        </div>
        <p className="muted small">These pairs can receive ordinary Nightjar labeling
          jobs once both accounts have verified native team membership.
          Native Supervisely job permissions must still be checked with both accounts.</p>
        <button className="btn primary" type="submit"
          disabled={busy||!first||!second||first===second||!name.trim()}>
          <Plus size={15}/> Create two-monitor pair</button>
      </form>
      <div className="job-plan-history">
        <h4>Active monitor pairs ({teams.length})</h4>
        {teams.map(t=><div className="job-plan-row" key={t.id}>
          <div><strong>{t.name}</strong>
            <span>{t.members.map(m=>m.name).join(" · ")}</span></div>
          <button className="btn danger-outline" disabled={busy}
            onClick={()=>void disband(t.id,t.name)}>
            <X size={14}/> Disband</button>
        </div>)}
        {!teams.length&&<p className="muted small">No monitor-based pairs yet.</p>}
      </div>
      <div className="settings-note" style={{marginTop:15}}>
        <ShieldCheck size={17}/>
        <p>Disbanding is a local, audited change only. Once jobs exist for a pair,
          Nightjar preserves their source assignments and blocks disbanding.</p>
      </div>
    </div>}
  </section>;
}
