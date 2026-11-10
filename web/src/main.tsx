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
import { AttendanceScreen } from "./attendance";
import { StaffScreen } from "./staff";
import { TicketsScreen } from "./tickets";
import { ActivitiesScreen } from "./activities";
import { DatasetScreen } from "./datasets";
import { CompetitionScreen } from "./competition";
import { QualityScreen } from "./reviews";
import { CloseoutScreen } from "./closeout";
import "./styles.css";

type Tab = "overview" | "people" | "attendance" | "tickets" | "activities" | "datasets" | "competition" | "quality" | "closeout" | "staff" | "settings";

function Login({ onLogin }: { onLogin: (auth: Auth) => void }) {
  const [mode, setMode] = useState<"login" | "register" | "complete">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [working, setWorking] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setError(""); setNotice(""); setWorking(true);
    try {
      if (mode === "login") {
        onLogin(await api<Auth>("/auth/login", "POST", {email, password}));
      } else if (mode === "register") {
        const result = await api<{message:string}>("/auth/register/request", "POST", {email});
        setNotice(result.message);
        setMode("complete");
      } else {
        const result = await api<{message:string}>("/auth/register/complete",
          "POST", {email, code, password});
        setMode("login"); setPassword(""); setCode("");
        setNotice(result.message);
      }
    } catch(exc) {setError((exc as Error).message);}
    finally {setWorking(false);}
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
        <span className="eyebrow">{mode === "login"?"WELCOME BACK":"MONITOR REGISTRATION"}</span>
        <h2>{mode === "login"?"Sign in to Nightjar":
          mode === "register"?"Create your monitor account":"Verify your email"}</h2>
        <p className="muted">{mode === "login"?
          "Sign in using your Nightjar staff account, not your Supervisely credentials.":
          "Only monitors already registered in the event roster can create an account. Email verification is required."}</p>
        <form onSubmit={submit} className="form-stack">
          <label>Email address<input type="email" autoComplete="username" autoFocus required
            value={email} onChange={e=>setEmail(e.target.value)}
            disabled={mode==="complete"} placeholder="you@institution.pt" /></label>
          {mode==="complete"&&<label>8-digit verification code<input
            inputMode="numeric" autoComplete="one-time-code" minLength={8} maxLength={8}
            pattern="[0-9]{8}" value={code} required
            onChange={e=>setCode(e.target.value)} placeholder="Enter email code"/></label>}
          {mode!=="register"&&<label>{mode==="login"?"Password":"Choose a password (12+ characters)"}
            <input type="password" autoComplete={mode==="login"?"current-password":"new-password"}
              required minLength={mode==="complete"?12:undefined}
              value={password} onChange={e=>setPassword(e.target.value)}
              placeholder="Enter password" /></label>}
          {error&&<div role="alert" className="alert danger">{error}</div>}
          {notice&&<div role="status" className="alert success">{notice}</div>}
          <button className="btn primary large" disabled={working} type="submit">
            {working?"Working…":mode==="login"?"Sign in":
              mode==="register"?"Email verification code":"Create account"} <ArrowRight size={17}/>
          </button>
        </form>
        <div className="settings-action-row" style={{marginTop:15}}>
          <button type="button" className="btn secondary" onClick={()=>{
            setMode(mode==="login"?"register":"login");setError("");setNotice("");setCode("");
          }}>{mode==="login"?"Create monitor account":"Back to sign in"}</button>
          {mode==="complete"&&<button type="button" className="btn secondary"
            onClick={()=>{setMode("register");setError("");}}>Request another code</button>}
        </div>
        <p className="login-hint"><CircleHelp size={15}/>
          Contact the organiser if your email is not on the approved monitor roster.</p>
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

  async function logout() {
    try {
      await api("/auth/logout","POST",undefined,auth?.csrf_token);
      setAuth(null); setTab("overview"); setLogoutError("");
    } catch(exc) {setLogoutError((exc as Error).message);}
  }
  if (loading) return <div className="loading-screen"><Bird size={33}/> Loading Nightjar…</div>;
  if (!auth) return <Login onLogin={a=>{setAuth(a);setTab("overview");}}/>;
  const organiser = auth.staff.roles.includes("organiser");
  const navigation: {id:Tab;label:string;icon:typeof LayoutDashboard}[] = [
    {id:"overview",label:"Overview",icon:LayoutDashboard},
    {id:"people",label:"People & Teams",icon:UsersRound},
    {id:"attendance",label:"Check-in",icon:ClipboardCheck},
    {id:"tickets",label:"Tickets & Rewards",icon:Ticket},
    {id:"activities",label:"Activities & Prizes",icon:Activity},
    {id:"datasets",label:"Dataset",icon:Database},
    {id:"competition",label:"Competition & Jobs",icon:UsersRound},
    {id:"quality",label:"Monitoring & Review",icon:ClipboardCheck},
    {id:"closeout",label:"Results & Exports",icon:CalendarDays},
    ...(organiser ? [{id:"staff" as Tab,label:"Staff Access",icon:ShieldCheck}] : []),
    {id:"settings",label:"Settings",icon:Settings2},
  ];
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="sidebar-logo"><div className="brand-mark"><Bird size={23}/></div>
        <div><strong>NIGHTJAR</strong><span>ANNOTATIATHON</span></div></div>
      <div className="sidebar-label">WORKSPACE</div>
      <nav aria-label="Main navigation">
        {navigation.map(item=><button key={item.id} className={"nav-link "+(tab===item.id?"active":"")}
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
        navigation.find(n=>n.id===tab)?.label}</strong></div>
        <div className="topbar-right"><EventSelector auth={auth}/><span className="status-dot"/> Event operations
          <span className="topbar-avatar">{auth.staff.name.slice(0,1).toUpperCase()}</span></div>
      </header>
      {tab==="overview"&&<Overview auth={auth}/>}
      {tab==="people"&&<PeopleScreen auth={auth}/>}
      {tab==="attendance"&&<AttendanceScreen auth={auth}/>}
      {tab==="tickets"&&<TicketsScreen auth={auth}/>}
      {tab==="activities"&&<ActivitiesScreen auth={auth}/>}
      {tab==="datasets"&&<DatasetScreen auth={auth}/>}
      {tab==="competition"&&<CompetitionScreen auth={auth}/>}
      {tab==="quality"&&<QualityScreen auth={auth}/>}
      {tab==="closeout"&&<CloseoutScreen auth={auth}/>}
      {tab==="staff"&&organiser&&<div className="page-body"><div className="page-heading"><div><span className="eyebrow">ORGANISER · PERMISSIONS</span><h2>Staff Access</h2><p className="muted">Manage staff roles independently of the attendee roster.</p></div></div><StaffScreen auth={auth}/></div>}
      {tab==="settings"&&<SettingsScreen auth={auth}/>}
      <footer className="page-footer"><Bird size={14}/> Nightjar Annotatiathon · Técnico</footer>
    </div>
  </div>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
