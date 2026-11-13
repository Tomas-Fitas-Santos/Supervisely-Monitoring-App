import { useEffect, useRef, useState, type ReactNode } from "react";
import { RefreshCw } from "lucide-react";

export function RefreshButton({refresh, children, disabled, onError}:{
  refresh:()=>Promise<unknown>; children:ReactNode; disabled?:boolean; onError?:(error:unknown)=>void;
}){
  const [state,setState]=useState<"idle"|"loading"|"done"|"failed">("idle");
  const running=useRef(false);
  const timer=useRef<number|undefined>(undefined);
  const mounted=useRef(false);
  useEffect(()=>{
    mounted.current=true;
    return ()=>{mounted.current=false;window.clearTimeout(timer.current);};
  },[]);
  async function run(){
    if(running.current)return;
    running.current=true;
    window.clearTimeout(timer.current);
    setState("loading");
    try{
      const result=await refresh();
      if(mounted.current)setState(result===false?"failed":"done");
    }catch(error){
      if(mounted.current){setState("failed");onError?.(error);}
    }finally{
      running.current=false;
      if(mounted.current)timer.current=window.setTimeout(()=>setState("idle"),2200);
    }
  }
  return <button type="button" className="btn secondary" disabled={disabled||state==="loading"}
    aria-busy={state==="loading"} onClick={()=>void run()}>
    <RefreshCw size={15} aria-hidden="true" className={state==="loading"?"refresh-icon-spinning":undefined}/>
    <span role="status">{state==="loading"?"Refreshing…":state==="done"?"Updated":
      state==="failed"?"Refresh failed":children}</span>
  </button>;
}
