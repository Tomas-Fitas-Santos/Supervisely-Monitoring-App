import { useEffect, useState } from "react";
import type React from "react";
import { AlertTriangle, CheckCircle2, X } from "lucide-react";

type ConfirmRequest={kind:"confirm";message:string;confirmLabel:string;
  danger:boolean;resolve:(value:boolean)=>void};
type TextRequest={kind:"text";message:string;initial:string;
  resolve:(value:string|null)=>void};
type DialogRequest=ConfirmRequest|TextRequest;
const pending:DialogRequest[]=[];
let notify:((next:DialogRequest|null)=>void)|null=null;

function enqueue<T>(create:(resolve:(value:T)=>void)=>DialogRequest):Promise<T>{
  return new Promise(resolve=>{
    const request=create(resolve);
    pending.push(request);
    if(pending.length===1)notify?.(request);
  });
}
export function confirmAction(message:string,options?:{
  confirmLabel?:string;danger?:boolean;
}):Promise<boolean>{
  const danger=options?.danger??
    /delete|remove|reset|deactivate|disband|archive|clear local|stop synchronization/i.test(message);
  return enqueue(resolve=>({kind:"confirm",message,
    confirmLabel:options?.confirmLabel||(danger?"Confirm action":"Continue"),
    danger,resolve}));
}
export function promptText(message:string,initial=""):Promise<string|null>{
  return enqueue(resolve=>({kind:"text",message,initial,resolve}));
}
export function AppDialogHost(){
  const [active,setActive]=useState<DialogRequest|null>(null);
  const [answer,setAnswer]=useState("");
  useEffect(()=>{
    const handler=(request:DialogRequest|null)=>{
      setActive(request);
      setAnswer(request?.kind==="text"?request.initial:"");
    };
    notify=handler;handler(pending[0]||null);
    return()=>{if(notify===handler)notify=null;};
  },[]);
  function finish(value:boolean|string|null){
    const current=pending.shift();
    if(current){
      if(current.kind==="confirm")current.resolve(value===true);
      else current.resolve(typeof value==="string"?value:null);
    }
    const next=pending[0]||null;
    setActive(next);
    setAnswer(next?.kind==="text"?next.initial:"");
  }
  if(!active)return null;
  return <div className="modal-overlay app-confirm-overlay" role="presentation">
    <form className="modal-card app-confirm-modal" role="dialog" aria-modal="true"
      aria-label={active.kind==="text"?"Enter confirmation":
        active.danger?"Confirm sensitive action":"Confirm action"}
      onKeyDown={e=>{if(e.key==="Escape"){e.preventDefault();finish(null);}}}
      onSubmit={(e:React.FormEvent)=>{
        e.preventDefault();
        if(active.kind==="confirm")finish(true);
        else finish(answer);
      }}>
      <div className="modal-heading">
        <h3>{active.kind==="text"?"Enter confirmation":
          active.danger?"Confirm sensitive action":"Confirm action"}</h3>
        <button type="button" className="icon-button" aria-label="Cancel"
          onClick={()=>finish(null)}><X size={19}/></button>
      </div>
      <div className="app-confirm-message">
        {active.kind==="confirm"&&active.danger?
          <AlertTriangle size={22}/>:<CheckCircle2 size={22}/>}
        <p>{active.message}</p>
      </div>
      {active.kind==="text"&&<label>Confirmation text
        <input autoFocus value={answer} onChange={e=>setAnswer(e.target.value)}/>
      </label>}
      <div className="modal-actions">
        <button type="button" className="btn secondary" onClick={()=>finish(null)}>Cancel</button>
        <button type="submit" className={"btn "+(
          active.kind==="confirm"&&active.danger?"danger-outline":"primary")}>
          {active.kind==="text"?"Submit":active.confirmLabel}
        </button>
      </div>
    </form>
  </div>;
}
