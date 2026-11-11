import { useCallback, useEffect, useState } from "react";
import { formatLisbonTime } from "./event_time";
import type React from "react";
import {
  CheckCircle2, Clock3, RefreshCw, Search, Undo2, UserRoundCheck,
  UsersRound, X
} from "lucide-react";
import { api, refreshSuperviselyRoster, type Auth, type Person, type PeopleResponse } from "./api";

type Summary={registered:number;present:number;not_arrived:number};
type TableDialog={person:Person;mode:"assign"|"edit"};

export function AttendanceScreen({auth}:{auth:Auth}) {
  const allowed=auth.staff.roles.some(r=>["organiser","monitor","reception"].includes(r));
  const [search,setSearch]=useState("");
  const [filter,setFilter]=useState<"all"|"present"|"missing">("all");
  const [page,setPage]=useState(1);
  const [people,setPeople]=useState<PeopleResponse>({items:[],total:0,page:1,size:100});
  const [summary,setSummary]=useState<Summary|null>(null);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [working,setWorking]=useState<string|null>(null);
  const [tableDialog,setTableDialog]=useState<TableDialog|null>(null);
  const [tableInput,setTableInput]=useState("");

  const refresh=useCallback(async()=>{
    try{
      const [roster,counts]=await Promise.all([
        api<PeopleResponse>("/people?kind=participant&search="+encodeURIComponent(search)+
          "&attendance="+filter+"&page="+page+"&size=100"),
        api<Summary>("/attendance/summary")
      ]);
      setPeople(roster);setSummary(counts);setError("");
    }catch(e){setError((e as Error).message);}
  },[search,page,filter]);

  useEffect(()=>{
    void refresh();
    const timer=window.setInterval(()=>{if(!document.hidden)void refresh();},5000);
    return ()=>window.clearInterval(timer);
  },[refresh]);

  async function syncNative(){
    try{
      const result=await refreshSuperviselyRoster(auth.csrf_token,true);
      await refresh();
      if(result?.status==="ok")setNotice(
        "Supervisely roster refreshed: "+result.created+" added, "+result.updated+" updated."
      );
      if(result?.status==="needs_review")setNotice(
        "Some Supervisely identities require organiser review in People & Teams."
      );
    }catch(e){setError("Supervisely sync: "+(e as Error).message);}
  }

  function openTable(person:Person,mode:TableDialog["mode"]){
    setTableDialog({person,mode});
    setTableInput(person.table_number?String(person.table_number):"");
    setError("");
  }

  async function applyAttendance(person:Person,checkOut:boolean,tableNumber?:number):Promise<boolean>{
    if(!allowed||working!==null)return false;
    setWorking(person.id);setError("");setNotice("");
    try{
      const result=await api<{changed:boolean;table_number:number|null}>(
        "/attendance/"+person.id+"/"+(checkOut?"reverse":"check-in"),
        "POST",checkOut?undefined:{table_number:tableNumber??person.table_number},
        auth.csrf_token
      );
      setNotice(!result.changed?"Attendance was already up to date.":
        checkOut?(result.table_number===null?
          person.name+" checked out. The unused table assignment was cleared.":
          person.name+" checked out. The teammate's table remains assigned."):
          person.name+" checked in at table "+result.table_number+".");
      await refresh();
      return true;
    }catch(e){setError((e as Error).message);return false;}
    finally{setWorking(null);}
  }

  async function checkInOrOut(person:Person){
    if(!allowed||working!==null)return;
    if(person.check_in_at){await applyAttendance(person,true);return;}
    if(person.table_number===null){openTable(person,"assign");return;}
    await applyAttendance(person,false);
  }

  async function submitTable(e:React.FormEvent<HTMLFormElement>){
    e.preventDefault();
    if(!tableDialog||working!==null)return;
    const number=Number(tableInput.trim());
    if(!Number.isInteger(number)||number<1||number>10000){
      setError("Enter a whole-number table between 1 and 10000.");
      return;
    }
    const {person,mode}=tableDialog;
    if(mode==="assign"){
      if(await applyAttendance(person,false,number))setTableDialog(null);
      return;
    }
    if(!person.team_id)return;
    setWorking(person.id);setError("");setNotice("");
    try{
      await api("/attendance/teams/"+person.team_id+"/table",
        "PUT",{table_number:number},auth.csrf_token);
      setNotice("Table updated to "+number+" for "+(person.team_name||person.name)+".");
      setTableDialog(null);
      await refresh();
    }catch(err){setError((err as Error).message);}
    finally{setWorking(null);}
  }

  const rows=people.items;
  return <div className="page-body admission-viewport">
    <div className="page-heading"><div>
      <span className="eyebrow">EVENT OPERATIONS · ADMISSION</span>
      <h2>Attendance & Check-in</h2>
      <p className="muted">Participants only. Check in directly when a team table is known;
        teammates inherit the assigned table. Check-out is one click.</p>
    </div><button className="btn secondary" onClick={()=>void syncNative()}>
      <RefreshCw size={15}/> Refresh from Supervisely</button></div>

    {error&&<div role="alert" className="alert danger">{error}</div>}
    {notice&&<div role="status" className="alert success">{notice}</div>}
    <div className="stat-grid compact">
      <div className="stat-card"><UsersRound size={20}/><span className="muted small">Participants</span><strong>{summary?.registered??"—"}</strong></div>
      <div className="stat-card"><UserRoundCheck size={20}/><span className="muted small">Checked in</span><strong>{summary?.present??"—"}</strong></div>
      <div className="stat-card"><Clock3 size={20}/><span className="muted small">Not arrived</span><strong>{summary?.not_arrived??"—"}</strong></div>
    </div>

    <section className="panel table-panel">
      <div className="panel-header-line">
        <div className="search-box"><Search size={17}/><input aria-label="Find participants"
          placeholder="Search participant name or email" value={search}
          onChange={e=>{setSearch(e.target.value);setPage(1);}}/></div>
        <div className="segmented">
          {([["all","All"],["present","Present"],["missing","Not arrived"]] as const).map(([k,v])=>
            <button key={k} className={filter===k?"selected":""}
              onClick={()=>{setFilter(k);setPage(1);}}>{v}</button>)}
        </div>
      </div>
      <div className="table-scroll"><table className="data-table">
        <thead><tr><th>Participant</th><th>Team</th><th>Table</th><th>Arrival</th><th>Action</th></tr></thead>
        <tbody>{rows.map(person=><tr key={person.id}>
          <td><div className="person-name"><div className="mini-avatar">{person.name[0]?.toUpperCase()}</div>
            <div><strong>{person.name}</strong><span>{person.email}</span></div></div></td>
          <td>{person.team_name||"—"}</td>
          <td>{person.table_number?
            <strong>Table {person.table_number}</strong>:
            <span className="muted small">Unassigned</span>}
            {allowed&&person.team_id&&<button className="link-button"
              disabled={working!==null} onClick={()=>openTable(person,"edit")}>Edit</button>}</td>
          <td>{person.check_in_at?
            <span className="membership joined"><CheckCircle2 size={12}/>
              {formatLisbonTime(person.check_in_at)}</span>:
            <span className="membership">Not arrived</span>}</td>
          <td>{allowed?
            <button className={"btn "+(person.check_in_at?"secondary":"primary")}
              disabled={working!==null} onClick={()=>void checkInOrOut(person)}>
              {person.check_in_at?<><Undo2 size={14}/> Check out</>:
                <><UserRoundCheck size={14}/> Check in</>}
            </button>:
            <span className="muted small">View only</span>}</td>
        </tr>)}</tbody>
      </table>{!rows.length&&<div className="empty-table">No participants match this view.</div>}</div>
      <div className="table-pagination"><span>Page {page} · {people.total} matching participants</span>
        <button className="btn secondary" disabled={page<=1} onClick={()=>setPage(v=>v-1)}>Previous</button>
        <button className="btn secondary" disabled={page*100>=people.total}
          onClick={()=>setPage(v=>v+1)}>Next</button>
      </div>
    </section>

    {tableDialog&&<div className="modal-overlay" role="presentation">
      <form className="modal-card" role="dialog" aria-modal="true"
        aria-labelledby="attendance-table-title" onSubmit={e=>void submitTable(e)}>
        <div className="modal-heading">
          <h3 id="attendance-table-title">{tableDialog.mode==="assign"?
            "Assign table & check in":"Edit team table"}</h3>
          <button type="button" className="icon-button" aria-label="Close dialog"
            disabled={working!==null} onClick={()=>setTableDialog(null)}><X size={19}/></button>
        </div>
        <p className="muted">Participant: <strong>{tableDialog.person.name}</strong>.
          {tableDialog.person.team_name?
            " The table is shared with "+tableDialog.person.team_name+".":
            " Set the table for this participant."}</p>
        <label>Table number
          <input type="number" inputMode="numeric" min={1} max={10000}
            required autoFocus value={tableInput}
            onChange={e=>setTableInput(e.target.value)}/>
        </label>
        {error&&<p className="alert danger" role="alert">{error}</p>}
        <div className="modal-actions">
          <button className="btn secondary" type="button" disabled={working!==null}
            onClick={()=>setTableDialog(null)}>Cancel</button>
          <button className="btn primary" type="submit" disabled={working!==null}>
            {working?"Saving…":tableDialog.mode==="assign"?"Assign & check in":"Save table"}
          </button>
        </div>
      </form>
    </div>}
  </div>;
}
