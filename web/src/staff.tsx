import { useCallback, useEffect, useState } from "react";
import { RefreshCw, ShieldCheck } from "lucide-react";
import { api, type Auth } from "./api";

type Approved = {
  email:string; role:"admin"|"organiser"; enabled:boolean;
  registered:boolean; active:boolean;
};

export function StaffScreen({auth}:{auth:Auth}) {
  const [people,setPeople]=useState<Approved[]>([]);
  const [error,setError]=useState("");
  const refresh=useCallback(async()=>{
    try{
      const data=await api<{items:Approved[]}>("/staff/approved");
      setPeople(data.items);setError("");
    }catch(e){setError((e as Error).message);}
  },[]);
  useEffect(()=>{void refresh();},[refresh]);
  if(!auth.staff.roles.includes("admin"))return null;
  return <section className="panel">
    <div className="panel-heading"><div>
      <span className="eyebrow">ADMIN ACCESS</span>
      <h3>Approved Nightjar accounts</h3>
      <p className="muted">App access is controlled by the approved email records
        in PostgreSQL. Event Monitor and Participant roles are independent.</p>
    </div><button className="btn secondary" onClick={()=>void refresh()}>
      <RefreshCw size={15}/> Refresh</button></div>
    {error&&<div className="alert danger">{error}</div>}
    <div className="staff-list">
      {people.map(person=><div className="staff-record" key={person.email}>
        <div className="staff-person"><div className="mini-avatar">
          {person.email.slice(0,1).toUpperCase()}</div><div>
          <strong>{person.email}</strong>
          <span>{person.role==="admin"?"Admin":"Organiser"}</span>
        </div></div>
        <div className="staff-duty-editor">
          <strong>{!person.enabled?"Access disabled":
            !person.registered?"Not registered yet":person.active?"Active":"Inactive"}</strong>
          <span>{person.enabled&&!person.registered?
            "This person can create their own Nightjar account.":""}</span>
        </div>
      </div>)}
    </div>
    <div className="settings-note" style={{marginTop:19}}>
      <ShieldCheck size={19}/>
      <p>Approved people register using their own email and a password they choose.
        No registration codes, SMTP, or Supervisely account linkage is required.</p>
    </div>
  </section>;
}
