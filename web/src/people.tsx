import { confirmAction } from "./dialogs";
import { formatLisbonTime } from "./event_time";
import { useCallback, useEffect, useState } from "react";
import type React from "react";
import {
  Check, ChevronLeft, ChevronRight, ClipboardCopy, FileSpreadsheet, Filter,
  Plus, RefreshCw, Search, ShieldCheck, Trash2, Upload, UserRound, UsersRound, X
} from "lucide-react";
import { api, refreshSuperviselyRoster, type Auth, type PeopleResponse, type Person, type Team, type TeamResponse } from "./api";
import { NativeMembershipPanel } from "./native";
import { InvitationBatches } from "./invitation_batches";
import { MonitorPairPanel } from "./monitor_pairs";
import { useTableSort, type SortValue } from "./table_sort";

type PersonForm = {name:string;email:string;kind:"participant"|"monitor";team_name:string;
  date_of_birth:string;phone:string;under_18:string;image_consent:string;
  data_consent:string;food_allergies:string;};
const emptyForm: PersonForm = {name:"",email:"",kind:"participant",team_name:"",
  date_of_birth:"",phone:"",under_18:"",image_consent:"",
  data_consent:"",food_allergies:""};
function consentLabel(value:string|undefined){
  if(!value)return "—";
  const key=value.trim().toLocaleLowerCase();
  if(["yes","sim","true","1","autorizo","authorized"].includes(key))return "Yes";
  if(["no","não","nao","false","0","não autorizo"].includes(key))return "No";
  return value;
}


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
    setMapping({name:choose(["monitor_name","name","full name","nome","participant name","display_name"]),
      email:choose(["monitor_email","email","e-mail","mail","email address"]),
      kind:choose(["event_role","role","type","kind","group"]),team:choose(["team","team_name","team name","equipa"])});
  }
  function payload() {
    return {csv_text:text,mapping:{name:mapping.name,email:mapping.email,
      kind:mapping.kind||null,team:mapping.team||null},default_kind:defaultKind};
  }
  async function execute(commit:boolean) {
    setBusy(true);setError("");
    try {
      if(!mapping.name||!mapping.email)throw Error("Map the name and email columns.");
      if(commit && !await confirmAction("Import this roster into PostgreSQL? Existing records may be updated."))return;
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
  const [mode,setMode]=useState<"teams"|"people"|"setup">("people");
  const [teams,setTeams]=useState<Team[]>([]);
  const [people,setPeople]=useState<PeopleResponse>({items:[],total:0,page:1,size:50});
  const [sort,setSort]=useState<"name"|"check_in"|"size">("name");
  const [search,setSearch]=useState("");
  const [kind,setKind]=useState("all");
  const [membership,setMembership]=useState("all");
  const [page,setPage]=useState(1);
  const rosterSort=useTableSort(()=>setPage(1));
  const memberSort=useTableSort();
  const sortQuery=rosterSort.query;
  const [selectedTeam,setSelectedTeam]=useState<string|null>(null);
  const [selected,setSelected]=useState<string[]>([]);
  const [showImporter,setShowImporter]=useState(false);
  const [editing,setEditing]=useState<Person|null|undefined>(undefined);
  const [personForm,setPersonForm]=useState<PersonForm>(emptyForm);
  const [modalError,setModalError]=useState("");
  const [modalStatus,setModalStatus]=useState("");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [message,setMessage]=useState("");
  const [inviteVersion,setInviteVersion]=useState(0);
  const [updated,setUpdated]=useState<Date|null>(null);
  const refresh=useCallback(async()=>{
    try {
      const [groups,individuals]=await Promise.all([
        api<TeamResponse>("/teams?sort="+sort),
        api<PeopleResponse>("/people?search="+encodeURIComponent(search)+
          "&kind="+kind+"&membership="+membership+"&page="+page+"&size=50"+sortQuery)
      ]);
      setTeams(groups.teams);setPeople(individuals);setUpdated(new Date());setError("");
    }catch(exc){setError((exc as Error).message);}
  },[sort,search,kind,membership,page,sortQuery]);
  useEffect(()=>{
    void refresh();
    const timer=window.setInterval(()=>{if(!document.hidden)void refresh();},5000);
    return ()=>window.clearInterval(timer);
  },[refresh]);
  // Also reconcile joined native identities periodically while this page is
  // open. The shared helper coalesces requests and applies a cooldown;
  // errors remain available through the explicit Refresh from Supervisely.
  useEffect(()=>{
    let active=true;
    async function refreshMembership(){
      if(document.hidden)return;
      try {
        const outcome=await refreshSuperviselyRoster(auth.csrf_token);
        if(!active||!outcome)return;
        if(outcome.status==="ok"&&(outcome.updated||outcome.created)){
          setInviteVersion(v=>v+1);
          await refresh();
        }
        if(outcome.status==="needs_review"){
          setMessage("Supervisely identities have conflicting matches. Open Invitations & membership → Verify & reconcile the team.");
        }
      }catch{
        // Show native connection errors only on explicit refresh.
      }
    }
    void refreshMembership();
    const timer=window.setInterval(()=>void refreshMembership(),90000);
    return ()=>{active=false;window.clearInterval(timer);};
  },[auth.csrf_token,refresh]);
  async function syncNative(){
    try {
      const result=await refreshSuperviselyRoster(auth.csrf_token,true);
      setInviteVersion(v=>v+1);
      await refresh();
      if(result?.status==="needs_review"){
        setMessage("Supervisely identities need review. Open membership sync to resolve conflicts.");
      }else if(result?.status==="ok"){
        setMessage("Supervisely synced: "+result.created+" added, "+result.updated+
          " updated, "+result.unmatched_native+" unlinked. "+
          (result.role_warnings?"Some roles need review.":""));
      }else if(result?.status==="archived"){
        setMessage("Archived events are read-only.");
      }
    }catch(e){setError("Supervisely sync: "+(e as Error).message);}
  }
  const activeTeam=teams.find(t=>t.id===selectedTeam);
  const shownTeams=teams.filter(t=>!search||
    t.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())||
    t.members.some(p=>p.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())||
      p.email.toLocaleLowerCase().includes(search.toLocaleLowerCase())));
  const competitionCount=teams.filter(t=>t.type==="competition").length;
  const selectedPeople=people.items.filter(p=>selected.includes(p.id));
  function openEdit(person?:Person) {
    setEditing(person||null);
    setPersonForm(person?{name:person.name,email:person.email,kind:person.kind,
      team_name:person.team_name||"",
      date_of_birth:person.date_of_birth||"",phone:person.phone||"",
      under_18:person.under_18||"",image_consent:person.image_consent||"",
      data_consent:person.data_consent||"",food_allergies:person.food_allergies||"",
    }:emptyForm);
    setError("");setMessage("");setModalError("");setModalStatus("");
  }
  async function savePerson(e:React.FormEvent) {
    e.preventDefault();setBusy(true);setError("");setModalError("");
    const desiredNativeRole=personForm.kind==="monitor"?"manager":"annotator";
    const isNativeChange=Boolean(editing?.supervisely_login &&
      (editing.kind!==personForm.kind || (editing.supervisely_role!=="admin" &&
      editing.supervisely_role!==desiredNativeRole)));
    setModalStatus(isNativeChange?"Updating and verifying the Supervisely team role…":
      "Saving the person to Nightjar…");
    try {
      // Saving a joined person's role waits for the verified Supervisely update.
      // On failure, the dialog stays open AND shows the actual API error here.
      await api(editing?"/people/"+editing.id:"/people",editing?"PATCH":"POST",
        personForm,auth.csrf_token);
      setEditing(undefined);
      setMessage("Person saved. Any joined member's role was verified in Supervisely.");
      setInviteVersion(v=>v+1);
      await refresh();
    }catch(exc){
      setModalError((exc as Error).message+
        (isNativeChange?" Refresh from Supervisely if the remote role may have changed.":""));
    }finally{setModalStatus("");setBusy(false);}
  }
  async function removeTeam(team:Team){
    if(team.type!=="competition")return;
    if(!await confirmAction(
      "Remove team "+team.name+" from this event? Its members will remain in the roster "+
      "but become unassigned. Historical data will be preserved. Teams with labeling jobs "+
      "cannot be removed."
    ))return;
    setBusy(true);setError("");setMessage("");
    try {
      const result=await api<{people_unassigned:number;monitor_pair_members_released:number}>(
        "/teams/"+team.id+"/remove","POST",undefined,auth.csrf_token);
      setSelectedTeam(null);
      await refresh();
      setMessage("Team removed. "+result.people_unassigned+" participants unassigned"+
        (result.monitor_pair_members_released?
          "; "+result.monitor_pair_members_released+" monitor pair members released":"")+
        ". No Supervisely memberships or jobs were deleted.");
    }catch(exc){setError((exc as Error).message);}
    finally{setBusy(false);}
  }
  async function deactivate(person:Person) {
    if(!await confirmAction("Deactivate "+person.name+"? Historical records will remain available."))return;
    setBusy(true);
    try {
      await api("/people/"+person.id,"PATCH",{active:false},auth.csrf_token);
      setEditing(undefined);setInviteVersion(v=>v+1);
      await refresh();setMessage("Person deactivated.");
    }catch(exc){setError((exc as Error).message);}
    finally{setBusy(false);}
  }
  async function synchronizeRole(person:Person) {
    setBusy(true);setModalError("");setModalStatus("Verifying Supervisely team membership…");
    try {
      const updated=await api<Person>("/people/"+person.id+"/supervisely-role",
        "POST",undefined,auth.csrf_token);
      setEditing({...person,...updated});
      setMessage("Supervisely role verified: "+(updated.supervisely_role||"not yet joined")+".");
      await refresh();
    }catch(exc){setModalError((exc as Error).message);}
    finally{setModalStatus("");setBusy(false);}
  }
  async function setMonitorApproval(person:Person,approved:boolean){
    setBusy(true);setModalError("");setModalStatus("Updating monitor registration approval…");
    try{
      const changed=await api<Person>("/people/"+person.id+"/monitor-registration",
        "PUT",{approved},auth.csrf_token);
      setEditing({...person,monitor_registration_approved:changed.monitor_registration_approved});
      setMessage(approved?"Monitor may now create a Nightjar-only account with their approved roster email.":
        "New Nightjar registration disabled for this monitor.");
      await refresh();
    }catch(exc){setModalError((exc as Error).message);}
    finally{setModalStatus("");setBusy(false);}
  }
  async function copyEmails() {
    if(selectedPeople.length===0)return;
    try {
      await navigator.clipboard.writeText(selectedPeople.map(p=>p.email).join(","));
      setMessage("Copied "+selectedPeople.length+" email addresses. Paste into Supervisely to send invitations.");
    }catch{setError("Could not access the clipboard; allow clipboard access and retry.");}
  }
  async function markSent() {
    if(!await confirmAction("Confirm you actually sent these invitations through the Supervisely website. Copying email addresses is not enough."))return;
    setBusy(true);setError("");
    try {
      await api("/people/invitations-reported","POST",{person_ids:selectedPeople.map(p=>p.id)},auth.csrf_token);
      setSelected([]);setInviteVersion(v=>v+1);
      setMessage("Invitation attempts recorded; native acceptance is not yet verified.");
      await refresh();
    }catch(exc){setError((exc as Error).message);}
    finally{setBusy(false);}
  }
  const showSensitive=auth.staff.roles.some(r=>r==="organiser"||r==="reception");
  const showAllergies=showSensitive||auth.staff.roles.includes("distribution");
  function personal(value:string|undefined){
    return showSensitive?(value?.trim()||"—"):"Restricted";
  }
  function peopleTable(items:Person[],full=true) {
    const columnSort=full?rosterSort:memberSort;
    const rows=full?items:memberSort.rows(items,(person,key)=>person[key as keyof Person] as SortValue);
    const header=columnSort.header;
    return <div className="table-scroll"><table className="data-table compact-roster-table">
      <thead><tr>{full&&admin&&<th className="check-cell">Select</th>}
        {header("name","Name")}{full&&<>
          {header("email","Email")}{showSensitive?header("phone","Phone"):<th>Phone</th>}
          {showSensitive?header("under_18","Under 18"):<th>Under 18</th>}
          {showSensitive?header("image_consent","Image consent"):<th>Image consent</th>}
          {showAllergies?header("food_allergies","Allergies"):<th>Allergies</th>}
        </>}
        {header("team_name","Team")}{header("supervisely_user_id","Supervisely ID")}
        {header("membership_status","Supervisely")}{header("check_in_at","Check-in")}
        {admin&&<th>Manage</th>}</tr></thead>
      <tbody>{rows.map(p=><tr key={p.id}>
        {full&&admin&&<td><input aria-label={"Select "+p.name} type="checkbox"
          checked={selected.includes(p.id)} onChange={e=>setSelected(prev=>
            e.target.checked?prev.length<50?[...prev,p.id]:prev:prev.filter(id=>id!==p.id))}/></td>}
        <td><div className="person-name"><div className="mini-avatar">{p.name.slice(0,1).toUpperCase()}</div>
          <div><strong>{p.name}</strong><span>{p.kind==="monitor"?"Monitor":"Participant"}{!full?" · "+p.email:""}</span></div></div></td>
        {full&&<>
          <td>{p.email}</td><td>{personal(p.phone)}</td>
          <td>{personal(showSensitive?consentLabel(p.under_18):"")}</td>
          <td>{personal(showSensitive?consentLabel(p.image_consent):"")}</td>
          <td>{showAllergies?(p.food_allergies?.trim()||"—"):"Restricted"}</td>
        </>}
        <td>{p.team_name||"Unassigned"}</td>
        <td>{p.supervisely_user_id!=null?
          <strong title={p.supervisely_login||"Verified Supervisely member"}>
            {p.supervisely_user_id}</strong>:
          <span className="muted small">Not linked</span>}</td>
        <td><span className={"membership "+(p.membership_status==="joined"?"joined":"")}>
          {statusName(p.membership_status)}</span>
          {p.membership_status==="joined"&&!p.supervisely_user_id&&
            <span className="native-role-chip">ID missing — refresh</span>}
          {p.supervisely_role&&<span className="native-role-chip">{p.supervisely_role}</span>}</td>
        <td>{p.kind==="monitor"?"Not required":p.check_in_at?formatLisbonTime(p.check_in_at):"Not arrived"}</td>
        {admin&&<td><button className="link-button" onClick={()=>openEdit(p)}>Edit</button></td>}
      </tr>)}</tbody>
    </table>{items.length===0&&<div className="empty-table">No people match these filters.</div>}</div>;
  }
  return <div className={"page-body "+((mode==="people"||mode==="teams")?"roster-viewport":"")}>
    <div className="page-heading"><div><span className="eyebrow">EVENT SETUP · PEOPLE</span>
      <h2>People & Teams</h2><p className="muted">Manage the roster, competition teams and Supervisely invitations.
        Event-day attendance is handled separately in Check-in. No monitor-to-team assignments are needed for shared annotation review.</p>
    </div><div className="heading-actions">
      <span className="last-refresh">{updated?"Updated "+updated.toLocaleTimeString():"Loading…"}</span>
      <button className="btn secondary" onClick={()=>void syncNative()}><RefreshCw size={16}/> Refresh from Supervisely</button>
      {admin&&<button className="btn primary" onClick={()=>openEdit()}><Plus size={17}/> Add person</button>}
    </div></div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {message&&<div className="alert success" role="status">{message}</div>}
    <div className="stat-grid compact">
      <div className="stat-card"><UsersRound size={19}/><span className="muted small">Competition teams</span><strong>{competitionCount}</strong></div>
      <div className="stat-card"><UserRound size={19}/><span className="muted small">Monitoring staff</span><strong>{teams.find(t=>t.type==="monitoring")?.members.length??0}</strong></div>
      <div className="stat-card"><Check size={19}/><span className="muted small">People listed</span><strong>{teams.reduce((n,t)=>n+t.members.length,0)}</strong></div>
    </div>
    <nav className="workspace-subtabs" aria-label="People and teams sections">
      <button className={mode==="people"?"active":""} onClick={()=>setMode("people")}>People & monitors</button>
      <button className={mode==="teams"?"active":""} onClick={()=>{setMode("teams");setSelectedTeam(null);}}>Teams</button>
      {admin&&<button className={mode==="setup"?"active":""} onClick={()=>setMode("setup")}>Invitations & membership</button>}
    </nav>
    {admin&&mode==="setup"&&<div className="toolbar-secondary">
      <button className="btn secondary" onClick={()=>setShowImporter(v=>!v)}><FileSpreadsheet size={17}/>
        {showImporter?"Close roster import":"Import CSV roster"}</button>
      <span className="muted small">Refresh from Supervisely updates joined participants and monitors, and adds new members with verified native email. Unmatched identities require review below.</span>
    </div>}
    {showImporter&&admin&&mode==="setup"&&<RosterImporter auth={auth} onComplete={()=>{setShowImporter(false);setInviteVersion(v=>v+1);void refresh();}}/>}
    {admin&&mode==="setup"&&<InvitationBatches auth={auth} version={inviteVersion}
      onComplete={()=>{setInviteVersion(v=>v+1);void refresh();}}/>}
    {admin&&mode==="setup"&&<NativeMembershipPanel auth={auth} onApplied={()=>{setInviteVersion(v=>v+1);void refresh();}}/>}
    {admin&&mode==="setup"&&<MonitorPairPanel auth={auth} onChanged={()=>void refresh()}/>}
    {(mode==="teams"||mode==="people")&&<section className="panel table-panel">
      <div className="panel-header-line">
        <div className="filter-bar">
          <div className="search-box"><Search size={16}/><input aria-label="Search roster"
            placeholder={mode==="teams"?"Search teams, members or emails…":"Search participants or monitors…"} value={search} onChange={e=>{setSearch(e.target.value);setPage(1);}}/></div>
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
        <div className="team-detail roster-team-detail">
          <button className="back-link" onClick={()=>setSelectedTeam(null)}><ChevronLeft size={16}/> All teams</button>
          <div className="team-detail-header">
            <div className="roster-team-detail-title"><h3>{activeTeam.name}</h3>
              <span className="muted small">{activeTeam.type==="monitoring"?"Monitoring staff":"Competition team"}</span>
            </div>
            <div className="roster-team-detail-stats">
              {admin&&activeTeam.type==="competition"&&
                <button type="button" className="btn danger-outline"
                  disabled={busy} onClick={()=>void removeTeam(activeTeam)}>
                  <Trash2 size={15}/> Remove team
                </button>}
              <span className="soft-pill">{activeTeam.members.length} members</span>
              {activeTeam.type==="competition"&&<>
                <span className="soft-pill">{activeTeam.checked_in} checked in</span>
                <span className="soft-pill">{activeTeam.table_number?"Table "+activeTeam.table_number:"Table not assigned"}</span>
              </>}
            </div>
          </div>
          <div className="roster-team-member-table">{peopleTable(activeTeam.members,false)}</div>
        </div>:
        <div className="team-list roster-team-grid">
          <div className="roster-team-grid-heading">
            <strong>{shownTeams.length} {shownTeams.length===1?"team":"teams"}</strong>
            <span className="muted small">Select a team to inspect its members and Supervisely status.</span>
          </div>
          {shownTeams.map(team=><button className={"team-row roster-team-card "+(team.type==="monitoring"?"pinned":"")}
            key={team.id} onClick={()=>setSelectedTeam(team.id)}>
            <div className="roster-team-card-top">
              <div className={"team-symbol "+(team.type==="monitoring"?"monitoring":"")}><UsersRound size={19}/></div>
              <div className="team-main"><strong>{team.name}</strong>
                <span>{team.type==="monitoring"?"Event monitoring staff":"Competition team"}</span>
              </div>
              <ChevronRight size={18} className="muted-icon"/>
            </div>
            <div className="roster-team-card-members">
              {team.members.length?team.members.map(member=><span key={member.id}>{member.name}</span>):
                <span className="muted">No members assigned</span>}
            </div>
            <div className="roster-team-card-footer">
              <span>{team.members.length} {team.members.length===1?"member":"members"}</span>
              {team.type==="competition"&&<>
                <span>{team.checked_in} checked in</span>
                <span>{team.table_number?"Table "+team.table_number:"No table"}</span>
              </>}
            </div>
          </button>)}
          {!shownTeams.length&&<div className="empty-table roster-team-grid-empty">No teams match this search.</div>}
        </div>)
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
    </section>}
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
          <p className="muted small">Supervisely role: Monitor → Manager, Participant → Annotator.
            The organiser keeps Admin. Joined members are updated remotely when you save.</p>
          {editing?.supervisely_login&&<div className="settings-note">
            <ShieldCheck size={18}/>
            <div><p className="muted small">
              Joined in Supervisely as {editing.supervisely_login} ({editing.supervisely_role}).
              Expected role for the selection: {personForm.kind==="monitor"?"Manager":"Annotator"}.
              Press <strong>Save person</strong> to update both systems.
            </p>
            {editing.kind===personForm.kind && editing.supervisely_role!=="admin" &&
              editing.supervisely_role!==(personForm.kind==="monitor"?"manager":"annotator") &&
              <button type="button" className="btn secondary" disabled={busy}
                onClick={()=>void synchronizeRole(editing)}>Sync existing saved role</button>}
            </div>
          </div>}
          <div className="form-grid triple roster-private-form">
            <label>Date of birth<input placeholder="YYYY-MM-DD" value={personForm.date_of_birth}
              onChange={e=>setPersonForm({...personForm,date_of_birth:e.target.value})}/></label>
            <label>Phone<input type="tel" value={personForm.phone}
              onChange={e=>setPersonForm({...personForm,phone:e.target.value})}/></label>
            {([
              ["under_18","Under 18"],
              ["image_consent","Image use consent"],
              ["data_consent","Data processing consent"],
            ] as const).map(([key,label])=><label key={key}>{label}
              <select value={personForm[key]} onChange={e=>setPersonForm({...personForm,[key]:e.target.value})}>
                <option value="">Not recorded</option><option value="Yes">Yes</option>
                <option value="No">No</option>
              </select>
            </label>)}
            <label>Food allergies / intolerances<input value={personForm.food_allergies}
              onChange={e=>setPersonForm({...personForm,food_allergies:e.target.value})}
              placeholder="Record dietary needs; blank if unknown"/></label>
          </div>
          {personForm.kind==="participant"&&<label>Competition team name<input value={personForm.team_name}
            onChange={e=>setPersonForm({...personForm,team_name:e.target.value})}
            placeholder="e.g. Team 014"/></label>}
          {editing?.kind==="monitor"&&personForm.kind==="monitor"&&<div className="settings-note">
            <ShieldCheck size={18}/>
            <div><p className="muted small">
              Nightjar account creation: <strong>{editing.monitor_registration_approved?
                "Approved":"Not approved"}</strong>. Only approved monitor emails
              can register after verifying email ownership.
            </p>
            <button type="button" disabled={busy} className="btn secondary"
              onClick={()=>void setMonitorApproval(editing,!editing.monitor_registration_approved)}>
              {editing.monitor_registration_approved?
                "Disable new registrations":"Approve for Nightjar registration"}
            </button></div>
          </div>}
          {modalError&&<div role="alert" className="alert danger">{modalError}</div>}
          {busy&&modalStatus&&<div role="status" className="alert" aria-live="polite">{modalStatus}</div>}
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
