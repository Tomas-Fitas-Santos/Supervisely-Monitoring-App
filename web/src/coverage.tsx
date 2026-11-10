import { useCallback, useEffect, useMemo, useState } from "react";
import type React from "react";
import {
  BarChart3, CheckCircle2, ChevronLeft, ChevronRight, Download,
  Eye, Film, FolderTree, RefreshCw, Save, Tags, Trash2
} from "lucide-react";
import { api, type Auth } from "./api";

type Dataset={id:string;name:string;status:string;frame_count:number;image_count:number;video_count:number};
type GroupRow={name:string;frames:number;images:number;videos:number;tagged_frames:number;species:number};
type FolderRow={source_folder:string;species_name:string|null;ecomorphology:string;
  manual:boolean;frames:number;images:number;videos:number};
type MovementRow={type:string;label:string;frames:number;videos:number};
type Segment={id:string;start_frame:number;end_frame:number;movement_type:string;notes:string};
type VideoRow={video_group:string;source_folder:string;ecomorphology:string;
  species_name:string|null;frames:number;tagged_frames:number;
  untagged_frames:number;segments:Segment[]};
type Coverage={dataset:Dataset;
  totals:{frames:number;images:number;annotatable_media:number;videos:number;
    species_folders:number;unclassified_frames:number;unclassified_images:number;
    tagged_frames:number;unlabeled_frames:number};
  groups:GroupRow[];folders:FolderRow[];movements:MovementRow[];
  reference:{groups:string[];movements:{type:string;label:string}[];
    taxonomy_source:string;frame_order:string;movement_policy:string}};
type VideoPage={page:number;size:number;total:number;items:VideoRow[]};
type FramePage={page:number;size:number;total:number;
  items:{id:string;relative_path:string;frame:number}[]};

const nf=new Intl.NumberFormat("en-GB");
const TARGET_PER_GROUP=7500;
const pct=(n:number,d:number)=>d?Math.round(100*n/d):0;

export function CoverageScreen({auth}:{auth:Auth}) {
  const organiser=auth.staff.roles.includes("organiser");
  const [datasets,setDatasets]=useState<Dataset[]>([]);
  const [datasetId,setDatasetId]=useState("");
  const [coverage,setCoverage]=useState<Coverage|null>(null);
  const [videoPage,setVideoPage]=useState<VideoPage|null>(null);
  const [groupFilter,setGroupFilter]=useState("all");
  const [videoStatus,setVideoStatus]=useState("all");
  const [videoSearch,setVideoSearch]=useState("");
  const [page,setPage]=useState(1);
  const [folderSearch,setFolderSearch]=useState("");
  const [editFolder,setEditFolder]=useState("");
  const [editGroup,setEditGroup]=useState("");
  const [editSpecies,setEditSpecies]=useState("");
  const [selectedVideo,setSelectedVideo]=useState("");
  const [movement,setMovement]=useState("rapid_head_turn");
  const [startFrame,setStartFrame]=useState(1);
  const [endFrame,setEndFrame]=useState(1);
  const [motionNotes,setMotionNotes]=useState("");
  const [framePage,setFramePage]=useState(1);
  const [framePreview,setFramePreview]=useState<FramePage|null>(null);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [busy,setBusy]=useState(false);
  const [refreshId,setRefreshId]=useState(0);

  useEffect(()=>{
    void api<{items:Dataset[]}>("/datasets").then(r=>{
      setDatasets(r.items);
      setDatasetId(old=>r.items.some(d=>d.id===old)?old:
        (r.items.find(d=>d.status==="ready")?.id||r.items[0]?.id||""));
    }).catch(e=>setError((e as Error).message));
  },[refreshId]);

  const loadSummary=useCallback(async()=>{
    if(!datasetId){setCoverage(null);return;}
    const r=await api<Coverage>("/coverage/datasets/"+datasetId);
    setCoverage(r);
  },[datasetId]);
  const loadVideos=useCallback(async()=>{
    if(!datasetId){setVideoPage(null);return;}
    const query=new URLSearchParams({
      page:String(page),size:"30",ecomorphology:groupFilter,
      search:videoSearch,status:videoStatus
    });
    setVideoPage(await api<VideoPage>(
      "/coverage/datasets/"+datasetId+"/videos?"+query
    ));
  },[datasetId,page,groupFilter,videoSearch,videoStatus]);

  useEffect(()=>{void loadSummary().catch(e=>setError((e as Error).message));},
    [loadSummary,refreshId]);
  useEffect(()=>{void loadVideos().catch(e=>setError((e as Error).message));},
    [loadVideos,refreshId]);

  const inspect=videoPage?.items.find(v=>v.video_group===selectedVideo);
  const loadFrames=useCallback(async()=>{
    if(!datasetId||!selectedVideo){setFramePreview(null);return;}
    const query=new URLSearchParams({
      video_group:selectedVideo,page:String(framePage),size:"12"
    });
    setFramePreview(await api<FramePage>(
      "/coverage/datasets/"+datasetId+"/video-frames?"+query
    ));
  },[datasetId,selectedVideo,framePage]);
  useEffect(()=>{void loadFrames().catch(e=>setError((e as Error).message));},
    [loadFrames,refreshId]);

  const groups=coverage?.groups||[];
  const movementRows=coverage?.movements||[];
  const totalFrames=coverage?.totals.frames||0;
  const maxGroupFrames=Math.max(...groups.map(x=>x.frames),TARGET_PER_GROUP);
  const sourceRows=useMemo(()=>(coverage?.folders||[]).filter(r=>{
    const query=folderSearch.trim().toLowerCase();
    return !query||[r.source_folder,r.species_name||"",r.ecomorphology]
      .some(s=>s.toLowerCase().includes(query));
  }),[coverage,folderSearch]);

  function refresh(){setError("");setRefreshId(n=>n+1);}
  function beginClassify(row:FolderRow){
    setEditFolder(row.source_folder);
    setEditGroup(row.ecomorphology==="Unclassified"?"":row.ecomorphology);
    setEditSpecies(row.species_name||"");
  }
  async function saveFolder(e:React.FormEvent){
    e.preventDefault();if(!editFolder)return;
    setBusy(true);setError("");setNotice("");
    try {
      await api("/coverage/datasets/"+datasetId+"/classification","PUT",{
        source_folder:editFolder,ecomorphology:editGroup,
        species_name:editSpecies.trim()||null
      },auth.csrf_token);
      setEditFolder("");
      await Promise.all([loadSummary(),loadVideos()]);
      setNotice("Folder ecomorphology and species classification saved.");
    }catch(exc){setError((exc as Error).message);}
    finally{setBusy(false);}
  }
  function firstFreeRange(video:VideoRow){
    const ordered=[...video.segments].sort((a,b)=>a.start_frame-b.start_frame);
    let start=1;let end=video.frames;
    for(const row of ordered){
      if(row.start_frame>start){end=row.start_frame-1;break;}
      start=Math.max(start,row.end_frame+1);
    }
    return {start:Math.min(start,video.frames),end:Math.max(start,end)};
  }
  function beginVideo(video:VideoRow){
    setSelectedVideo(video.video_group);setFramePage(1);setFramePreview(null);
    const range=firstFreeRange(video);
    setStartFrame(range.start);setEndFrame(range.end);
    setMovement("rapid_head_turn");setMotionNotes("");
  }
  async function addMovement(e:React.FormEvent){
    e.preventDefault();if(!inspect)return;
    setBusy(true);setError("");setNotice("");
    try{
      await api("/coverage/datasets/"+datasetId+"/movements","POST",{
        video_group:inspect.video_group,start_frame:startFrame,end_frame:endFrame,
        movement_type:movement,notes:motionNotes
      },auth.csrf_token);
      setNotice("Observed primary movement assigned to "+nf.format(endFrame-startFrame+1)+" frames.");
      await Promise.all([loadSummary(),loadVideos()]);
      setSelectedVideo("");
    }catch(exc){setError((exc as Error).message);}
    finally{setBusy(false);}
  }
  async function removeSegment(segment:Segment){
    if(!window.confirm("Remove this observed-movement range? It will become unlabeled."))return;
    setBusy(true);setError("");setNotice("");
    try {
      await api("/coverage/datasets/"+datasetId+"/movements/"+segment.id,
        "DELETE",undefined,auth.csrf_token);
      setNotice("Movement interval removed.");
      await Promise.all([loadSummary(),loadVideos()]);
      setSelectedVideo("");
    }catch(exc){setError((exc as Error).message);}
    finally{setBusy(false);}
  }
  const labelFor=(type:string)=>coverage?.reference.movements.find(
    m=>m.type===type)?.label||type;

  return <div className="page-body coverage-page">
    <div className="page-heading">
      <div><span className="eyebrow">DATASET · ECOMORPHOLOGICAL COVERAGE</span>
        <h2>Dataset Coverage & Movement Review</h2>
        <p className="muted">Count unique imported frames by body-plan group, species and observed head movement.</p>
      </div>
      <div className="heading-actions">
        <button className="btn secondary" onClick={refresh}><RefreshCw size={16}/> Refresh</button>
        {datasetId&&<a className="btn secondary" href={"/api/v1/coverage/datasets/"+datasetId+"/export.csv"}
          download="nightjar-dataset-coverage.csv"><Download size={16}/> Export CSV</a>}
      </div>
    </div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}
    <div className="panel coverage-dataset-select">
      <label>Dataset<select value={datasetId} onChange={e=>{
        setDatasetId(e.target.value);setPage(1);setSelectedVideo("");setCoverage(null);
      }}>
        {!datasets.length&&<option value="">No datasets uploaded</option>}
        {datasets.map(d=><option value={d.id} key={d.id}>{d.name} · {d.status}</option>)}
      </select></label>
      <span className="muted small">Counts are unique inventory records, not multiplied by annotation-job overlap. The 16 groups come from the supplied workbook.</span>
    </div>
    {!coverage?<section className="panel"><p className="muted">
      {datasetId?"Loading dataset coverage…":"Upload a dataset first to see frame coverage."}
    </p></section>:<>
      <div className="coverage-kpis">
        {([
          ["Unique video frames",coverage.totals.frames,Film],
          ["Standalone images",coverage.totals.images,FolderTree],
          ["Source videos",coverage.totals.videos,Eye],
          ["Movement-labeled frames",coverage.totals.tagged_frames,Tags],
          ["Unlabeled video frames",coverage.totals.unlabeled_frames,BarChart3],
        ] as const).map(([label,value,Icon])=><div className="stat-card" key={label}>
          <Icon size={19}/><span className="muted small">{label}</span>
          <strong>{nf.format(value)}</strong>
        </div>)}
      </div>
      <div className="content-grid coverage-panels">
        <section className="panel">
          <div className="panel-heading"><div>
            <span className="eyebrow">16 OPERATIONAL ECOMORPHOLOGIES</span>
            <h3>Frame coverage by body plan</h3>
          </div><BarChart3 size={21}/></div>
          <p className="muted small">Target: {nf.format(TARGET_PER_GROUP)} unique video frames per group, based on the workbook's 120,000-frame planning target. Still images are separate.</p>
          <div className="coverage-bars">
            {groups.map(g=><div key={g.name} className="coverage-bar-row">
              <div className="coverage-bar-head"><span>{g.name}</span>
                <strong>{nf.format(g.frames)}</strong></div>
              <div className="coverage-bar-track" role="meter" aria-label={g.name+" video frames"}
                aria-valuemin={0} aria-valuemax={maxGroupFrames} aria-valuenow={g.frames}>
                <i style={{width:pct(g.frames,maxGroupFrames)+"%"}}/>
              </div>
            </div>)}
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading"><div>
            <span className="eyebrow">OBSERVED, NOT PREDICTED</span>
            <h3>Frames by movement type</h3>
          </div><Tags size={21}/></div>
          <p className="muted small">Movement labels apply only to explicitly reviewed, non-overlapping frame intervals. The rest remain unlabeled.</p>
          <div className="coverage-bars">
            {movementRows.map(m=><div key={m.type} className="coverage-bar-row">
              <div className="coverage-bar-head"><span>{m.label}</span>
                <strong>{nf.format(m.frames)}</strong></div>
              <div className={"coverage-bar-track "+(m.type==="unlabeled"?"unlabeled":"")}
                role="meter" aria-label={m.label+" frames"} aria-valuemin={0}
                aria-valuemax={Math.max(1,totalFrames)} aria-valuenow={m.frames}>
                <i style={{width:pct(m.frames,totalFrames)+"%"}}/>
              </div>
            </div>)}
          </div>
        </section>
      </div>
      <section className="panel" style={{marginTop:20}}>
        <span className="eyebrow">COVERAGE INVENTORY</span>
        <h3>Frames, still images and videos per ecomorphology</h3>
        <div className="table-scroll">
          <table className="data-table coverage-table">
            <thead><tr><th>Ecomorphology</th><th>Video frames</th><th>Images</th>
              <th>Videos</th><th>Species folders</th><th>Movement labeled</th>
              <th>Target progress</th></tr></thead>
            <tbody>{groups.map(g=><tr key={g.name}>
              <td><strong>{g.name}</strong></td>
              <td>{nf.format(g.frames)}</td><td>{nf.format(g.images)}</td>
              <td>{nf.format(g.videos)}</td><td>{nf.format(g.species)}</td>
              <td>{nf.format(g.tagged_frames)}</td>
              <td>{g.name==="Unclassified"?"Review required":
                pct(g.frames,TARGET_PER_GROUP)+"%"}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </section>
      <section className="panel" style={{marginTop:20}}>
        <div className="coverage-section-heading">
          <div><span className="eyebrow">SOURCE FOLDERS · SPECIES</span>
            <h3>Species and ecomorphology classification</h3>
          </div>
          <label className="coverage-search">Search<input placeholder="Species, folder, group…"
            value={folderSearch} onChange={e=>setFolderSearch(e.target.value)}/></label>
        </div>
        <p className="muted small">Known eBird species folders are suggested from your workbook. Unknown folders remain unclassified until an organiser maps them. Mapping a folder does not change any original files.</p>
        <div className="table-scroll coverage-folder-table">
          <table className="data-table"><thead><tr><th>Source folder</th><th>Species</th>
            <th>Ecomorphology</th><th>Frames</th><th>Images</th><th>Videos</th>
            {organiser&&<th>Manage</th>}</tr></thead>
            <tbody>{sourceRows.map(r=><tr key={r.source_folder}>
              <td>{r.source_folder}</td><td>{r.species_name||"Not identified"}</td>
              <td>{r.ecomorphology==="Unclassified"?
                <strong className="coverage-warning">Unclassified</strong>:r.ecomorphology}</td>
              <td>{nf.format(r.frames)}</td><td>{nf.format(r.images)}</td>
              <td>{nf.format(r.videos)}</td>
              {organiser&&<td><button className="btn secondary" type="button"
                onClick={()=>beginClassify(r)}>Classify</button></td>}
            </tr>)}</tbody>
          </table>
        </div>
        {organiser&&editFolder&&<form className="coverage-editor" onSubmit={e=>void saveFolder(e)}>
          <div><span className="eyebrow">EDIT SOURCE FOLDER</span>
            <strong>{editFolder}</strong></div>
          <label>Species name<input value={editSpecies}
            onChange={e=>setEditSpecies(e.target.value)} maxLength={200}
            placeholder="e.g. House Sparrow"/></label>
          <label>Ecomorphology<select value={editGroup} required
            onChange={e=>setEditGroup(e.target.value)}>
            <option value="">Select ecomorphology</option>
            {coverage.reference.groups.map(g=><option value={g} key={g}>{g}</option>)}
          </select></label>
          <div className="settings-action-row">
            <button className="btn primary" type="submit" disabled={busy||!editGroup}>
              <Save size={15}/> Save classification</button>
            <button className="btn secondary" type="button"
              onClick={()=>setEditFolder("")}>Cancel</button>
          </div>
        </form>}
      </section>

      <section className="panel" style={{marginTop:20}}>
        <div className="coverage-section-heading"><div>
          <span className="eyebrow">VIDEO FRAME INSPECTION</span>
          <h3>Review actual movement intervals</h3>
        </div><span className="muted small">{videoPage?nf.format(videoPage.total)+" videos":"Loading…"}</span></div>
        <p className="muted small">Assign the primary visible movement only to the frames where it occurs. Numbering uses alphabetically ordered frame filenames, starting from 1. Existing ranges cannot overlap.</p>
        <div className="coverage-filters">
          <label>Ecomorphology<select value={groupFilter} onChange={e=>{
            setGroupFilter(e.target.value);setPage(1);setSelectedVideo("");
          }}><option value="all">All groups</option>
            {groups.map(g=><option key={g.name} value={g.name}>{g.name}</option>)}
          </select></label>
          <label>Labeling<select value={videoStatus} onChange={e=>{
            setVideoStatus(e.target.value);setPage(1);setSelectedVideo("");
          }}><option value="all">All videos</option>
            <option value="unlabeled">Has unlabeled frames</option>
            <option value="tagged">Has labeled frames</option>
          </select></label>
          <label>Search video<input placeholder="Video title or species"
            value={videoSearch} onChange={e=>{setVideoSearch(e.target.value);setPage(1);}}/></label>
        </div>
        <div className="table-scroll">
          <table className="data-table coverage-table"><thead><tr>
            <th>Video</th><th>Ecomorphology</th><th>Total frames</th>
            <th>Movement labeled</th><th>Unlabeled</th><th>Review</th>
          </tr></thead>
          <tbody>{(videoPage?.items||[]).map(v=><tr key={v.video_group}>
            <td><strong>{v.video_group}</strong>
              <span className="coverage-secondary">{v.species_name||v.source_folder}</span></td>
            <td>{v.ecomorphology}</td><td>{nf.format(v.frames)}</td>
            <td>{nf.format(v.tagged_frames)}</td><td>{nf.format(v.untagged_frames)}</td>
            <td><button className="btn secondary" type="button" onClick={()=>beginVideo(v)}>
              <Eye size={14}/> Inspect</button></td>
          </tr>)}
          {!videoPage?.items.length&&<tr><td colSpan={6}>
            <span className="muted">No videos match the current filters.</span></td></tr>}
          </tbody></table>
        </div>
        {videoPage&&<div className="table-pagination">
          <span>Page {videoPage.page} of {Math.max(1,Math.ceil(videoPage.total/videoPage.size))}</span>
          <button className="btn secondary" type="button" disabled={page<=1}
            onClick={()=>{setPage(n=>n-1);setSelectedVideo("");}}><ChevronLeft size={14}/> Previous</button>
          <button className="btn secondary" type="button"
            disabled={page*videoPage.size>=videoPage.total}
            onClick={()=>{setPage(n=>n+1);setSelectedVideo("");}}>Next <ChevronRight size={14}/></button>
        </div>}
        {inspect&&<div className="coverage-inspector">
          <div className="coverage-section-heading"><div>
            <span className="eyebrow">INSPECTING VIDEO</span>
            <h3>{inspect.video_group}</h3>
            <p className="muted small">{nf.format(inspect.frames)} frames · {nf.format(inspect.untagged_frames)} still unlabeled</p>
          </div><button className="btn secondary" type="button"
            onClick={()=>{setSelectedVideo("");setFramePreview(null);}}>Close</button></div>
          <div className="coverage-frames">
            {(framePreview?.items||[]).map(f=><div className="coverage-frame" key={f.id}>
              <img alt={"Source video frame "+f.frame}
                loading="lazy"
                src={"/api/v1/review/datasets/"+datasetId+"/assets/"+f.id+"/image"}/>
              <div><strong>Frame {nf.format(f.frame)}</strong>
                {organiser&&<span className="coverage-frame-actions">
                  <button type="button" onClick={()=>setStartFrame(f.frame)}>Set start</button>
                  <button type="button" onClick={()=>setEndFrame(f.frame)}>Set end</button>
                </span>}
              </div>
            </div>)}
          </div>
          {framePreview&&<div className="table-pagination">
            <span>Frames {nf.format((framePage-1)*framePreview.size+1)}–
              {nf.format(Math.min(framePage*framePreview.size,framePreview.total))}
              {" of "+nf.format(framePreview.total)}</span>
            <button type="button" className="btn secondary" disabled={framePage<=1}
              onClick={()=>setFramePage(n=>n-1)}>Previous frames</button>
            <button type="button" className="btn secondary"
              disabled={framePage*framePreview.size>=framePreview.total}
              onClick={()=>setFramePage(n=>n+1)}>Next frames</button>
          </div>}
          <div className="coverage-existing">
            <h4>Recorded movement intervals</h4>
            {inspect.segments.length?inspect.segments.map(s=><div className="coverage-tag" key={s.id}>
              <span><strong>{labelFor(s.movement_type)}</strong> · Frames {nf.format(s.start_frame)}–{nf.format(s.end_frame)}
                {s.notes&&<small>{s.notes}</small>}</span>
              {organiser&&<button className="btn danger-outline" type="button"
                disabled={busy} onClick={()=>void removeSegment(s)}><Trash2 size={14}/> Remove</button>}
            </div>):<p className="muted small">No observed movement types labeled for this video yet.</p>}
          </div>
          {organiser&&inspect.untagged_frames>0&&<form className="coverage-motion-form"
            onSubmit={e=>void addMovement(e)}>
            <h4>Label an observed interval</h4>
            <div className="coverage-filters">
              <label>Start frame<input type="number" min={1} max={inspect.frames}
                value={startFrame} onChange={e=>setStartFrame(Number(e.target.value))}/></label>
              <label>End frame<input type="number" min={1} max={inspect.frames}
                value={endFrame} onChange={e=>setEndFrame(Number(e.target.value))}/></label>
              <label>Primary observed movement<select value={movement}
                onChange={e=>setMovement(e.target.value)}>
                {coverage.reference.movements.map(m=><option key={m.type}
                  value={m.type}>{m.label}</option>)}
              </select></label>
            </div>
            <label>Review notes (optional)<textarea maxLength={1000} rows={2}
              value={motionNotes} onChange={e=>setMotionNotes(e.target.value)}
              placeholder="Evidence or movement cue observed in these frames"/></label>
            <button className="btn primary" type="submit"
              disabled={busy||startFrame<1||endFrame<startFrame||endFrame>inspect.frames}>
              <CheckCircle2 size={15}/> Save observed movement</button>
          </form>}
          {!organiser&&<p className="muted small">Only organisers can change movement classifications. All monitors can inspect the recorded coverage.</p>}
        </div>}
      </section>
      <p className="muted small coverage-footnote">Classification groups are operational ecomorphologies, not phylogenetic taxa. Per-movement totals exclude unlabeled frames. Standalone images count separately because movement cannot be established from a single still frame.</p>
    </>}
  </div>;
}
