import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity, Award, ArrowRight, Bird, CalendarDays, CheckCircle2, ChevronDown,
  CircleHelp, ClipboardCheck, Cloud, Database, LayoutDashboard, LogOut,
  RefreshCw, Settings2, ShieldCheck, Ticket, UsersRound
} from "lucide-react";
import { api, refreshSuperviselyRoster, type Auth } from "./api";
import { PeopleScreen } from "./people";
import { SettingsScreen } from "./settings";
import { EventSelector } from "./events";
import { StaffScreen } from "./staff";
import { TicketsScreen } from "./tickets";
import { ActivitiesScreen } from "./activities";
import { DatasetWorkspace } from "./dataset_workspace";
import { CompetitionScreen } from "./competition";
import { QualityScreen } from "./reviews";
import { CloseoutScreen } from "./closeout";
import "./styles.css";

type Tab = "overview" | "people" | "tickets" | "activities" | "datasets" | "competition" | "quality" | "closeout" | "staff" | "settings";

function Login({ onLogin }: { onLogin: (auth: Auth) => void }) {
  const [mode,setMode] = useState<"login"|"register">("login");
  const [email,setEmail] = useState("");
  const [password,setPassword] = useState("");
  const [confirmation,setConfirmation] = useState("");
  const [error,setError] = useState("");
  const [notice,setNotice] = useState("");
  const [working,setWorking] = useState(false);

  async function submit(e:React.FormEvent) {
    e.preventDefault();
    setError("");setNotice("");setWorking(true);
    try {
      const accountEmail=email.trim().toLowerCase();
      if(mode==="register") {
        if(password!==confirmation)
          throw new Error("The two passwords do not match.");
        const result=await api<{registered:boolean;message:string}>(
          "/auth/register","POST",{email:accountEmail,password});
        try {
          // Registration and the independent Supervisely link are separate.
          onLogin(await api<Auth>("/auth/login","POST",{email:accountEmail,password}));
        }catch(loginError) {
          setMode("login");
          setConfirmation("");
          setPassword("");
          setNotice(result.message);
          setError("Automatic sign-in failed: "+(loginError as Error).message);
        }
      }else{
        onLogin(await api<Auth>("/auth/login","POST",{email:accountEmail,password}));
      }
    }catch(exc){setError((exc as Error).message);}
    finally{setWorking(false);}
  }
  function switchMode(){
    setMode(v=>v==="login"?"register":"login");
    setError("");setNotice("");setPassword("");setConfirmation("");
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
        <span className="eyebrow">{mode==="login"?"WELCOME BACK":"MONITOR REGISTRATION"}</span>
        <h2>{mode==="login"?"Sign in to Nightjar":"Create your monitor account"}</h2>
        <p className="muted">{mode==="login"?
          "Sign in with your Nightjar account, not your Supervisely credentials.":
          "Use the email already listed for you as an approved monitor. Create a Nightjar-only password; link Supervisely separately in Settings."}</p>
        <form onSubmit={e=>void submit(e)} className="form-stack">
          <label>Email address<input type="email" autoComplete="username" autoFocus required
            value={email} onChange={e=>setEmail(e.target.value)}
            placeholder="you@institution.pt"/></label>
          <label>{mode==="login"?"Password":"Choose your Nightjar password (12+ characters)"}
            <input type="password" autoComplete={mode==="login"?"current-password":"new-password"}
              required minLength={mode==="register"?12:undefined}
              value={password} onChange={e=>setPassword(e.target.value)}
              placeholder="Enter password"/></label>
          {mode==="register"&&<label>Confirm Nightjar password
            <input type="password" autoComplete="new-password" minLength={12}
              required value={confirmation} onChange={e=>setConfirmation(e.target.value)}
              placeholder="Enter the same password again"/></label>}
          {error&&<div role="alert" className="alert danger">{error}</div>}
          {notice&&<div role="status" className="alert success">{notice}</div>}
          <button className="btn primary large" disabled={working} type="submit">
            {working?"Working…":mode==="login"?"Sign in":"Create account and sign in"}
            <ArrowRight size={17}/>
          </button>
        </form>
        <div className="settings-action-row" style={{marginTop:15}}>
          <button type="button" className="btn secondary" disabled={working} onClick={switchMode}>
            {mode==="login"?"Create monitor account":"Back to sign in"}
          </button>
        </div>
        <p className="login-hint"><CircleHelp size={15}/>
          Only approved monitors in the current SQL event roster can register.
          If your email is not listed, ask the organiser to add or approve you.</p>
      </div>
      <div className="login-bottom">TÉCNICO · Bird Annotatiathon</div>
    </section>
  </main>;
}

function Overview({ auth }: { auth: Auth }) {
  const [stats, setStats] = useState<{people:number;monitors:number;competition_teams:number}|null>(null);
  const [error,setError] = useState("");
  const refresh = useCallback(async () => {
    try {
      const { totals } = await api<{totals:{people:number;monitors:number;competition_teams:number}}>("/teams");
      setStats(totals); setError("");
    } catch(exc) {setError((exc as Error).message);}
  },[]);
  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => {if(!document.hidden)void refresh();}, 5000);
    return () => window.clearInterval(id);
  },[refresh]);
  return <div className="page-body">
    <div className="page-heading"><div><span className="eyebrow">EVENT OVERVIEW</span>
      <h2>Good to see you, {auth.staff.name.split(" ")[0]}.</h2>
      <p className="muted">Your central workspace for the Bird Annotatiathon.</p></div>
      <button className="btn secondary" onClick={()=>void refresh()}><RefreshCw size={15}/> Refresh</button></div>
    {error && <div className="alert danger" role="alert">{error}</div>}
    <div className="stat-grid">
      {[
        { label: "Registered people", n: stats?.people, icon: UsersRound, accent: "mint" },
        { label: "Competition teams", n: stats?.competition_teams, icon: Activity, accent: "lavender" },
        { label: "Event monitors", n: stats?.monitors, icon: ShieldCheck, accent: "peach" },
      ].map(card=><div className="stat-card" key={card.label}><div className={"stat-icon "+card.accent}><card.icon size={21}/></div>
        <span className="muted small">{card.label}</span><strong>{card.n ?? "—"}</strong></div>)}
    </div>
    <div className="content-grid">
      <section className="panel">
        <span className="eyebrow">YOUR WORKSPACE</span><h3>Prepare your event</h3>
        <p className="muted">Import the roster, organise teams and configure Supervisely independently. No mandatory setup wizard.</p>
        <div className="step-row"><CheckCircle2 size={18}/><div><strong>Local application</strong>
          <span>Staff sessions and shared PostgreSQL records</span></div></div>
        <div className="step-row"><UsersRound size={18}/><div><strong>People & Teams</strong>
          <span>CSV import, sorting, editing and invitation email selection</span></div></div>
        <div className="step-row"><Settings2 size={18}/><div><strong>Supervisely Settings</strong>
          <span>Team and workspace configuration, separate from login</span></div></div>
      </section>
      <section className="panel">
        <span className="eyebrow">IMPLEMENTATION STATUS</span><h3>Building the platform</h3>
        <p className="muted">The complete local workflow is available as separate operational modules:</p>
        <div className="coming-row"><Award size={18}/> Activities & prize allocation now available</div>
        <div className="coming-row"><Cloud size={18}/> Dataset upload and balanced job planning</div>
        <div className="coming-row"><ClipboardCheck size={18}/> Annotation monitoring and review</div>
        <div className="coming-row"><CalendarDays size={18}/> Event finalisation and data export</div>
      </section>
    </div>
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
  const organiser = auth.staff.roles.includes("organiser");
  const has=(...roles:string[])=>auth.staff.roles.some(role=>roles.includes(role));
  const navigation: {id:Tab;label:string;icon:typeof LayoutDashboard}[] = [
    {id:"overview",label:"Overview",icon:LayoutDashboard},
    {id:"people",label:"People & Teams",icon:UsersRound},
    ...(has("organiser","reception","distribution","activity")?
      [{id:"tickets" as Tab,label:"Tickets & Rewards",icon:Ticket}]:[]),
    ...(has("organiser","activity","monitor","reception")?
      [{id:"activities" as Tab,label:"Activities & Prizes",icon:Activity}]:[]),
    ...(has("organiser","monitor","observer")?
      [{id:"datasets" as Tab,label:"Dataset",icon:Database}]:[]),
    ...(has("organiser","monitor","observer","activity")?
      [{id:"competition" as Tab,label:"Competition & Jobs",icon:UsersRound}]:[]),
    ...(has("organiser","monitor","observer")?
      [{id:"quality" as Tab,label:"Monitoring & Review",icon:ClipboardCheck}]:[]),
    ...(has("organiser","observer")?
      [{id:"closeout" as Tab,label:"Results & Exports",icon:CalendarDays}]:[]),
    ...(organiser ? [{id:"staff" as Tab,label:"Staff Access",icon:ShieldCheck}] : []),
    {id:"settings",label:"Settings",icon:Settings2},
  ];
  const accessibleTab=navigation.some(item=>item.id===tab)?tab:"overview";
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="sidebar-logo"><div className="brand-mark"><Bird size={23}/></div>
        <div><strong>NIGHTJAR</strong><span>ANNOTATIATHON</span></div></div>
      <div className="sidebar-label">WORKSPACE</div>
      <nav aria-label="Main navigation">
        {navigation.map(item=><button key={item.id} className={"nav-link "+(accessibleTab===item.id?"active":"")}
          onClick={()=>setTab(item.id)}><item.icon size={19}/>{item.label}
          {tab===item.id&&<ArrowRight size={15} className="nav-end"/>}</button>)}
      </nav>
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
      {accessibleTab==="overview"&&<Overview auth={auth}/>}
      {accessibleTab==="people"&&<PeopleScreen auth={auth}/>}
      {accessibleTab==="tickets"&&<TicketsScreen auth={auth}/>}
      {accessibleTab==="activities"&&<ActivitiesScreen auth={auth}/>}
      {accessibleTab==="datasets"&&<DatasetWorkspace auth={auth}/>}
      {accessibleTab==="competition"&&<CompetitionScreen auth={auth}/>}
      {accessibleTab==="quality"&&<QualityScreen auth={auth}/>}
      {accessibleTab==="closeout"&&<CloseoutScreen auth={auth}/>}
      {accessibleTab==="staff"&&organiser&&<div className="page-body"><div className="page-heading"><div><span className="eyebrow">ORGANISER · PERMISSIONS</span><h2>Staff Access</h2><p className="muted">Manage staff roles independently of the attendee roster.</p></div></div><StaffScreen auth={auth}/></div>}
      {accessibleTab==="settings"&&<SettingsScreen auth={auth}/>}
      <footer className="page-footer"><Bird size={14}/> Nightjar Annotatiathon · Técnico</footer>
    </div>
  </div>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
