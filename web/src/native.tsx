import { useState } from "react";
import { AlertTriangle, CheckCircle2, Cloud, RefreshCw, ShieldCheck } from "lucide-react";
import { api, type Auth } from "./api";

type Matched={person_id:string;person_name:string;person_email:string;native_user_id:number;
  native_login:string;native_role:string;method:string;role_warning:boolean};
type Native={id:number;login:string;email:string|null;role:string};
type Local={person_id:string;name:string;email:string;current_status:string};
type Conflict={native_user_id:number;login:string;reason:string};
type Preview={plan_id:string;team_id:number;team_name:string;expires_at:number;
  members_total:number;roster_total:number;matched:Matched[];unmatched_native:Native[];
  unmatched_roster:Local[];conflicts:Conflict[];notice:string;remote_mutations:boolean};
type Applied={applied:number;already_applied:boolean;native_mutations:boolean};

export function NativeMembershipPanel({auth,onApplied}:{auth:Auth;onApplied:()=>void}) {
  const [open,setOpen]=useState(false);
  const [preview,setPreview]=useState<Preview|null>(null);
  const [manual,setManual]=useState<Record<number,string>>({});
  const [error,setError]=useState("");
  const [message,setMessage]=useState("");
  const [busy,setBusy]=useState(false);
  const [applied,setApplied]=useState(false);
  const dirty=Object.keys(manual).length>0;

  async function refresh(mapping:Record<number,string>={}) {
    setBusy(true);setError("");setMessage("");
    try {
      const next=await api<Preview>("/people/supervisely/preview","POST",{
        manual_links:Object.entries(mapping).filter(([,id])=>Boolean(id)).map(([uid,pid])=>({
          native_user_id:Number(uid),person_id:pid
        }))
      },auth.csrf_token);
      setPreview(next);setManual({});setApplied(false);
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function apply(){
    if(!preview || dirty || preview.conflicts.length)return;
    if(!window.confirm(
      "Apply "+preview.matched.length+" verified team membership record(s) to Nightjar? "+
      "This updates only the selected event's LOCAL roster, not Supervisely."
    ))return;
    setBusy(true);setError("");
    try {
      const result=await api<Applied>(
        "/people/supervisely/plans/"+preview.plan_id+"/apply",
        "POST",undefined,auth.csrf_token
      );
      setMessage(result.applied+" roster entries updated from verified native membership. "+
        "No remote invitations or role changes were sent.");
      setApplied(true);
      onApplied();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  return <section className="panel native-panel">
    <div className="panel-heading"><div>
      <span className="eyebrow">LIVE SUPERVISELY MEMBERSHIP</span>
      <h3>Verify & reconcile the team</h3>
      <p className="muted">Read the actual team roster, inspect differences and confirm which local records should be marked joined.</p>
    </div><Cloud size={24}/></div>
    <div className="native-panel-actions">
      <button className="btn secondary" onClick={()=>{setOpen(v=>!v);setPreview(null);setManual({});}}>
        {open?"Close integration":"Open membership sync"}</button>
      {open&&<button className="btn primary" disabled={busy} onClick={()=>void refresh()}>
        <RefreshCw size={15}/> Refresh from Supervisely</button>}
    </div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {message&&<div className="alert success" role="status">{message}</div>}
    {open&&<>
      <div className="settings-note"><ShieldCheck size={18}/>
        <p>Read-only native calls use your backend API token. This feature cannot invite/remove members or edit remote roles. Missing email addresses are <strong>not</strong> guessed; you may explicitly map them below.</p>
      </div>
      {preview&&<>
        <div className="native-summary">
          <span><strong>{preview.team_name}</strong> · Team {preview.team_id}</span>
          <span>{preview.members_total} native member(s) · {preview.roster_total} local people · {preview.matched.length} match(es)</span>
        </div>
        {preview.conflicts.length>0&&<div className="advisory"><AlertTriangle size={17}/>
          {preview.conflicts.length} ambiguous identity mapping(s) detected. Resolve them before applying.</div>}
        {preview.matched.some(m=>m.role_warning)&&<div className="advisory">
          <AlertTriangle size={17}/>
          {preview.matched.filter(m=>m.role_warning).length} user(s) have roles different from Nightjar's expected monitor/annotator roles. Roles will NOT be changed remotely.</div>}
        <div className="native-preview-lists">
          <div><h4>Verified matches ({preview.matched.length})</h4>
            <div className="native-scroll">
            {preview.matched.map(m=><div className="native-line" key={m.person_id}>
              <CheckCircle2 size={14}/><span><strong>{m.person_name}</strong> · {m.native_login}
                <small> {m.method.replaceAll("_"," ")} · {m.native_role}{m.role_warning?" · Role warning":""}</small></span>
              </div>)}
            </div>
          </div>
          <div><h4>Native members not linked ({preview.unmatched_native.length})</h4>
            <p className="muted small">Choose a specific roster person if their native username differs from their registration email.</p>
            <div className="native-scroll">{preview.unmatched_native.map(member=>
              <div className="native-line" key={member.id}>
                <Cloud size={14}/>
                <span><strong>{member.login}</strong><small>{member.email||"Email unavailable"} · {member.role}</small></span>
                <select aria-label={"Link "+member.login+" to roster"}
                  value={manual[member.id]||""} onChange={e=>setManual(old=>({...old,[member.id]:e.target.value}))}>
                  <option value="">Leave unmatched</option>
                  {preview.unmatched_roster.map(local=><option key={local.person_id}
                    value={local.person_id}>{local.name} · {local.email}</option>)}
                </select>
              </div>)}</div>
          </div>
          <div><h4>Roster people without a native match ({preview.unmatched_roster.length})</h4>
            <div className="native-scroll">{preview.unmatched_roster.map(person=>
              <div className="native-line" key={person.person_id}><AlertTriangle size={13}/>
                <span><strong>{person.name}</strong><small>{person.email} · {person.current_status}</small></span>
              </div>)}</div>
          </div>
        </div>
        {dirty&&<button className="btn secondary" disabled={busy}
          onClick={()=>void refresh(manual)}>Review manual mappings</button>}
        <div className="native-footer">
          <span className="muted small">{preview.notice} · Expires {new Date(preview.expires_at*1000).toLocaleTimeString()}</span>
          {!applied&&<button className="btn primary" disabled={busy||dirty||
            !!preview.conflicts.length||!preview.matched.length}
            onClick={()=>void apply()}>Confirm local reconciliation ({preview.matched.length})</button>}
        </div>
      </>}
    </>}
  </section>;
}
