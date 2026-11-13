import { RefreshButton } from "./refresh_button";
import { useCallback, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { api, type Auth } from "./api";

type Approved = {
  email:string; role:"admin"|"organiser"; enabled:boolean;
  registered:boolean; active:boolean; login_ready:boolean;
  login_issue:"access_disabled"|"not_registered"|"account_disabled"|
    "role_mismatch"|"temporarily_locked"|"duplicate_email_records"|null;
  last_login_at:number|null;
};
const REASONS:Record<Exclude<Approved["login_issue"],null>,string>={
  access_disabled:"Access disabled",
  not_registered:"No account in this installation",
  account_disabled:"Account disabled",
  role_mismatch:"Account role needs reconciliation",
  temporarily_locked:"Temporarily locked after failed attempts",
  duplicate_email_records:"Conflicting staff email records — contact Admin",
};

export function StaffScreen({auth}:{auth:Auth}) {
  const [people,setPeople]=useState<Approved[]>([]);
  const [error,setError]=useState("");
  const refresh=useCallback(async()=>{
    try{
      const data=await api<{items:Approved[]}>("/staff/approved");
      setPeople(data.items);setError("");
    }catch(e){setError((e as Error).message);return false;}
  },[]);
  useEffect(()=>{void refresh();},[refresh]);
  if(!auth.staff.roles.includes("admin"))return null;

  const eligible=people.filter(p=>p.enabled);
  const ready=eligible.filter(p=>p.login_ready).length;
  const confirmed=eligible.filter(p=>p.login_ready&&p.last_login_at!==null).length;
  return <section className="panel">
    <div className="panel-heading"><div>
      <span className="eyebrow">ADMIN ACCESS · SIGN-IN VERIFICATION</span>
      <h3>Approved Nightjar accounts</h3>
      <p className="muted">Live PostgreSQL account status and successful login history.
        Event Monitor and Participant roles are separate from staff permissions.</p>
    </div><RefreshButton refresh={refresh}>Refresh login status</RefreshButton></div>
    {error&&<div role="alert" className="alert danger">{error}</div>}
    <div className="settings-row"><span>Registered, enabled and not locked</span>
      <strong>{ready} / {eligible.length}</strong></div>
    <div className="settings-row"><span>Ready accounts with a recorded successful sign-in</span>
      <strong>{confirmed} / {eligible.length}</strong></div>
    <div className="staff-list">
      {people.map(person=><div className="staff-record" key={person.email}>
        <div className="staff-person"><div className="mini-avatar">
          {person.email.slice(0,1).toUpperCase()}</div><div>
          <strong>{person.email}</strong>
          <span>{person.role==="admin"?"Admin":"Organiser"}</span>
        </div></div>
        <div className="staff-duty-editor">
          <strong>{person.login_issue?REASONS[person.login_issue]:
            person.last_login_at!==null?"Login verified":"Ready to sign in"}</strong>
          <span>{person.login_issue==="not_registered"&&person.enabled?
            "Approved email, but no matching account in this Nightjar database. If this account was created on a different installation, use Create account here.":""}</span>
          {person.login_ready&&<span>{person.last_login_at!==null?
            "Last successful sign-in: "+new Date(person.last_login_at*1000).toLocaleString():
            "No successful sign-in recorded yet. Ask this person to sign in."}</span>}
        </div>
      </div>)}
    </div>
    <div className="settings-note" style={{marginTop:19}}>
      <ShieldCheck size={19}/>
      <p>Approved people use their own Nightjar passwords; they do not need
        Supervisely for app login. A recorded sign-in confirms that the
        Nightjar password and role check succeeded at that time, but is not
        proof of email ownership or that a future login cannot fail.
        To check each person, ask them to sign in, then refresh this page.
        No passwords should be shared with the organiser.</p>
    </div>
  </section>;
}
