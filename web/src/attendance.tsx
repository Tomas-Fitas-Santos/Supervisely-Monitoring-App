import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Clock3, RefreshCw, Search, Undo2, UserRoundCheck, UsersRound } from "lucide-react";
import { api, type Auth, type Person, type PeopleResponse } from "./api";

type Summary = { registered: number; present: number; not_arrived: number };
export function AttendanceScreen({auth}:{auth:Auth}) {
  const allowed = auth.staff.roles.some(r=>r==="organiser"||r==="reception");
  const [search,setSearch] = useState("");
  const [filter,setFilter] = useState<"all"|"present"|"missing">("all");
  const [page,setPage] = useState(1);
  const [people,setPeople] = useState<PeopleResponse>({items:[],total:0,page:1,size:50});
  const [summary,setSummary] = useState<Summary|null>(null);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [working,setWorking]=useState<string|null>(null);
  const refresh=useCallback(async()=>{
    try{
      const [p,s]=await Promise.all([
        api<PeopleResponse>("/people?search="+encodeURIComponent(search)+"&attendance="+filter+"&page="+page+"&size=100"),
        api<Summary>("/attendance/summary")
      ]);
      setPeople(p);setSummary(s);setError("");
    }catch(e){setError((e as Error).message);}
  },[search,page,filter]);
  useEffect(()=>{
    void refresh();
    const timer=window.setInterval(()=>{if(!document.hidden)void refresh();},5000);
    return ()=>window.clearInterval(timer);
  },[refresh]);
  async function toggle(person:Person) {
    if(!allowed)return;
    const reverse=Boolean(person.check_in_at);
    if(!window.confirm(
      (reverse?"Reverse check-in for ":"Confirm physical arrival of ")+person.name+
      (person.table_number?" at table "+person.table_number:"")+"?"
    ))return;
    let tableNumber: number | undefined;
    if(!reverse&&!person.table_number) {
      const raw=window.prompt(
        "Assign a table number for "+(person.team_name||person.name)+
        ". Their teammate will automatically use the same table.",
        ""
      );
      if(raw===null)return;
      const n=Number(raw.trim());
      if(!Number.isInteger(n)||n<1||n>10000){setError("Enter a whole-number table between 1 and 10000.");return;}
      tableNumber=n;
    }
    setWorking(person.id);setError("");setNotice("");
    try {
      const result=await api<{changed:boolean;table_number:number}>(
        "/attendance/"+person.id+"/"+(reverse?"reverse":"check-in"),"POST",
        reverse?undefined:{table_number:tableNumber??person.table_number},auth.csrf_token
      );
      setNotice(result.changed?(reverse?"Check-in reversed.":"Arrival recorded. ")+
        (reverse?"":"Table "+result.table_number+"."):"Already recorded; no duplicate created.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setWorking(null);}
  }
  async function editTeamTable(person:Person) {
    if(!person.team_id||!allowed)return;
    const raw=window.prompt("Correct the table assigned to "+person.team_name+":",String(person.table_number??""));
    if(raw===null)return;
    const n=Number(raw.trim());
    if(!Number.isInteger(n)||n<1||n>10000){setError("Enter a whole-number table between 1 and 10000.");return;}
    setWorking(person.id);setError("");
    try{
      await api("/attendance/teams/"+person.team_id+"/table","PUT",{table_number:n},auth.csrf_token);
      setNotice("Team table updated to "+n+".");await refresh();
    }catch(e){setError((e as Error).message);}finally{setWorking(null);}
  }
  const rows=people.items;
  return <div className="page-body">
    <div className="page-heading"><div><span className="eyebrow">EVENT OPERATIONS · ADMISSION</span>
      <h2>Attendance & Check-in</h2>
      <p className="muted">Search by name or email. Enter the team table at the first arrival; partners inherit it automatically.</p>
      </div><button className="btn secondary" onClick={()=>void refresh()}><RefreshCw size={15}/> Refresh</button></div>
    {error&&<div role="alert" className="alert danger">{error}</div>}
    {notice&&<div role="status" className="alert success">{notice}</div>}
    <div className="stat-grid compact">
      <div className="stat-card"><UsersRound size={20}/><span className="muted small">Registered</span><strong>{summary?.registered??"—"}</strong></div>
      <div className="stat-card"><UserRoundCheck size={20}/><span className="muted small">Checked in</span><strong>{summary?.present??"—"}</strong></div>
      <div className="stat-card"><Clock3 size={20}/><span className="muted small">Not arrived</span><strong>{summary?.not_arrived??"—"}</strong></div>
    </div>
    <section className="panel table-panel">
      <div className="panel-header-line">
        <div className="search-box"><Search size={17}/><input aria-label="Find attendees"
          placeholder="Search name or email" value={search}
          onChange={e=>{setSearch(e.target.value);setPage(1);}}/></div>
        <div className="segmented">
          {([["all","All"],["present","Present"],["missing","Not arrived"]] as const).map(([k,v])=>
            <button key={k} className={filter===k?"selected":""} onClick={()=>{setFilter(k);setPage(1);}}>{v}</button>)}
        </div>
      </div>
      <div className="table-scroll"><table className="data-table">
        <thead><tr><th>Person</th><th>Team</th><th>Table</th><th>Arrival</th><th>Action</th></tr></thead>
        <tbody>{rows.map(person=><tr key={person.id}>
          <td><div className="person-name"><div className="mini-avatar">{person.name[0]?.toUpperCase()}</div><div>
            <strong>{person.name}</strong><span>{person.email}</span></div></div></td>
          <td>{person.team_name|| (person.kind==="monitor"?"Monitoring Team":"—")}</td>
          <td>{person.table_number?<strong>Table {person.table_number}</strong>:
            <span className="muted small">Unassigned</span>}
            {allowed&&person.team_id&&<button className="link-button" disabled={working!==null}
              onClick={()=>void editTeamTable(person)}>Edit</button>}</td>
          <td>{person.check_in_at?<span className="membership joined"><CheckCircle2 size={12}/> {new Date(person.check_in_at*1000).toLocaleString()}</span>:
            <span className="membership">Not arrived</span>}</td>
          <td>{allowed?<button className={"btn "+(person.check_in_at?"secondary":"primary")}
            disabled={working!==null} onClick={()=>void toggle(person)}>
            {person.check_in_at?<><Undo2 size={14}/> Reverse</>:<><UserRoundCheck size={14}/> Check in</>}
          </button>:<span className="muted small">View only</span>}</td>
        </tr>)}</tbody></table>{!rows.length&&<div className="empty-table">No attendees match this view.</div>}</div>
      <div className="table-pagination"><span>Showing page {page} · {people.total} matching records</span>
        <button className="btn secondary" disabled={page<=1} onClick={()=>setPage(p=>p-1)}>Previous</button>
        <button className="btn secondary" disabled={page*100>=people.total} onClick={()=>setPage(p=>p+1)}>Next</button>
      </div>
    </section>
    <p className="muted small" style={{marginTop:16}}>Reception verifies identity by name and email. Every arrival and table correction is audited; there is no QR-code requirement.</p>
  </div>;
}
