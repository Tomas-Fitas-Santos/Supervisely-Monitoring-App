import { useCallback, useEffect, useState } from "react";
import type React from "react";
import {
  Activity as ActivityIcon, Award, CalendarDays, CheckCircle2,
  ClipboardCheck, Clock3, MapPin, Plus, RefreshCw, Save, Trash2, Trophy
} from "lucide-react";
import { api, type Auth, type PeopleResponse, type Person, type Team, type TeamResponse } from "./api";

type Activity={id:string;name:string;description:string;location:string;
  start_at:number|null;end_at:number|null;status:"scheduled"|"live"|"completed"|"cancelled";
  awards_finalised_at:number|null;active:boolean};
type Result={id:string;subject_type:"person"|"team";subject_id:string;
  subject_name:string;rank:number;score_points:number|null;notes:string};
type Prize={id:string;category_id:string;category_name:string;min_rank:number;
  max_rank:number;quantity:number};
type AwardRecord={id:string;person_id:string;person_name:string;prize_id:string;
  distribution_id:string;awarded_at:number};
type Detail={activity:Activity;results:Result[];prizes:Prize[];awards:AwardRecord[]};
type Category={id:string;name:string;active:boolean};
const localDate=(value:number|null)=>value?new Date(value*1000).toLocaleString():"Not scheduled";
const parseInput=(value:string)=>value?Math.floor(new Date(value).getTime()/1000):null;

export function ActivitiesScreen({auth}:{auth:Auth}) {
  const canEdit=auth.staff.roles.some(r=>r==="organiser"||r==="activity"||r==="monitor");
  const organiser=auth.staff.roles.includes("organiser");
  const [section,setSection]=useState<"schedule"|"results">("schedule");
  const [items,setItems]=useState<Activity[]>([]);
  const [selected,setSelected]=useState("");
  const [detail,setDetail]=useState<Detail|null>(null);
  const [people,setPeople]=useState<Person[]>([]);
  const [teams,setTeams]=useState<Team[]>([]);
  const [categories,setCategories]=useState<Category[]>([]);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [busy,setBusy]=useState(false);
  const [addOpen,setAddOpen]=useState(false);
  const [name,setName]=useState("");
  const [description,setDescription]=useState("");
  const [location,setLocation]=useState("");
  const [start,setStart]=useState("");
  const [end,setEnd]=useState("");
  const [subjectType,setSubjectType]=useState<"person"|"team">("team");
  const [subjectId,setSubjectId]=useState("");
  const [rank,setRank]=useState(1);
  const [score,setScore]=useState("");
  const [notes,setNotes]=useState("");
  const [categoryId,setCategoryId]=useState("");
  const [prizeFrom,setPrizeFrom]=useState(1);
  const [prizeTo,setPrizeTo]=useState(1);
  const [prizeQty,setPrizeQty]=useState(1);
  const refresh=useCallback(async()=>{
    const [a,t,c]=await Promise.all([
      api<{items:Activity[]}>("/activities"),
      api<TeamResponse>("/teams"),
      api<{items:Category[]}>("/tickets/categories"),
    ]);
    setItems(a.items);
    setSelected(current=>a.items.some(x=>x.id===current)?current:(a.items[0]?.id||""));
    setTeams(t.teams.filter(x=>x.type!=="monitoring"));
    setCategories(c.items);setCategoryId(old=>old||c.items.find(x=>x.active)?.id||"");
    let rows:Person[]=[];let page=1;let total=0;
    do{
      const p=await api<PeopleResponse>("/people?size=200&page="+page+"&kind=participant");
      rows=rows.concat(p.items);total=p.total;page+=1;
    }while(rows.length<total&&page<=26);
    setPeople(rows);
  },[]);
  const loadDetail=useCallback(async()=>{
    if(!selected){setDetail(null);return;}
    try{setDetail(await api<Detail>("/activities/"+selected));setError("");}
    catch(e){setError((e as Error).message);}
  },[selected]);
  useEffect(()=>{void refresh().catch(e=>setError((e as Error).message));},[refresh]);
  useEffect(()=>{void loadDetail();},[loadDetail]);
  useEffect(()=>{
    const interval=window.setInterval(()=>{
      if(!document.hidden){void refresh().catch(()=>{});void loadDetail();}
    },10000);
    return()=>window.clearInterval(interval);
  },[refresh,loadDetail]);
  async function execute<T>(fn:()=>Promise<T>,message:string){
    setBusy(true);setError("");setNotice("");
    try{await fn();setNotice(message);await refresh();await loadDetail();}
    catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function addActivity(e:React.FormEvent){
    e.preventDefault();
    setBusy(true);setError("");setNotice("");
    try{
      const result=await api<Activity>("/activities","POST",{
        name,description,location,start_at:parseInput(start),end_at:parseInput(end),
      },auth.csrf_token);
      setAddOpen(false);setName("");setDescription("");setLocation("");setStart("");setEnd("");
      await refresh();setSelected(result.id);setNotice("Activity created.");
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function changeStatus(status:Activity["status"]){
    if(!selected)return;
    await execute(()=>api("/activities/"+selected,"PATCH",{status},auth.csrf_token),
      "Activity status changed to "+status+".");
  }
  async function saveResult(e:React.FormEvent){
    e.preventDefault();if(!selected||!subjectId)return;
    await execute(()=>api("/activities/"+selected+"/results","PUT",{
      subject_type:subjectType,subject_id:subjectId,rank,
      score_points:score.trim()?Number(score):null,notes,
    },auth.csrf_token),"Result recorded.");
    setSubjectId("");setRank(1);setScore("");setNotes("");
  }
  async function addPrize(e:React.FormEvent){
    e.preventDefault();if(!selected||!categoryId)return;
    await execute(()=>api("/activities/"+selected+"/prizes","POST",{
      category_id:categoryId,min_rank:prizeFrom,max_rank:prizeTo,quantity:prizeQty,
    },auth.csrf_token),"Prize rule saved.");
  }
  async function removeResult(result:Result){
    if(!selected||!window.confirm("Delete ranked result for "+result.subject_name+"?"))return;
    await execute(()=>api("/activities/"+selected+"/results/"+result.id,"DELETE",undefined,
      auth.csrf_token),"Result removed.");
  }
  async function removePrize(prize:Prize){
    if(!selected||!window.confirm("Remove this prize rule?"))return;
    await execute(()=>api("/activities/"+selected+"/prizes/"+prize.id,"DELETE",undefined,
      auth.csrf_token),"Prize rule removed.");
  }
  async function finalise(){
    if(!selected||!window.confirm(
      "Finalise the activity's results and allocate all matching prize entitlements? "+
      "Prize allocations will be recorded in Tickets & Rewards and results will be locked. "+
      "This does NOT physically issue tickets."
    ))return;
    await execute(()=>api("/activities/"+selected+"/finalise","POST",undefined,
      auth.csrf_token),"Results finalised and qualifying prizes allocated. Physical issuance remains separate.");
  }
  const active=detail?.activity;
  const frozen=Boolean(active?.awards_finalised_at)||active?.status==="cancelled";
  const subjects=subjectType==="team"?teams:people;
  const count=(state:Activity["status"])=>items.filter(a=>a.status===state).length;
  return <div className="page-body">
    <div className="page-heading"><div><span className="eyebrow">EVENT OPERATIONS · ACTIVITIES</span>
      <h2>Activities & Prizes</h2>
      <p className="muted">Plan sessions, record individual or team results and award trackable ticket entitlements.</p>
      </div><div className="heading-actions"><button className="btn secondary" onClick={()=>void refresh()}>
        <RefreshCw size={15}/> Refresh</button>
        {canEdit&&<button className="btn primary" onClick={()=>{setSection("schedule");setAddOpen(x=>!x);}}>
          <Plus size={15}/> New activity</button>}</div></div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}
    <nav className="workspace-subtabs" aria-label="Activities and prizes sections">
      <button className={section==="schedule"?"active":""} onClick={()=>setSection("schedule")}>Activity schedule</button>
      <button className={section==="results"?"active":""} onClick={()=>setSection("results")}>Results & prizes</button>
    </nav>
    <div className="stat-grid compact">
      <div className="stat-card"><CalendarDays size={19}/><span className="muted small">Scheduled</span>
        <strong>{count("scheduled")}</strong></div>
      <div className="stat-card"><ActivityIcon size={19}/><span className="muted small">In progress</span>
        <strong>{count("live")}</strong></div>
      <div className="stat-card"><Trophy size={19}/><span className="muted small">Completed</span>
        <strong>{count("completed")}</strong></div>
    </div>
    {addOpen&&canEdit&&section==="schedule"&&<section className="panel activities-create">
      <span className="eyebrow">SCHEDULE AN ACTIVITY</span><h3>New event activity</h3>
      <form className="form-stack" onSubmit={e=>void addActivity(e)}>
        <label>Name<input required maxLength={200} value={name} onChange={e=>setName(e.target.value)}
          placeholder="e.g. Bird morphology challenge"/></label>
        <label>Description<input maxLength={5000} value={description} onChange={e=>setDescription(e.target.value)}
          placeholder="What will participants do?"/></label>
        <label>Location<input maxLength={200} value={location} onChange={e=>setLocation(e.target.value)}
          placeholder="Hall A / Seminar room"/></label>
        <div className="form-grid">
          <label>Start<input type="datetime-local" value={start} onChange={e=>setStart(e.target.value)}/></label>
          <label>End<input type="datetime-local" value={end} onChange={e=>setEnd(e.target.value)}/></label>
        </div><button className="btn primary" disabled={busy}>Create activity</button>
      </form>
    </section>}
    <div className="content-grid activities-layout activities-subtab-layout">
      {section==="schedule"&&<section className="panel activity-list-panel">
        <span className="eyebrow">EVENT SCHEDULE</span><h3>Event activities</h3>
        <div className="activity-cards">{items.map(item=>
          <button key={item.id} className={"activity-card "+(selected===item.id?"selected":"")}
            onClick={()=>{setSelected(item.id);setSubjectId("");}}>
            <div className="activity-card-top"><strong>{item.name}</strong>
              <span className={"activity-status "+item.status}>{item.status}</span></div>
            <span><Clock3 size={13}/> {localDate(item.start_at)}</span>
            {item.location&&<span><MapPin size={13}/> {item.location}</span>}
            {item.awards_finalised_at&&<span className="activity-awarded"><CheckCircle2 size={13}/> Rewards finalised</span>}
          </button>)}</div>
        {!items.length&&<p className="muted small">No activities scheduled. Create the first one to begin.</p>}
      </section>}
      {section==="results"&&<section className="panel activity-detail-panel">
        <label className="activity-results-selector">Activity to review
          <select value={selected} onChange={e=>{setSelected(e.target.value);setSubjectId("");}}>
            <option value="">Choose an activity…</option>
            {items.map(item=><option key={item.id} value={item.id}>
              {item.name} · {item.status}
            </option>)}
          </select>
        </label>
        {!detail?<div className="empty-table">Select an activity to view its results and prize rules.</div>:<>
          <div className="activity-detail-heading"><div><span className="eyebrow">ACTIVITY DETAILS</span>
            <h3>{detail.activity.name}</h3>
            <p className="muted small">{detail.activity.description||"No description entered."}</p>
            <p className="muted small">{localDate(detail.activity.start_at)} · {detail.activity.location||"Location not assigned"}</p>
          </div>{active?.awards_finalised_at&&<CheckCircle2 size={24}/>}</div>
          {canEdit&&!frozen&&<div className="activity-status-actions"><span className="muted small">Set status:</span>
            {(["scheduled","live","completed","cancelled"] as const).map(state=>
              <button key={state} className={"btn "+(active?.status===state?"primary":"secondary")}
                disabled={busy||active?.status===state}
                onClick={()=>void changeStatus(state)}>{state}</button>)}
          </div>}
          <div className="activity-subsection"><div className="activity-section-heading">
            <h4><ClipboardCheck size={16}/> Ranked results</h4>
            <span className="muted small">{detail.results.length} recorded</span></div>
            {detail.results.length?<div className="table-scroll"><table className="data-table">
              <thead><tr><th>Rank</th><th>Individual / Team</th><th>Score</th><th>Action</th></tr></thead>
              <tbody>{detail.results.map(r=><tr key={r.id}>
                <td><strong>#{r.rank}</strong></td><td>{r.subject_name} <span className="muted small">({r.subject_type})</span></td>
                <td>{r.score_points??"—"}</td>
                <td>{organiser&&!frozen?<button className="link-button" disabled={busy}
                  onClick={()=>void removeResult(r)}><Trash2 size={14}/> Remove</button>:"—"}</td>
              </tr>)}</tbody></table></div>:<p className="muted small">No results entered.</p>}
            {canEdit&&!frozen&&<form className="activity-result-form" onSubmit={e=>void saveResult(e)}>
              <div className="form-grid"><label>Ranking type<select value={subjectType}
                onChange={e=>{setSubjectType(e.target.value as typeof subjectType);setSubjectId("");}}>
                <option value="team">Team</option><option value="person">Individual participant</option>
              </select></label>
                <label>Recipient<select required value={subjectId} onChange={e=>setSubjectId(e.target.value)}>
                  <option value="">Choose…</option>
                  {subjects.map(subject=><option key={subject.id} value={subject.id}>{subject.name}</option>)}
                </select></label></div>
              <div className="form-grid"><label>Rank<input required type="number" min={1}
                value={rank} onChange={e=>setRank(Number(e.target.value))}/></label>
                <label>Score (optional)<input type="number" value={score}
                  onChange={e=>setScore(e.target.value)}/></label></div>
              <label>Notes<input value={notes} maxLength={3000} onChange={e=>setNotes(e.target.value)}/></label>
              <button className="btn secondary" disabled={busy||!subjectId}><Save size={14}/> Record result</button>
            </form>}
          </div>
          <div className="activity-subsection"><div className="activity-section-heading">
            <h4><Award size={16}/> Prize rules</h4>
            <span className="muted small">{detail.prizes.length} rules</span></div>
            {detail.prizes.map(p=><div className="policy-row" key={p.id}>
              <div><strong>Ranks {p.min_rank}–{p.max_rank}: {p.quantity} × {p.category_name}</strong>
                <span>Entitlements allocated when results are finalised.</span></div>
              {organiser&&!frozen&&<button className="link-button" disabled={busy}
                onClick={()=>void removePrize(p)}><Trash2 size={14}/> Remove</button>}
            </div>)}
            {organiser&&!frozen&&<form className="activity-result-form" onSubmit={e=>void addPrize(e)}>
              <label>Ticket reward<select value={categoryId} required
                onChange={e=>setCategoryId(e.target.value)}>
                {categories.filter(c=>c.active).map(c=><option key={c.id} value={c.id}>{c.name}</option>)}
              </select></label>
              <div className="form-grid triple">
                <label>From rank<input type="number" min={1} required value={prizeFrom}
                  onChange={e=>setPrizeFrom(Number(e.target.value))}/></label>
                <label>To rank<input type="number" min={1} required value={prizeTo}
                  onChange={e=>setPrizeTo(Number(e.target.value))}/></label>
                <label>Tickets each<input type="number" min={1} max={100} required value={prizeQty}
                  onChange={e=>setPrizeQty(Number(e.target.value))}/></label>
              </div>
              <button className="btn secondary" disabled={busy||!categoryId}><Plus size={14}/> Add prize rule</button>
            </form>}
          </div>
          {organiser&&!frozen&&<div className="activity-finalise">
            <strong>Finalise results & allocate prizes</strong>
            <p className="muted small">This permanently locks results and prize rules. Qualifying participants receive ticket allocations; no physical tickets are issued automatically.</p>
            <button className="btn primary" disabled={busy||!detail.results.length}
              onClick={()=>void finalise()}><Trophy size={16}/> Finalise and allocate</button>
          </div>}
          {active?.awards_finalised_at&&<div className="activity-award-summary">
            <CheckCircle2 size={20}/><div><strong>Awards finalised</strong>
              <p>{detail.awards.length} recipient allocations · {localDate(active.awards_finalised_at)}</p></div></div>}
          {detail.awards.length>0&&<details className="activity-award-details"><summary>View allocated prize recipients</summary>
            <div className="table-scroll"><table className="data-table"><thead><tr>
              <th>Recipient</th><th>Prize rule</th><th>Time</th>
            </tr></thead><tbody>{detail.awards.map(award=><tr key={award.id}>
              <td>{award.person_name}</td>
              <td>{detail.prizes.find(p=>p.id===award.prize_id)?.category_name||"—"}</td>
              <td>{localDate(award.awarded_at)}</td>
            </tr>)}</tbody></table></div></details>}
        </>}
      </section>}
    </div>
  </div>;
}
