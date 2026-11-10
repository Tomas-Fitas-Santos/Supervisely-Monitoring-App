import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Save, ShieldCheck } from "lucide-react";
import { api, type Auth, type Role, type Staff } from "./api";

const ROLES: Role[]=["organiser","monitor","reception","distribution","activity","observer"];
type StaffRecord = Staff & {active:boolean};
export function StaffScreen({auth}:{auth:Auth}) {
  const [people,setPeople]=useState<StaffRecord[]>([]);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [drafts,setDrafts]=useState<Record<string,Role[]>>({});
  const [working,setWorking]=useState<string|null>(null);
  const refresh=useCallback(async()=>{
    try {
      const result=await api<{items:StaffRecord[]}>("/staff");
      setPeople(result.items);
      setDrafts(Object.fromEntries(result.items.map(p=>[p.id,p.roles])));
      setError("");
    }catch(e){setError((e as Error).message);}
  },[]);
  useEffect(()=>{void refresh();},[refresh]);
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
  return <section className="panel">
    <div className="panel-heading"><div><span className="eyebrow">STAFF ACCESS</span>
      <h3>Staff roles & permissions</h3>
      <p className="muted">One account can hold several roles. Changes are enforced by the backend.</p>
      </div><button className="btn secondary" onClick={()=>void refresh()}><RefreshCw size={15}/> Refresh</button></div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}
    <div className="staff-list">
      {people.map(person=><div className="staff-record" key={person.id}>
        <div className="staff-person"><div className="mini-avatar">{person.name[0]?.toUpperCase()}</div>
          <div><strong>{person.name}</strong><span>{person.email} {person.id===auth.staff.id?"· You":""}</span></div></div>
        <div className="role-options">{ROLES.map(role=>
          <label className="role-choice" key={role}><input type="checkbox"
            disabled={!person.active} checked={(drafts[person.id]||[]).includes(role)}
            onChange={()=>toggle(person.id,role)}/>{role}</label>)}</div>
        <button className="btn primary" onClick={()=>void save(person)}
          disabled={!person.active||working!==null||JSON.stringify([...(drafts[person.id]||[])].sort())===JSON.stringify([...person.roles].sort())}>
          <Save size={15}/> Save
        </button>
      </div>)}
    </div>
    <div className="settings-note" style={{marginTop:19}}>
      <ShieldCheck size={19}/><p>Only organisers may edit staff permissions. The system prevents removal of the last active organiser. Staff identities and attendee roster entries are separate records.</p>
    </div>
  </section>;
}
