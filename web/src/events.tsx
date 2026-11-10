import { useCallback, useEffect, useState } from "react";
import type React from "react";
import {
  AlertTriangle, Archive, CalendarDays, CheckCircle2, Download, Plus,
  RefreshCw, RotateCcw, ShieldAlert
} from "lucide-react";
import { api, type Auth } from "./api";

type EventRecord = {id:string;name:string;status:"active"|"archived";
  created_at:number;archived_at:number|null;reset_from_id:string|null};
type EventList={items:EventRecord[];current_event_id:string};
type ResetMode="selected"|"fresh";
type ResetArea="attendance"|"invitations"|"tickets"|"roster"|"activities";
type ResetPreview={plan_id:string;event_id:string;event_name:string;mode:ResetMode;
  areas:ResetArea[];counts:{people:number;teams:number;checked_in:number;
    manual_invites:number;ticket_batches:number;ticket_ledger_entries:number;activities:number;activity_results:number;activity_awards:number};
  expires_at:number;confirmation_phrase:string;notes:string[]};
type Snapshot={id:string;event_id:string;created_at:number;reset_plan_id:string};

const AREAS:{key:ResetArea;title:string;description:string}[]=[
  {key:"attendance",title:"Attendance",description:"Clear check-in timestamps for this event."},
  {key:"invitations",title:"Manual invitation reports",description:"Clear unverified 'reported invited' labels only; native memberships are not changed."},
  {key:"tickets",title:"Ticket balances",description:"Compensate allocations, issuance and redemption in the ledger; preserve transaction history and category policies."},
  {key:"roster",title:"People & Teams",description:"Archive roster entries and teams. Reset dependent ticket and activity results when present."},
  {key:"activities",title:"Activities & Prizes",description:"Archive activity schedules and ranked results. Also reset tickets when awards have been allocated."},
];
const stamp=(v:number)=>new Date(v*1000).toLocaleString();

export function EventSelector({auth}:{auth:Auth}) {
  const [value,setValue]=useState<EventList|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  useEffect(()=>{
    api<EventList>("/events").then(setValue).catch(exc=>setError((exc as Error).message));
  },[]);
  async function switchEvent(id:string){
    if(!value||id===value.current_event_id)return;
    setBusy(true);setError("");
    try{
      await api("/events/"+id+"/select","POST",undefined,auth.csrf_token);
      window.location.reload();
    }catch(e){setError((e as Error).message);setBusy(false);}
  }
  if(!value)return <div className="topbar-event">{error||"Loading events…"}</div>;
  const current=value.items.find(e=>e.id===value.current_event_id);
  return <div className="topbar-event">
    <CalendarDays size={15}/>
    <select aria-label="Selected Nightjar event" disabled={busy}
      value={value.current_event_id} onChange={e=>void switchEvent(e.target.value)}>
      {value.items.map(event=><option key={event.id} value={event.id}>
        {event.name}{event.status==="archived"?" (Archived)":""}
      </option>)}
    </select>
    {current?.status==="archived"&&<span className="archived-flag">Read only</span>}
  </div>;
}

export function EventManagement({auth}:{auth:Auth}) {
  const [data,setData]=useState<EventList|null>(null);
  const [snapshots,setSnapshots]=useState<Snapshot[]>([]);
  const [newName,setNewName]=useState("");
  const [mode,setMode]=useState<ResetMode>("selected");
  const [areas,setAreas]=useState<ResetArea[]>(["attendance"]);
  const [preview,setPreview]=useState<ResetPreview|null>(null);
  const [typed,setTyped]=useState("");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const refresh=useCallback(async()=>{
    const next=await api<EventList>("/events");
    setData(next);
    const list=await api<{items:Snapshot[]}>("/events/"+next.current_event_id+"/snapshots");
    setSnapshots(list.items);
  },[]);
  useEffect(()=>{void refresh().catch(e=>setError((e as Error).message));},[refresh]);
  const selected=data?.items.find(e=>e.id===data.current_event_id);
  const canReset=selected?.status==="active";
  function resetDraft(){
    setPreview(null);setTyped("");setNotice("");
  }
  async function createEvent(e:React.FormEvent){
    e.preventDefault();
    setBusy(true);setError("");setNotice("");
    try{
      await api("/events","POST",{name:newName.trim()},auth.csrf_token);
      window.location.reload();
    }catch(e){setError((e as Error).message);setBusy(false);}
  }
  async function archiveOrReopen(action:"archive"|"reopen"){
    if(!selected)return;
    const answer=window.prompt(
      (action==="archive"?"Archive":"Reopen")+" '"+selected.name+
      "'? Enter the full event name to confirm:");
    if(answer!==selected.name)return;
    setBusy(true);setError("");setNotice("");
    try{
      await api("/events/"+selected.id+"/"+action,"POST",{confirmation:answer},auth.csrf_token);
      window.location.reload();
    }catch(e){setError((e as Error).message);setBusy(false);}
  }
  async function getPreview(){
    setBusy(true);setError("");setNotice("");
    try{
      const result=await api<ResetPreview>("/events/resets/preview","POST",
        {mode,areas:mode==="selected"?areas:[]},auth.csrf_token);
      setPreview(result);setTyped("");
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function execute(){
    if(!preview||typed!==preview.confirmation_phrase)return;
    if(!window.confirm("Final confirmation: perform this event reset now?"))return;
    setBusy(true);setError("");setNotice("");
    try{
      const result=await api<{mode:ResetMode;snapshot_id:string}>(
        "/events/resets/"+preview.plan_id+"/execute",
        "POST",{confirmation:typed},auth.csrf_token
      );
      if(result.mode==="fresh"){window.location.reload();return;}
      setNotice("Selected reset completed. The previous state is preserved in a downloadable snapshot.");
      resetDraft();
      await refresh();
    }catch(e){setError((e as Error).message);setBusy(false);}
    finally{setBusy(false);}
  }
  async function downloadSnapshot(record:Snapshot){
    if(!selected)return;
    setBusy(true);setError("");
    try{
      const data=await api<unknown>(
        "/events/"+selected.id+"/snapshots/"+record.id
      );
      const file=new Blob([JSON.stringify(data,null,2)],{type:"application/json"});
      const url=URL.createObjectURL(file);
      const anchor=document.createElement("a");
      anchor.href=url;anchor.download="nightjar-event-snapshot-"+record.id+".json";
      document.body.append(anchor);anchor.click();anchor.remove();
      URL.revokeObjectURL(url);
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  return <section className="panel event-manager" aria-label="Event Data Management">
    <div className="panel-heading"><div><span className="eyebrow">EVENT DATA MANAGEMENT</span>
      <h3>Events, archives & resets</h3>
      <p className="muted">Each event has isolated people, teams, tickets and attendance. Staff accounts and shared Supervisely connection settings are kept separately.</p></div>
      <button className="btn secondary" onClick={()=>void refresh()} disabled={busy}>
        <RefreshCw size={15}/> Refresh</button></div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}
    <div className="event-detail-strip">
      <CalendarDays size={20}/>
      <div><strong>{selected?.name||"Loading event…"}</strong>
        <span>{selected?.status==="archived"?"Archived · Read-only":"Active event"} · {data?.items.length??0} saved event(s)</span>
      </div>
      {selected&&<button className={"btn "+(canReset?"secondary":"primary")}
        disabled={busy} onClick={()=>void archiveOrReopen(canReset?"archive":"reopen")}>
        {canReset?<><Archive size={14}/> Archive event</>:<><RotateCcw size={14}/> Reopen event</>}
      </button>}
    </div>
    <form onSubmit={e=>void createEvent(e)} className="event-create-row">
      <label>Create a new independent event<input minLength={3} maxLength={200}
        value={newName} required placeholder="e.g. Annotatiathon 2027"
        onChange={e=>setNewName(e.target.value)}/></label>
      <button type="submit" className="btn primary" disabled={busy}><Plus size={16}/> Create & switch</button>
    </form>
    <div className="event-reset-section">
      <div className="event-reset-heading"><div><span className="eyebrow">CONTROLLED RESET</span>
        <h3>Reset the current event</h3>
        <p className="muted small">Always preview changes before executing. Remote Supervisely annotations and datasets are never deleted.</p></div>
        <ShieldAlert size={23}/></div>
      {!canReset&&<div className="advisory"><AlertTriangle size={18}/>
        Archived events are read-only. Reopen this event or switch to an active event to reset it.</div>}
      <div className="event-mode-selector">
        <label className={mode==="selected"?"active":""}><input type="radio" name="reset-mode"
          checked={mode==="selected"} disabled={!canReset}
          onChange={()=>{setMode("selected");resetDraft();}}/>
          <strong>Reset selected areas</strong><span>Clear only the chosen operational state.</span></label>
        <label className={mode==="fresh"?"active":""}><input type="radio" name="reset-mode"
          checked={mode==="fresh"} disabled={!canReset}
          onChange={()=>{setMode("fresh");resetDraft();}}/>
          <strong>Full fresh start</strong><span>Archive the entire current event and create an independent clean run.</span></label>
      </div>
      {mode==="selected"&&<div className="event-area-options">{AREAS.map(area=>
        <label key={area.key} className="event-area-choice">
          <input type="checkbox" checked={areas.includes(area.key)} disabled={!canReset}
            onChange={e=>{setAreas(old=>e.target.checked?[...old,area.key]:old.filter(x=>x!==area.key));resetDraft();}}/>
          <div><strong>{area.title}</strong><span>{area.description}</span></div>
        </label>)}</div>}
      <button className="btn secondary" disabled={busy||!canReset||(mode==="selected"&&!areas.length)}
        onClick={()=>void getPreview()}><ShieldAlert size={16}/> Preview reset impact</button>
      {preview&&<div className="reset-confirmation-panel">
        <span className="eyebrow">RESET IMPACT PREVIEW</span>
        <strong>{preview.event_name}</strong>
        <p className="muted small">Preview expires {stamp(preview.expires_at)}. Selected areas: {preview.areas.join(", ")}.</p>
        <div className="reset-count-grid">{Object.entries(preview.counts).map(([key,value])=>
          <div key={key}><span>{key.replace(/_/g," ")}</span><strong>{value}</strong></div>)}</div>
        {preview.notes.map(note=><p key={note} className="muted small">• {note}</p>)}
        <div className="reset-phrase"><label>Type <strong>{preview.confirmation_phrase}</strong> to confirm
          <input autoComplete="off" spellCheck={false} value={typed}
            onChange={e=>setTyped(e.target.value)}/></label>
          <button className="btn danger-outline" disabled={busy||typed!==preview.confirmation_phrase}
            onClick={()=>void execute()}><ShieldAlert size={15}/> Execute reset</button></div>
      </div>}
    </div>
    <div className="event-snapshots">
      <span className="eyebrow">RESET SNAPSHOTS</span>
      <h3>Event snapshots</h3>
      <p className="muted small">Snapshots live in PostgreSQL and contain personal attendee data. Download and store them securely before high-impact changes. They are not independent database backups or automatic restore points.</p>
      {!snapshots.length?<p className="muted small">No reset snapshots saved for this event.</p>:
        snapshots.map(record=><div className="snapshot-row" key={record.id}>
          <span><CheckCircle2 size={16}/> {stamp(record.created_at)} · {record.id.slice(0,8)}</span>
          <button className="btn secondary" disabled={busy} onClick={()=>void downloadSnapshot(record)}>
            <Download size={15}/> Download JSON</button>
        </div>)}
    </div>
  </section>;
}
