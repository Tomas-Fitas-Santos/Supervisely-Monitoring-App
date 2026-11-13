/** Append one ZIP or selected folder into an already-finalized dataset.
 * The server owns saved offsets, isolated staging and atomic inventory commits.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, CloudUpload, RefreshCw, Trash2 } from "lucide-react";
import { uploadApi as api, uploadFile, uploadSources, uploadDirectorySources, validateOffset } from "./upload_helpers";
import { confirmAction } from "./dialogs";
import { useDatasetSelection } from "./dataset_selection";
import { DatasetSourcePicker } from "./dataset_source_picker";

type UploadKind="zip"|"folder";
type Batch={id:string;dataset_id:string;status:string;upload_kind:UploadKind;
  received_bytes:number;started_revision:number;error_code:string|null};
type Receipt={path:string;received_bytes:number;expected_bytes:number};
type BatchDetails=Batch&{files:Receipt[];file?:Receipt|null;next_after?:string|null};

export function AppendVideos({datasetId,csrf,onCommitted}:{
  datasetId:string;csrf:string;onCommitted:()=>Promise<unknown>|void;
}){
  const [kind,setKind]=useState<UploadKind>("folder");
  const selection=useDatasetSelection(kind);
  const files=selection.files;
  const [batches,setBatches]=useState<Batch[]>([]);
  const [activeId,setActiveId]=useState<string|null>(null);
  const [running,setRunning]=useState(false);
  const [progress,setProgress]=useState({done:0,total:0});
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [stage,setStage]=useState("");
  const [expanded,setExpanded]=useState(false);
  const [discovering,setDiscovering]=useState(false);
  const controller=useRef<AbortController|null>(null);
  const lastCommitted=useRef<string>("");
  const fileRef=useRef<HTMLInputElement|null>(null);
  const refresh=useCallback(async()=>{
    const result=await api<{items:Batch[]}>(`/datasets/${datasetId}/append-batches`);
    setBatches(result.items);
    setActiveId(old=>old&&result.items.some(b=>b.id===old)?old:
      result.items.find(b=>["uploading","indexing","failed"].includes(b.status))?.id||null);
    const latestCommitted=result.items.find(b=>b.status==="committed")?.id||"";
    if(latestCommitted&&latestCommitted!==lastCommitted.current){
      lastCommitted.current=latestCommitted;
      void onCommitted();
    }
  },[datasetId,onCommitted]);
  useEffect(()=>{
    let live=true;
    const tick=()=>{if(live)void refresh().catch(()=>undefined);};
    tick();
    const timer=window.setInterval(()=>{if(!document.hidden)tick();},9000);
    return()=>{live=false;window.clearInterval(timer);};
  },[refresh]);
  useEffect(()=>{
    selection.clear();setActiveId(null);setError("");setNotice("");
  },[datasetId]);
  const active=batches.find(b=>b.id===activeId)||null;
  useEffect(()=>{if(active||running)setExpanded(true);},[active?.id,running]);
  const total=selection.prepared?.total||0;

  async function sendBatch(batch:Batch){
    const signal=controller.current!.signal;
    const directory=selection.directory;
    const sources=selection.prepared?.sources||new Map<string,File>();
    let total=selection.prepared?.total||0;
    setDiscovering(Boolean(directory));
    setStage("Checking saved upload progress…");
    const previous=new Map<string,Receipt>();
    let after:string|null=null;
    do {
      const details:BatchDetails=await api<BatchDetails>(
        `/datasets/${datasetId}/append-batches/${batch.id}?limit=1000`+
        (after?"&after="+encodeURIComponent(after):""),"GET",undefined,undefined,signal);
      for(const row of details.files){
        const source=sources.get(row.path);
        if(!directory&&(!source||source.size!==row.expected_bytes))throw new Error("Reselected files do not match the saved batch paths and sizes.");
        validateOffset(row.received_bytes,row.expected_bytes);previous.set(row.path,row);
      }
      setStage(`Checked ${previous.size.toLocaleString()} saved file receipts…`);
      if(details.next_after&&details.next_after===after)throw new Error("Upload progress paging did not advance.");
      after=details.next_after||null;
    }while(after);
    let done=0;if(!directory)for(const row of previous.values())done+=row.received_bytes;
    setProgress({done,total});let count=0;
    const send=async(path: string,file: File)=>{
      const saved=previous.get(path);
      if(saved&&saved.expected_bytes!==file.size)throw new Error("Reselected files do not match the saved batch paths and sizes.");
      previous.delete(path);
      if(directory&&saved)done+=saved.received_bytes;
      setStage(`Uploading files: ${count.toLocaleString()} completed${directory?" · Reading folder progressively":` / ${files.length.toLocaleString()}`}`);
      await uploadFile({file,offset:saved?.received_bytes||0,csrf,signal,
        url:`/datasets/${datasetId}/append-batches/${batch.id}/chunk?relative_path=`+encodeURIComponent(path),
        readOffset:async()=>{
          const state=await api<BatchDetails>(`/datasets/${datasetId}/append-batches/${batch.id}?relative_path=`+encodeURIComponent(path),
            "GET",undefined,undefined,signal);
          return state.file?.received_bytes||0;
        },onProgress:bytes=>{done+=bytes;setProgress({done,total});}});
      count++;
    };
    if(directory)await uploadDirectorySources(directory,send,signal,(_count,bytes)=>{
      total=bytes;setProgress({done,total});
    });
    else await uploadSources(sources,send);
    if(previous.size)throw new Error("Reselected folder is missing files from the saved batch.");
    setDiscovering(false);
    setStage("Starting local indexing…");
    await api(`/datasets/${datasetId}/append-batches/${batch.id}/finish`,
      "POST",undefined,csrf,signal);
    setNotice("Batch uploaded. Local indexing will commit complete videos into the existing dataset.");
    selection.clear();if(fileRef.current)fileRef.current.value="";
    void refresh().catch(()=>undefined);
  }

  async function upload(){
    if(running||!selection.ready)return;
    setRunning(true);setError("");setNotice("");
    controller.current=new AbortController();setStage("Preparing append upload…");
    try{
      const reuse=active?.status==="uploading"?active:null;
      if(reuse&&reuse.upload_kind!==kind)
        throw new Error("Choose the same upload format as the existing batch.");
      const target=reuse||await api<Batch>(
        `/datasets/${datasetId}/append-batches`,"POST",{upload_kind:kind},csrf,controller.current.signal);
      setActiveId(target.id);
      await sendBatch(target);
    }catch(e){setError((e as Error).message);}
    finally{controller.current=null;setRunning(false);void refresh().catch(()=>undefined);}
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

  return <details className="dataset-append" open={expanded}
    onToggle={e=>setExpanded(e.currentTarget.open)}>
    <summary><span><CloudUpload size={18}/><strong>Add videos to this dataset</strong></span>
      <ChevronDown size={17}/></summary>
    <div className="dataset-append-body">
    <p className="muted small">Add a ZIP or folder of complete videos before assigning jobs.
      Use new video names; existing videos and frames cannot be replaced.</p>
    <div className="dataset-append-toolbar">
    <label>Append upload format
      <select value={kind} disabled={running||Boolean(active&&active.status==="uploading")}
        onChange={e=>{setKind(e.target.value as UploadKind);selection.clear();
          if(fileRef.current)fileRef.current.value="";}}>
        <option value="folder">Folder</option>
        <option value="zip">ZIP archive</option>
      </select>
    </label>
    <button className="btn secondary" type="button" disabled={running}
      onClick={()=>void refresh().catch(e=>setError((e as Error).message))}><RefreshCw size={14}/> Refresh</button>
    </div>
    <DatasetSourcePicker kind={kind} selection={selection} inputRef={fileRef}
      disabled={running||Boolean(active&&active.status!=="uploading")} append
      folderLabel="Choose a folder to append" zipLabel="Choose a ZIP to append"/>
    <p className="dataset-selection muted small">{selection.directory?`Folder selected: ${selection.directory.name}`:
      files.length?`${files.length.toLocaleString()} file(s) selected · ${Math.round(total/1048576)} MiB`:"No files selected"}</p>
    {active&&<p className="muted small">Latest batch: {active.status}
      {" "}({active.upload_kind}, revision {active.started_revision})
      {active.error_code?" · "+active.error_code:""}</p>}
    {running&&<p className="muted small" role="status">{stage}</p>}
    {running&&<progress max={progress.total||1} value={discovering?undefined:progress.done}/>}
    {running&&<p className="muted small">{Math.round(progress.done/1048576)}
      /{Math.round(progress.total/1048576)} MiB uploaded</p>}
    {error&&<p className="alert danger" role="alert">{error}</p>}
    {notice&&<p className="alert success" role="status">{notice}</p>}
    <div className="button-row">
      <button className="btn secondary" type="button"
        disabled={running||!selection.ready||Boolean(active&&active.status!=="uploading")}
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
    </div>
  </details>;
}
