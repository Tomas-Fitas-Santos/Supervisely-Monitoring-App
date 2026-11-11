import { useEffect, useState } from "react";
import type React from "react";
import { AlertTriangle, CheckCircle2, Cloud, LockKeyhole, RefreshCw, Save, ShieldCheck, Trash2 } from "lucide-react";
import { api, type Auth, type Integration } from "./api";
import { EventManagement } from "./events";

type Shared={server_url:string;team_id:number|null;workspace_id:number|null;
  verified:boolean;last_verified_at:number|null;editable:boolean};
type Personal={linked:boolean;user_id:number|null;login:string|null;role:string|null;
  verified_team_id:number|null;verified_at:number|null;matches_current_team:boolean};
type ResetPreview={
  event_name:string;event_status:string;preserved_teams:number;
  preserved_people:number;datasets:number;jobs:number;known_native_projects:number[];
  workspace_id:number|null;team_id:number|null;remote_scope:string;
  remote_action:string;preserved:string[];cleared:string[];
  confirmation_phrase:string;
  operation:{status:string;error_code:string|null;attempts:number}|null;
};

export function SettingsScreen({auth}:{auth:Auth}) {
  const organiser=auth.staff.roles.includes("organiser");
  const [shared,setShared]=useState<Shared|null>(null);
  const [personal,setPersonal]=useState<Personal|null>(null);
  const [config,setConfig]=useState<Integration|null>(null);
  const [server,setServer]=useState("https://app.supervisely.com");
  const [team,setTeam]=useState("");
  const [workspace,setWorkspace]=useState("");
  const [sharedToken,setSharedToken]=useState("");
  const [myToken,setMyToken]=useState("");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [message,setMessage]=useState("");
  const [resetPreview,setResetPreview]=useState<ResetPreview|null>(null);
  const [resetPhrase,setResetPhrase]=useState("");
  const [resetBusy,setResetBusy]=useState(false);

  async function reload(){
    const [teamSettings,link]=await Promise.all([
      api<Shared>("/settings/supervisely/shared"),
      api<Personal>("/settings/supervisely/personal"),
    ]);
    setShared(teamSettings);setPersonal(link);
    if(organiser){
      const result=await api<Integration>("/settings/supervisely");
      setConfig(result);
      setServer(result.settings.server_url);
      setTeam(String(result.settings.team_id??""));
      setWorkspace(String(result.settings.workspace_id??""));
    }
  }
  useEffect(()=>{void reload().catch(e=>setError((e as Error).message));},[]);

  async function work(action:()=>Promise<void>){
    setBusy(true);setError("");setMessage("");
    try {await action();await reload();}
    catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  function saveTeam(e:React.FormEvent){
    e.preventDefault();
    void work(async()=>{
      await api("/settings/supervisely","PUT",{supervisely:{
        server_url:server,team_id:team?Number(team):null,
        workspace_id:workspace?Number(workspace):null,
      }},auth.csrf_token);
      setMessage("Shared team and workspace saved for every monitor account.");
    });
  }
  function saveSharedToken(e:React.FormEvent){
    e.preventDefault();
    void work(async()=>{
      await api("/settings/supervisely/credential","PUT",
        {api_token:sharedToken},auth.csrf_token);
      setSharedToken("");
      setMessage("Organiser integration account verified. API token encrypted and saved.");
    });
  }
  function savePersonalToken(e:React.FormEvent){
    e.preventDefault();
    void work(async()=>{
      await api("/settings/supervisely/personal","POST",
        {api_token:myToken},auth.csrf_token);
      setMyToken("");
      setMessage("Your Supervisely account was verified and linked. Personal token not stored.");
    });
  }
  async function loadResetPreview(){
    setResetBusy(true);setError("");
    try{
      setResetPreview(await api<ResetPreview>("/settings/reset/preview"));
    }catch(e){setError((e as Error).message);}
    finally{setResetBusy(false);}
  }
  async function requestFullReset(){
    if(!resetPreview||resetPhrase!==resetPreview.confirmation_phrase)return;
    if(!window.confirm(
      "Archive this event's Nightjar projects in Supervisely and clear its " +
      "local event data? This cannot be undone in Nightjar. Team memberships " +
      "and monitor accounts will be preserved."))return;
    setResetBusy(true);setError("");setMessage("");
    try{
      await api("/settings/reset/request","POST",
        {confirmation:resetPhrase},auth.csrf_token);
      setResetPhrase("");
      setMessage("Full event reset queued. The Supervisely worker must remove native jobs and archive their datasets/projects before local deletion.");
      await loadResetPreview();
    }catch(e){setError((e as Error).message);}
    finally{setResetBusy(false);}
  }
  async function retryFullReset(){
    setResetBusy(true);setError("");setMessage("");
    try{
      await api("/settings/reset/retry","POST",undefined,auth.csrf_token);
      setMessage("Reset retry queued for the Supervisely worker.");
      await loadResetPreview();
    }catch(e){setError((e as Error).message);}
    finally{setResetBusy(false);}
  }
  return <div className="page-body">
    <div className="page-heading"><div><span className="eyebrow">ACCOUNT & INTEGRATION</span>
      <h2>Application settings</h2>
      <p className="muted">Nightjar sign-in is separate from Supervisely. Event integration is shared.</p>
    </div></div>
    {error&&<div role="alert" className="alert danger">{error}</div>}
    {message&&<div role="status" className="alert success">{message}</div>}
    <div className="settings-layout">
      <section className="panel">
        <div className="panel-heading"><div>
          <span className="eyebrow">SHARED EVENT CONFIGURATION</span>
          <h3>Supervisely team & workspace</h3>
          <p className="muted">{organiser?
            "Only the organiser may change these IDs; every monitor sees the same settings.":
            "Configured by the organiser. Team and workspace IDs are read-only for monitors."}</p>
        </div><Cloud size={24}/></div>
        {organiser?<form className="form-stack" onSubmit={saveTeam}>
          <label>Supervisely Server<input type="url" required value={server}
            onChange={e=>setServer(e.target.value)}/></label>
          <div className="form-grid">
            <label>Team ID<input type="number" min="1" value={team}
              onChange={e=>setTeam(e.target.value)}/></label>
            <label>Workspace ID<input type="number" min="1" value={workspace}
              onChange={e=>setWorkspace(e.target.value)}/></label>
          </div>
          <button className="btn primary" disabled={busy} type="submit">
            <Save size={16}/> Save team settings</button>
        </form>:<div className="form-stack">
          <div className="settings-row"><span>Server</span><strong>{shared?.server_url??"—"}</strong></div>
          <div className="settings-row"><span>Team ID</span><strong>{shared?.team_id??"—"}</strong></div>
          <div className="settings-row"><span>Workspace ID</span><strong>{shared?.workspace_id??"—"}</strong></div>
          <div className="settings-note"><ShieldCheck size={18}/>
            <p>These are shared across all monitoring accounts; your own Supervisely key cannot edit them.</p>
          </div>
        </div>}
        {organiser&&<>
          <div className="settings-divider"/>
          <h3>Organiser integration token</h3>
          <p className="muted">This single team-level credential is used for membership synchronization and the annotation worker. It is saved encrypted in PostgreSQL; it is never returned to the browser.</p>
          <form className="form-stack" onSubmit={saveSharedToken}>
            <label>Supervisely API token<input type="password" autoComplete="off"
              value={sharedToken} required minLength={8}
              onChange={e=>setSharedToken(e.target.value)}
              placeholder={config?.credential_configured?"Configured — paste to replace":"Paste organiser API token"}/></label>
            <div className="settings-action-row">
              <button type="submit" className="btn primary" disabled={busy||!sharedToken}>
                <LockKeyhole size={16}/> Verify and save token</button>
              <button type="button" className="btn secondary" disabled={busy||!config?.credential_configured}
                onClick={()=>void work(async()=>{
                  await api("/settings/supervisely/verify","POST",undefined,auth.csrf_token);
                  setMessage("Live team verified with the saved organiser credential.");
                })}><CheckCircle2 size={16}/> Verify connection</button>
              <button type="button" className="btn danger-outline"
                disabled={busy||!config?.credential_configured}
                onClick={()=>{if(window.confirm("Remove the saved team integration token? Native synchronization and the worker will stop until a new token is saved."))void work(async()=>{
                  await api("/settings/supervisely/credential","DELETE",undefined,auth.csrf_token);
                  setMessage("Organiser integration credential removed.");
                });}}>Remove token</button>
            </div>
          </form>
          <div className="settings-row"><span>Integration token</span>
            <strong>{config?.credential_configured?"Encrypted and saved":"Not configured"}</strong></div>
          <div className="settings-row"><span>Team verified</span>
            <strong>{config?.verified?"Yes":"Not yet"}</strong></div>
          <div className="settings-divider"/>
          <h3>Monitor account access</h3>
          <p className="muted">Monitors already approved in the active event's SQL
            roster create their own Nightjar accounts using that roster email
            and an app-only password. No verification emails or SMTP are required.
            Registration approval is managed in People &amp; Teams.</p>
        </>}
      </section>

      <aside className="settings-side">
        <section className="panel connection-panel">
          <span className="eyebrow">PERSONAL ACCOUNT</span>
          <h3>My Supervisely connection</h3>
          <p className="muted">Verify your own Supervisely identity. Your token is discarded after verification; this does not change the shared team configuration.</p>
          <div className="connection-state">{personal?.linked&&personal.matches_current_team?
            <><CheckCircle2 size={19}/> Linked · {personal.login}</>:
            <><span className="state-circle"/> Not linked to this team</>}</div>
          {personal?.linked&&<>
            <div className="settings-row"><span>Supervisely user ID</span>
              <strong>{personal.user_id??"—"}</strong></div>
            <div className="settings-row"><span>Team role</span>
              <strong>{personal.role??"—"}</strong></div>
          </>}
          <form className="form-stack" onSubmit={savePersonalToken}>
            <label>Personal Supervisely API token
              <input type="password" autoComplete="off" minLength={8} required
                value={myToken} onChange={e=>setMyToken(e.target.value)}
                placeholder="Paste your own personal token"/></label>
            <button className="btn primary" disabled={busy||!myToken} type="submit">
              <ShieldCheck size={16}/> Verify my account</button>
          </form>
          {personal?.linked&&<button type="button" className="btn secondary"
            disabled={busy} onClick={()=>void work(async()=>{
              await api("/settings/supervisely/personal","DELETE",undefined,auth.csrf_token);
              setMessage("Personal Supervisely account unlinked.");
            })}>Unlink my account</button>}
        </section>
      </aside>
    </div>
    {organiser&&<section className="panel full-reset-panel">
      <div className="panel-heading"><div>
        <span className="eyebrow">DANGER ZONE · ORGANISER ONLY</span>
        <h3>Reset event operations — preserve people and teams</h3>
        <p className="muted">Start this event from a clean operational state without deleting Supervisely team members or Nightjar accounts. This resets only the selected event and only its Nightjar-owned Supervisely projects, not unrelated projects.</p>
      </div><AlertTriangle size={24} color="#a64634"/></div>
      <button className="btn secondary" disabled={resetBusy}
        onClick={()=>void loadResetPreview()}><RefreshCw size={15}/>
        {resetPreview?"Refresh reset status":"Preview full reset"}</button>
      {resetPreview&&<div className="full-reset-preview">
        <div className="full-reset-counts">
          <div><span>Current event</span><strong>{resetPreview.event_name}</strong></div>
          <div><span>Local datasets to clear</span><strong>{resetPreview.datasets}</strong></div>
          <div><span>Jobs to clear</span><strong>{resetPreview.jobs}</strong></div>
          <div><span>Preserved teams</span><strong>{resetPreview.preserved_teams}</strong></div>
          <div><span>Preserved roster members</span><strong>{resetPreview.preserved_people}</strong></div>
        </div>
        <div className="full-reset-scopes">
          <div><h4>Will be cleared</h4><ul>{resetPreview.cleared.map(x=><li key={x}>{x}</li>)}</ul></div>
          <div><h4>Will be preserved</h4><ul>{resetPreview.preserved.map(x=><li key={x}>{x}</li>)}</ul></div>
        </div>
        <p className="muted small">Remote cleanup in workspace #{resetPreview.workspace_id??"—"} of team #{resetPreview.team_id??"—"}: permanently remove Nightjar labeling jobs for this event, then archive their Supervisely datasets and projects. Archived datasets/projects are hidden but recoverable; permanent purging requires Supervisely instance-root privileges. Team and joined members are never deleted. Local event data and files are cleared only after remote cleanup succeeds.</p>
        {resetPreview.operation&&<div className="job-plan-row">
          <div><strong>Reset: {resetPreview.operation.status}</strong>
            <span>{resetPreview.operation.error_code||"No reported error"} · {resetPreview.operation.attempts} worker attempts</span></div>
          {resetPreview.operation.status==="attention"&&
            <button className="btn secondary" disabled={resetBusy}
              onClick={()=>void retryFullReset()}>Retry cleanup</button>}
        </div>}
        {resetPreview.event_status==="active"&&
          <div className="full-reset-confirm">
            <label>Type <strong>{resetPreview.confirmation_phrase}</strong> to enable reset
              <input value={resetPhrase} autoComplete="off"
                onChange={e=>setResetPhrase(e.target.value)} />
            </label>
            <button className="btn danger-outline" disabled={resetBusy||
              resetPhrase!==resetPreview.confirmation_phrase}
              onClick={()=>void requestFullReset()}><Trash2 size={16}/> Reset this event</button>
          </div>}
        {resetPreview.event_status==="resetting"&&
          <p className="advisory">The event is locked while its native jobs are removed, its datasets/projects archived, and local data cleared. Keep the Supervisely worker running; refresh for status.</p>}
      </div>}
    </section>}
    {organiser&&<EventManagement auth={auth}/>}
  </div>;
}
