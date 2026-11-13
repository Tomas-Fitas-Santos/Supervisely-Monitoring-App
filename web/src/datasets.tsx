import { RefreshButton } from "./refresh_button";
import { confirmAction } from "./dialogs";
import { AppendVideos } from "./dataset_append";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type React from "react";
import {
  AlertTriangle, Archive, CheckCircle2, CloudUpload, FileArchive,
  FolderOpen, HardDrive, Layers3, RotateCcw, Trash2, PauseCircle
} from "lucide-react";
import { api, type Auth } from "./api";
import { uploadApi } from "./upload_helpers";
import { beginDatasetTransfer, clearDatasetTransfer, getDatasetTransfer,
  validateDatasetSources,
  stopDatasetTransfer, watchDatasetTransfer, type DatasetTransferState } from "./dataset_transfer";

type Dataset={id:string;name:string;upload_kind:"zip"|"folder";
  status:string;source_bytes:number;image_count:number;frame_count:number;
  video_count:number;created_at:number;last_activity_at:number;error_code:string|null;
  inventory_revision:number;mirrored_revision:number;configuration_revision:number;
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
  const [files,setFiles]=useState<File[]>([]);
  const fileInputRef=useRef<HTMLInputElement|null>(null);
  function clearFileSelection(){
    setFiles([]);
    // A retained native <input type=file> shows stale file counts after the
    // transfer clears React state and may not fire a new change event when
    // the organiser reselects the same folder to resume.
    if(fileInputRef.current)fileInputRef.current.value="";
  }
  const [busy,setBusy]=useState(false);
  const [preparing,setPreparing]=useState("");
  const preparation=useRef<AbortController|null>(null);
  const selectedBytes=useMemo(()=>files.reduce((sum,file)=>sum+file.size,0),[files]);
  const [transfer,setTransfer]=useState<DatasetTransferState|null>(getDatasetTransfer);
  useEffect(()=>watchDatasetTransfer(setTransfer),[]);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const refresh=useCallback(async()=>{
    try{
      const result=await uploadApi<{items:Dataset[]}>("/datasets");
      setDatasets(result.items);
      setSelected(old=>result.items.some(d=>d.id===old)?old:(result.items[0]?.id||""));
    }catch(e){setError((e as Error).message);return false;}
  },[]);
  useEffect(()=>{void refresh();},[refresh]);
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
    if(!files.length){setError("Choose a ZIP archive or dataset folder first.");return;}
    if(transfer?.running){setError("Another dataset transfer is already running.");return;}
    setBusy(true);setError("");setNotice("");
    let created:Dataset|null=null;
    try {
      preparation.current=new AbortController();
      await validateDatasetSources(files,mode,preparation.current.signal,
        count=>setPreparing(`Checking file metadata: ${count} / ${files.length}`));
      setPreparing("Creating upload record�");
      created=await uploadApi<Dataset>("/datasets/uploads","POST",{
        name:name.trim()||(mode==="folder"?
          (files[0].webkitRelativePath.split("/")[0]||files[0].name):
          files[0].name.replace(/\.zip$/i,"")),
        upload_kind:mode,extraction_policy:extractionPolicy
      },auth.csrf_token,preparation.current.signal);
      setSelected(created.id);
      setDatasets(old=>[created!,...old.filter(d=>d.id!==created!.id)]);
      beginDatasetTransfer({
        datasetId:created.id,name:created.name,kind:mode,
        files,csrf:auth.csrf_token,resume:false
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
    if(!dataset||!files.length)return;
    if(dataset.upload_kind!==mode){
      setError("Select the same upload format as the interrupted dataset.");return;
    }
    try {
      setError("");setNotice("");
      beginDatasetTransfer({
        datasetId:dataset.id,name:dataset.name,kind:dataset.upload_kind,
        files,csrf:auth.csrf_token,resume:true,
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
  return <div className="page-body">
    <div className="page-heading"><div><span className="eyebrow">DATA · IMPORT & INVENTORY</span>
      <h2>Dataset Management</h2>
      <p className="muted">Upload bird videos organised by species. Nightjar keeps each video's frames together for labeling jobs.</p>
    </div><RefreshButton refresh={refresh} disabled={busy}>Refresh</RefreshButton></div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}
    <div className="stat-grid compact">
      <div className="stat-card"><HardDrive size={20}/><span className="muted small">Datasets</span>
        <strong>{datasets.length}</strong></div>
      <div className="stat-card"><Layers3 size={20}/><span className="muted small">Available video frames</span>
        <strong>{dataset?.frame_count??0}</strong></div>
      <div className="stat-card"><FolderOpen size={20}/><span className="muted small">Selected videos</span>
        <strong>{dataset?.video_count??0}</strong></div>
    </div>
    <div className="content-grid datasets-layout">
      {admin&&<section className="panel">
        <span className="eyebrow">PRE-EVENT MEDIA LIBRARY</span><h3>Import and preload videos</h3>
        <p className="muted small">Choose a local folder in your browser without running commands. Files are transferred securely to Nightjar, then uploaded to Supervisely by its separate worker. If the dataset is already under Nightjar\u0027s linked host source root, use <strong>Link host folder (optional)</strong> instead for a faster no-copy import. Automatic labeling accepts both full and sampled source videos. When importing an existing frame folder, Nightjar orders the <strong>uploaded frames</strong> by their original frame numbers (or natural filename order), even when source-video frame numbers have gaps. For raw videos, <strong>Every source frame</strong> extracts the entire video; sampled extraction is also supported. Upload a folder or ZIP with <code>dataset-name/bird-species/video.mp4</code> or <code>dataset-name/bird-species/video-title/frame.jpg</code>. Multiple species and videos are supported. <strong>No frames.json or other metadata file is required in either upload format.</strong> Source videos are decoded according to the selected extraction mode. For existing frame folders, Nightjar recognizes filenames such as <code>ColumbaPalumbus_f000013.jpg</code>, or infers a consistent natural filename order. Missing original-video frames are expected in sampled datasets and do not block automatic distribution.</p>
        <form className="form-stack" onSubmit={e=>void ingest(e)}>
          <label>Dataset name<input maxLength={200} value={name} placeholder="BirdGaze 2026"
            onChange={e=>setName(e.target.value)}/></label>
          <label>Frame extraction<select value={extractionPolicy} disabled={busy}
            onChange={e=>setExtractionPolicy(e.target.value as typeof extractionPolicy)}>
            <option value="all_frames">Every source frame</option>
            <option value="sample_1fps">Sample one frame per second</option>
          </select></label>
          <label>Upload format<select value={mode} disabled={busy}
            onChange={e=>{setMode(e.target.value as "zip"|"folder");clearFileSelection();}}>
            <option value="zip">ZIP of species folders</option>
            <option value="folder">Folder containing species folders</option>
          </select></label>
          {mode==="zip"?<label className="dataset-drop">
            <FileArchive size={28}/><span>Select .zip file</span>
            <input ref={fileInputRef} aria-label="ZIP dataset" type="file" accept=".zip,application/zip"
              disabled={busy} onChange={e=>setFiles(Array.from(e.target.files||[]))}/>
          </label>:<label className="dataset-drop">
            <FolderOpen size={28}/><span>Choose dataset folder on this computer</span>
            <input ref={fileInputRef} aria-label="Folder dataset" type="file" multiple
              {...{webkitdirectory:"",directory:""}}
              disabled={busy} onChange={e=>setFiles(Array.from(e.target.files||[]))}/>
          </label>}
          <p className="muted small">{files.length} file(s) selected · {formatBytes(
            selectedBytes
          )}</p>
          {preparing&&<p role="status" className="muted small">{preparing} <button type="button" className="btn secondary" onClick={()=>preparation.current?.abort()}>Cancel preparation</button></p>}
          {transfer?.running&&<div className="dataset-progress">
            <strong className="small">{transfer.phase==="preparing"?"Checking selected file metadata":
              transfer.phase==="reconciling"?"Checking saved upload progress":
              transfer.phase==="validating"?"Validating local media":
              "Uploading "+transfer.name+" in this browser tab"}</strong>
            <progress max={transfer.total||1} value={transfer.done}/>
            <span>{formatBytes(transfer.done)} / {formatBytes(transfer.total)}
              · {transfer.files}/{transfer.count} files · Safe to navigate within Nightjar</span>
          </div>}
          <button className="btn primary" disabled={busy||!files.length||Boolean(transfer?.running)}>
            <CloudUpload size={16}/> {busy?"Preparing…":transfer?.running?"Transfer active…":"Upload & validate"}
          </button>
        </form>
        <div className="settings-note" style={{marginTop:16}}><AlertTriangle size={17}/>
          <p>After local import, the worker uploads all frames into one Supervisely dataset.
            The automatic jobs later select those existing frames without uploading duplicates.
            No labeling job is created by the dataset upload. Keep backups of local media.</p></div>
      </section>}
      <section className="panel">
        <span className="eyebrow">LOCAL DATASETS</span><h3>Imported collections</h3>
        <div className="dataset-cards">{datasets.map(d=><button key={d.id}
          onClick={()=>setSelected(d.id)} className={"dataset-card "+(selected===d.id?"selected":"")}>
          <div><strong>{d.name}</strong><span>{d.upload_kind.toUpperCase()} · {formatBytes(d.source_bytes)}</span></div>
          <span className={"dataset-status "+d.status}>{d.status==="ready"?<CheckCircle2 size={14}/>:<Archive size={14}/>}
            {d.status}</span></button>)}
        {!datasets.length&&<p className="muted small">No dataset uploaded yet.</p>}</div>
        {dataset&&<div className="dataset-detail"><h4>{dataset.name}</h4>
          <p className="muted small">{dataset.video_count} videos ·
            {dataset.frame_count} frames available for annotation</p>
          <p className="muted small">Local status: {dataset.status} · Native project: {dataset.native_project_id||"Not created yet"}
            {" "}· Inventory revision {dataset.inventory_revision} · Mirrored revision {dataset.mirrored_revision}</p>
          {dataset.status==="ready"&&<div className="dataset-mirror-status">
            <strong>Supervisely media upload: {mirror?.ready_for_automatic_jobs?
              "Complete":"Not complete"}</strong>
            <p className="muted small">{mirror?.state||"Checking…"} · {mirror?.uploaded_frames??0} /
              {mirror?.total_frames??dataset.frame_count} frames mirrored
              {mirror?.attempts? " · attempt "+mirror.attempts:""}</p>
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
              All frames are mirrored. Before automatic 30-frame job distribution,
              use Job Distribution → Automatic distribution → Check source frames
              to verify source-video identity and consecutive frame positions.
              Manual assignments can independently duplicate a video in their separate remote dataset.</p>}
          </div>}
          {admin&&dataset.status==="ready"&&
            <AppendVideos key={dataset.id} datasetId={dataset.id}
              csrf={auth.csrf_token} onCommitted={refresh}/>}
          {videos.length>0&&<div className="settings-note">
            <div><label>Estimated storage · target frame count<input type="number" min={1} max={1000000000}
              value={projectedFrames} onChange={e=>setProjectedFrames(Math.min(1000000000,Math.max(1,Number(e.target.value)||1)))}/></label>
              <p>Approximately {formatBytes(videos.reduce((sum,video)=>sum+video.extracted_bytes,0)
                /Math.max(1,videos.reduce((sum,video)=>sum+video.frame_count,0))*projectedFrames)} for {projectedFrames.toLocaleString()} frames at the average size in this collection.
                Allow additional storage for original videos, staging, annotations, exports and backups.</p></div>
          </div>}
          {dataset.status==="ready"&&videosLoaded&&!videos.length&&<p className="advisory">
            This collection predates source-frame provenance. For ordered jobs, each frame still needs a source-video grouping and an ordering key; sampled footage is supported.</p>}
          {videos.map(video=><div key={video.id} className="settings-note">
            <p><strong>{video.video_group}</strong> · {video.frame_count} frames · {formatBytes(video.extracted_bytes)} frame files
              <br/>{video.extraction_policy} · Density {video.density} · Frame identity {video.identity_status}
              {video.identity_notes.map(note=><span key={note}><br/>{note}</span>)}</p>
          </div>)}
          {transfer?.datasetId===dataset.id&&transfer.error&&
            <p className="alert danger" role="alert">{transfer.error}</p>}
          {admin&&dataset.status!=="ready"&&<div className="dataset-recovery-actions">
            {transfer?.datasetId===dataset.id&&transfer.running?
              <button type="button" className="btn secondary" disabled={busy||transfer.phase==="validating"}
                onClick={()=>void stopSelected()}><PauseCircle size={15}/> Stop upload</button>:
              <>
                <button type="button" className="btn secondary"
                  disabled={busy||!files.length||Boolean(transfer?.running)}
                  onClick={()=>void resumeSelected()}><RotateCcw size={15}/> Resume with selected files</button>
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
