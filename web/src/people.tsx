import { useCallback, useEffect, useState } from "react";
import type React from "react";
import {
  Check, ChevronLeft, ChevronRight, ClipboardCopy, FileSpreadsheet, Filter,
  Plus, RefreshCw, Search, Upload, UserRound, UsersRound, X
} from "lucide-react";
import { api, type Auth, type PeopleResponse, type Person, type Team, type TeamResponse } from "./api";
import { NativeMembershipPanel } from "./native";
import { MonitorAssignmentsPanel } from "./monitoring";
import { MonitorPairPanel } from "./monitor_pairs";

type PersonForm = {name:string;email:string;kind:"participant"|"monitor";team_name:string};
const emptyForm: PersonForm = {name:"",email:"",kind:"participant",team_name:""};

function statusName(status: string) {
  return ({joined:"Joined", pending:"Pending (verified)", reported_sent:"Reported invited",
    not_invited:"Not invited"} as Record<string,string>)[status] || "Unknown";
}

function RosterImporter({auth,onComplete}:{auth:Auth;onComplete:()=>void}) {
  const [text,setText]=useState("");
  const [headers,setHeaders]=useState<string[]>([]);
  const [mapping,setMapping]=useState({name:"",email:"",kind:"",team:""});
  const [defaultKind,setDefaultKind]=useState<"participant"|"monitor">("participant");
  const [filename,setFilename]=useState("");
  const [preview,setPreview]=useState<{rows:number;created:number;changed:number;unchanged:number}|null>(null);
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  async function openFile(e:React.ChangeEvent<HTMLInputElement>) {
    const file=e.target.files?.[0];
    if(!file)return;
    setError("");setPreview(null);
    if(file.size>5_000_000){setError("CSV must be smaller than 5 MB.");return;}
    const next=await file.text();
    const names=(next.replace(/^\uFEFF/,"").split(/\r?\n/,1)[0]||"")
      .split(",").map(v=>v.trim().replace(/^"|"$/g,""));
    setText(next);setHeaders(names);setFilename(file.name);
    function choose(words:string[]) {return names.find(h=>words.includes(h.trim().toLowerCase()))||"";}
    setMapping({name:choose(["name","full name","nome","participant name","display_name"]),
      email:choose(["email","e-mail","mail","email address"]),
      kind:choose(["role","type","kind","group"]),team:choose(["team","team_name","team name","equipa"])});
  }
  function payload() {
    return {csv_text:text,mapping:{name:mapping.name,email:mapping.email,
      kind:mapping.kind||null,team:mapping.team||null},default_kind:defaultKind};
  }
  async function execute(commit:boolean) {
    setBusy(true);setError("");
    try {
      if(!mapping.name||!mapping.email)throw Error("Map the name and email columns.");
      if(commit && !window.confirm("Import this roster into PostgreSQL? Existing records may be updated."))return;
      const report=await api<typeof preview>(commit?"/roster/import":"/roster/preview",
        "POST",payload(),auth.csrf_token);
      setPreview(report);
      if(commit){setText("");setHeaders([]);setFilename("");onComplete();}
    } catch(exc){setError((exc as Error).message);}
    finally{setBusy(false);}
  }
  function column(field:keyof typeof mapping,label:string) {
    return <label>{label}<select value={mapping[field]} onChange={e=>
      {setMapping({...mapping,[field]:e.target.value});setPreview(null);}}>
      <option value="">Not mapped</option>{headers.map((h,i)=><option key={i} value={h}>{h}</option>)}
    </select></label>;
  }
  return <section className="panel import-panel">
    <div className="panel-heading"><div><span className="eyebrow">ROSTER IMPORT</span>
      <h3>Import people from CSV</h3><p className="muted">Preview changes before updating the shared database. Existing attendance and Supervisely status are preserved.</p></div>
      <FileSpreadsheet size={27} className="faint-icon"/></div>
    <label className="file-drop"><Upload size={22}/><strong>{filename||"Choose a CSV file"}</strong>
      <span>CSV · Up to 5 MB</span><input type="file" accept=".csv,text/csv" onChange={e=>void openFile(e)}/></label>
    {headers.length>0&&<div className="mapping-grid">
      {column("name","Name column *")}{column("email","Email column *")}
      {column("team","Team column")}{column("kind","Role column")}
      <label>Default role when not mapped<select value={defaultKind} onChange={e=>
        setDefaultKind(e.target.value as "participant"|"monitor")}>
        <option value="participant">Participant</option><option value="monitor">Monitor</option>
      </select></label>
    </div>}
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {preview&&<div className="preview-line"><Check size={17}/>
      {preview.rows} rows · {preview.created} new · {preview.changed} changed · {preview.unchanged} unchanged
    </div>}
    {headers.length>0&&<div className="actions-right">
      <button className="btn secondary" disabled={busy} onClick={()=>void execute(false)}>Preview changes</button>
      <button className="btn primary" disabled={busy||!preview} onClick={()=>void execute(true)}>
        {busy?"Working…":"Confirm import"}</button>
    </div>}
  </section>;
}

export function PeopleScreen({auth}:{auth:Auth}) {
  const admin=auth.staff.roles.includes("organiser");
  const [mode,setMode]=useState<"teams"|"people">("teams");
  const [teams,setTeams]=useState<Team[]>([]);
  const [people,setPeople]=useState<PeopleResponse>({items:[],total:0,page:1,size:50});
  const [sort,setSort]=useState<"name"|"check_in"|"size">("name");
  const [search,setSearch]=useState("");
  const [kind,setKind]=useState("all");
  const [membership,setMembership]=useState("all");
  const [page,setPage]=useState(1);
  const [selectedTeam,setSelectedTeam]=useState<string|null>(null);
  const [selected,setSelected]=useState<string[]>([]);
  const [showImporter,setShowImporter]=useState(false);
  const [editing,setEditing]=useState<Person|null|undefined>(undefined);
  const [personForm,setPersonForm]=useState<PersonForm>(emptyForm);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [message,setMessage]=useState("");
  const [updated,setUpdated]=useState<Date|null>(null);
  const refresh=useCallback(async()=>{
    try {
      const [groups,individuals]=await Promise.all([
        api<TeamResponse>("/teams?sort="+sort),
        api<PeopleResponse>("/people?search="+encodeURIComponent(search)+
          "&kind="+kind+"&membership="+membership+"&page="+page+"&size=50")
      ]);
      setTeams(groups.teams);setPeople(individuals);setUpdated(new Date());setError("");
    }catch(exc){setError((exc as Error).message);}
  },[sort,search,kind,membership,page]);
  useEffect(()=>{
    void refresh();
    const timer=window.setInterval(()=>{if(!document.hidden)void refresh();},5000);
    return ()=>window.clearInterval(timer);
  },[refresh]);
  const activeTeam=teams.find(t=>t.id===selectedTeam);
  const shownTeams=teams.filter(t=>t.type==="monitoring"||
    !search||t.name.toLowerCase().includes(search.toLowerCase())||
    t.members.some(p=>p.name.toLowerCase().includes(search.toLowerCase())));
  const selectedPeople=people.items.filter(p=>selected.includes(p.id));
  function openEdit(person?:Person) {
    setEditing(person||null);
    setPersonForm(person?{name:person.name,email:person.email,kind:person.kind,
      team_name:person.team_name||""}:emptyForm);
    setError("");setMessage("");
  }
  async function savePerson(e:React.FormEvent) {
    e.preventDefault();setBusy(true);setError("");
    try {
      await api(editing?"/people/"+editing.id:"/people",editing?"PATCH":"POST",personForm,auth.csrf_token);
      setEditing(undefined);setMessage("Person saved.");await refresh();
    }catch(exc){setError((exc as Error).message);}
    finally{setBusy(false);}
  }
  async function deactivate(person:Person) {
    if(!window.confirm("Deactivate "+person.name+"? Historical records will remain available."))return;
    setBusy(true);
    try {
      await api("/people/"+person.id,"PATCH",{active:false},auth.csrf_token);
      setEditing(undefined);await refresh();setMessage("Person deactivated.");
    }catch(exc){setError((exc as Error).message);}
    finally{setBusy(false);}
  }
  async function copyEmails() {
    if(selectedPeople.length===0)return;
    try {
      await navigator.clipboard.writeText(selectedPeople.map(p=>p.email).join(","));
      setMessage("Copied "+selectedPeople.length+" email addresses. Paste into Supervisely to send invitations.");
    }catch{setError("Could not access the clipboard; allow clipboard access and retry.");}
  }
  async function markSent() {
    if(!window.confirm("Confirm you actually sent these invitations through the Supervisely website. Copying email addresses is not enough."))return;
    setBusy(true);setError("");
    try {
      await api("/people/invitations-reported","POST",{person_ids:selectedPeople.map(p=>p.id)},auth.csrf_token);
      setSelected([]);setMessage("Invitation attempts recorded; native acceptance is not yet verified.");
      await refresh();
    }catch(exc){setError((exc as Error).message);}
    finally{setBusy(false);}
  }
  function peopleTable(items:Person[],selectable=true) {
    return <div className="table-scroll"><table className="data-table">
      <thead><tr>{selectable&&admin&&<th className="check-cell">Select</th>}
        <th>Name</th><th>Role</th><th>Team</th><th>Supervisely</th><th>Check-in</th>
        {admin&&<th>Manage</th>}</tr></thead>
      <tbody>{items.map(p=><tr key={p.id}>
        {selectable&&admin&&<td><input aria-label={"Select "+p.name} type="checkbox"
          checked={selected.includes(p.id)} onChange={e=>setSelected(prev=>
            e.target.checked?prev.length<50?[...prev,p.id]:prev:prev.filter(id=>id!==p.id))}/></td>}
        <td><div className="person-name"><div className="mini-avatar">{p.name.slice(0,1).toUpperCase()}</div>
          <div><strong>{p.name}</strong><span>{p.email}</span></div></div></td>
        <td><span className="soft-pill">{p.kind==="monitor"?"Monitor":"Participant"}</span></td>
        <td>{p.team_name||"—"}</td>
        <td><span className={"membership "+(p.membership_status==="joined"?"joined":"")}>
          {statusName(p.membership_status)}</span>{p.supervisely_role&&<span className="native-role-chip">{p.supervisely_role}</span>}</td>
        <td>{p.check_in_at?new Date(p.check_in_at*1000).toLocaleTimeString():"Not arrived"}</td>
        {admin&&<td><button className="link-button" onClick={()=>openEdit(p)}>Edit</button></td>}
      </tr>)}</tbody>
    </table>{items.length===0&&<div className="empty-table">No people match these filters.</div>}</div>;
  }
  return <div className="page-body">
    <div className="page-heading"><div><span className="eyebrow">EVENT SETUP · PEOPLE</span>
      <h2>People & Teams</h2><p className="muted">Manage your event roster and prepare invitations for the shared Supervisely team.</p>
    </div><div className="heading-actions">
      <span className="last-refresh">{updated?"Updated "+updated.toLocaleTimeString():"Loading…"}</span>
      <button className="btn secondary" onClick={()=>void refresh()}><RefreshCw size={16}/> Sync view</button>
      {admin&&<button className="btn primary" onClick={()=>openEdit()}><Plus size={17}/> Add person</button>}
    </div></div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {message&&<div className="alert success" role="status">{message}</div>}
    <div className="stat-grid compact">
      <div className="stat-card"><UsersRound size={19}/><span className="muted small">Competition teams</span><strong>{Math.max(teams.length-1,0)}</strong></div>
      <div className="stat-card"><UserRound size={19}/><span className="muted small">Monitoring staff</span><strong>{teams.find(t=>t.type==="monitoring")?.members.length??0}</strong></div>
      <div className="stat-card"><Check size={19}/><span className="muted small">People listed</span><strong>{teams.reduce((n,t)=>n+t.members.length,0)}</strong></div>
    </div>
    {admin&&<div className="toolbar-secondary">
      <button className="btn secondary" onClick={()=>setShowImporter(v=>!v)}><FileSpreadsheet size={17}/>
        {showImporter?"Close roster import":"Import CSV roster"}</button>
      <span className="muted small">Sync view refreshes local PostgreSQL only. Use the native sync panel below to check real Supervisely membership.</span>
    </div>}
    {showImporter&&admin&&<RosterImporter auth={auth} onComplete={()=>{setShowImporter(false);void refresh();}}/>}
    {admin&&<NativeMembershipPanel auth={auth} onApplied={()=>void refresh()}/>}
    {admin&&<MonitorAssignmentsPanel auth={auth} onApplied={()=>void refresh()}/>}
    {admin&&<MonitorPairPanel auth={auth} onChanged={()=>void refresh()}/>}
    <section className="panel table-panel">
      <div className="panel-header-line">
        <div className="segmented"><button className={mode==="teams"?"selected":""}
          onClick={()=>{setMode("teams");setSelectedTeam(null);}}><UsersRound size={16}/> Teams</button>
          <button className={mode==="people"?"selected":""}
          onClick={()=>{setMode("people");setSelectedTeam(null);}}><UserRound size={16}/> People</button></div>
        <div className="filter-bar">
          <div className="search-box"><Search size={16}/><input aria-label="Search roster"
            placeholder="Search names or teams…" value={search} onChange={e=>{setSearch(e.target.value);setPage(1);}}/></div>
          {mode==="teams"?
            <label className="inline-filter"><Filter size={15}/><select value={sort} onChange={e=>setSort(e.target.value as typeof sort)}>
              <option value="name">Alphabetical</option><option value="check_in">Check-in time</option><option value="size">Team size</option>
            </select></label>:
            <><select aria-label="Role filter" value={kind} onChange={e=>{setKind(e.target.value);setPage(1);}}>
              <option value="all">All roles</option><option value="participant">Participants</option><option value="monitor">Monitors</option></select>
              <select aria-label="Supervisely status filter" value={membership}
                onChange={e=>{setMembership(e.target.value);setPage(1);}}>
                <option value="all">All statuses</option><option value="not_invited">Not invited</option>
                <option value="reported_sent">Reported sent</option><option value="pending">Verified pending</option>
                <option value="joined">Joined</option></select></>}
        </div>
      </div>
      {mode==="teams" ? (activeTeam?
        <div className="team-detail">
          <button className="back-link" onClick={()=>setSelectedTeam(null)}><ChevronLeft size={16}/> All teams</button>
          <div className="team-detail-header"><h3>{activeTeam.name}</h3>
            {activeTeam.type==="competition"&&<span className="soft-pill">Responsible monitor: {activeTeam.monitor_name||"Unassigned"}</span>}
            <span className="soft-pill">{activeTeam.members.length} member(s)</span></div>
          {peopleTable(activeTeam.members,false)}
        </div>:
        <div className="team-list">{shownTeams.map(team=><button className={"team-row "+(team.type==="monitoring"?"pinned":"")}
          key={team.id} onClick={()=>setSelectedTeam(team.id)}>
          <div className={"team-symbol "+(team.type==="monitoring"?"monitoring":"")}><UsersRound size={19}/></div>
          <div className="team-main"><strong>{team.name}</strong>
            <span>{team.type==="monitoring"?"Pinned · Event staff":team.members.map(p=>p.name).join(" · ")||"No members assigned"}</span></div>
          <span className="team-count">{team.members.length} members</span>
          {team.type==="competition"&&<span className="team-monitor-name">{team.monitor_name||"Monitor unassigned"}</span>}
          <span className="team-attendance">{team.checked_in} checked in</span>
          <ChevronRight size={18} className="muted-icon"/>
        </button>)}{shownTeams.length===0&&<div className="empty-table">No teams found.</div>}</div>)
        :<><div className="table-summary"><span>{people.total} matching people · Page {page}</span>
          {admin&&selectedPeople.length>0&&<div className="batch-actions">
            <span>{selectedPeople.length}/50 selected</span>
            <button className="btn secondary" onClick={()=>void copyEmails()}><ClipboardCopy size={15}/> Copy comma-separated emails</button>
            <button className="btn secondary" disabled={busy} onClick={()=>void markSent()}>Mark sent in Supervisely</button>
          </div>}</div>
          {peopleTable(people.items)}
          <div className="table-pagination"><button className="btn secondary" disabled={page<=1}
            onClick={()=>setPage(v=>v-1)}><ChevronLeft size={16}/> Previous</button>
            <span>{Math.min((page-1)*50+1,people.total)}–{Math.min(page*50,people.total)} of {people.total}</span>
            <button className="btn secondary" disabled={page*50>=people.total}
              onClick={()=>setPage(v=>v+1)}>Next <ChevronRight size={16}/></button></div></>}
    </section>
    {editing!==undefined&&<div className="modal-overlay" role="presentation">
      <div className="modal-card" role="dialog" aria-modal="true" aria-label={editing?"Edit person":"Add person"}>
        <div className="modal-heading"><h3>{editing?"Edit person":"Add person"}</h3>
          <button className="icon-button" onClick={()=>setEditing(undefined)} aria-label="Close"><X size={19}/></button></div>
        <form onSubmit={e=>void savePerson(e)} className="form-stack">
          <label>Full name<input value={personForm.name} required maxLength={200}
            onChange={e=>setPersonForm({...personForm,name:e.target.value})}/></label>
          <label>Email<input type="email" required value={personForm.email}
            onChange={e=>setPersonForm({...personForm,email:e.target.value})}/></label>
          <label>Role<select value={personForm.kind} onChange={e=>setPersonForm({...personForm,
            kind:e.target.value as PersonForm["kind"]})}>
            <option value="participant">Participant</option><option value="monitor">Monitor</option></select></label>
          {personForm.kind==="participant"&&<label>Competition team name<input value={personForm.team_name}
            onChange={e=>setPersonForm({...personForm,team_name:e.target.value})}
            placeholder="e.g. Team 014"/></label>}
          <div className="modal-actions">{editing&&<button type="button" disabled={busy}
            className="btn danger-outline" onClick={()=>void deactivate(editing)}>Deactivate</button>}
            <button className="btn secondary" type="button" onClick={()=>setEditing(undefined)}>Cancel</button>
            <button className="btn primary" disabled={busy} type="submit">{busy?"Saving…":"Save person"}</button>
          </div>
        </form>
      </div>
    </div>}
  </div>;
}
