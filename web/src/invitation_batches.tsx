import { confirmAction } from "./dialogs";
import { useCallback, useEffect, useState } from "react";
import { ClipboardCopy, RefreshCw, ShieldCheck, UsersRound } from "lucide-react";
import { api, type Auth } from "./api";

type Recipient={id:string;name:string;email:string};
type Batch={kind:"monitor"|"participant";available:number;selected:number;
  more_after_batch:number;items:Recipient[];native_invites_sent:boolean};

function InvitationGroup({auth,kind,version,onComplete}:{
  auth:Auth;kind:"monitor"|"participant";version:number;onComplete:()=>void;
}){
  const [batch,setBatch]=useState<Batch|null>(null);
  const [copied,setCopied]=useState<string[]>([]);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [message,setMessage]=useState("");
  const load=useCallback(async()=>{
    const result=await api<Batch>("/people/invitations/next?kind="+kind+"&limit=50");
    setBatch(result);setCopied([]);
  },[kind]);
  useEffect(()=>{void load().catch(e=>setError((e as Error).message));},[load,version]);

  const label=kind==="monitor"?"Monitors":"Participants";
  const nativeRole=kind==="monitor"?"Manager":"Annotator";
  const ready=Boolean(batch?.items.length);
  const sameBatch=Boolean(batch?.items.length &&
    batch.items.every(p=>copied.includes(p.id)) &&
    copied.length===batch.items.length);

  async function copy(){
    if(!ready||busy||!batch)return;
    setBusy(true);setError("");setMessage("");
    try{
      const text=batch.items.map(p=>p.email).join(",");
      await navigator.clipboard.writeText(text);
      setCopied(batch.items.map(p=>p.id));
      setMessage("Copied "+batch.items.length+" "+label.toLowerCase()+
        " emails. Paste into Supervisely and invite as "+nativeRole+".");
    }catch{setError("Clipboard access was denied. Open Nightjar over HTTPS and allow clipboard permissions.");}
    finally{setBusy(false);}
  }
  async function markInvited(){
    if(!batch||!sameBatch||busy)return;
    if(!await confirmAction("Have you actually submitted this batch of "+batch.items.length+
      " "+label.toLowerCase()+" invitations in Supervisely as "+nativeRole+
      "? Copying addresses is not the same as sending invitations."))return;
    setBusy(true);setError("");setMessage("");
    try{
      await api("/people/invitations-reported","POST",
        {person_ids:batch.items.map(p=>p.id)},auth.csrf_token);
      const amount=batch.items.length;
      await load();
      setMessage(amount+" invitations recorded as sent. The next uninvited batch is ready.");
      onComplete();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  return <section className="panel" style={{flex:"1 1 310px",minWidth:0}}>
    <div className="panel-heading"><div>
      <span className="eyebrow">SUPERVISELY INVITATION QUEUE</span>
      <h3>{label} · {nativeRole}</h3>
      <p className="muted">Up to 50 people who have not been invited yet, across every roster page.</p>
    </div><UsersRound size={22}/></div>
    <div className="settings-row"><span>Not invited</span>
      <strong>{batch?.available??"…"}</strong></div>
    <div className="settings-row"><span>Next batch</span>
      <strong>{batch?.selected??"…"} of 50</strong></div>
    {batch&&batch.more_after_batch>0&&<p className="muted small">
      {batch.more_after_batch} more after the current batch.</p>}
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {message&&<div className="alert success" role="status">{message}</div>}
    <div className="settings-action-row" style={{display:"flex",flexWrap:"wrap",gap:8}}>
      <button className="btn primary" disabled={busy||!ready} onClick={()=>void copy()}>
        <ClipboardCopy size={15}/> Copy {batch?.selected??0} emails</button>
      <button className="btn secondary" disabled={busy||!sameBatch}
        onClick={()=>void markInvited()}>
        <ShieldCheck size={15}/> Mark batch invited</button>
      <button className="btn secondary" disabled={busy} onClick={()=>void load()}>
        <RefreshCw size={15}/> Refresh</button>
    </div>
    <p className="muted small">Only mark a batch invited after sending it through
      Supervisely. Joining/accepting later does not put the person back into this queue.</p>
  </section>;
}

export function InvitationBatches({auth,version,onComplete}:{
  auth:Auth;version:number;onComplete:()=>void;
}){
  return <div style={{display:"flex",flexWrap:"wrap",gap:16,marginBottom:16}}>
    <InvitationGroup auth={auth} kind="monitor" version={version} onComplete={onComplete}/>
    <InvitationGroup auth={auth} kind="participant" version={version} onComplete={onComplete}/>
  </div>;
}
