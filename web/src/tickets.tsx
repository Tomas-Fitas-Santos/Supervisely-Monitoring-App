import { useCallback, useEffect, useState } from "react";
import type React from "react";
import { AlertTriangle, ClipboardCheck, Gift, RefreshCw, RotateCcw, Ticket } from "lucide-react";
import { api, type Auth, type Person, type PeopleResponse, type Team, type TeamResponse } from "./api";

type Category={id:string;slug:string;name:string;active:boolean;warning_enabled:boolean;
  warning_threshold:number|null;warning_window_minutes:number};
type Preview={id:string;status:string;count:number;quantity:number;operation:string;
  category:Category;scope:string;soft_warning_count:number;soft_warning_ids:string[];
  recipients:{id:string;name:string;email:string}[];confirmed_at:number|null;reversed_at:number|null};
type Batch={id:string;category:string;operation:string;quantity:number;recipients:number;
  status:string;confirmed_at:number|null};
type Balance={person_id:string;category_id:string;allocated:number;issued:number;redeemed:number;
  allocated_remaining:number;issued_remaining:number};

export function TicketsScreen({auth}:{auth:Auth}) {
  const canDistribute=auth.staff.roles.some(r=>["organiser","distribution","reception"].includes(r));
  const organiser=auth.staff.roles.includes("organiser");
  const [section,setSection]=useState<"distribution"|"policies"|"history">("distribution");
  const [categories,setCategories]=useState<Category[]>([]);
  const [batches,setBatches]=useState<Batch[]>([]);
  const [balances,setBalances]=useState<Balance[]>([]);
  const [people,setPeople]=useState<Person[]>([]);
  const [teams,setTeams]=useState<Team[]>([]);
  const [category,setCategory]=useState("");
  const [operation,setOperation]=useState<"allocate"|"issue"|"redeem">("issue");
  const [scope,setScope]=useState<"individuals"|"teams"|"all_checked_in"|"all_registered">("all_checked_in");
  const [quantity,setQuantity]=useState(1);
  const [selection,setSelection]=useState<string[]>([]);
  const [search,setSearch]=useState("");
  const [preview,setPreview]=useState<Preview|null>(null);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [busy,setBusy]=useState(false);
  const [editor,setEditor]=useState<string|null>(null);
  const [warningLimit,setWarningLimit]=useState("2");
  const [warningMinutes,setWarningMinutes]=useState("60");
  const [warningEnabled,setWarningEnabled]=useState(true);
  const [newCategoryName,setNewCategoryName]=useState("");
  const refresh=useCallback(async()=>{
    try{
      const [cats,history,ledger,ts]=await Promise.all([
        api<{items:Category[]}>("/tickets/categories"),
        api<{items:Batch[]}>("/tickets/batches"),
        api<{items:Balance[]}>("/tickets/balances"),
        api<TeamResponse>("/teams"),
      ]);
      setCategories(cats.items);setBatches(history.items);setBalances(ledger.items);setTeams(ts.teams.filter(t=>t.type!=="monitoring"));
      setCategory(old=>old||cats.items.find(c=>c.active)?.id||"");
      let all:Person[]=[];let page=1;let total=0;
      do{
        const result=await api<PeopleResponse>("/people?size=200&page="+page);
        all=[...all,...result.items];total=result.total;page++;
      }while(all.length<total && page<=26);
      setPeople(all);setError("");
    }catch(e){setError((e as Error).message);}
  },[]);
  useEffect(()=>{
    void refresh();
    const timer=window.setInterval(()=>{if(!document.hidden)void refresh();},7500);
    return ()=>window.clearInterval(timer);
  },[refresh]);
  function invalidate(){setPreview(null);setNotice("");}
  function choose(id:string){invalidate();setSelection(prev=>prev.includes(id)?prev.filter(x=>x!==id):[...prev,id]);}
  async function makePreview(){
    setError("");setNotice("");setBusy(true);
    try{
      const result=await api<Preview>("/tickets/preview","POST",{
        category_id:category,operation,quantity,
        selection:{scope,person_ids:scope==="individuals"?selection:[],
          team_ids:scope==="teams"?selection:[]},
      },auth.csrf_token);
      setPreview(result);
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function confirm(){
    if(!preview)return;
    if(!window.confirm(
      "Confirm "+preview.operation+" of "+preview.quantity+" "+preview.category.name+
      " ticket(s) for "+preview.count+" person(s)? This creates audited ledger records."
    ))return;
    setBusy(true);setError("");
    try{
      const result=await api<Preview>("/tickets/batches/"+preview.id+"/confirm","POST",undefined,auth.csrf_token);
      setPreview(result);
      setNotice("Batch confirmed for "+result.count+" person(s)."+
        (result.soft_warning_count?" "+result.soft_warning_count+" soft warning(s) were recorded.":""));
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function reverse(batch:Batch) {
    if(!window.confirm("Reverse the entire batch "+batch.id+"? Reversals are audited and only possible if subsequent ticket use permits."))return;
    setBusy(true);setError("");setNotice("");
    try {
      await api("/tickets/batches/"+batch.id+"/reverse","POST",undefined,auth.csrf_token);
      setNotice("Batch reversed, original ledger history preserved.");await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  function edit(cat:Category){
    setEditor(cat.id);setWarningLimit(String(cat.warning_threshold??2));
    setWarningMinutes(String(cat.warning_window_minutes));setWarningEnabled(cat.warning_enabled);
  }
  async function savePolicy(){
    if(!editor)return;
    setBusy(true);setError("");
    try{
      await api("/tickets/categories/"+editor,"PATCH",{
        warning_enabled:warningEnabled,
        warning_threshold:Number(warningLimit),
        warning_window_minutes:Number(warningMinutes),
      },auth.csrf_token);
      setEditor(null);setNotice("Advisory threshold saved. No hard limits are enforced.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function addCategory(e:React.FormEvent){
    e.preventDefault();setBusy(true);setError("");
    try{
      const slug=newCategoryName.trim().toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"");
      await api("/tickets/categories","POST",{name:newCategoryName,slug},auth.csrf_token);
      setNewCategoryName("");setNotice("New category added.");await refresh();
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  const shown=(scope==="individuals"?people:teams).filter(p=>
    p.name.toLowerCase().includes(search.toLowerCase()));
  const byId=new Map(people.map(p=>[p.id,p.name]));
  return <div className="page-body">
    <div className="page-heading"><div><span className="eyebrow">EVENT OPERATIONS · DISTRIBUTION</span>
      <h2>Tickets & Rewards</h2>
      <p className="muted">Allocate, physically issue and redeem tickets as separate auditable actions.</p>
      </div><button className="btn secondary" onClick={()=>void refresh()}><RefreshCw size={15}/> Refresh</button></div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}
    <nav className="workspace-subtabs" aria-label="Tickets and rewards sections">
      <button className={section==="distribution"?"active":""} onClick={()=>setSection("distribution")}>Issue & redeem</button>
      <button className={section==="policies"?"active":""} onClick={()=>setSection("policies")}>Ticket categories & warnings</button>
      <button className={section==="history"?"active":""} onClick={()=>setSection("history")}>History & balances</button>
    </nav>
    <div className="stat-grid compact">{categories.slice(0,3).map(cat=>
      <div className="stat-card" key={cat.id}><Ticket size={20}/><span className="muted small">{cat.name} · Issued</span>
        <strong>{balances.filter(b=>b.category_id===cat.id).reduce((n,b)=>n+b.issued,0)}</strong>
        <span className="muted small">Redeemed: {balances.filter(b=>b.category_id===cat.id).reduce((n,b)=>n+b.redeemed,0)}</span>
      </div>)}</div>
    <div className="content-grid tickets-grid ticket-section-grid">
      {section==="distribution"&&<>
      {canDistribute&&<section className="panel">
        <span className="eyebrow">BATCH DISTRIBUTION</span><h3>Prepare a ticket batch</h3>
        <p className="muted small">Preview an immutable recipient snapshot before confirming. The same batch cannot be applied twice.</p>
        <div className="form-stack">
          <label>Ticket category<select value={category} onChange={e=>{setCategory(e.target.value);invalidate();}}>
            {categories.filter(c=>c.active||c.id===category).map(c=><option key={c.id} value={c.id}>{c.name}</option>)}
          </select></label>
          <div className="form-grid">
            <label>Operation<select value={operation} onChange={e=>{setOperation(e.target.value as typeof operation);invalidate();}}>
              <option value="allocate">Allocate only</option>
              <option value="issue">Physically issue</option>
              <option value="redeem">Redeem physically issued</option>
            </select></label>
            <label>Quantity per person<input type="number" min={1} max={100} value={quantity}
              onChange={e=>{setQuantity(Number(e.target.value));invalidate();}}/></label>
          </div>
          <label>Recipients<select value={scope} onChange={e=>{setScope(e.target.value as typeof scope);setSelection([]);invalidate();}}>
            <option value="all_checked_in">All checked-in people</option>
            <option value="all_registered">All registered people</option>
            <option value="individuals">Selected individuals</option>
            <option value="teams">Selected competition teams</option>
          </select></label>
          {(scope==="individuals"||scope==="teams")&&<div className="recipient-picker">
            <input aria-label="Filter recipients" placeholder="Filter names…" value={search} onChange={e=>setSearch(e.target.value)}/>
            <div className="recipient-options">{shown.slice(0,400).map(item=>
              <label key={item.id} className="role-choice"><input type="checkbox"
                checked={selection.includes(item.id)} onChange={()=>choose(item.id)}/>{item.name}</label>)}
            </div><span className="muted small">{selection.length} selected</span>
          </div>}
          <button className="btn primary" disabled={busy||!category||quantity<1} onClick={()=>void makePreview()}>
            <ClipboardCheck size={17}/> Preview recipients</button>
        </div>
        {preview&&<div className="preview-box">
          <span className="eyebrow">CONFIRMATION PREVIEW</span>
          <h3>{preview.count} recipients · {preview.category.name}</h3>
          <p className="muted small">{preview.operation} {preview.quantity} ticket(s) per person</p>
          {preview.soft_warning_count>0&&<div className="advisory"><AlertTriangle size={18}/>
            <span>{preview.soft_warning_count} recipient(s) exceed the configured rolling issuance threshold. This is a warning, not a restriction.</span></div>}
          <details><summary>Review recipient snapshot</summary>
            <div className="recipient-preview">{preview.recipients.map(r=><div key={r.id}>{r.name} <span>{r.email}</span></div>)}</div>
          </details>
          {preview.status==="preview"?<button className="btn primary large" disabled={busy} onClick={()=>void confirm()}>
            Confirm {preview.operation} <Gift size={17}/></button>:<div className="preview-line">
              <ClipboardCheck size={18}/> {preview.status==="confirmed"?"Batch confirmed":"Batch "+preview.status}</div>}
        </div>}
      </section>}
      </>}
      {section==="policies"&&<section className="panel">
        <span className="eyebrow">CATEGORY POLICY</span><h3>Ticket categories</h3>
        <p className="muted small">Warnings are advisory only. Defaults: pizza over 2/hour; drinks over 3/hour.</p>
        {categories.map(cat=><div className="policy-row" key={cat.id}>
          <div><strong>{cat.name}</strong><span>{cat.warning_enabled?
            "Warn over "+cat.warning_threshold+" / "+cat.warning_window_minutes+" min":"No warning"}</span></div>
          {organiser&&<button className="link-button" onClick={()=>edit(cat)}>Edit</button>}
        </div>)}
        {editor&&organiser&&<div className="policy-editor">
          <label className="role-choice"><input type="checkbox" checked={warningEnabled}
            onChange={e=>setWarningEnabled(e.target.checked)}/> Enable soft warning</label>
          <div className="form-grid"><label>Threshold<input type="number" min="1"
            value={warningLimit} onChange={e=>setWarningLimit(e.target.value)}/></label>
            <label>Minutes<input type="number" min="1" value={warningMinutes}
              onChange={e=>setWarningMinutes(e.target.value)}/></label></div>
          <div className="actions-right"><button className="btn secondary" onClick={()=>setEditor(null)}>Cancel</button>
            <button className="btn primary" disabled={busy} onClick={()=>void savePolicy()}>Save</button></div>
        </div>}
        {organiser&&<form className="category-form" onSubmit={e=>void addCategory(e)}>
          <label>New category<input required value={newCategoryName} onChange={e=>setNewCategoryName(e.target.value)}
            placeholder="e.g. Merchandise"/></label>
          <button className="btn secondary" disabled={busy}>Add category</button>
        </form>}
        <div className="settings-note" style={{marginTop:18}}><AlertTriangle size={18}/>
          <p>Warnings never stop a distribution. Recipients and issuing staff remain recorded for auditing.</p></div>
      </section>}
    </div>
    {section==="history"&&<>
    <section className="panel" style={{marginTop:22}}>
      <span className="eyebrow">DISTRIBUTION HISTORY</span><h3>Confirmed batches</h3>
      <div className="table-scroll"><table className="data-table"><thead><tr>
        <th>Category</th><th>Operation</th><th>Recipients</th><th>Quantity each</th><th>Date</th><th>Status</th><th>Action</th>
      </tr></thead><tbody>{batches.map(batch=><tr key={batch.id}>
        <td><strong>{batch.category}</strong></td><td>{batch.operation}</td><td>{batch.recipients}</td>
        <td>{batch.quantity}</td>
        <td>{batch.confirmed_at?new Date(batch.confirmed_at*1000).toLocaleString():"—"}</td>
        <td>{batch.status}</td><td>{organiser&&batch.status==="confirmed"?
          <button className="link-button" disabled={busy} onClick={()=>void reverse(batch)}>
            <RotateCcw size={13}/> Reverse</button>:"—"}</td>
      </tr>)}</tbody></table>{!batches.length&&<div className="empty-table">
        No ticket batches have been confirmed.</div>}</div>
    </section>
    <section className="panel" style={{marginTop:22}}>
      <span className="eyebrow">PER-PERSON LEDGER</span><h3>Individual ticket balances</h3>
      <div className="table-scroll"><table className="data-table"><thead><tr>
        <th>Person</th><th>Category</th><th>Allocated</th><th>Issued</th><th>Redeemed</th><th>Available to issue</th><th>Available to redeem</th>
      </tr></thead><tbody>{balances.map(b=><tr key={b.person_id+b.category_id}>
        <td>{byId.get(b.person_id)||b.person_id}</td>
        <td>{categories.find(c=>c.id===b.category_id)?.name||"—"}</td>
        <td>{b.allocated}</td><td>{b.issued}</td><td>{b.redeemed}</td>
        <td>{b.allocated_remaining}</td><td>{b.issued_remaining}</td>
      </tr>)}</tbody></table>{!balances.length&&<div className="empty-table">No ticket activity yet.</div>}</div>
    </section></>}
  </div>;
}
