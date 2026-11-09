import { useEffect, useState } from "react";
import type React from "react";
import { CheckCircle2, Cloud, Database, LockKeyhole, Save, ShieldCheck } from "lucide-react";
import { api, type Auth, type Integration } from "./api";

export function SettingsScreen({auth}:{auth:Auth}) {
  const [config,setConfig]=useState<Integration|null>(null);
  const [server,setServer]=useState("https://app.supervisely.com");
  const [team,setTeam]=useState("");
  const [workspace,setWorkspace]=useState("");
  const [error,setError]=useState("");
  const [message,setMessage]=useState("");
  const [busy,setBusy]=useState(false);
  useEffect(()=>{
    api<Integration>("/settings/supervisely").then(result=>{
      setConfig(result);setServer(result.settings.server_url);
      setTeam(String(result.settings.team_id??""));
      setWorkspace(String(result.settings.workspace_id??""));
    }).catch(exc=>setError((exc as Error).message));
  },[]);
  async function save(e:React.FormEvent) {
    e.preventDefault();setBusy(true);setMessage("");setError("");
    try {
      const result=await api<Integration>("/settings/supervisely","PUT",
        {supervisely:{server_url:server,team_id:team?Number(team):null,
          workspace_id:workspace?Number(workspace):null}},auth.csrf_token);
      setConfig(result);setMessage("Settings saved to PostgreSQL. Native membership and access are not yet verified.");
    }catch(exc){setError((exc as Error).message);}
    finally{setBusy(false);}
  }
  return <div className="page-body">
    <div className="page-heading"><div><span className="eyebrow">ORGANISER SETTINGS</span>
      <h2>Application settings</h2><p className="muted">Manage integrations independently of staff authentication and event logistics.</p>
    </div></div>
    {error&&<div role="alert" className="alert danger">{error}</div>}
    {message&&<div role="status" className="alert success">{message}</div>}
    <div className="settings-layout">
      <section className="panel">
        <div className="panel-heading"><div><span className="eyebrow">EXTERNAL CONNECTION</span>
          <h3>Supervisely</h3><p className="muted">Configure the single shared native event team.</p></div>
          <div className="settings-icon"><Cloud size={24}/></div></div>
        <form className="form-stack" onSubmit={e=>void save(e)}>
          <label>Server URL<input type="url" value={server} required
            onChange={e=>setServer(e.target.value)} placeholder="https://app.supervisely.com"/></label>
          <div className="form-grid">
            <label>Team ID<input type="number" min="1" value={team}
              onChange={e=>setTeam(e.target.value)} placeholder="125980"/></label>
            <label>Workspace ID<input type="number" min="1" value={workspace}
              onChange={e=>setWorkspace(e.target.value)} placeholder="Enter workspace ID"/></label>
          </div>
          <div className="settings-note"><ShieldCheck size={18}/>
            <p>These IDs are saved locally. Verification against Supervisely is added with the integration module; saving does not change native membership.</p>
          </div>
          <button type="submit" className="btn primary" disabled={busy}>
            <Save size={17}/>{busy?"Saving…":"Save settings"}</button>
        </form>
      </section>
      <aside className="settings-side">
        <section className="panel connection-panel">
          <span className="eyebrow">CONNECTION STATUS</span><h3>Native API access</h3>
          <div className="connection-state">{config?.verified?
            <><CheckCircle2 size={19}/> Verified</>:<><span className="state-circle"/> Not verified</>}</div>
          <div className="settings-divider"/>
          <div className="settings-row"><span>Credential configured</span>
            <strong>{config?.credential_configured?"Yes":"No"}</strong></div>
          <div className="settings-row"><span>Team ID</span><strong>{config?.settings.team_id??"—"}</strong></div>
          <div className="settings-row"><span>Workspace ID</span><strong>{config?.settings.workspace_id??"—"}</strong></div>
          <p className="muted small">Later, the organiser will be able to configure or rotate an API credential securely here. Individual staff accounts will never need one.</p>
        </section>
        <section className="panel secure-panel"><LockKeyhole size={21}/>
          <div><h4>Security by design</h4>
            <p className="muted small">Supervisely API credentials are kept separate from user login. Secret values are never returned by these settings endpoints.</p></div>
        </section>
        <section className="panel secure-panel"><Database size={21}/>
          <div><h4>Independent logistics</h4>
            <p className="muted small">Rosters, check-ins, food and drink tickets continue to use PostgreSQL even when Supervisely is disconnected.</p></div>
        </section>
      </aside>
    </div>
  </div>;
}
