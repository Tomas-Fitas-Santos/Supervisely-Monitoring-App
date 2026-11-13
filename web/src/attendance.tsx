import { RefreshButton } from "./refresh_button";
import { useCallback, useEffect, useState } from "react";
import { formatLisbonTime } from "./event_time";
import type React from "react";
import {
  CheckCircle2, Clock3, RefreshCw, Search, Undo2, UserRoundCheck,
  UsersRound, X
} from "lucide-react";
import { api, refreshSuperviselyRoster, type Auth, type Person, type PeopleResponse, type Team, type TeamResponse } from "./api";
import { useTableSort } from "./table_sort";

type Summary={registered:number;present:number;not_arrived:number};

export function AttendanceScreen({auth}:{auth:Auth}) {
  const organiser=auth.staff.roles.includes("organiser");
  const allowed=auth.staff.roles.some(r=>["organiser","monitor","reception"].includes(r));
  const [search,setSearch]=useState("");
  const [filter,setFilter]=useState<"all"|"present"|"missing">("all");
  const [page,setPage]=useState(1);
  const peopleSort=useTableSort(()=>setPage(1));
  const teamSort=useTableSort();
  const sortQuery=peopleSort.query;
  const [people,setPeople]=useState<PeopleResponse>({items:[],total:0,page:1,size:100});
  const [summary,setSummary]=useState<Summary|null>(null);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [working,setWorking]=useState<string|null>(null);
  const [section,setSection]=useState<"checkin"|"tables">("checkin");
  const [teams,setTeams]=useState<Team[]>([]);
  const [tableDialog,setTableDialog]=useState<Team|null>(null);
  const [tableInput,setTableInput]=useState("");

  const refresh=useCallback(async()=>{
    try{
      const [roster,counts,grouped]=await Promise.all([
        api<PeopleResponse>("/people?kind=participant&search="+encodeURIComponent(search)+
          "&attendance="+filter+"&page="+page+"&size=100"+sortQuery),
        api<Summary>("/attendance/summary"),
        api<TeamResponse>("/teams")
      ]);
      setPeople(roster);setSummary(counts);setTeams(grouped.teams.filter(t=>t.type==="competition"));setError("");
    }catch(e){setError((e as Error).message);return false;}
  },[search,page,filter,sortQuery]);

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

  function openTable(team:Team){
    setTableDialog(team);
    setTableInput(team.table_number?String(team.table_number):"");
    setError("");
  }

  async function applyAttendance(person:Person,checkOut:boolean):Promise<boolean>{
    if(!allowed||working!==null)return false;
    setWorking(person.id);setError("");setNotice("");
    try{
      const result=await api<{changed:boolean;table_number:number|null}>(
        "/attendance/"+person.id+"/"+(checkOut?"reverse":"check-in"),
        "POST",undefined,
        auth.csrf_token
      );
      setNotice(!result.changed?"Attendance was already up to date.":
        checkOut?person.name+" checked out.":person.name+" checked in.");
      await refresh();
      return true;
    }catch(e){setError((e as Error).message);return false;}
    finally{setWorking(null);}
  }

  async function checkInOrOut(person:Person){
    if(!allowed||working!==null)return;
    if(person.check_in_at){await applyAttendance(person,true);return;}
    await applyAttendance(person,false);
  }

  async function submitTable(e:React.FormEvent<HTMLFormElement>){
    e.preventDefault();
    if(!tableDialog||working!==null)return;
    const number=tableInput.trim()===""?null:Number(tableInput.trim());
    if(number!==null&&(!Number.isInteger(number)||number<1||number>10000)){
      setError("Enter a whole-number table between 1 and 10000.");
      return;
    }
    setWorking(tableDialog.id);setError("");setNotice("");
    try{
      await api("/attendance/teams/"+tableDialog.id+"/table",
        "PUT",{table_number:number},auth.csrf_token);
      setNotice("Table updated for "+tableDialog.name+".");
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
      <p className="muted">Check in verified participants with one click. Assign team tables separately.</p>
    </div><div className="heading-actions">
      <RefreshButton refresh={refresh}>Refresh check-in list</RefreshButton>
      {organiser&&<button className="btn secondary" onClick={()=>void syncNative()}>
        <RefreshCw size={15}/> Sync Supervisely membership</button>}
    </div></div>

    {error&&<div role="alert" className="alert danger">{error}</div>}
    {notice&&<div role="status" className="alert success">{notice}</div>}
    <div className="stat-grid compact">
      <div className="stat-card"><UsersRound size={20}/><span className="muted small">Participants</span><strong>{summary?.registered??"—"}</strong></div>
      <div className="stat-card"><UserRoundCheck size={20}/><span className="muted small">Checked in</span><strong>{summary?.present??"—"}</strong></div>
      <div className="stat-card"><Clock3 size={20}/><span className="muted small">Not arrived</span><strong>{summary?.not_arrived??"—"}</strong></div>
    </div>

    <nav className="workspace-subtabs" aria-label="Attendance sections">
      <button className={section==="checkin"?"active":""} onClick={()=>setSection("checkin")}>Check-in</button>
      <button className={section==="tables"?"active":""} onClick={()=>setSection("tables")}>Table assignment</button>
    </nav>
    {section==="tables"&&<section className="panel table-panel"><div className="table-scroll"><table className="data-table">
      <thead><tr>{teamSort.header("name","Team")}{teamSort.header("members","Checked-in members")}
        {teamSort.header("table","Table")}<th>Action</th></tr></thead>
      <tbody>{teamSort.rows(teams.filter(t=>t.checked_in>0),(team,key)=>
        key==="name"?team.name:key==="table"?team.table_number:
          team.members.filter(p=>p.check_in_at).map(p=>p.name).join(", ")
      ).map(team=><tr key={team.id}>
        <td><strong>{team.name}</strong></td>
        <td>{team.members.filter(p=>p.check_in_at).map(p=>p.name).join(", ")}</td>
        <td>{team.table_number?"Table "+team.table_number:"Unassigned"}</td>
        <td>{allowed&&<button className="btn secondary" disabled={working!==null} onClick={()=>openTable(team)}>Assign / change</button>}</td>
      </tr>)}</tbody></table></div></section>}
    {section==="checkin"&&<section className="panel table-panel">
      <div className="panel-header-line">
        <div className="search-box"><Search size={17}/><input aria-label="Find participants by name, email or team"
          placeholder="Search name, email or team" value={search}
          onChange={e=>{setSearch(e.target.value);setPage(1);}}/></div>
        <div className="segmented">
          {([["all","All"],["present","Present"],["missing","Not arrived"]] as const).map(([k,v])=>
            <button key={k} className={filter===k?"selected":""}
              onClick={()=>{setFilter(k);setPage(1);}}>{v}</button>)}
        </div>
      </div>
      <div className="table-scroll"><table className="data-table">
        <thead><tr>{peopleSort.header("name","Participant")}{peopleSort.header("team_name","Team")}
          {peopleSort.header("supervisely_user_id","Supervisely ID")}{peopleSort.header("table_number","Table")}
          {peopleSort.header("check_in_at","Arrival")}<th>Action</th></tr></thead>
        <tbody>{rows.map(person=><tr key={person.id}>
          <td><div className="person-name"><div className="mini-avatar">{person.name[0]?.toUpperCase()}</div>
            <div><strong>{person.name}</strong><span>{person.email}</span></div></div></td>
          <td>{person.team_name||"—"}</td>
          <td>{person.supervisely_user_id===null?<strong style={{color:"#b42318"}}>None</strong>:
            <>{person.supervisely_user_id}{person.membership_status!=="joined"||!person.native_verified_at?
              <span className="membership"> Not verified</span>:null}</>}</td>
          <td>{person.table_number?
            <strong>Table {person.table_number}</strong>:
            <span className="muted small">Unassigned</span>}
          </td>
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
    </section>}

    {tableDialog&&<div className="modal-overlay" role="presentation">
      <form className="modal-card" role="dialog" aria-modal="true"
        aria-labelledby="attendance-table-title" onSubmit={e=>void submitTable(e)}>
        <div className="modal-heading">
          <h3 id="attendance-table-title">Assign team table</h3>
          <button type="button" className="icon-button" aria-label="Close dialog"
            disabled={working!==null} onClick={()=>setTableDialog(null)}><X size={19}/></button>
        </div>
        <p className="muted">Team: <strong>{tableDialog.name}</strong>. Leave blank to clear the table.</p>
        <label>Table number
          <input type="number" inputMode="numeric" min={1} max={10000}
            autoFocus value={tableInput}
            onChange={e=>setTableInput(e.target.value)}/>
        </label>
        {error&&<p className="alert danger" role="alert">{error}</p>}
        <div className="modal-actions">
          <button className="btn secondary" type="button" disabled={working!==null}
            onClick={()=>setTableDialog(null)}>Cancel</button>
          <button className="btn primary" type="submit" disabled={working!==null}>
            {working?"Saving…":"Save table"}
          </button>
        </div>
      </form>
    </div>}
  </div>;
}
