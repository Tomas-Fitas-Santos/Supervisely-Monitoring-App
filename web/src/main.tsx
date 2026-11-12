import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { AppDialogHost } from "./dialogs";
import {
  Activity, Award, ArrowRight, Bird, CalendarDays, CheckCircle2, ChevronDown,
  CircleHelp, ClipboardCheck, Cloud, Database, LayoutDashboard, LogOut,
  RefreshCw, Settings2, ShieldCheck, Ticket, UsersRound
} from "lucide-react";
import { api, refreshSuperviselyRoster, type Auth } from "./api";
import { PeopleScreen } from "./people";
import { AttendanceScreen } from "./attendance";
import { SettingsScreen } from "./settings";
import { EventSelector } from "./events";
import { StaffScreen } from "./staff";
import { TicketsScreen } from "./tickets";
import { ActivitiesScreen } from "./activities";
import { DatasetWorkspace } from "./dataset_workspace";
import { CompetitionScreen } from "./competition";
import { SharedReviewScreen } from "./shared_review";
import { CloseoutScreen } from "./closeout";
import "./styles.css";

type Tab = "overview" | "checkin" | "tickets" | "activities" | "competition" |
  "quality" | "closeout" | "people" | "datasets" | "job_setup" |
  "staff" | "settings" | "account";

function Login({ onLogin }: { onLogin: (auth: Auth) => void }) {
  const [mode,setMode] = useState<"login"|"register">("login");
  const [email,setEmail] = useState("");
  const [name,setName] = useState("");
  const [password,setPassword] = useState("");
  const [confirmation,setConfirmation] = useState("");
  const [showPassword,setShowPassword] = useState(false);
  const [error,setError] = useState("");
  const [working,setWorking] = useState(false);
  async function submit(e:React.FormEvent) {
    e.preventDefault();setError("");setWorking(true);
    try {
      const accountEmail=email.trim().toLowerCase();
      if(mode==="register"){
        if(password!==confirmation)throw new Error("Passwords do not match.");
        await api("/auth/setup/register","POST",{email:accountEmail,name:name.trim(),password});
      }
      onLogin(await api<Auth>("/auth/login","POST",{email:accountEmail,password}));
    }catch(exc){setError((exc as Error).message);}
    finally{setWorking(false);}
  }
  function switchMode(){
    setMode(v=>v==="login"?"register":"login");setError("");
    setPassword("");setConfirmation("");setShowPassword(false);
  }
  return <main className="login-shell">
    <section className="login-art">
      <div className="login-brand"><div className="brand-mark"><Bird size={25}/></div>
        <span>NIGHTJAR <strong>ANNOTATIATHON</strong></span></div>
      <div className="login-art-text">
        <span className="eyebrow light">TÉCNICO · EVENT OPERATIONS</span>
        <h1>Every team.<br/>Every moment.<br/><em>One platform.</em></h1>
        <p>Organise people, coordinate the competition and keep your event running seamlessly.</p>
      </div>
      <div className="login-art-footer"><ShieldCheck size={17}/> Authorised event staff only</div>
    </section>
    <section className="login-panel">
      <div className="login-card">
        <div className="mobile-brand"><Bird size={23}/> NIGHTJAR</div>
        <span className="eyebrow">{mode==="login"?"WELCOME BACK":"APPROVED STAFF REGISTRATION"}</span>
        <h2>{mode==="login"?"Sign in to Nightjar":"Create your Nightjar account"}</h2>
        <p className="muted">{mode==="login"?
          "Sign in with your Nightjar email and password, not your Supervisely credentials.":
          "If your email is in the approved Admin or Organiser list, choose your own password and create your account. No registration code is required."}</p>
        <form onSubmit={e=>void submit(e)} className="form-stack">
          {mode==="register"&&<label>Full name
            <input type="text" autoComplete="name" required maxLength={200}
              value={name} onChange={e=>setName(e.target.value)}
              placeholder="Your name"/></label>}
          <label>Email address<input type="email" autoComplete="username" autoFocus required
            value={email} onChange={e=>setEmail(e.target.value)}
            placeholder="you@institution.pt"/></label>
          <label>{mode==="login"?"Password":"Create a password (12+ characters)"}
            <input type={showPassword?"text":"password"}
              autoComplete={mode==="login"?"current-password":"new-password"} required
              minLength={mode==="register"?12:undefined}
              value={password} onChange={e=>setPassword(e.target.value)}
              placeholder="Enter password"/></label>
          {mode==="register"&&<label>Confirm password
            <input type={showPassword?"text":"password"} autoComplete="new-password"
              required minLength={12} value={confirmation}
              onChange={e=>setConfirmation(e.target.value)}
              placeholder="Re-enter password"/></label>}
          <label className="muted small"><input type="checkbox" checked={showPassword}
            onChange={e=>setShowPassword(e.target.checked)}/> Show password</label>
          {error&&<div role="alert" className="alert danger">{error}</div>}
          <button className="btn primary large" disabled={working} type="submit">
            {working?"Working…":mode==="login"?"Sign in":"Create account and sign in"}
            <ArrowRight size={17}/>
          </button>
        </form>
        <div className="settings-action-row" style={{marginTop:15}}>
          <button type="button" className="btn secondary" disabled={working} onClick={switchMode}>
            {mode==="login"?"Create account":"Back to sign in"}
          </button>
        </div>
        <p className="login-hint"><CircleHelp size={15}/>
          Only approved Admin and Organiser emails can register. Existing accounts must sign in;
          creating a new account cannot replace an existing password.</p>
      </div>
      <div className="login-bottom">TÉCNICO · Bird Annotatiathon</div>
    </section>
  </main>;
}

function Overview({auth,onNavigate}:{
  auth:Auth;onNavigate:(tab:Tab)=>void;
}) {
  const roles=auth.staff.roles;
  const organiser=roles.includes("organiser")||roles.includes("admin");
  const reviewer=organiser||roles.includes("monitor");
  const admits=reviewer||roles.includes("reception");
  const tickets=reviewer||roles.some(role=>["distribution","reception","activity"].includes(role));
  const [stats,setStats]=useState<{
    teams:number;monitors:number;participants:number;
    present:number;reviewPending:number|null;reviewCompleted:number|null
  }|null>(null);
  const [error,setError]=useState("");
  const refresh=useCallback(async()=>{
    try {
      const [teams,attendance,queue]=await Promise.all([
        api<{totals:{people:number;monitors:number;competition_teams:number}}>("/teams"),
        api<{registered:number;present:number;not_arrived:number}>("/attendance/summary"),
        reviewer?api<{pending:number;active:number;completed:number}>("/review-queue/status"):
          Promise.resolve(null)
      ]);
      setStats({
        teams:teams.totals.competition_teams,monitors:teams.totals.monitors,
        participants:attendance.registered,present:attendance.present,
        reviewPending:queue?.pending??null,reviewCompleted:queue?.completed??null
      });
      setError("");
    }catch(exc){setError((exc as Error).message);}
  },[reviewer]);
  useEffect(()=>{
    void refresh();
    const id=window.setInterval(()=>{if(!document.hidden)void refresh();},12000);
    return()=>window.clearInterval(id);
  },[refresh]);
  const actions:{tab:Tab;label:string;description:string;icon:typeof UsersRound}[]=[
    ...(admits?[{
      tab:"checkin" as Tab,label:"Check-in",description:"Track arrivals and table assignments",icon:CheckCircle2
    }]:[]),
    ...(tickets?[{
      tab:"tickets" as Tab,label:"Tickets & Rewards",description:"Issue and track event tickets",icon:Ticket
    }]:[]),
    ...(roles.some(role=>["organiser","monitor","activity","reception"].includes(role))?[{
      tab:"activities" as Tab,label:"Activities",description:"Schedule sessions and record results",icon:Activity
    }]:[]),
    ...(reviewer?[{
      tab:"quality" as Tab,label:"Shared frame review",description:"Review the next available annotated frame",icon:ClipboardCheck
    }]:[]),
    ...(roles.some(role=>["organiser","monitor","observer"].includes(role))?[{
      tab:"closeout" as Tab,label:"Results & Rankings",description:"View verified team scores and event progress",icon:Award
    }]:[])
  ];
  return <div className="page-body event-overview">
    <div className="page-heading"><div><span className="eyebrow">EVENT OPERATIONS · LIVE OVERVIEW</span>
      <h2>Annotatiathon overview</h2>
      <p className="muted">Attendance, monitoring and results for the selected event.</p></div>
      <button className="btn secondary" onClick={()=>void refresh()}><RefreshCw size={15}/> Refresh</button>
    </div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    <div className="stat-grid compact">
      <div className="stat-card"><UsersRound size={20}/><span className="muted small">Participants</span>
        <strong>{stats?.participants??"—"}</strong></div>
      <div className="stat-card"><CheckCircle2 size={20}/><span className="muted small">Checked in</span>
        <strong>{stats?.present??"—"}</strong></div>
      <div className="stat-card"><Activity size={20}/><span className="muted small">Competition teams</span>
        <strong>{stats?.teams??"—"}</strong></div>
      {reviewer&&<>
        <div className="stat-card"><ClipboardCheck size={20}/><span className="muted small">Frames ready for review</span>
          <strong>{stats?.reviewPending??"—"}</strong></div>
        <div className="stat-card"><ShieldCheck size={20}/><span className="muted small">Frames reviewed</span>
          <strong>{stats?.reviewCompleted??"—"}</strong></div>
      </>}
    </div>
    <section className="panel event-overview-actions">
      <div className="panel-heading"><div>
        <span className="eyebrow">EVENT WORKSPACES</span>
        <h3>Event operations</h3>
        <p className="muted">Open the relevant workspace. All monitors review from one shared queue.</p>
      </div></div>
      <div className="overview-quick-actions">
        {actions.map(action=><button className="overview-quick-action" key={action.tab}
          onClick={()=>onNavigate(action.tab)}>
          <action.icon size={22}/><span><strong>{action.label}</strong>
            <small>{action.description}</small></span><ArrowRight size={17}/>
        </button>)}
      </div>
    </section>
    {organiser&&<section className="panel event-overview-actions">
      <div className="panel-heading"><div>
        <span className="eyebrow">ORGANISER ONLY</span>
        <h3>Event setup and administration</h3>
        <p className="muted">Manage the roster, data, jobs and event configuration. Other monitors do not see these workspaces.</p>
      </div></div>
      <div className="overview-quick-actions">
        {([
          {tab:"people" as Tab,label:"People & Teams",description:"Roster, teams and invitations",icon:UsersRound},
          {tab:"datasets" as Tab,label:"Dataset",description:"Prepare event media",icon:Database},
          {tab:"job_setup" as Tab,label:"Job Distribution",description:"Create manual or automatic jobs",icon:Cloud},
          {tab:"settings" as Tab,label:"Settings",description:"Shared account and event configuration",icon:Settings2}
        ]).map(action=><button className="overview-quick-action" key={action.tab}
          onClick={()=>onNavigate(action.tab)}>
          <action.icon size={22}/><span><strong>{action.label}</strong>
            <small>{action.description}</small></span><ArrowRight size={17}/>
        </button>)}
      </div>
    </section>}
  </div>;
}

function App() {
  const [auth,setAuth] = useState<Auth | null>(null);
  const [loading,setLoading] = useState(true);
  const [tab,setTab] = useState<Tab>("overview");
  const [logoutError,setLogoutError] = useState("");
  useEffect(() => {
    api<Auth>("/auth/me").then(setAuth).catch(()=>setAuth(null)).finally(()=>setLoading(false));
  }, []);
  // On every full app load/login, update joined members before normal
  // local page polling takes over. An unavailable native API must not block
  // staff access to the locally stored attendance and event data.
  useEffect(() => {
    if (!auth) return;
    void refreshSuperviselyRoster(auth.csrf_token).catch(() => undefined);
  }, [auth?.staff.id, auth?.csrf_token, tab]);
  // Roles are database-backed, and organisers can change them mid-event.
  // Keep the open browser's navigation in sync with current staff privileges.
  useEffect(() => {
    if (!auth) return;
    let active=true;
    const timer=window.setInterval(()=>{
      if(document.hidden)return;
      void api<Auth>("/auth/me").then(current=>{
        if(!active)return;
        setAuth(previous=>{
          if(!previous||previous.staff.id!==current.staff.id)return previous;
          return previous.staff.name!==current.staff.name||
            JSON.stringify(previous.staff.roles)!==JSON.stringify(current.staff.roles)?
            current:previous;
        });
      }).catch(()=>undefined);
    },15000);
    return()=>{active=false;window.clearInterval(timer);};
  },[auth?.staff.id]);

  async function logout() {
    try {
      await api("/auth/logout","POST",undefined,auth?.csrf_token);
      setAuth(null); setTab("overview"); setLogoutError("");
    } catch(exc) {setLogoutError((exc as Error).message);}
  }
  if (loading) return <div className="loading-screen"><Bird size={33}/> Loading Nightjar…</div>;
  if (!auth) return <Login onLogin={a=>{setAuth(a);setTab("overview");}}/>;
  const has=(...roles:string[])=>auth.staff.roles.includes("admin")||auth.staff.roles.some(role=>roles.includes(role));
  const organiser=has("organiser");
  const eventNavigation: {id:Tab;label:string;icon:typeof LayoutDashboard}[] = [
    {id:"overview",label:"Overview",icon:LayoutDashboard},
    ...(has("organiser","monitor","reception")?
      [{id:"checkin" as Tab,label:"Check-in",icon:CheckCircle2}]:[]),
    ...(has("organiser","monitor","reception","distribution","activity")?
      [{id:"tickets" as Tab,label:"Tickets & Rewards",icon:Ticket}]:[]),
    ...(has("organiser","monitor","activity","reception")?
      [{id:"activities" as Tab,label:"Activities & Prizes",icon:Activity}]:[]),
    ...(has("organiser","monitor","observer","activity")?
      [{id:"competition" as Tab,label:"Job Progress",icon:UsersRound}]:[]),
    ...(has("organiser","monitor","observer")?
      [{id:"quality" as Tab,label:"Monitoring & Review",icon:ClipboardCheck}]:[]),
    ...(has("organiser","monitor","observer")?
      [{id:"closeout" as Tab,label:"Results & Rankings",icon:CalendarDays}]:[]),
  ];
  const setupNavigation: {id:Tab;label:string;icon:typeof LayoutDashboard}[] = organiser?[
    {id:"people",label:"People & Teams",icon:UsersRound},
    {id:"datasets",label:"Dataset",icon:Database},
    {id:"job_setup",label:"Job Distribution",icon:Cloud},
    ...(has("admin")?[{id:"staff" as Tab,label:"Staff Access",icon:ShieldCheck}]:[]),
    {id:"settings",label:"Settings",icon:Settings2},
  ]:[];
  const accountNavigation: {id:Tab;label:string;icon:typeof LayoutDashboard}[] =
    organiser?[]:[{id:"account",label:"My Supervisely Account",icon:Settings2}];
  const navigation=[...eventNavigation,...setupNavigation,...accountNavigation];
  const accessibleTab=navigation.some(item=>item.id===tab)?tab:"overview";
  return <div className="app-shell">
    <AppDialogHost/>
    <aside className="sidebar">
      <div className="sidebar-logo"><div className="brand-mark"><Bird size={23}/></div>
        <div><strong>NIGHTJAR</strong><span>ANNOTATIATHON</span></div></div>
      <div className="sidebar-label">EVENT OPERATIONS</div>
      <nav aria-label="Event operations">
        {eventNavigation.map(item=><button key={item.id} className={"nav-link "+(accessibleTab===item.id?"active":"")}
          onClick={()=>setTab(item.id)}><item.icon size={19}/>{item.label}
          {accessibleTab===item.id&&<ArrowRight size={15} className="nav-end"/>}</button>)}
      </nav>
      {organiser&&<>
        <div className="sidebar-label section-break">EVENT SETUP · ORGANISERS</div>
        <nav aria-label="Organiser event setup">
          {setupNavigation.map(item=><button key={item.id} className={"nav-link "+(accessibleTab===item.id?"active":"")}
            onClick={()=>setTab(item.id)}><item.icon size={19}/>{item.label}
            {accessibleTab===item.id&&<ArrowRight size={15} className="nav-end"/>}</button>)}
        </nav>
      </>}
      {accountNavigation.length>0&&<>
        <div className="sidebar-label section-break">ACCOUNT</div>
        <nav aria-label="My account">
          {accountNavigation.map(item=><button key={item.id} className={"nav-link "+(accessibleTab===item.id?"active":"")}
            onClick={()=>setTab(item.id)}><item.icon size={19}/>{item.label}
            {accessibleTab===item.id&&<ArrowRight size={15} className="nav-end"/>}</button>)}
        </nav>
      </>}
      <div className="sidebar-spacer"/>
      <div className="sidebar-info"><Database size={17}/><div><strong>Shared database</strong>
        <span>PostgreSQL · Shared state</span></div></div>
      <div className="account-pane">
        <div className="avatar">{auth.staff.name.slice(0,1).toUpperCase()}</div>
        <div className="account-text"><strong>{auth.staff.name}</strong><span>{auth.staff.roles.join(" · ")}</span></div>
        <ChevronDown size={15}/>
      </div>
      <button className="nav-link signout" onClick={()=>void logout()}><LogOut size={18}/> Sign out</button>
      {logoutError && <p className="sidebar-error">{logoutError}</p>}
    </aside>
    <div className="main-area">
      <header className="topbar"><div className="breadcrumb">Nightjar <span>/</span> <strong>{
        navigation.find(n=>n.id===accessibleTab)?.label}</strong></div>
        <div className="topbar-right"><EventSelector auth={auth}/><span className="status-dot"/> Event operations
          <span className="topbar-avatar">{auth.staff.name.slice(0,1).toUpperCase()}</span></div>
      </header>
      {accessibleTab==="overview"&&<Overview auth={auth} onNavigate={setTab}/>}
      {accessibleTab==="people"&&organiser&&<PeopleScreen auth={auth}/>}
      {accessibleTab==="checkin"&&<AttendanceScreen auth={auth}/>}
      {accessibleTab==="tickets"&&<TicketsScreen auth={auth}/>}
      {accessibleTab==="activities"&&<ActivitiesScreen auth={auth}/>}
      {accessibleTab==="datasets"&&organiser&&<DatasetWorkspace auth={auth}/>}
      {accessibleTab==="competition"&&<CompetitionScreen auth={auth} view="progress"/>}
      {accessibleTab==="job_setup"&&organiser&&<CompetitionScreen auth={auth} view="setup"/>}
      {accessibleTab==="quality"&&<SharedReviewScreen auth={auth}/>}
      {accessibleTab==="closeout"&&<CloseoutScreen auth={auth}/>}
      {accessibleTab==="staff"&&has("admin")&&<div className="page-body"><div className="page-heading"><div><span className="eyebrow">EVENT SETUP · PERMISSIONS</span><h2>Staff Access</h2><p className="muted">Only admins administer the approved Nightjar accounts.</p></div></div><StaffScreen auth={auth}/></div>}
      {((accessibleTab==="settings"&&organiser)||accessibleTab==="account")&&<SettingsScreen auth={auth}/>}
      <footer className="page-footer"><Bird size={14}/> Nightjar Annotatiathon · Técnico</footer>
    </div>
  </div>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
