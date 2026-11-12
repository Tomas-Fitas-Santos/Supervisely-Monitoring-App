import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Send, ShieldCheck } from "lucide-react";
import { api, type Auth, type Staff } from "./api";

type StaffRecord = Staff & {active:boolean};

export function StaffScreen({auth}:{auth:Auth}) {
  const [people,setPeople]=useState<StaffRecord[]>([]);
  const [email,setEmail]=useState("");
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [working,setWorking]=useState(false);
  const refresh=useCallback(async()=>{
    try {
      const data=await api<{items:StaffRecord[]}>("/staff");
      setPeople(data.items.filter(p=>p.roles.some(r=>r==="admin"||r==="organiser")));
      setError("");
    }catch(e){setError((e as Error).message);}
  },[]);
  useEffect(()=>{void refresh();},[refresh]);
  async function sendLink(){
    if(!email.trim())return;
    setWorking(true);setError("");setNotice("");
    try {
      const result=await api<{message:string}>("/auth/setup/request","POST",
        {email:email.trim().toLowerCase()});
      setNotice(result.message);
    }catch(e){setError((e as Error).message);}
    finally{setWorking(false);}
  }
  if(!auth.staff.roles.includes("admin"))return null;
  return <section className="panel">
    <div className="panel-heading"><div>
      <span className="eyebrow">ADMIN ACCESS</span>
      <h3>Admin and Organiser accounts</h3>
      <p className="muted">App login access is independent of Supervisely and event
      Monitor/Participant assignments. The approved email list is maintained
      in private server configuration.</p>
    </div><button className="btn secondary" onClick={()=>void refresh()}>
      <RefreshCw size={15}/> Refresh</button></div>
    {error&&<div className="alert danger">{error}</div>}
    {notice&&<div className="alert success">{notice}</div>}
    <section className="staff-registration-codes">
      <div className="panel-heading"><div>
        <h3>Email an account setup / recovery link</h3>
        <p className="muted">Approved organisers receive a private email link.
          No registration code or event-role approval is needed.</p>
      </div><Send size={22}/></div>
      <label>Approved email
        <input type="email" value={email} onChange={e=>setEmail(e.target.value)}
          placeholder="organiser@institution.pt"/></label>
      <button className="btn primary" type="button" disabled={working||!email.trim()}
        onClick={()=>void sendLink()}>{working?"Sending…":"Email setup link"}</button>
    </section>
    <div className="staff-list">
      {people.map(person=><div className="staff-record" key={person.id}>
        <div className="staff-person"><div className="mini-avatar">
          {person.name.slice(0,1).toUpperCase()}</div><div>
          <strong>{person.name}</strong><span>{person.email}</span>
        </div></div>
        <div className="staff-duty-editor">
          <strong>{person.roles.includes("admin")?"Admin":"Organiser"}</strong>
          <span>{person.active?"Active account":"Inactive; reconcile server allowlist"}</span>
        </div>
      </div>)}
    </div>
    <div className="settings-note" style={{marginTop:19}}>
      <ShieldCheck size={19}/>
      <p>Only Admins manage the approved app access list. Event roles never
        deactivate these accounts. Creating an account requires verification
        through the address owner's email inbox.</p>
    </div>
  </section>;
}
