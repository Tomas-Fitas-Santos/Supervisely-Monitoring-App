import { RefreshButton } from "./refresh_button";
import { confirmAction } from "./dialogs";
import { AppendVideos } from "./dataset_append";
import { useEffect, useMemo, useRef, useState } from "react";
import type React from "react";
import {
  Archive, CheckCircle2, CloudUpload,
  FolderOpen, HardDrive, Layers3, RotateCcw, Trash2, PauseCircle
} from "lucide-react";
import { api, type Auth } from "./api";
import { singleFlight } from "./single_flight";
import { uploadApi } from "./upload_helpers";
import { useDatasetSelection } from "./dataset_selection";
import { DatasetSourcePicker } from "./dataset_source_picker";
import { beginDatasetTransfer, clearDatasetTransfer, getDatasetTransfer,
  stopDatasetTransfer, watchDatasetTransfer, type DatasetTransferState } from "./dataset_transfer";

type Dataset={id:string;name:string;upload_kind:"zip"|"folder"|"mounted";
  status:string;source_bytes:number;image_count:number;frame_count:number;
  video_count:number;created_at:number;last_activity_at:number;error_code:string|null;
  inventory_revision:number;mirrored_revision:number;configuration_revision:number;
  index_progress?:{phase:string;files:number;checked_frames:number;updated_at:number;reused_checks?:number}|null;
  native_project_id:number|null};
type MirrorStatus={state:string;uploaded_frames:number;total_frames:number;
  ready_for_automatic_jobs:boolean;error_code:string|null;
  pending_seconds:number|null;started_seconds_ago:number|null;attempts:number};
type Morph={name:string;kind:string;count:number};
type Summary={dataset:Dataset;morphologies:Morph[];total_assets:number};
type SourceVideo={id:string;video_group:string;density:string;extraction_policy:string;
  frame_count:number;extracted_bytes:number;identity_status:string;identity_notes:string[]};
const formatBytes=(n:number)=>n>=1e9?(n/1e9).toFixed(2)+" GB":n>=1e6?
  (n/1e6).toFixed(1)+" MB":(n/1000).toFixed(1)+" KB";

export function DatasetScreen({auth}:{auth:Auth}) {
  const admin=auth.staff.roles.includes("organiser");
  const [datasets,setDatasets]=useState<Dataset[]>([]);
  const [selected,setSelected]=useState("");
  const [summary,setSummary]=useState<Summary|null>(null);
  const [mirror,setMirror]=useState<MirrorStatus|null>(null);
  const [mode,setMode]=useState<"zip"|"folder">("folder");
  const [name,setName]=useState("");
  const [extractionPolicy,setExtractionPolicy]=useState<"all_frames"|"sample_1fps">("all_frames");
  const [videoResult,setVideoResult]=useState<{datasetId:string;items:SourceVideo[]}|null>(null);
  const videosLoaded=videoResult?.datasetId===selected;
  const videos=videosLoaded?videoResult.items:[];
  const [projectedFrames,setProjectedFrames]=useState(175500);
  const selection=useDatasetSelection(mode);
  const files=selection.files;
  const fileInputRef=useRef<HTMLInputElement|null>(null);
  function clearFileSelection(){
    selection.clear();
    // A retained native <input type=file> shows stale file counts after the
    // transfer clears React state and may not fire a new change event when
    // the organiser reselects the same folder to resume.
    if(fileInputRef.current)fileInputRef.current.value="";
  }
  const [busy,setBusy]=useState(false);
  const [preparing,setPreparing]=useState("");
  const preparation=useRef<AbortController|null>(null);
  const selectedBytes=selection.prepared?.total||0;
  const [transfer,setTransfer]=useState<DatasetTransferState|null>(getDatasetTransfer);
  useEffect(()=>watchDatasetTransfer(setTransfer),[]);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const refresh=useMemo(()=>singleFlight(async()=>{
    try{
      const result=await uploadApi<{items:Dataset[]}>("/datasets");
      setDatasets(result.items);
      setSelected(old=>result.items.some(d=>d.id===old)?old:(result.items[0]?.id||""));
    }catch(e){setError((e as Error).message);return false;}
  }),[]);
  useEffect(()=>{void refresh();},[refresh]);
  const indexing=datasets.some(d=>d.status==="indexing");
  useEffect(()=>{
    if(!indexing)return;
    const timer=window.setInterval(()=>{if(!document.hidden)void refresh();},5000);
    return()=>window.clearInterval(timer);
  },[indexing,refresh]);
  useEffect(()=>{if(transfer && !transfer.running)void refresh();},
    [transfer?.datasetId,transfer?.phase,transfer?.running,refresh]);
  useEffect(()=>{
    if(!selected){setSummary(null);return;}
    const item=datasets.find(x=>x.id===selected);
    if(item?.status!=="ready"){setSummary(null);return;}
    void api<Summary>("/datasets/"+selected+"/summary").then(setSummary)
      .catch(e=>setError((e as Error).message));
  },[selected,datasets]);
  useEffect(()=>{
    if(!selected){setMirror(null);return;}
    let live=true;
    async function updateMirror(){
      try{
        const result=await api<MirrorStatus>("/datasets/"+selected+"/mirror-status");
        if(live)setMirror(result);
      }catch(e){if(live)setMirror(null);}
    }
    void updateMirror();
    const timer=window.setInterval(()=>{if(!document.hidden)void updateMirror();},12000);
    return()=>{live=false;window.clearInterval(timer);};
  },[selected]);
  async function retryMirror(){
    if(!dataset||busy)return;
    setBusy(true);setError("");setNotice("");
    try{
      await api("/datasets/"+dataset.id+"/mirror/retry","POST",undefined,auth.csrf_token);
      setNotice("Full-dataset Supervisely replication queued for the worker.");
      setMirror(await api<MirrorStatus>("/datasets/"+dataset.id+"/mirror-status"));
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function retryIndex(){
    if(!dataset||busy)return;
    setBusy(true);setError("");
    try{
      await uploadApi("/datasets/"+dataset.id+(dataset.upload_kind==="mounted"?
        "/mounted/retry":"/finalize?background=true"),"POST",undefined,auth.csrf_token);
      setNotice("Local validation queued. Saved source files will be reused.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  const dataset=datasets.find(d=>d.id===selected);
  useEffect(()=>{
    let active=true;
    if(selected&&dataset?.status==="ready"){
      void api<{items:SourceVideo[]}>("/datasets/"+selected+"/videos")
        .then(response=>{if(active)setVideoResult({datasetId:selected,items:response.items});})
        .catch(e=>{if(active)setError((e as Error).message);});
    }
    return()=>{active=false;};
  },[selected,dataset?.status]);
  const groups=useMemo(()=>summary?.morphologies.reduce<Record<string,{frames:number}>>(
    (acc,m)=>{
      const row=acc[m.name]||(acc[m.name]={frames:0});
      if(m.kind==="video_frame")row.frames+=m.count;
      return acc;
    },{})||{},[summary]);
  async function ingest(e:React.FormEvent) {
    e.preventDefault();
    if(!selection.ready){setError("Choose a ZIP archive or dataset folder first.");return;}
    if(transfer?.running){setError("Another dataset transfer is already running.");return;}
    setBusy(true);setError("");setNotice("");
    let created:Dataset|null=null;
    try {
      preparation.current=new AbortController();
      setPreparing("Creating upload record…");
      created=await uploadApi<Dataset>("/datasets/uploads","POST",{
        name:name.trim()||selection.directory?.name||(mode==="folder"?
          (files[0].webkitRelativePath.split("/")[0]||files[0].name):
          files[0].name.replace(/\.zip$/i,"")),
        upload_kind:mode,extraction_policy:extractionPolicy
      },auth.csrf_token,preparation.current.signal);
      setSelected(created.id);
      setDatasets(old=>[created!,...old.filter(d=>d.id!==created!.id)]);
      beginDatasetTransfer({
        datasetId:created.id,name:created.name,kind:mode,
        files,csrf:auth.csrf_token,resume:false,
        directory:selection.directory,prepared:selection.prepared,
      });
      setNotice("Transfer started. When local validation completes, the native worker will replicate every frame to Supervisely before event launch.");
      clearFileSelection();setName("");
      void refresh();
    }catch(e) {
      setError((e as Error).message+(created?" The unfinished record can be removed below.":""));
      void refresh();
    }finally{setBusy(false);setPreparing("");preparation.current=null;}
  }
  async function resumeSelected() {
    if(!dataset||dataset.upload_kind==="mounted"||!selection.ready)return;
    if(dataset.upload_kind!==mode){
      setError("Select the same upload format as the interrupted dataset.");return;
    }
    try {
      setError("");setNotice("");
      beginDatasetTransfer({
        datasetId:dataset.id,name:dataset.name,kind:dataset.upload_kind,
        files,csrf:auth.csrf_token,resume:true,
        directory:selection.directory,prepared:selection.prepared,
      });
      clearFileSelection();
      setNotice("Resuming original files from server-verified upload offsets.");
    }catch(e){setError((e as Error).message);}
  }
  async function stopSelected() {
    if(!dataset)return;
    setBusy(true);setError("");
    try {
      await stopDatasetTransfer(dataset.id,auth.csrf_token);
      setNotice("Upload stopped. Partial files can be resumed or deleted.");
      void refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function deleteSelected() {
    if(!dataset||dataset.status==="ready")return;
    if(!await confirmAction("Delete the incomplete dataset '"+dataset.name+
      "' and all its partial local files? This cannot be undone."))return;
    setBusy(true);setError("");setNotice("");
    try {
      if(transfer?.running && transfer.datasetId===dataset.id){
        await stopDatasetTransfer(dataset.id,auth.csrf_token);
      }
      // Older browser sessions may have left the database row uploading.
      if(dataset.status==="uploading"){
        await api("/datasets/"+dataset.id+"/interrupt","POST",undefined,auth.csrf_token);
      }
      await api("/datasets/"+dataset.id,"DELETE",undefined,auth.csrf_token);
      clearDatasetTransfer(dataset.id);
      setSelected("");
      setNotice("Incomplete dataset and its partial local files deleted.");
      void refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  return <div className="page-body dataset-upload-page">
    <div className="page-heading"><div><span className="eyebrow">DATA · IMPORT & INVENTORY</span>
      <h2>Dataset Management</h2>
      <p className="muted">Upload bird videos organised by species. Nightjar keeps each video's frames together for labeling jobs.</p>
    </div><RefreshButton refresh={refresh} disabled={busy}>Refresh</RefreshButton></div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}
    <div className="stat-grid compact">
      <div className="stat-card"><HardDrive size={20}/><span className="muted small">Datasets</span>
        <strong>{datasets.length}</strong></div>
      <div className="stat-card"><Layers3 size={20}/><span className="muted small">Video frames</span>
        <strong>{dataset?.frame_count??0}</strong></div>
      <div className="stat-card"><FolderOpen size={20}/><span className="muted small">Videos</span>
        <strong>{dataset?.video_count??0}</strong></div>
    </div>
    <div className="content-grid datasets-layout dataset-import-layout">
      {admin&&<section className="panel dataset-import-panel">
        <span className="eyebrow">NEW DATASET</span><h3>Upload a dataset</h3>
        <p className="muted small">Choose a folder or ZIP from your computer. After validation,
          Nightjar uploads the images to Supervisely in the background.</p>
        <form className="form-stack" onSubmit={e=>void ingest(e)}>
          <label>Dataset name<input maxLength={200} value={name} placeholder="BirdGaze 2026"
            onChange={e=>setName(e.target.value)}/></label>
          <div className="dataset-form-options">
          <label>Upload format<select value={mode} disabled={busy}
            onChange={e=>{setMode(e.target.value as "zip"|"folder");clearFileSelection();}}>
            <option value="zip">ZIP archive</option>
            <option value="folder">Folder</option>
          </select></label>
          <label>Frame extraction<select value={extractionPolicy} disabled={busy}
            onChange={e=>setExtractionPolicy(e.target.value as typeof extractionPolicy)}>
            <option value="all_frames">Every source frame</option>
            <option value="sample_1fps">Sample one frame per second</option>
          </select></label>
          </div>
          <DatasetSourcePicker kind={mode} selection={selection} inputRef={fileInputRef}
            disabled={busy||Boolean(transfer?.running)}
            folderLabel="Choose dataset folder on this computer" zipLabel="Select .zip file"/>
          <div className="dataset-upload-actions">
          <p className="dataset-selection muted small">{selection.directory?
            `Folder selected: ${selection.directory.name}`:
            files.length?`${files.length.toLocaleString()} file(s) selected · ${formatBytes(selectedBytes)}`:"No files selected"}</p>
          <button className="btn primary" disabled={busy||!selection.ready||Boolean(transfer?.running)}>
            <CloudUpload size={16}/> {busy?"Preparing…":transfer?.running?"Transfer active…":"Upload & validate"}
          </button>
          </div>
          {preparing&&<p role="status" className="muted small">{preparing} <button type="button" className="btn secondary" onClick={()=>preparation.current?.abort()}>Cancel preparation</button></p>}
          {transfer?.running&&<div className="dataset-progress">
            <strong className="small">{transfer.phase==="preparing"?"Checking selected file metadata":
              transfer.phase==="reconciling"?"Checking saved upload progress":
              transfer.phase==="validating"?"Validating local media":
              "Uploading "+transfer.name+" in this browser tab"}</strong>
            <progress max={transfer.total||1} value={transfer.discovering?undefined:transfer.done}/>
            <span>{formatBytes(transfer.done)} / {formatBytes(transfer.total)}
              · {transfer.files}/{transfer.count} files {transfer.discovering?"discovered so far · Reading folder while uploading":"· Safe to navigate within Nightjar"}</span>
          </div>}
          <p className="dataset-transfer-help muted small">Keep this tab open during transfer.
            Validation and Supervisely upload continue on the server.</p>
        </form>
        <details className="dataset-help">
          <summary>Folder structure & import options</summary>
          <div className="dataset-help-content">
            <p>Use species folders containing videos or extracted frame folders:</p>
            <pre>dataset / species / video.mp4{"\n"}dataset / species / video-name / frame_000001.jpg</pre>
            <p>For a separate standalone-image dataset, upload <code>dataset/images/category/photo.jpg</code>.
              Mounted image datasets use <code>dataset/category/photo.jpg</code>.
              In Job Distribution, choose the standalone-image queue and link it to your video plan to process videos first.</p>
            <p>No frames.json or other metadata file is required in either upload format.
              Frame numbers or natural filename order determine the image order. Sampled frames are supported.</p>
            <p><strong>Every source frame</strong> extracts all frames from raw videos.
              Choose <strong>Sample one frame per second</strong> for a smaller collection.</p>
            <p>Already on the server? Use <strong>Link host folder (optional)</strong> to avoid a browser transfer.</p>
            <p>Importing creates no labeling jobs. Automatic jobs later reuse the images in Supervisely.</p>
          </div>
        </details>
      </section>}
      <section className="panel dataset-library-panel">
        <div className="dataset-panel-heading"><div>
          <span className="eyebrow">YOUR DATA</span><h3>Imported collections</h3>
        </div><RefreshButton refresh={refresh} disabled={busy}>Refresh</RefreshButton></div>
        <div className="dataset-cards">{datasets.map(d=><button key={d.id}
          onClick={()=>setSelected(d.id)} className={"dataset-card "+(selected===d.id?"selected":"")}>
          <div><strong>{d.name}</strong><span>{d.upload_kind.toUpperCase()} · {formatBytes(d.source_bytes)}</span></div>
          <span className={"dataset-status "+d.status}>{d.status==="ready"?<CheckCircle2 size={14}/>:<Archive size={14}/>}
            {d.status}</span></button>)}
        {!datasets.length&&<p className="muted small">No dataset uploaded yet.</p>}</div>
        {dataset&&<div className="dataset-detail"><h4>{dataset.name}</h4>
          <p className="muted small">{dataset.video_count} videos ·
            {dataset.frame_count} frames available for annotation</p>
          {dataset.status==="indexing"&&<div className="dataset-index-status" role="status">
            <strong>Validating local data in the background</strong>
            <p>{dataset.index_progress
              ?`${dataset.index_progress.phase}: ${dataset.index_progress.files.toLocaleString()} files indexed, ${dataset.index_progress.checked_frames.toLocaleString()} frames checked.`
              :"Waiting for the local indexer."}</p>
            {!!dataset.index_progress?.reused_checks&&<p className="muted small">
              {dataset.index_progress.reused_checks.toLocaleString()} saved file checks reused.</p>}
            <p className="muted small">You can close the browser. Supervisely upload starts after validation completes.
              If progress does not advance, check the indexer service logs.</p>
          </div>}
          {dataset.status==="failed"&&<p role="alert" className="alert danger">
            Local validation stopped. Source files are retained for retry.</p>}
          {admin&&dataset.status==="failed"&&["upload_index_failed","mounted_index_failed"].includes(dataset.error_code||"")&&
            <button className="btn secondary" disabled={busy} onClick={()=>void retryIndex()}>
              <RotateCcw size={15}/> Retry local validation</button>}
          {dataset.status==="ready"&&<div className="dataset-mirror-status">
            <strong>{mirror?.ready_for_automatic_jobs?<CheckCircle2 size={17}/>:<CloudUpload size={17}/>}
              {!mirror?"Checking Supervisely upload":mirror.ready_for_automatic_jobs?
                "Ready in Supervisely":"Supervisely upload in progress"}</strong>
            <p className="muted small">{(mirror?.uploaded_frames??0).toLocaleString()} /
              {(mirror?.total_frames??dataset.frame_count+dataset.image_count).toLocaleString()} images uploaded</p>
            {mirror?.state==="pending"&&mirror.pending_seconds!=null&&mirror.pending_seconds>90&&
              <p role="status" className="alert warning">
                Replication has been queued for {Math.floor(mirror.pending_seconds/60)} minutes
                without starting. The dedicated Supervisely upload worker may be stopped.
                Check the mirror-worker container and its logs. This does not require
                reuploading the local dataset.
              </p>}
            {mirror?.state==="running"&&mirror.uploaded_frames===0&&
              <p className="muted small">The worker is preparing the Supervisely project or
                sending its first batch. Progress updates when a batch is saved.</p>}
            {mirror?.state==="running"&&mirror.started_seconds_ago!=null&&
              mirror.started_seconds_ago>1800&&mirror.uploaded_frames===0&&
              <p role="status" className="alert warning">
                Replication has been running without recorded frame progress for more than
                30 minutes. Inspect mirror-worker logs before restarting anything.
              </p>}
            <progress value={mirror?.uploaded_frames??0}
              max={mirror?.total_frames||dataset.frame_count||1}/>
            {mirror?.error_code&&<p role="alert" className="alert danger">{mirror.error_code}</p>}
            {admin&&mirror&&["attention","not_started"].includes(mirror.state)&&
              <button className="btn secondary" type="button" disabled={busy}
                onClick={()=>void retryMirror()}><RotateCcw size={15}/> Retry Supervisely upload</button>}
            {mirror?.ready_for_automatic_jobs&&<p className="muted small">
              Images are available for jobs. Check imported frames in
              <strong> Job Distribution</strong> before creating an automatic plan.</p>}
          </div>}
          {admin&&dataset.status==="ready"&&
            <AppendVideos key={dataset.id} datasetId={dataset.id}
              csrf={auth.csrf_token} onCommitted={refresh}/>}
          {videos.length>0&&<details className="dataset-help">
            <summary>Estimate storage</summary>
            <div className="dataset-help-content">
            <div><label>Estimated storage · target frame count<input type="number" min={1} max={1000000000}
              value={projectedFrames} onChange={e=>setProjectedFrames(Math.min(1000000000,Math.max(1,Number(e.target.value)||1)))}/></label>
              <p>Approximately {formatBytes(videos.reduce((sum,video)=>sum+video.extracted_bytes,0)
                /Math.max(1,videos.reduce((sum,video)=>sum+video.frame_count,0))*projectedFrames)} for {projectedFrames.toLocaleString()} frames at the average size in this collection.
                Allow additional storage for original videos, staging, annotations, exports and backups.</p></div>
            </div>
          </details>}
          {dataset.status==="ready"&&videosLoaded&&!videos.length&&<p className="advisory">
            This collection predates source-frame provenance. For ordered jobs, each frame still needs a source-video grouping and an ordering key; sampled footage is supported.</p>}
          {videos.length>0&&<details className="dataset-help">
            <summary>Video details <span>{videos.length.toLocaleString()} videos</span></summary>
            <div className="dataset-video-list">
          {videos.map(video=><div key={video.id} className="dataset-video-row">
            <p><strong>{video.video_group}</strong> · {video.frame_count} frames · {formatBytes(video.extracted_bytes)} frame files
              <br/>{video.extraction_policy} · Density {video.density} · Frame identity {video.identity_status}
              {video.identity_notes.map(note=><span key={note}><br/>{note}</span>)}</p>
          </div>)}
            </div>
          </details>}
          <details className="dataset-help">
            <summary>Import details</summary>
            <dl className="dataset-import-details">
              <div><dt>Local status</dt><dd>{dataset.status}</dd></div>
              <div><dt>Supervisely project</dt><dd>{dataset.native_project_id||"Not created yet"}</dd></div>
              <div><dt>Inventory revision</dt><dd>{dataset.inventory_revision}</dd></div>
              <div><dt>Mirrored revision</dt><dd>{dataset.mirrored_revision}</dd></div>
            </dl>
          </details>
          {transfer?.datasetId===dataset.id&&transfer.error&&
            <p className="alert danger" role="alert">{transfer.error}</p>}
          {admin&&!["ready","indexing"].includes(dataset.status)&&<div className="dataset-recovery-actions">
            {transfer?.datasetId===dataset.id&&transfer.running?
              <button type="button" className="btn secondary" disabled={busy||transfer.phase==="validating"}
                onClick={()=>void stopSelected()}><PauseCircle size={15}/> Stop upload</button>:
              <>
                {dataset.upload_kind!=="mounted"&&<button type="button" className="btn secondary"
                  disabled={busy||!selection.ready||Boolean(transfer?.running)}
                  onClick={()=>void resumeSelected()}><RotateCcw size={15}/> Resume with selected files</button>}
                {dataset.status==="uploading"&&<button type="button" className="btn secondary"
                  disabled={busy} onClick={()=>void stopSelected()}>
                  <PauseCircle size={15}/> Mark interrupted</button>}
              </>
            }
            <button type="button" className="btn danger-outline"
              disabled={busy||Boolean(transfer?.running&&transfer.datasetId!==dataset.id)}
              onClick={()=>void deleteSelected()}><Trash2 size={15}/> Delete incomplete dataset</button>
          </div>}
        </div>}
      </section>
    </div>
    {summary&&<section className="panel" style={{marginTop:20}}>
      <span className="eyebrow">BIRD SPECIES</span><h3>Video frame distribution</h3>
      <div className="table-scroll"><table className="data-table"><thead><tr>
        <th>Bird species / morphology</th><th>Video frames</th>
      </tr></thead><tbody>{Object.entries(groups).sort(([a],[b])=>a.localeCompare(b))
        .map(([m,count])=><tr key={m}><td><strong>{m}</strong></td>
          <td>{count.frames}</td></tr>)}</tbody></table></div>
      <p className="muted small">Planning balances assignment counts by morphology and
        uses frame count to track dataset coverage. Automatic jobs use blocks of up to 30 frames in uploaded dataset order (original source frame numbers may have gaps), and can carry a video's remaining frames into the next video.</p>
    </section>}
  </div>;
}
