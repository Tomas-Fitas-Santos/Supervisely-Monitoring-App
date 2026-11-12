import { useCallback, useEffect, useState } from "react";
import type React from "react";
import { CloudUpload, FolderOpen, RefreshCw, RotateCcw, HardDrive, CheckCircle2 } from "lucide-react";
import { api, type Auth } from "./api";
import { confirmAction } from "./dialogs";

type Dataset = {
  id:string;name:string;upload_kind:string;source_folder?:string|null;
  status:string;source_bytes:number;image_count:number;frame_count:number;
  video_count:number;native_project_id:number|null;error_code:string|null;
};
type Mirror = {
  state:string;uploaded_frames:number;total_frames:number;
  ready_for_automatic_jobs:boolean;error_code:string|null;attempts:number;
  pending_seconds:number|null;started_seconds_ago:number|null;
};
type Index = {state:string;error_code:string|null;attempts:number;
  pending_seconds:number|null;started_seconds_ago:number|null};
type Sources = {mounted:boolean;folders:string[]};
const readableBytes=(n:number)=>n>=1e9?(n/1e9).toFixed(2)+" GB":n>=1e6?
  (n/1e6).toFixed(1)+" MB":(n/1000).toFixed(1)+" KB";

export function MountedDatasetLibrary({auth}:{auth:Auth}){
  const [datasets,setDatasets]=useState<Dataset[]>([]);
  const [selected,setSelected]=useState("");
  const [sources,setSources]=useState<Sources>({mounted:false,folders:[]});
  const [folder,setFolder]=useState("");
  const [name,setName]=useState("");
  const [policy,setPolicy]=useState<"all_frames"|"sample_1fps">("all_frames");
  const [mirror,setMirror]=useState<Mirror|null>(null);
  const [index,setIndex]=useState<Index|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const refresh=useCallback(async()=>{
    try{
      const result=await api<{items:Dataset[]}>("/datasets");
      setDatasets(result.items);
      setSelected(old=>result.items.some(d=>d.id===old)?old:result.items[0]?.id||"");
    }catch(e){setError((e as Error).message);}
  },[]);
  const refreshSources=useCallback(async()=>{
    try{
      const result=await api<Sources>("/datasets/mounted-folders");
      setSources(result);
      setFolder(old=>result.folders.includes(old)?old:result.folders[0]||"");
    }catch(e){setError((e as Error).message);}
  },[]);
  useEffect(()=>{void refresh();void refreshSources();},[refresh,refreshSources]);
  const dataset=datasets.find(d=>d.id===selected);
  useEffect(()=>{
    if(!selected){setMirror(null);setIndex(null);return;}
    let active=true;
    const update=async()=>{
      try{
        const results=await Promise.all([
          api<Mirror>("/datasets/"+selected+"/mirror-status"),
          api<Index>("/datasets/"+selected+"/mounted/status"),
        ]);
        if(active){setMirror(results[0]);setIndex(results[1]);}
      }catch{if(active){setMirror(null);setIndex(null);}}
    };
    void update();
    const id=window.setInterval(()=>{if(!document.hidden)void update();},5000);
    return()=>{active=false;window.clearInterval(id);};
  },[selected]);
  useEffect(()=>{
    const id=window.setInterval(()=>{if(!document.hidden)void refresh();},5000);
    return()=>window.clearInterval(id);
  },[refresh]);
  async function register(e:React.FormEvent){
    e.preventDefault();
    if(!folder)return;
    setBusy(true);setError("");setNotice("");
    try{
      const created=await api<Dataset>("/datasets/mounted","POST",{
        folder,name:name.trim()||folder,extraction_policy:policy,
      },auth.csrf_token);
      setSelected(created.id);
      setName("");
      setNotice("Folder linked. Nightjar's local indexer will prepare frames. The Supervisely upload then needs the separate native worker.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function retryIndex(){
    if(!dataset)return;
    setBusy(true);setError("");setNotice("");
    try{
      await api("/datasets/"+dataset.id+"/mounted/retry","POST",undefined,auth.csrf_token);
      setNotice("Source indexing queued again.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function retryMirror(){
    if(!dataset)return;
    setBusy(true);setError("");setNotice("");
    try{
      await api("/datasets/"+dataset.id+"/mirror/retry","POST",undefined,auth.csrf_token);
      setNotice("Supervisely upload queued for the worker.");
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function deleteFailed(){
    if(!dataset||dataset.status!=="failed")return;
    if(!await confirmAction("Delete the failed dataset registration? The original mounted folder will not be deleted."))return;
    setBusy(true);setError("");setNotice("");
    try{
      await api("/datasets/"+dataset.id,"DELETE",undefined,auth.csrf_token);
      setSelected("");
      setNotice("Failed registration deleted. The source files were not changed.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  return <div className="page-body">
    <div className="page-heading"><div>
      <span className="eyebrow">DATA · LINK & MIRROR</span>
      <h2>Dataset Management</h2>
      <p className="muted">Select an existing folder on the application host. Frames are sent straight to Supervisely without a browser upload.</p>
    </div><button className="btn secondary" onClick={()=>{void refresh();void refreshSources();}} disabled={busy}>
      <RefreshCw size={15}/> Refresh</button></div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}
    <div className="stat-grid compact">
      <div className="stat-card"><HardDrive size={20}/><span className="muted small">Datasets</span>
        <strong>{datasets.length}</strong></div>
      <div className="stat-card"><FolderOpen size={20}/><span className="muted small">Available frames</span>
        <strong>{dataset?.frame_count||0}</strong></div>
      <div className="stat-card"><FolderOpen size={20}/><span className="muted small">Videos</span>
        <strong>{dataset?.video_count||0}</strong></div>
    </div>
    <div className="content-grid datasets-layout">
      <section className="panel">
        <span className="eyebrow">SOURCE DIRECTORY</span><h3>Link folder & upload directly</h3>
        <p className="muted small">Source files remain in the selected read-only folder.
          Nightjar indexes automatically; raw videos are decoded into local working frames.
          The separate Supervisely worker performs the remote SDK upload after indexing.</p>
        {sources.folders.length===0&&<div className="alert warning" role="status">
          <strong>No dataset folders visible to Nightjar.</strong>
          <p>If no host folders are linked, use the <strong>Upload folder or ZIP ·
            choose on this computer</strong> tab to open your computer's folder
            picker directly in Nightjar, without commands. Zero-copy import is
            available only for folders already exposed by the Docker host's
            read-only source mount.</p>
          <p>{sources.mounted
            ?"The mounted source directory currently contains no selectable dataset folders."
            :"The dataset source volume is not accessible in the running Nightjar container."}
            {" "}A remote browser cannot directly grant the server access to a folder on this PC.</p>
          <button className="btn secondary" type="button" disabled={busy}
            onClick={()=>void refreshSources()}><RefreshCw size={15}/> Check folders again</button>
        </div>}
        <form className="form-stack" onSubmit={e=>void register(e)}>
          <label>Dataset folder<select value={folder} disabled={busy||!sources.mounted||sources.folders.length===0}
            onChange={e=>{setFolder(e.target.value);if(!name)setName(e.target.value);}}>
            {!sources.folders.length&&<option value="">No folders available</option>}
            {sources.folders.map(x=><option key={x} value={x}>{x}</option>)}
          </select></label>
          <label>Dataset name<input maxLength={200} value={name} placeholder="BirdGaze 2026"
            onChange={e=>setName(e.target.value)}/></label>
          <label>Frame extraction<select value={policy} disabled={busy}
            onChange={e=>setPolicy(e.target.value as typeof policy)}>
            <option value="all_frames">Every source frame (30-frame jobs)</option>
            <option value="sample_1fps">One frame per second</option>
          </select></label>
          <p className="muted small">Folder structure: <code>dataset/species/video.mp4</code> or
            <code> dataset/species/video-name/frame_000001.jpg</code>.
            Already-extracted frames are read directly, without being copied.
            Raw video files must be decoded to image frames for labeling.</p>
          <button className="btn primary" disabled={busy||!sources.mounted||!folder}>
            <CloudUpload size={16}/>{busy?"Registering…":"Link folder & start Supervisely upload"}
          </button>
        </form>
        <div className="settings-note" style={{marginTop:16}}>
          <p>Leave the source folder unchanged and mounted during upload and event reviews.
            The separate Supervisely upload worker must be running with its Team/Workspace connection verified.
            No labeling jobs are created by linking the dataset.</p>
        </div>
      </section>
      <section className="panel">
        <span className="eyebrow">REGISTERED DATASETS</span><h3>Collections & status</h3>
        <div className="dataset-cards">
          {datasets.map(d=><button key={d.id} onClick={()=>setSelected(d.id)}
            className={"dataset-card "+(selected===d.id?"selected":"")}>
            <div><strong>{d.name}</strong><span>{d.upload_kind.toUpperCase()} · {readableBytes(d.source_bytes)}</span></div>
            <span className={"dataset-status "+d.status}>{d.status}</span>
          </button>)}
          {!datasets.length&&<p className="muted small">No dataset registered yet.</p>}
        </div>
        {dataset&&<div className="dataset-detail">
          <h4>{dataset.name}</h4>
          <p className="muted small">{dataset.source_folder?"Source folder: "+dataset.source_folder+" · ":""}
            {dataset.video_count} videos · {dataset.frame_count} video frames · {dataset.image_count} images</p>
          {dataset.status==="indexing"&&<div className="settings-note">
            <strong>{index?.state==="pending"?"Waiting for local dataset indexer…":
              index?.state==="running"?"Indexing the mounted source folder…":
              "Checking the local indexing queue…"}</strong>
            <p className="muted small">Local index state: {index?.state||"checking"}.
              {index?.state==="running"?
                " Files are being read and frames prepared. Counts appear when indexing finishes.":
                " No remote Supervisely upload starts until indexing finishes."}
              {" "}The browser can be closed.</p>
            {index?.state==="pending"&&index.pending_seconds!=null&&index.pending_seconds>30&&
              <p className="muted small">The local indexer may be stopped.
                On the Docker host run <code>docker compose --env-file .env.live -f compose.live.yaml up -d indexer</code>
                and check <code>docker compose --env-file .env.live -f compose.live.yaml logs --tail=80 indexer</code>.</p>}
            {index?.state==="running"&&index.started_seconds_ago!=null&&
              <p className="muted small">Indexing started {Math.floor(index.started_seconds_ago/60)} minutes ago.</p>}
          </div>}
          {dataset.status==="failed"&&<div className="alert danger">
            Source indexing failed. Check worker logs before retrying. Error: {index?.error_code||dataset.error_code||"unknown"}
          </div>}
          {dataset.status==="failed"&&dataset.upload_kind==="mounted"&&
            <div className="dataset-recovery-actions">
              <button className="btn secondary" disabled={busy} onClick={()=>void retryIndex()}>
                <RotateCcw size={15}/> Retry indexing</button>
              <button className="btn danger-outline" disabled={busy} onClick={()=>void deleteFailed()}>
                Delete failed registration</button>
            </div>}
          {dataset.status==="ready"&&<div className="dataset-mirror-status">
            <strong>{mirror?.ready_for_automatic_jobs?
              <><CheckCircle2 size={15}/> All frames mirrored to Supervisely; check source identity before planning jobs</>:
              "Supervisely upload in progress / not ready"}</strong>
            <p className="muted small">State: {mirror?.state||"checking"} ·
              {mirror?.uploaded_frames||0} / {mirror?.total_frames||dataset.frame_count} frames uploaded
              {mirror?.attempts?" · attempt "+mirror.attempts:""}</p>
            <progress value={mirror?.uploaded_frames||0} max={mirror?.total_frames||dataset.frame_count||1}/>
            {mirror?.error_code&&<div className="alert danger">{mirror.error_code}</div>}
            {mirror?.state==="pending"&&mirror.pending_seconds!=null&&mirror.pending_seconds>90&&
              <p className="muted small">Local indexing is complete, but Supervisely upload is queued.
                Verify the Team/Workspace connection in Settings, then start the separate upload worker:
                <code> docker compose --env-file .env.live -f compose.live.yaml --profile native up -d mirror-worker</code>.
                Inspect <code>docker compose --env-file .env.live -f compose.live.yaml --profile native logs --tail=80 mirror-worker</code>.</p>}
            {mirror?.state==="attention"&&<button className="btn secondary" disabled={busy}
              onClick={()=>void retryMirror()}><RotateCcw size={15}/> Retry upload</button>}
          </div>}
        </div>}
      </section>
    </div>
  </div>;
}
