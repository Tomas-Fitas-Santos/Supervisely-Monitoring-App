import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity, ArrowRight, Bird, CalendarDays, CheckCircle2, ChevronDown,
  CircleHelp, ClipboardCheck, Cloud, Database, LayoutDashboard, LogOut,
  RefreshCw, Settings2, ShieldCheck, Ticket, UsersRound
} from "lucide-react";
import { api, type Auth } from "./api";
import { PeopleScreen } from "./people";
import { SettingsScreen } from "./settings";
import "./styles.css";

type Tab = "overview" | "people" | "settings";

function Login({ onLogin }: { onLogin: (auth: Auth) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setError(""); setWorking(true);
    try { onLogin(await api<Auth>("/auth/login", "POST", { email, password })); }
    catch (exc) { setError((exc as Error).message); }
    finally { setWorking(false); }
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
        <span className="eyebrow">WELCOME BACK</span>
        <h2>Sign in to Nightjar</h2>
        <p className="muted">Use your Annotatiathon staff account. A Supervisely API token is not needed to sign in.</p>
        <form onSubmit={submit} className="form-stack">
          <label>Email address<input type="email" autoComplete="username" autoFocus required
            value={email} onChange={e=>setEmail(e.target.value)}
            placeholder="you@institution.pt" /></label>
          <label>Password<input type="password" autoComplete="current-password" required
            value={password} onChange={e=>setPassword(e.target.value)}
            placeholder="Enter your password" /></label>
          {error && <div role="alert" className="alert danger">{error}</div>}
          <button className="btn primary large" disabled={working} type="submit">
            {working ? "Signing in…" : "Sign in"} <ArrowRight size={17}/>
          </button>
        </form>
        <p className="login-hint"><CircleHelp size={15}/> Contact the organiser if you do not have a staff account.</p>
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
        <p className="muted">The new app is being implemented in stages. These workflows are not yet available in the rebuild:</p>
        <div className="coming-row"><Ticket size={18}/> Tickets, check-in & activities</div>
        <div className="coming-row"><Cloud size={18}/> Dataset upload & distribution</div>
        <div className="coming-row"><ClipboardCheck size={18}/> Annotation monitoring & evaluation</div>
        <div className="coming-row"><CalendarDays size={18}/> Event finalization & export</div>
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
    ...(organiser ? [{id:"settings" as Tab,label:"Settings",icon:Settings2}] : []),
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
      <div className="sidebar-info"><Database size={17}/><div><strong>Local database</strong>
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
        <div className="topbar-right"><span className="status-dot"/> Local development
          <span className="topbar-avatar">{auth.staff.name.slice(0,1).toUpperCase()}</span></div>
      </header>
      {tab==="overview"&&<Overview auth={auth}/>}
      {tab==="people"&&<PeopleScreen auth={auth}/>}
      {tab==="settings"&&organiser&&<SettingsScreen auth={auth}/>}
      <footer className="page-footer"><Bird size={14}/> Nightjar Annotatiathon · Técnico</footer>
    </div>
  </div>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
