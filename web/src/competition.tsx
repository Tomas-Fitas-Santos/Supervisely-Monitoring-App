import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2, ClipboardCheck, Layers3, PlayCircle, Plus, RefreshCw,
  Send, Trash2, UsersRound, Video
} from "lucide-react";
import { api, type Auth } from "./api";

type Dataset={id:string;name:string;status:string;image_count:number;
  frame_count:number;video_count:number};
type Plan={id:string;dataset_id:string;status:string;jobs_per_team:number;
  redundancy:number;video_limit:number|null};
type Created={plan_id:string;status:string;assets:number;assignment_copies:number;
  job_count:number;min_frames_per_team:number;max_frames_per_team:number;
  morphologies:string[]};
type Binding={person_id:string;person_name:string;native_job_id:number;
  native_status:string;last_synced_at:number|null};
type Job={id:string;plan_id:string;team_id:string;team_name:string;
  sequence:number;frame_count:number;video_count:number;status:string;
  review_status:string;morphology_counts:Record<string,number>;
  assignee_person_id:string|null;assignee_name:string|null;release_mode:string;
  native_submissions:Binding[]};
type Media={id:string;relative_path:string;morphology:string;video_group:string|null};
type MediaPage={items:Media[];total:number;page:number;size:number};
type NativeOperation={id:string;target_id:string;kind:string;status:string;attempts:number;error_code:string|null};
type NativeStatus={operations:NativeOperation[];native_bindings:number;
  bindings_by_status:Record<string,number>;last_sync:number|null};
type TeamOption={id:string;name:string;members:{id:string;name:string;native_ready:boolean}[]};
type VideoOption={video_group:string;frames:number;morphology:string};
type ManualOptions={teams:TeamOption[];videos:VideoOption[];
  existing:{team_id:string;video_group:string;assignee_person_id:string|null;
    job_id:string;status:string}[]};
type Draft={team_id:string;assignee_person_id:string;video_group:string};
type AnnotationClass={name:string;shape:"rectangle"|"point";color:string;description:string};

export function CompetitionScreen({auth}:{auth:Auth}) {
  const admin=auth.staff.roles.includes("organiser");
  const [section,setSection]=useState<"overview"|"manual"|"automatic">("overview");
  const [datasets,setDatasets]=useState<Dataset[]>([]);
  const [dataset,setDataset]=useState("");
  const [plans,setPlans]=useState<Plan[]>([]);
  const [jobs,setJobs]=useState<Job[]>([]);
  const [chosen,setChosen]=useState("");
  const [media,setMedia]=useState<MediaPage|null>(null);
  const [page,setPage]=useState(1);
  const [redundancy,setRedundancy]=useState(1);
  const [jobsPerTeam,setJobsPerTeam]=useState(4);
  const [videoLimit,setVideoLimit]=useState("");
  const [created,setCreated]=useState<Created|null>(null);
  const [native,setNative]=useState<NativeStatus|null>(null);
  const [annotationClasses,setAnnotationClasses]=useState<AnnotationClass[]>([]);
  const [options,setOptions]=useState<ManualOptions|null>(null);
  const [teamId,setTeamId]=useState("");
  const [personId,setPersonId]=useState("");
  const [videoGroup,setVideoGroup]=useState("");
  const [drafts,setDrafts]=useState<Draft[]>([]);
  const [dispatch,setDispatch]=useState<"hold"|"publish">("publish");
  const [filter,setFilter]=useState<"all"|"review"|"active"|"held">("all");
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [busy,setBusy]=useState(false);

  const refresh=useCallback(async()=>{
    try {
      const [ds,ps,js,ns]=await Promise.all([
        api<{items:Dataset[]}>("/datasets"),
        api<{items:Plan[]}>("/labeling/plans"),
        api<{items:Job[]}>("/labeling/jobs?limit=2000"),
        api<NativeStatus>("/native/overview"),
      ]);
      setDatasets(ds.items.filter(item=>item.status==="ready"));
      setPlans(ps.items);setJobs(js.items);setNative(ns);
      setDataset(old=>ds.items.some(item=>item.id===old&&item.status==="ready")?old:
        (ds.items.find(item=>item.status==="ready")?.id||""));
      setChosen(old=>js.items.some(item=>item.id===old)?old:(js.items[0]?.id||""));
    }catch(e){setError((e as Error).message);}
  },[]);

  const refreshOptions=useCallback(async()=>{
    if(!dataset){setOptions(null);return;}
    try {
      const next=await api<ManualOptions>("/labeling/manual/options?dataset_id="+encodeURIComponent(dataset));
      setOptions(next);
      setTeamId(old=>next.teams.some(t=>t.id===old)?old:(next.teams[0]?.id||""));
    }catch(e){setError((e as Error).message);}
  },[dataset]);
  useEffect(()=>{void refresh();},[refresh]);
  useEffect(()=>{
    void api<{items:AnnotationClass[]}>("/labeling/annotation-classes")
      .then(response=>setAnnotationClasses(response.items))
      .catch(error=>setError((error as Error).message));
  },[]);
  useEffect(()=>{setDrafts([]);void refreshOptions();},[refreshOptions]);
  useEffect(()=>{
    if(!chosen){setMedia(null);return;}
    void api<MediaPage>("/labeling/jobs/"+chosen+"/media?page="+page+"&size=50")
      .then(setMedia).catch(e=>setError((e as Error).message));
  },[chosen,page]);
  const currentTeam=options?.teams.find(t=>t.id===teamId);
  useEffect(()=>{
    setPersonId(old=>currentTeam?.members.some(m=>m.id===old)?old:
      (currentTeam?.members[0]?.id||""));
  },[currentTeam]);
  const availableVideos=useMemo(()=>{
    const occupied=new Set((options?.existing||[]).filter(x=>x.team_id===teamId)
      .map(x=>x.video_group));
    for(const draft of drafts)if(draft.team_id===teamId)occupied.add(draft.video_group);
    return (options?.videos||[]).filter(video=>!occupied.has(video.video_group));
  },[options,teamId,drafts]);
  useEffect(()=>{
    setVideoGroup(old=>availableVideos.some(v=>v.video_group===old)?old:
      (availableVideos[0]?.video_group||""));
  },[availableVideos]);

  async function generate(){
    setBusy(true);setError("");setNotice("");
    try {
      const response=await api<Created>("/labeling/plans","POST",{
        dataset_id:dataset,jobs_per_team:jobsPerTeam,redundancy,
        video_limit:videoLimit.trim()?Number(videoLimit):null,
      },auth.csrf_token);
      setCreated(response);
      setNotice(response.job_count+" individual jobs planned. Nothing was published yet.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function approve(plan:Plan){
    if(!window.confirm("Approve this automatic distribution? Approval does not publish jobs."))return;
    setBusy(true);setError("");setNotice("");
    try {
      await api("/labeling/plans/"+plan.id+"/approve","POST",undefined,auth.csrf_token);
      setNotice("Plan approved. Launch it to release one video to each available annotator.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function nativeAction(endpoint:string,description:string,body?:unknown){
    if(!window.confirm(description))return;
    setBusy(true);setError("");setNotice("");
    try {
      const result=await api<{status?:string;pending_operations?:number}>(
        endpoint,"POST",body,auth.csrf_token);
      setNotice(result.pending_operations!==undefined?
        result.pending_operations+" first jobs queued for the Supervisely worker.":
        "Dispatch updated. Check worker status below.");
      await refresh();await refreshOptions();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  function addDraft(){
    if(!currentTeam||!personId||!videoGroup)return;
    if(!currentTeam.members.some(m=>m.id===personId))return;
    setDrafts(old=>[...old,{team_id:teamId,assignee_person_id:personId,video_group:videoGroup}]);
    setError("");
  }
  async function saveManual(){
    if(!drafts.length)return;
    if(dispatch==="publish"&&!window.confirm(
      "Queue "+drafts.length+" job(s) for the live Supervisely worker? "+
      "Every row is one video assigned to one person."))return;
    setBusy(true);setError("");setNotice("");
    try {
      const result=await api<{count:number;message:string}>("/labeling/manual/jobs","POST",{
        dataset_id:dataset,assignments:drafts,dispatch,
      },auth.csrf_token);
      setDrafts([]);
      setNotice(result.count+" video job(s) created. "+result.message);
      setSection("overview");
      await refresh();await refreshOptions();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  const filteredJobs=jobs.filter(job=>filter==="all"||
    (filter==="review"&&job.status==="on_review")||
    (filter==="active"&&["queued","publishing","in_progress"].includes(job.status)&&job.release_mode!=="manual_hold")||
    (filter==="held"&&job.release_mode==="manual_hold"));
  const selectedJob=jobs.find(job=>job.id===chosen);
  const awaiting=jobs.filter(j=>j.status==="on_review").length;
  const active=jobs.filter(j=>["queued","publishing","in_progress"].includes(j.status)).length;
  const complete=jobs.filter(j=>j.status==="completed").length;

  return <div className="page-body">
    <div className="page-heading"><div>
      <span className="eyebrow">ANNOTATIATHON · JOB OPERATIONS</span>
      <h2>Competition & Jobs</h2>
      <p className="muted">Create manual assignments, manage the publishing queue and configure optional automatic distribution in separate workspaces.</p>
    </div><button className="btn secondary" onClick={()=>{void refresh();void refreshOptions();}}>
      <RefreshCw size={15}/> Refresh status</button></div>
    {error&&<div role="alert" className="alert danger">{error}</div>}
    {notice&&<div role="status" className="alert success">{notice}</div>}
    <nav className="workspace-subtabs" aria-label="Competition and jobs sections">
      <button className={section==="overview"?"active":""} onClick={()=>setSection("overview")}>
        Jobs & worker queue</button>
      {admin&&<button className={section==="manual"?"active":""} onClick={()=>setSection("manual")}>
        Create video jobs</button>}
      {admin&&<button className={section==="automatic"?"active":""} onClick={()=>setSection("automatic")}>
        Automatic distribution</button>}
    </nav>
    {section==="overview"&&<div className="stat-grid compact">
      <div className="stat-card"><Layers3 size={19}/><span className="muted small">In queue / labeling</span><strong>{active}</strong></div>
      <div className="stat-card"><ClipboardCheck size={19}/><span className="muted small">Awaiting review</span><strong>{awaiting}</strong></div>
      <div className="stat-card"><CheckCircle2 size={19}/><span className="muted small">Accepted</span><strong>{complete}</strong></div>
    </div>}

    {admin&&section==="manual"&&<section className="panel manual-job-panel">
      <div className="panel-heading">
        <div><span className="eyebrow">DURING THE EVENT · MANUAL DISPATCH</span>
          <h3>Assign a video to an annotator</h3>
          <p>Build one or more jobs for existing teams. Each row becomes a separate Supervisely labeling job for the selected person, with one complete video. No new event is created.</p></div>
        <Video size={25} className="faint-icon"/>
      </div>
      <details className="annotation-palette">
        <summary>Supervisely annotation tools · 1 bird bounding box + 8 keypoint classes</summary>
        <p className="muted small">These classes are configured automatically on the Supervisely project before new jobs are published. Vivid points mean visible landmarks; muted points mean occluded landmarks.</p>
        <div className="annotation-class-grid">
          {annotationClasses.map(cls=><div className="annotation-class" key={cls.name}>
            <span className={"annotation-swatch "+(cls.shape==="rectangle"?"rectangle":"point")}
              style={{backgroundColor:cls.shape==="point"?cls.color:"transparent",
                borderColor:cls.color}}/>
            <div><strong>{cls.name}</strong><small>{cls.description}</small></div>
          </div>)}
        </div>
      </details>
      <div className="form-grid triple">
        <label>Source collection<select value={dataset} onChange={e=>setDataset(e.target.value)}>
          <option value="">Select collection…</option>
          {datasets.map(ds=><option key={ds.id} value={ds.id}>{ds.name} · {ds.video_count} videos</option>)}
        </select></label>
        <label>Competition team<select value={teamId} onChange={e=>setTeamId(e.target.value)}>
          {!options?.teams.length&&<option value="">No eligible teams</option>}
          {options?.teams.map(team=><option key={team.id} value={team.id}>{team.name}</option>)}
        </select></label>
        <label>Individual annotator<select value={personId} onChange={e=>setPersonId(e.target.value)}>
          {currentTeam?.members.map(person=><option key={person.id} value={person.id}>
            {person.name}{person.native_ready?"":" · Not linked"}
          </option>)}
        </select></label>
      </div>
      <div className="manual-video-row">
        <label>Video (always exactly one per job)<select value={videoGroup}
          onChange={e=>setVideoGroup(e.target.value)}>
          {!availableVideos.length&&<option value="">No unassigned videos for this team</option>}
          {availableVideos.map(video=><option key={video.video_group} value={video.video_group}>
            {video.video_group} · {video.frames} frames
          </option>)}
        </select></label>
        <button className="btn secondary" disabled={!videoGroup||!personId||busy}
          onClick={addDraft}><Plus size={15}/> Add assignment</button>
      </div>
      <p className="muted small">An assigned video cannot be selected twice for the same team. Other teams may receive the same video independently for cross-team comparison.</p>
      {drafts.length>0&&<div className="manual-draft">
        <div className="panel-heading"><h4>Assignments to create ({drafts.length})</h4>
          <button className="btn secondary" onClick={()=>setDrafts([])} disabled={busy}>Clear all</button></div>
        {drafts.map((item,index)=><div className="job-plan-row" key={index}>
          <div><strong>{options?.teams.find(t=>t.id===item.team_id)?.name} · {
            options?.teams.find(t=>t.id===item.team_id)?.members.find(m=>m.id===item.assignee_person_id)?.name}</strong>
            <span>{item.video_group}</span></div>
          <button className="btn secondary" aria-label="Remove assignment" disabled={busy}
            onClick={()=>setDrafts(old=>old.filter((_,i)=>i!==index))}><Trash2 size={14}/></button>
        </div>)}
        <div className="manual-dispatch-choice">
          <label><input type="radio" checked={dispatch==="publish"}
            onChange={()=>setDispatch("publish")}/>
            <span><strong>Publish now</strong><small>Queue for worker; Supervisely creates the job.</small></span></label>
          <label><input type="radio" checked={dispatch==="hold"}
            onChange={()=>setDispatch("hold")}/>
            <span><strong>Save for later</strong><small>Keep in Nightjar; publish manually when ready.</small></span></label>
        </div>
        <button className="btn primary" disabled={busy} onClick={()=>void saveManual()}>
          <Send size={15}/>{dispatch==="publish"?"Queue jobs for Supervisely":"Save held jobs"}</button>
      </div>}
    </section>}

    {section==="overview"&&<>
    <div className="content-grid competition-layout">
      <section className="panel">
        <span className="eyebrow">JOB INVENTORY</span><h3>Individual assignments</h3>
        <div className="filter-bar manual-filter">
          <label>Show<select value={filter} onChange={e=>setFilter(e.target.value as typeof filter)}>
            <option value="all">All jobs</option><option value="review">Awaiting review</option>
            <option value="active">In queue / labeling</option><option value="held">Saved for later</option>
          </select></label>
        </div>
        <div className="job-list">{filteredJobs.map(job=><button key={job.id}
          onClick={()=>{setChosen(job.id);setPage(1);}}
          className={"job-list-row "+(chosen===job.id?"selected":"")}>
          <div><strong>{job.team_name} · {job.assignee_name||"Legacy shared assignment"}</strong>
            <span>{job.video_count===1?"1 video":job.video_count+" videos"} · {job.frame_count} frames · #{job.sequence}</span>
          </div><span className={"dataset-status "+job.status}>{job.release_mode==="manual_hold"?
            "held":job.status.replaceAll("_"," ")}</span>
        </button>)}
        {!filteredJobs.length&&<p className="muted small">No matching jobs.</p>}</div>
      </section>
      <section className="panel">
        <span className="eyebrow">SELECTED ASSIGNMENT</span>
        <h3>{selectedJob?selectedJob.team_name+" · "+(selectedJob.assignee_name||"Historical pair job"):"Select a job"}</h3>
        {selectedJob&&<>
          <div className="job-detail-grid">
            <div><span>Status</span><strong>{selectedJob.release_mode==="manual_hold"?"Saved, not published":selectedJob.status.replaceAll("_"," ")}</strong></div>
            <div><span>Media</span><strong>{selectedJob.video_count} video(s) · {selectedJob.frame_count} frames</strong></div>
            <div><span>Monitor decision</span><strong>{selectedJob.review_status.replaceAll("_"," ")}</strong></div>
          </div>
          {selectedJob.native_submissions?.map(binding=><div className="job-plan-row" key={binding.native_job_id}>
            <div><strong>{binding.person_name}</strong><span>Supervisely job #{binding.native_job_id}</span></div>
            <span className={"dataset-status "+binding.native_status}>
              {binding.native_status.replaceAll("_"," ")}</span>
          </div>)}
          {!selectedJob.native_submissions?.length&&<p className="muted small">No native job yet. If published, the worker will create it.</p>}
          {admin&&selectedJob.release_mode==="manual_hold"&&<button className="btn primary"
            disabled={busy} onClick={()=>void nativeAction(
              "/labeling/manual/jobs/"+selectedJob.id+"/publish",
              "Publish this saved video assignment to Supervisely?")}>
            <Send size={15}/> Publish selected job</button>}
        </>}
        <div className="native-operations">
          <h4>Supervisely synchronization</h4>
          <p className="muted small">The live Docker worker performs uploads and checks submitted jobs every 12 seconds. Job creation and submission updates are not performed by this browser.</p>
          <div className="morph-counts">
            <span>Native jobs: {native?.native_bindings??0}</span>
            <span>Last poll: {native?.last_sync?new Date(native.last_sync*1000).toLocaleString():"Not yet"}</span>
            {Object.entries(native?.bindings_by_status||{}).map(([status,n])=>
              <span key={status}>{status}: {n}</span>)}
          </div>
          {native?.operations.filter(op=>["attention","blocked"].includes(op.status))
            .map(op=><div className="job-plan-row" key={op.id}>
              <div><strong>Needs attention: {op.error_code||"Remote uncertainty"}</strong>
                <span>Job {op.target_id} · {op.attempts} attempts</span></div>
              {admin&&<button className="btn secondary" disabled={busy}
                onClick={()=>void nativeAction("/native/operations/"+op.id+"/retry",
                  "Retry after verifying that no duplicate Supervisely job was created.")}>
                <RefreshCw size={14}/> Retry</button>}
            </div>)}
        </div>
      </section>
    </div>

    {selectedJob&&media&&<section className="panel" style={{marginTop:20}}>
      <div className="panel-heading"><div><span className="eyebrow">ASSIGNED SOURCE VIDEO</span>
        <h3>{media.items[0]?.video_group||"Standalone images"}</h3>
        <p>{media.total} frames. The worker uploads them together to one native dataset for this individual job.</p>
      </div></div>
      <div className="table-scroll"><table className="data-table"><thead><tr>
        <th>Frame source</th><th>Morphology</th><th>Video</th>
      </tr></thead><tbody>{media.items.map(item=><tr key={item.id}>
        <td>{item.relative_path}</td><td>{item.morphology}</td>
        <td>{item.video_group||"Still image"}</td>
      </tr>)}</tbody></table></div>
      <div className="table-pagination"><span>Page {page} of {Math.max(1,Math.ceil(media.total/50))}</span>
        <button className="btn secondary" disabled={page<=1} onClick={()=>setPage(n=>n-1)}>Previous</button>
        <button className="btn secondary" disabled={page*50>=media.total} onClick={()=>setPage(n=>n+1)}>Next</button></div>
    </section>}

    </>}
    {admin&&section==="automatic"&&<section className="panel manual-automatic-panel">
      <div className="panel-heading"><div><span className="eyebrow">AUTOMATIC PLANNING</span>
        <h3>Generate jobs from a video collection</h3>
        <p>Automatically distribute whole videos across eligible teams, with one job per person and video.</p>
      </div></div>

      <p className="muted small">Each job contains exactly one entire video. <strong>Maximum jobs per team</strong> limits how many distinct video jobs a team receives. <strong>Different teams per video</strong> controls cross-team redundancy; it never creates duplicate copies for the two members of the same team. Jobs alternate between the two annotators. <strong>Maximum videos per team</strong> is an optional cap. Creating a plan is a draft: only approval and launch send it to the worker.</p>
      <div className="form-grid triple">
        <label>Maximum jobs per team<input type="number" min={1} max={100}
          value={jobsPerTeam} onChange={e=>setJobsPerTeam(Number(e.target.value))}/></label>
        <label>Different teams per video<input type="number" min={1} max={100}
          value={redundancy} onChange={e=>setRedundancy(Number(e.target.value))}/></label>
        <label>Maximum videos per team (optional)<input type="number" min={0}
          value={videoLimit} placeholder="Unlimited"
          onChange={e=>setVideoLimit(e.target.value)}/></label>
      </div>
      <button className="btn secondary" disabled={busy||!dataset||plans.some(p=>p.status==="approved")}
        onClick={()=>void generate()}><UsersRound size={15}/> Generate draft plan</button>
      {created&&<div className="job-plan-summary"><strong>{created.job_count} jobs created in a draft</strong>
        <p>{created.assets} source frames · {created.min_frames_per_team}–{created.max_frames_per_team} frames per team</p></div>}
      <div className="job-plan-history"><h4>Plans in this event</h4>
        {plans.map(plan=><div className="job-plan-row" key={plan.id}>
          <div><strong>{plan.jobs_per_team===0?"Manual assignments":"Automatic plan"} · {plan.status}</strong>
            <span>{plan.jobs_per_team===0?"Jobs added as needed":
              plan.jobs_per_team+" maximum jobs/team · "+plan.redundancy+"× different-team coverage"}</span></div>
          {plan.status==="planned"&&<button className="btn secondary" disabled={busy}
            onClick={()=>void approve(plan)}><ClipboardCheck size={15}/> Approve</button>}
          {plan.status==="approved"&&<button className="btn primary" disabled={busy}
            onClick={()=>void nativeAction("/native/launch",
              "Launch this plan? The worker creates one native job per individual annotator.",
              {plan_id:plan.id})}><PlayCircle size={15}/> Launch plan</button>}
          {plan.status==="running"&&plan.jobs_per_team!==0&&<button className="btn secondary"
            disabled={busy} onClick={()=>void nativeAction(
              "/native/plans/"+plan.id+"/pause","Pause automatic releases?")}>Pause releases</button>}
          {plan.status==="paused"&&<button className="btn primary" disabled={busy}
            onClick={()=>void nativeAction(
              "/native/plans/"+plan.id+"/resume","Resume automatic releases?")}>Resume</button>}
        </div>)}
      </div>
    </section>}
  </div>;
}
