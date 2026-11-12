import { confirmAction } from "./dialogs";
import { useCallback, useEffect, useState } from "react";
import type React from "react";
import { AlertTriangle, ClipboardCheck, Gift, RefreshCw, Ticket } from "lucide-react";
import { api, type Auth, type Person, type PeopleResponse } from "./api";
import { useTableSort } from "./table_sort";

type Category={id:string;slug:string;name:string;active:boolean;warning_enabled:boolean;
  warning_threshold:number|null;warning_window_minutes:number};
type Preview={id:string;status:string;count:number;quantity:number;operation:string;
  category:Category;scope:string;soft_warning_count:number;soft_warning_ids:string[];
  recipients:{id:string;name:string;email:string}[];confirmed_at:number|null;reversed_at:number|null};
type Balance={person_id:string;category_id:string;issued:number};

export function TicketsScreen({auth}:{auth:Auth}) {
  const canDistribute=auth.staff.roles.some(r=>["organiser","monitor","distribution","reception"].includes(r));
  const organiser=auth.staff.roles.includes("organiser");
  const [section,setSection]=useState<"distribution"|"policies"|"history">("distribution");
  const [categories,setCategories]=useState<Category[]>([]);
  const [balances,setBalances]=useState<Balance[]>([]);
  const balanceSort=useTableSort();
  const [people,setPeople]=useState<Person[]>([]);
  const [category,setCategory]=useState("");
  const [quantity,setQuantity]=useState(1);
  const [recipient,setRecipient]=useState("");
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
      const [cats,ledger]=await Promise.all([
        api<{items:Category[]}>("/tickets/categories"),
        api<{items:Balance[]}>("/tickets/balances"),
      ]);
      setCategories(cats.items);setBalances(ledger.items);
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
  function choose(id:string){invalidate();setRecipient(id);}
  async function makePreview(){
    setError("");setNotice("");setBusy(true);
    try{
      const result=await api<Preview>("/tickets/preview","POST",{
        category_id:category,operation:"issue",quantity,
        selection:{scope:"individuals",person_ids:[recipient]},
      },auth.csrf_token);
      setPreview(result);
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function confirm(){
    if(!preview)return;
    if(!await confirmAction(
      "Hand out "+preview.quantity+" "+preview.category.name+
      " ticket(s) to "+preview.recipients[0]?.name+"?"
    ))return;
    setBusy(true);setError("");
    try{
      const result=await api<Preview>("/tickets/batches/"+preview.id+"/confirm","POST",undefined,auth.csrf_token);
      setPreview(null);
      setRecipient("");
      setNotice("Handout recorded for "+result.recipients[0]?.name+"."+
        (result.soft_warning_count?" "+result.soft_warning_count+" soft warning(s) were recorded.":""));
      await refresh();
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
  const shown=people.filter(person=>
    [person.name,person.email,person.team_name].some(value=>value?.toLowerCase().includes(search.trim().toLowerCase())));
  const issuedByPerson=new Map(balances.map(b=>[b.person_id+":"+b.category_id,b.issued]));
  const issued=(personId:string,categoryId:string)=>issuedByPerson.get(personId+":"+categoryId)??0;
  const countCategories=categories.filter(cat=>cat.active||balances.some(b=>b.category_id===cat.id&&b.issued>0));
  return <div className="page-body">
    <div className="page-heading"><div><span className="eyebrow">EVENT OPERATIONS · DISTRIBUTION</span>
      <h2>Tickets & Rewards</h2>
      <p className="muted">Hand out tickets and see how many each person has received.</p>
      </div><button className="btn secondary" onClick={()=>void refresh()}><RefreshCw size={15}/> Refresh</button></div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}
    <nav className="workspace-subtabs" aria-label="Tickets and rewards sections">
      <button className={section==="distribution"?"active":""} onClick={()=>setSection("distribution")}>Hand out tickets</button>
      <button className={section==="policies"?"active":""} onClick={()=>setSection("policies")}>Ticket categories & warnings</button>
      <button className={section==="history"?"active":""} onClick={()=>setSection("history")}>Tickets per person</button>
    </nav>
    <div className="stat-grid compact">{categories.slice(0,3).map(cat=>
      <div className="stat-card" key={cat.id}><Ticket size={20}/><span className="muted small">{cat.name} · Issued</span>
        <strong>{balances.filter(b=>b.category_id===cat.id).reduce((n,b)=>n+b.issued,0)}</strong>
      </div>)}</div>
    <div className="content-grid tickets-grid ticket-section-grid">
      {section==="distribution"&&<>
      {canDistribute&&<section className="panel">
        <h3>Hand out tickets</h3>
        <div className="form-stack">
          <label>Ticket category<select value={category} onChange={e=>{setCategory(e.target.value);invalidate();}}>
            {categories.filter(c=>c.active||c.id===category).map(c=><option key={c.id} value={c.id}>{c.name}</option>)}
          </select></label>
          <label>Quantity<input type="number" min={1} max={100} value={quantity}
            onChange={e=>{setQuantity(Number(e.target.value));invalidate();}}/></label>
          <div className="recipient-picker">
            <input aria-label="Search people" placeholder="Search name, email or team" value={search} onChange={e=>setSearch(e.target.value)}/>
            <div className="recipient-options">{shown.map(person=>
              <label key={person.id} className="role-choice"><input type="radio" name="ticket-recipient"
                checked={recipient===person.id} onChange={()=>choose(person.id)}/>
                {person.name}{person.team_name?" · "+person.team_name:""}</label>)}
              {!shown.length&&<p className="muted small">No people match your search.</p>}
            </div>
          </div>
          <button className="btn primary" disabled={busy||!category||!recipient||!Number.isInteger(quantity)||quantity<1||quantity>100} onClick={()=>void makePreview()}>
            <ClipboardCheck size={17}/> Review handout</button>
        </div>
        {preview&&<div className="preview-box">
          <span className="eyebrow">CONFIRMATION PREVIEW</span>
          <h3>{preview.recipients[0]?.name} · {preview.category.name}</h3>
          <p className="muted small">{preview.quantity} ticket(s)</p>
          {preview.soft_warning_count>0&&<div className="advisory"><AlertTriangle size={18}/>
            <span>{preview.soft_warning_count} recipient(s) exceed the configured rolling issuance threshold. This is a warning, not a restriction.</span></div>}
          <button className="btn primary large" disabled={busy} onClick={()=>void confirm()}>
            Confirm handout <Gift size={17}/></button>
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
    {section==="history"&&<section className="panel" style={{marginTop:22}}>
      <h3>Tickets handed out per person</h3>
      <input aria-label="Search people" placeholder="Search name, email or team" value={search} onChange={e=>setSearch(e.target.value)}/>
      <div className="table-scroll"><table className="data-table"><thead><tr>
        {balanceSort.header("person","Person")}
        {countCategories.map(cat=>balanceSort.header(cat.id,cat.name))}
      </tr></thead><tbody>{balanceSort.rows(shown,(person,key)=>key==="person"?person.name:issued(person.id,key)).map(person=>
        <tr key={person.id}><td>{person.name}{person.team_name&&<span className="muted small"> · {person.team_name}</span>}</td>
          {countCategories.map(cat=><td key={cat.id}>{issued(person.id,cat.id)}</td>)}
        </tr>)}</tbody></table>{!shown.length&&<div className="empty-table">No people match your search.</div>}</div>
    </section>}
  </div>;
}
