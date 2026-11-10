import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Save, ShieldCheck } from "lucide-react";
import { api, type Auth, type Role, type Staff } from "./api";

const ROLE_OPTIONS:{role:Role;label:string;purpose:string}[]=[
  {role:"organiser",label:"Event organiser",purpose:"Full setup and staff permissions"},
  {role:"monitor",label:"Annotation reviewer",purpose:"Review assigned teams and inspect rankings"},
  {role:"reception",label:"Reception & check-in",purpose:"Check in attendees and assign tables"},
  {role:"distribution",label:"Food & drinks",purpose:"Issue and redeem tickets"},
  {role:"activity",label:"Activities & prizes",purpose:"Manage activity results and awards"},
  {role:"observer",label:"Read-only observer",purpose:"View event dashboards without edits"},
];
const DUTIES:{label:string;roles:Role[]}[]=[
  {label:"Annotation reviewer",roles:["monitor"]},
  {label:"Reception & check-in",roles:["reception"]},
  {label:"Food & drinks",roles:["distribution"]},
  {label:"Activities & prizes",roles:["activity"]},
  {label:"Reviewer + reception",roles:["monitor","reception"]},
  {label:"Reviewer + food & drinks",roles:["monitor","distribution"]},
  {label:"Read-only observer",roles:["observer"]},
];
type StaffRecord = Staff & {active:boolean};
export function StaffScreen({auth}:{auth:Auth}) {
  const [people,setPeople]=useState<StaffRecord[]>([]);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [drafts,setDrafts]=useState<Record<string,Role[]>>({});
  const [working,setWorking]=useState<string|null>(null);
  const [query,setQuery]=useState("");
  const refresh=useCallback(async()=>{
    try {
      const result=await api<{items:StaffRecord[]}>("/staff");
      setPeople(result.items);
      setDrafts(Object.fromEntries(result.items.map(p=>[p.id,p.roles])));
      setError("");
    }catch(e){setError((e as Error).message);}
  },[]);
  useEffect(()=>{void refresh();},[refresh]);
  function chooseDuty(id:string,duty:string){
    const option=DUTIES.find(row=>row.label===duty);
    if(option)setDrafts(old=>({...old,[id]:option.roles}));
  }
  function toggle(id:string,role:Role) {
    setDrafts(old=>{
      const selected=old[id]||[];
      return {...old,[id]:selected.includes(role)?selected.filter(r=>r!==role):[...selected,role]};
    });
  }
  async function save(person:StaffRecord) {
    const roles=drafts[person.id]||[];
    if(!roles.length){setError("Every active staff account must have at least one role.");return;}
    setWorking(person.id);setError("");setNotice("");
    try {
      await api("/staff/"+person.id+"/roles","PUT",{roles},auth.csrf_token);
      setNotice("Permissions saved for "+person.name+". The change applies to their next API request.");
      await refresh();
    }catch(e){setError((e as Error).message);}finally{setWorking(null);}
  }
  const filtered=people.filter(person=>(person.name+" "+person.email).toLowerCase().includes(query.toLowerCase()));
  return <section className="panel">
    <div className="panel-heading"><div><span className="eyebrow">STAFF ACCESS</span>
      <h3>Assign event duties</h3>
      <p className="muted">Change who handles check-in, food and drinks, annotation review, or activities at any time. Quick duties are presets; multiple permissions can also be combined manually. Nothing changes in Supervisely.</p>
      </div><button className="btn secondary" onClick={()=>void refresh()}><RefreshCw size={15}/> Refresh</button></div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}
    <label className="staff-search">Find monitor or staff account
      <input value={query} placeholder="Search by name or email…" onChange={e=>setQuery(e.target.value)}/>
    </label>
    <div className="staff-list">
      {filtered.map(person=><div className="staff-record" key={person.id}>
        <div className="staff-person"><div className="mini-avatar">{person.name[0]?.toUpperCase()}</div>
          <div><strong>{person.name}</strong><span>{person.email} {person.id===auth.staff.id?"· You":""}</span></div></div>
        <div className="staff-duty-editor">
          <label className="staff-duty-preset">Quick duty
            <select value="" disabled={!person.active} onChange={e=>chooseDuty(person.id,e.target.value)}>
              <option value="">Choose a role preset…</option>
              {DUTIES.map(duty=><option key={duty.label} value={duty.label}>{duty.label}</option>)}
            </select>
          </label>
          <div className="role-options">{ROLE_OPTIONS.map(option=>
            <label className="role-choice" key={option.role}
              title={option.purpose}>
              <input type="checkbox" disabled={!person.active}
                checked={(drafts[person.id]||[]).includes(option.role)}
                onChange={()=>toggle(person.id,option.role)}/>
              <span><strong>{option.label}</strong><small>{option.purpose}</small></span>
            </label>)}</div>
        </div>
        <button className="btn primary" onClick={()=>void save(person)}
          disabled={!person.active||working!==null||JSON.stringify([...(drafts[person.id]||[])].sort())===JSON.stringify([...person.roles].sort())}>
          <Save size={15}/> Save
        </button>
      </div>)}
    </div>
    {!filtered.length&&<p className="muted">No staff accounts match this search.</p>}
    <div className="settings-note" style={{marginTop:19}}>
      <ShieldCheck size={19}/><p>Only organisers may edit staff permissions. The system prevents removal of the last active organiser. Staff identities and attendee roster entries are separate records.</p>
    </div>
  </section>;
}
