import { useCallback, useEffect, useMemo, useState } from "react";
import type React from "react";
import {
  AlertTriangle, Archive, CheckCircle2, CloudUpload, FileArchive,
  FolderOpen, HardDrive, Layers3, RefreshCw, RotateCcw, Trash2, PauseCircle
} from "lucide-react";
import { api, type Auth } from "./api";
import { beginDatasetTransfer, clearDatasetTransfer, getDatasetTransfer,
  stopDatasetTransfer, watchDatasetTransfer, type DatasetTransferState } from "./dataset_transfer";

type Dataset={id:string;name:string;upload_kind:"zip"|"folder";
  status:string;source_bytes:number;image_count:number;frame_count:number;
  video_count:number;created_at:number;last_activity_at:number;error_code:string|null;
  native_project_id:number|null};
type Morph={name:string;kind:string;count:number};
type Summary={dataset:Dataset;morphologies:Morph[];total_assets:number};
const formatBytes=(n:number)=>n>=1e9?(n/1e9).toFixed(2)+" GB":n>=1e6?
  (n/1e6).toFixed(1)+" MB":(n/1000).toFixed(1)+" KB";

export function DatasetScreen({auth}:{auth:Auth}) {
  const admin=auth.staff.roles.includes("organiser");
  const [datasets,setDatasets]=useState<Dataset[]>([]);
  const [selected,setSelected]=useState("");
  const [summary,setSummary]=useState<Summary|null>(null);
  const [mode,setMode]=useState<"zip"|"folder">("zip");
  const [name,setName]=useState("");
  const [files,setFiles]=useState<File[]>([]);
  const [busy,setBusy]=useState(false);
  const [transfer,setTransfer]=useState<DatasetTransferState|null>(getDatasetTransfer);
  useEffect(()=>watchDatasetTransfer(setTransfer),[]);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const refresh=useCallback(async()=>{
    try{
      const result=await api<{items:Dataset[]}>("/datasets");
      setDatasets(result.items);
      setSelected(old=>result.items.some(d=>d.id===old)?old:(result.items[0]?.id||""));
    }catch(e){setError((e as Error).message);}
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
  const dataset=datasets.find(d=>d.id===selected);
  const groups=useMemo(()=>summary?.morphologies.reduce<Record<string,{images:number;frames:number}>>(
    (acc,m)=>{
      const row=acc[m.name]||(acc[m.name]={images:0,frames:0});
      if(m.kind==="image")row.images+=m.count;else row.frames+=m.count;
      return acc;
    },{})||{},[summary]);
  async function ingest(e:React.FormEvent) {
    e.preventDefault();
    if(!files.length){setError("Choose a ZIP archive or dataset folder first.");return;}
    if(transfer?.running){setError("Another dataset transfer is already running.");return;}
    setBusy(true);setError("");setNotice("");
    let created:Dataset|null=null;
    try {
      created=await api<Dataset>("/datasets/uploads","POST",{
        name:name.trim()||files[0].name.replace(/\.zip$/i,""),
        upload_kind:mode
      },auth.csrf_token);
      setSelected(created.id);
      beginDatasetTransfer({
        datasetId:created.id,name:created.name,kind:mode,
        files,csrf:auth.csrf_token,resume:false
      });
      setNotice("Transfer started. You can navigate to other Nightjar pages.");
      setFiles([]);setName("");
      await refresh();
    }catch(e) {
      setError((e as Error).message+(created?" The unfinished record can be removed below.":""));
      await refresh();
    }finally{setBusy(false);}
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
      setFiles([]);
      setNotice("Resuming original files from server-verified upload offsets.");
    }catch(e){setError((e as Error).message);}
  }
  async function stopSelected() {
    if(!dataset)return;
    setBusy(true);setError("");
    try {
      await stopDatasetTransfer(dataset.id,auth.csrf_token);
      setNotice("Upload stopped. Partial files can be resumed or deleted.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function deleteSelected() {
    if(!dataset||dataset.status==="ready")return;
    if(!window.confirm("Delete the incomplete dataset '"+dataset.name+
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
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  return <div className="page-body">
    <div className="page-heading"><div><span className="eyebrow">DATA · IMPORT & INVENTORY</span>
      <h2>Dataset Management</h2>
      <p className="muted">Upload the bird dataset once, validate morphologies and keep video frames together.</p>
    </div><button className="btn secondary" onClick={()=>void refresh()} disabled={busy}>
      <RefreshCw size={15}/> Refresh</button></div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}
    <div className="stat-grid compact">
      <div className="stat-card"><HardDrive size={20}/><span className="muted small">Datasets</span>
        <strong>{datasets.length}</strong></div>
      <div className="stat-card"><Layers3 size={20}/><span className="muted small">Selected images</span>
        <strong>{dataset?.image_count??0}</strong></div>
      <div className="stat-card"><FolderOpen size={20}/><span className="muted small">Selected videos</span>
        <strong>{dataset?.video_count??0}</strong></div>
    </div>
    <div className="content-grid datasets-layout">
      {admin&&<section className="panel">
        <span className="eyebrow">PRIVATE LOCAL STORAGE</span><h3>Import dataset</h3>
        <p className="muted small">Expected structure: dataset-name/images/morphology/image.jpg and dataset-name/videos/morphology/video-title/frame.jpg. A ZIP can wrap the dataset folder.</p>
        <form className="form-stack" onSubmit={e=>void ingest(e)}>
          <label>Dataset name<input maxLength={200} value={name} placeholder="BirdGaze 2026"
            onChange={e=>setName(e.target.value)}/></label>
          <label>Upload format<select value={mode} disabled={busy}
            onChange={e=>{setMode(e.target.value as "zip"|"folder");setFiles([]);}}>
            <option value="zip">ZIP containing dataset folder</option>
            <option value="folder">Dataset folder with images and videos</option>
          </select></label>
          {mode==="zip"?<label className="dataset-drop">
            <FileArchive size={28}/><span>Select .zip file</span>
            <input aria-label="ZIP dataset" type="file" accept=".zip,application/zip"
              disabled={busy} onChange={e=>setFiles(Array.from(e.target.files||[]))}/>
          </label>:<label className="dataset-drop">
            <FolderOpen size={28}/><span>Select entire dataset folder</span>
            <input aria-label="Folder dataset" type="file" multiple
              {...{webkitdirectory:"",directory:""}}
              disabled={busy} onChange={e=>setFiles(Array.from(e.target.files||[]))}/>
          </label>}
          <p className="muted small">{files.length} file(s) selected · {formatBytes(
            files.reduce((sum,f)=>sum+f.size,0)
          )}</p>
          {transfer?.running&&<div className="dataset-progress">
            <strong className="small">{transfer.phase==="validating"?"Validating local media":
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
          <p>This import stays on local disk. It does not create a Supervisely project or
            publish any job until an organiser authorises that separately. Keep backups of local media.</p></div>
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
          <p className="muted small">{dataset.image_count} stand-alone images ·
            {dataset.frame_count} video frames · {dataset.video_count} atomic video groups</p>
          <p className="muted small">Local status: {dataset.status} · Native project: {dataset.native_project_id||"Not published"}</p>
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
      <span className="eyebrow">MORPHOLOGY COVERAGE</span><h3>Dataset distribution</h3>
      <div className="table-scroll"><table className="data-table"><thead><tr>
        <th>Species / Morphology</th><th>Images</th><th>Video frames</th><th>Total</th>
      </tr></thead><tbody>{Object.entries(groups).sort(([a],[b])=>a.localeCompare(b))
        .map(([m,count])=><tr key={m}><td><strong>{m}</strong></td>
          <td>{count.images}</td><td>{count.frames}</td>
          <td>{count.images+count.frames}</td></tr>)}</tbody></table></div>
      <p className="muted small">Planning balances assignment counts by morphology and
        uses frame count as job weight. Every frame of one video stays in the same job.</p>
    </section>}
  </div>;
}
