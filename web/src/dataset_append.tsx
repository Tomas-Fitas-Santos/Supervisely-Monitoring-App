/** Append one ZIP or selected folder into an already-finalized dataset.
 * The server owns saved offsets, isolated staging and atomic inventory commits.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { CloudUpload, FolderOpen, RefreshCw, Trash2 } from "lucide-react";
import { api } from "./api";
import { confirmAction } from "./dialogs";
import { validateDatasetSources } from "./dataset_transfer";

type UploadKind="zip"|"folder";
type Batch={id:string;dataset_id:string;status:string;upload_kind:UploadKind;
  received_bytes:number;started_revision:number;error_code:string|null};
type BatchDetails=Batch&{files:{path:string;received_bytes:number;expected_bytes:number}[]};
const CHUNK=8*1024*1024;
const pathFor=(file:File,kind:UploadKind)=>kind==="zip"?"source.zip":
  (file.webkitRelativePath||file.name).replaceAll("\\","/");

async function errorFrom(response:Response){
  const body=await response.json().catch(()=>({}));
  return new Error(typeof body.detail==="string"?body.detail:
    "Upload rejected ("+response.status+")");
}

export function AppendVideos({datasetId,csrf,onCommitted}:{
  datasetId:string;csrf:string;onCommitted:()=>Promise<unknown>|void;
}){
  const [kind,setKind]=useState<UploadKind>("folder");
  const [files,setFiles]=useState<File[]>([]);
  const [batches,setBatches]=useState<Batch[]>([]);
  const [activeId,setActiveId]=useState<string|null>(null);
  const [running,setRunning]=useState(false);
  const [progress,setProgress]=useState({done:0,total:0});
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const controller=useRef<AbortController|null>(null);
  const fileRef=useRef<HTMLInputElement|null>(null);
  const refresh=useCallback(async()=>{
    const result=await api<{items:Batch[]}>(`/datasets/${datasetId}/append-batches`);
    setBatches(result.items);
    setActiveId(old=>old&&result.items.some(b=>b.id===old)?old:
      result.items.find(b=>["uploading","indexing","failed"].includes(b.status))?.id||null);
    if(result.items.some(b=>b.status==="committed"))void onCommitted();
  },[datasetId,onCommitted]);
  useEffect(()=>{
    let live=true;
    const tick=()=>{if(live)void refresh().catch(()=>undefined);};
    tick();
    const timer=window.setInterval(()=>{if(!document.hidden)tick();},9000);
    return()=>{live=false;window.clearInterval(timer);};
  },[refresh]);
  useEffect(()=>{
    setFiles([]);setActiveId(null);setError("");setNotice("");
  },[datasetId]);
  const active=batches.find(b=>b.id===activeId)||null;
  const total=files.reduce((sum,file)=>sum+file.size,0);
  function selectedFiles(next:File[]){setFiles(next);setError("");}

  async function sendBatch(batch:Batch){
    if(!files.length)throw new Error("Reselect the source ZIP or complete video folder.");
    validateDatasetSources(files,batch.upload_kind);
    const details=await api<BatchDetails>(
      `/datasets/${datasetId}/append-batches/${batch.id}`);
    const previous=new Map(details.files.map(row=>[row.path,row]));
    const controllerValue=new AbortController();
    controller.current=controllerValue;
    let done=0;
    for(const file of files){
      const path=pathFor(file,batch.upload_kind);
      const row=previous.get(path);
      if(row&&row.expected_bytes!==file.size){
        throw new Error("Reselected file differs from saved upload: "+path);
      }
      done+=row?.received_bytes||0;
    }
    if(details.files.some(row=>!files.some(file=>
      pathFor(file,batch.upload_kind)===row.path))){
      throw new Error("The reselected folder omits a file in this saved batch.");
    }
    setProgress({done,total});
    for(const file of files){
      const path=pathFor(file,batch.upload_kind);
      let offset=previous.get(path)?.received_bytes||0;
      let retries=0;
      while(offset<file.size){
        if(controllerValue.signal.aborted)throw new Error("Upload paused. Reselect the same files to resume.");
        const end=Math.min(offset+CHUNK,file.size);
        try{
          const response=await fetch(
            `/api/v1/datasets/${datasetId}/append-batches/${batch.id}/chunk?`+
              "relative_path="+encodeURIComponent(path)+"&offset="+offset+"&total="+file.size,{
                method:"PUT",credentials:"same-origin",
                headers:{"X-CSRF-Token":csrf,"Content-Type":"application/octet-stream"},
                body:file.slice(offset,end),signal:controllerValue.signal,
              }
          );
          if(!response.ok)throw await errorFrom(response);
          const receipt=await response.json() as {received_bytes:number};
          if(receipt.received_bytes<=offset||receipt.received_bytes>end)
            throw new Error("Unexpected server upload offset.");
          done+=receipt.received_bytes-offset;
          offset=receipt.received_bytes;retries=0;
          setProgress({done,total});
        }catch(e){
          if(controllerValue.signal.aborted)throw e;
          // A successful chunk may have lost its HTTP response: the server
          // offset is authoritative before we retry.
          const state=await api<BatchDetails>(
            `/datasets/${datasetId}/append-batches/${batch.id}`);
          const saved=state.files.find(item=>item.path===path)?.received_bytes||0;
          if(saved>offset&&saved<=end){
            done+=saved-offset;offset=saved;retries=0;
            setProgress({done,total});
          }else if(++retries>=3)throw e;
        }
      }
    }
    await api(`/datasets/${datasetId}/append-batches/${batch.id}/finish`,
      "POST",undefined,csrf);
    setNotice("Batch uploaded. Local indexing will commit complete videos into the existing dataset.");
    setFiles([]);if(fileRef.current)fileRef.current.value="";
    await refresh();
  }

  async function upload(){
    if(running||!files.length)return;
    setRunning(true);setError("");setNotice("");
    try{
      const reuse=active?.status==="uploading"?active:null;
      if(reuse&&reuse.upload_kind!==kind)
        throw new Error("Choose the same upload format as the existing batch.");
      const target=reuse||await api<Batch>(
        `/datasets/${datasetId}/append-batches`,"POST",{upload_kind:kind},csrf);
      setActiveId(target.id);
      await sendBatch(target);
    }catch(e){setError((e as Error).message);}
    finally{controller.current=null;setRunning(false);await refresh().catch(()=>undefined);}
  }

  async function retry(){
    if(!active)return;
    setRunning(true);setError("");
    try{
      await api(`/datasets/${datasetId}/append-batches/${active.id}/retry`,
        "POST",undefined,csrf);
      setNotice("Local indexing resumed. Committed source clips are not replaced.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setRunning(false);}
  }

  async function discard(){
    if(!active||!await confirmAction(
      "Discard this uncommitted append batch? The existing dataset will remain unchanged."))return;
    setRunning(true);setError("");
    try{
      await api(`/datasets/${datasetId}/append-batches/${active.id}`,
        "DELETE",undefined,csrf);
      setNotice("Uncommitted batch discarded. The original dataset is unchanged.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setRunning(false);}
  }

  return <div className="settings-note">
    <div className="panel-heading"><div>
      <strong>Add videos to this dataset</strong>
      <p className="muted small">Append another ZIP or folder containing complete
        species/video clips. New species may overlap existing batches, but
        existing video names and source frames cannot be replaced.</p>
    </div><button className="btn secondary" type="button" disabled={running}
      onClick={()=>void refresh()}><RefreshCw size={14}/> Refresh</button></div>
    <label>Append upload format
      <select value={kind} disabled={running||Boolean(active&&active.status==="uploading")}
        onChange={e=>{setKind(e.target.value as UploadKind);setFiles([]);
          if(fileRef.current)fileRef.current.value="";}}>
        <option value="folder">Folder of species/video clips</option>
        <option value="zip">ZIP of species/video clips</option>
      </select>
    </label>
    <label className="dataset-drop">
      {kind==="folder"?<FolderOpen size={23}/>:<CloudUpload size={23}/>}
      <span>{kind==="folder"?"Choose a folder to append":"Choose a ZIP to append"}</span>
      {kind==="folder"?<input ref={fileRef} type="file" multiple
        {...{webkitdirectory:"",directory:""}}
        disabled={running||Boolean(active&&active.status!=="uploading")}
        onChange={e=>selectedFiles(Array.from(e.target.files||[]))}/>:
        <input ref={fileRef} type="file" accept=".zip,application/zip"
          disabled={running||Boolean(active&&active.status!=="uploading")}
          onChange={e=>selectedFiles(Array.from(e.target.files||[]))}/>}
    </label>
    <p className="muted small">{files.length} file(s) selected ·
      {" "}{Math.round(total/1048576)} MiB</p>
    {active&&<p className="muted small">Latest batch: {active.status}
      {" "}({active.upload_kind}, revision {active.started_revision})
      {active.error_code?" · "+active.error_code:""}</p>}
    {running&&<progress max={progress.total||1} value={progress.done}/>}
    {running&&<p className="muted small">{Math.round(progress.done/1048576)}
      /{Math.round(progress.total/1048576)} MiB uploaded</p>}
    {error&&<p className="alert danger" role="alert">{error}</p>}
    {notice&&<p className="alert success" role="status">{notice}</p>}
    <div className="button-row">
      <button className="btn secondary" type="button"
        disabled={running||!files.length||Boolean(active&&active.status!=="uploading")}
        onClick={()=>void upload()}><CloudUpload size={15}/>
        {active?.status==="uploading"?"Resume and finish append":"Upload and append"}
      </button>
      {running&&<button className="btn secondary" type="button"
        onClick={()=>controller.current?.abort()}>Pause upload</button>}
      {active?.status==="failed"&&<button className="btn secondary"
        type="button" disabled={running} onClick={()=>void retry()}>
        <RefreshCw size={15}/> Retry indexing</button>}
      {(active?.status==="uploading"||active?.status==="failed")&&
        <button className="btn danger-outline" type="button" disabled={running}
          onClick={()=>void discard()}><Trash2 size={15}/> Discard batch</button>}
    </div>
  </div>;
}
