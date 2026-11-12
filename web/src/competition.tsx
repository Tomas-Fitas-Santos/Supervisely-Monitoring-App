import { confirmAction } from "./dialogs";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2, ClipboardCheck, Layers3, PlayCircle, RefreshCw,
  Send, Trash2, UsersRound, Video
} from "lucide-react";
import { api, refreshSuperviselyRoster, type Auth, type Team, type TeamResponse } from "./api";

type Dataset={id:string;name:string;status:string;image_count:number;
  frame_count:number;video_count:number};
type PlanMode="legacy"|"shared_30_v1";
type Plan={id:string;dataset_id:string;status:string;jobs_per_team:number;mode:PlanMode;
  redundancy:number;video_limit:number|null};
type Created={job_count:number;assets:number;min_frames_per_team:number;max_frames_per_team:number}
  |{primary_blocks:number;unique_frames:number;short_tails:number;repeat_frames:number;
    mixed_video_blocks:number;short_final_frames:number};
type Binding={person_id:string;person_name:string;native_job_id:number;
  native_status:string;last_synced_at:number|null};
type Job={id:string;plan_id:string;team_id:string;team_name:string;
  sequence:number;frame_count:number;video_count:number;status:string;
  review_status:string;correction_round:number;
  annotation_snapshot_round:number|null;annotation_snapshot_frames:number|null;
  structurally_accepted_frames:number;structurally_pending_frames:number;
  morphology_counts:Record<string,number>;
  assignee_person_id:string|null;assignee_name:string|null;release_mode:string;
  native_submissions:Binding[]};
type Media={id:string;relative_path:string;morphology:string;video_group:string|null};
type MediaPage={items:Media[];total:number;page:number;size:number};
type NativeOperation={id:string;target_id:string;kind:string;status:string;attempts:number;error_code:string|null};
type NativeStatus={operations:NativeOperation[];native_bindings:number;
  bindings_by_status:Record<string,number>;last_sync:number|null};
type TeamOption={id:string;name:string;members:{id:string;name:string;native_ready:boolean}[]};
type VideoOption={video_group:string;frames:number;morphology:string};
type BlockOption={video_group:string;block_ordinal:number;first_frame:number;
  last_frame:number;frames:number;morphology:string;reserved:boolean};
type ManualOptions={teams:TeamOption[];videos:VideoOption[];blocks:BlockOption[];
  existing:{team_id:string;video_group:string;assignee_person_id:string|null;
    job_id:string;status:string}[]};
type Draft={team_id:string;assignee_person_id:string;video_group:string;block_ordinal:number};
type AnnotationClass={name:string;shape:"rectangle"|"point";color:string;description:string};
type AssigneeOption={id:string;name:string;team_name:string;team_id:string;native_ready:boolean};
type MirrorStatus={state:string;uploaded_frames:number;total_frames:number;
  ready_for_automatic_jobs:boolean;error_code:string|null};
type SharedReadiness={dataset_id:string;source_ready:boolean;mirror_ready:boolean;
  ready_for_distribution:boolean;source_frames:number;source_videos:number;
  primary_jobs:number;issues:string[];extra_issues:number};

export function CompetitionScreen({auth,view="progress"}:{
  auth:Auth;view?:"progress"|"setup"
}) {
  const admin=auth.staff.roles.includes("organiser");
  const [section,setSection]=useState<"overview"|"manual"|"automatic">(
    view==="setup"?"manual":"overview"
  );
  const [datasets,setDatasets]=useState<Dataset[]>([]);
  const [dataset,setDataset]=useState("");
  const [plans,setPlans]=useState<Plan[]>([]);
  const [jobs,setJobs]=useState<Job[]>([]);
  const [attendanceTeams,setAttendanceTeams]=useState<Team[]>([]);
  const [chosen,setChosen]=useState("");
  const [media,setMedia]=useState<MediaPage|null>(null);
  const [page,setPage]=useState(1);
  const [jobsPerTeam,setJobsPerTeam]=useState(4);
  const [planMode,setPlanMode]=useState<PlanMode>("shared_30_v1");
  const [videoLimit,setVideoLimit]=useState("");
  const [created,setCreated]=useState<Created|null>(null);
  const [native,setNative]=useState<NativeStatus|null>(null);
  const [annotationClasses,setAnnotationClasses]=useState<AnnotationClass[]>([]);
  const [options,setOptions]=useState<ManualOptions|null>(null);
  const [teamId,setTeamId]=useState("");
  const [allowDuplicates,setAllowDuplicates]=useState(false);
  const [highPriority,setHighPriority]=useState(false);
  const [assignees,setAssignees]=useState<AssigneeOption[]>([]);
  const [reassignTo,setReassignTo]=useState("");
  const [mirror,setMirror]=useState<MirrorStatus|null>(null);
  const [readiness,setReadiness]=useState<SharedReadiness|null>(null);
  const [readinessError,setReadinessError]=useState("");
  const [drafts,setDrafts]=useState<Draft[]>([]);
  const [dispatch,setDispatch]=useState<"hold"|"publish">("publish");
  const [filter,setFilter]=useState<"all"|"review"|"active"|"held">("all");
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [busy,setBusy]=useState(false);

  const refresh=useCallback(async()=>{
    try {
      const [ds,ps,js,ns,ts]=await Promise.all([
        api<{items:Dataset[]}>("/datasets"),
        api<{items:Plan[]}>("/labeling/plans"),
        api<{items:Job[]}>("/labeling/jobs?limit=2000"),
        api<NativeStatus>("/native/overview"),
        api<TeamResponse>("/teams"),
      ]);
      setAttendanceTeams(ts.teams.filter(team=>team.type==="competition"));
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
    const timer=window.setInterval(()=>{if(!document.hidden)void refresh();},5000);
    return ()=>window.clearInterval(timer);
  },[refresh]);
  const syncAssignees=useCallback(async()=>{
    setBusy(true);setError("");
    try {
      const result=await refreshSuperviselyRoster(auth.csrf_token,true);
      const [assigneeData]=await Promise.all([
        api<{items:AssigneeOption[]}>("/labeling/manual/assignees"),
        refreshOptions(),
      ]);
      setAssignees(assigneeData.items);
      if(result?.status==="needs_review"){
        setNotice("Some Supervisely identities need manual matching in People & Teams → Membership sync.");
      } else {
        setNotice("Supervisely team accounts refreshed. "+
          (result?.updated??0)+" identities updated; "+
          (result?.unmatched_native??0)+" still require review.");
      }
    }catch(e){setError("Could not refresh Supervisely team members: "+(e as Error).message);}
    finally{setBusy(false);}
  },[auth.csrf_token,refreshOptions]);
  useEffect(()=>{
    if(section==="manual"){void syncAssignees();}
  },[section]); // Reconcile accepted invitations when entering manual assignment.

  useEffect(()=>{
    void api<{items:AssigneeOption[]}>("/labeling/manual/assignees")
      .then(result=>setAssignees(result.items)).catch(()=>undefined);
  },[]);
  useEffect(()=>{
    if(!dataset){setMirror(null);return;}
    void api<MirrorStatus>("/datasets/"+dataset+"/mirror-status")
      .then(setMirror).catch(()=>setMirror(null));
  },[dataset,native]);
  useEffect(()=>{
    if(!dataset||planMode!=="shared_30_v1"||section!=="automatic"){
      setReadiness(null);setReadinessError("");return;
    }
    let active=true;
    setReadiness(null);setReadinessError("");
    void api<SharedReadiness>("/labeling/datasets/"+dataset+"/shared-readiness")
      .then(result=>{if(active)setReadiness(result);})
      .catch(e=>{if(active)setReadinessError((e as Error).message);});
    return ()=>{active=false;};
  },[dataset,planMode,section]);
  async function checkReadiness(){
    if(!dataset)return;
    try {
      setReadinessError("");
      const result=await api<SharedReadiness>(
        "/labeling/datasets/"+dataset+"/shared-readiness");
      setReadiness(result);
    }catch(e){setReadinessError((e as Error).message);}
  }
  useEffect(()=>{
    void api<{items:AnnotationClass[]}>("/labeling/annotation-classes")
      .then(response=>setAnnotationClasses(response.items))
      .catch(error=>setError((error as Error).message));
  },[]);
  useEffect(()=>{setDrafts([]);void refreshOptions();},[refreshOptions]);
  useEffect(()=>setCreated(null),[dataset,planMode]);
  useEffect(()=>{
    if(!chosen){setMedia(null);return;}
    void api<MediaPage>("/labeling/jobs/"+chosen+"/media?page="+page+"&size=50")
      .then(setMedia).catch(e=>setError((e as Error).message));
  },[chosen,page]);
  const currentTeam=options?.teams.find(t=>t.id===teamId);
  useEffect(()=>setReassignTo(""),[chosen]);
  const selectedForTeam=drafts.filter(x=>x.team_id===teamId);
  const availableBlocks=useMemo(()=>{
    const claimed=drafts.map(d=>d.video_group+":"+d.block_ordinal);
    return (options?.blocks||[]).filter(block=>{
      const key=block.video_group+":"+block.block_ordinal;
      return allowDuplicates||!block.reserved&&!claimed.includes(key)||
        drafts.some(d=>d.team_id===teamId&&key===d.video_group+":"+d.block_ordinal);
    });
  },[options,allowDuplicates,drafts,teamId]);
  function toggleVideo(group:string,ordinal:number){
    setDrafts(prev=>prev.some(row=>row.team_id===teamId&&row.video_group===group&&
      row.block_ordinal===ordinal)?
      prev.filter(row=>!(row.team_id===teamId&&row.video_group===group&&row.block_ordinal===ordinal)):
      [...prev,{team_id:teamId,video_group:group,block_ordinal:ordinal,assignee_person_id:""}]);
  }
  function changeDraftAssignee(index:number,personId:string){
    setDrafts(old=>old.map((row,i)=>i===index?
      {...row,assignee_person_id:personId}:row));
  }

  async function generate(){
    setBusy(true);setError("");setNotice("");
    try {
      const response=await api<Created>("/labeling/plans","POST",planMode==="shared_30_v1"?{
        dataset_id:dataset,mode:planMode,
      }:{
        dataset_id:dataset,jobs_per_team:jobsPerTeam,redundancy:1,
        video_limit:videoLimit.trim()?Number(videoLimit):null,
      },auth.csrf_token);
      setCreated(response);
      setNotice("primary_blocks" in response?
        response.primary_blocks+" primary blocks planned. Nothing was published yet.":
        response.job_count+" individual jobs planned. Nothing was published yet.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function approve(plan:Plan){
    if(!await confirmAction("Approve this automatic distribution? Approval does not publish jobs."))return;
    setBusy(true);setError("");setNotice("");
    try {
      await api("/labeling/plans/"+plan.id+"/approve","POST",undefined,auth.csrf_token);
      setNotice(plan.mode==="shared_30_v1"?
        "Shared queue approved. Launch it to let the worker assign available blocks.":
        "Plan approved. Launch it to release one video to each available annotator.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function nativeAction(endpoint:string,description:string,body?:unknown){
    if(!await confirmAction(description))return;
    setBusy(true);setError("");setNotice("");
    try {
      const result=await api<{status?:string;pending_operations?:number;note?:string}>(
        endpoint,"POST",body,auth.csrf_token);
      setNotice(result.note??(result.pending_operations!==undefined?
        result.pending_operations+" first jobs queued for the Supervisely worker.":
        "Dispatch updated. Check worker status below."));
      await refresh();await refreshOptions();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function saveManual(){
    if(!drafts.length)return;
    if(drafts.some(item=>!item.assignee_person_id)){
      setError("Select an annotator for each chosen 30-frame segment before creating jobs.");return;
    }
    if(dispatch==="publish"&&!await confirmAction(
      "Queue "+drafts.length+" job(s) for the live Supervisely worker? "+
      "Every row is 30 consecutive frames of one video assigned to one person."))return;
    setBusy(true);setError("");setNotice("");
    try {
      const result=await api<{count:number;message:string}>("/labeling/manual/jobs","POST",{
        dataset_id:dataset,assignments:drafts,dispatch,
        high_priority:dispatch==="publish"&&highPriority,
        allow_duplicates:allowDuplicates,
      },auth.csrf_token);
      setDrafts([]);
      setNotice(result.count+" 30-frame job(s) created. "+result.message);
      setSection("overview");
      await refresh();await refreshOptions();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function reassignSelected(){
    if(!selectedJob||!reassignTo||busy)return;
    if(!await confirmAction("Reassign this job to the selected annotator? The recipient may belong to another team."))return;
    setBusy(true);setError("");setNotice("");
    try{
      const result=await api<{status:string}>(
        "/labeling/manual/jobs/"+selectedJob.id+"/reassign","POST",
        {person_id:reassignTo},auth.csrf_token);
      setNotice(result.status==="updated"?
        "Assignment changed in Nightjar before publishing.":
        "Supervisely reassignment queued. The worker will confirm before Nightjar changes the owner.");
      await refresh();
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
  const complete=jobs.filter(j=>j.status==="completed"||(
    j.assignee_person_id!==null&&
    ["shared_primary","shared_repeat","manual_released","manual_hold"].includes(j.release_mode)&&
    j.status==="on_review"&&j.annotation_snapshot_round===j.correction_round&&
    j.annotation_snapshot_frames===j.frame_count
  )).length;

  return <div className="page-body">
    <div className="page-heading"><div>
      <span className="eyebrow">{view==="setup"?"EVENT SETUP · JOB DISTRIBUTION":"EVENT OPERATIONS · JOB STATUS"}</span>
      <h2>{view==="setup"?"Job Distribution":"Job Progress"}</h2>
      <p className="muted">{view==="setup"?
        "Create and publish jobs using the manual or automatic distribution tools.":
        "Follow the publishing queue, annotation progress and submissions."}</p>
    </div><button className="btn secondary" onClick={()=>{void refresh();void refreshOptions();}}>
      <RefreshCw size={15}/> Refresh status</button></div>
    {error&&<div role="alert" className="alert danger">{error}</div>}
    {notice&&<div role="status" className="alert success">{notice}</div>}
    <nav className="workspace-subtabs" aria-label="Competition and jobs sections">
      <button className={section==="overview"?"active":""} onClick={()=>setSection("overview")}>
        Jobs & worker queue</button>
      {admin&&view==="setup"&&<button className={section==="manual"?"active":""} onClick={()=>setSection("manual")}>
        Create video jobs</button>}
      {admin&&view==="setup"&&<button className={section==="automatic"?"active":""} onClick={()=>setSection("automatic")}>
        Automatic distribution</button>}
    </nav>
    {section==="overview"&&<div className="stat-grid compact">
      <div className="stat-card"><Layers3 size={19}/><span className="muted small">In queue / labeling</span><strong>{active}</strong></div>
      <div className="stat-card"><ClipboardCheck size={19}/><span className="muted small">Submitted / checking</span><strong>{awaiting}</strong></div>
      <div className="stat-card"><CheckCircle2 size={19}/><span className="muted small">Complete submissions</span><strong>{complete}</strong></div>
    </div>}
    {section==="overview"&&<section className="panel">
      <div className="panel-heading"><div><span className="eyebrow">SHARED QUEUE · TEAM READINESS</span>
        <h3>Checked-in annotators</h3><p>Jobs start only after launch. Late arrivals appear on refresh and receive work while the queue is running.</p></div></div>
      {attendanceTeams.filter(t=>t.checked_in>0).map(team=><div key={team.id} className="job-plan-row">
        <strong>{team.name}</strong>
        {team.members.filter(person=>person.check_in_at).map(person=>{
          const assigned=jobs.filter(job=>job.assignee_person_id===person.id)
            .sort((a,b)=>a.sequence-b.sequence);
          const current=assigned.find(job=>["queued","publishing","in_progress","needs_correction"].includes(job.status));
          const verified=person.supervisely_user_id!==null&&person.membership_status==="joined"&&person.native_verified_at!==null;
          return <p key={person.id}>{person.name} · Supervisely {person.supervisely_user_id??"None"} · {verified?
            current?"Job "+(assigned.indexOf(current)+1)+" · "+current.status:
              assigned.length?assigned.length+" assigned · waiting for next job":"Ready · waiting for launch or block":
            "Supervisely membership not verified"}</p>;
        })}
      </div>)}
      {!attendanceTeams.some(t=>t.checked_in>0)&&<p className="muted">No participants checked in yet.</p>}
    </section>}

    {admin&&view==="setup"&&section==="manual"&&<section className="panel manual-job-panel">
      <button className="btn secondary" disabled={busy} onClick={()=>void syncAssignees()}>
        <RefreshCw size={15}/> Refresh Supervisely accounts</button>
      <div className="panel-heading">
        <div><span className="eyebrow">DURING THE EVENT · MANUAL DISPATCH</span>
          <h3>Assign a 30-frame segment to an annotator</h3>
          <p>Select consecutive 30-frame segments from one video for each job. Each part can go to a different team member.</p></div>
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
        <label>1 · Source dataset<select value={dataset} onChange={e=>setDataset(e.target.value)}>
          <option value="">Select source…</option>
          {datasets.map(ds=><option key={ds.id} value={ds.id}>{ds.name} · {ds.video_count} videos</option>)}
        </select></label>
        <label>1 · Select team<select value={teamId} onChange={e=>setTeamId(e.target.value)}>
          {!options?.teams.length&&<option value="">No eligible teams</option>}
          {options?.teams.map(team=><option key={team.id} value={team.id}>{team.name}</option>)}
        </select></label>
      </div>
      <h4>2 · Select video segments for {currentTeam?.name||"this team"}</h4>
      <p className="muted small">Each segment is one 30-frame job. Different teams can receive different parts of one video. Repeating exactly the same segment requires explicit permission.</p>
      <label className="manual-option-checkbox"><input type="checkbox" checked={allowDuplicates}
        onChange={e=>setAllowDuplicates(e.target.checked)}/> Allow duplicate video segments</label>
      <div className="manual-video-choices">
        {availableBlocks.map(block=><label key={block.video_group+":"+block.block_ordinal}
          className="manual-video-choice">
          <input type="checkbox" disabled={busy}
            checked={selectedForTeam.some(x=>x.video_group===block.video_group&&
              x.block_ordinal===block.block_ordinal)}
            onChange={()=>toggleVideo(block.video_group,block.block_ordinal)}/>
          <span><strong>{block.video_group} · Part {block.block_ordinal}</strong>
            <small>Frames {block.first_frame}–{block.last_frame} · {block.morphology}
              {block.reserved?" · Already assigned":""}</small></span>
        </label>)}
        {!availableBlocks.length&&<p className="muted small">
          No complete 30-frame segments are available. Check video lengths or allow duplicates.</p>}
      </div>
      {drafts.length>0&&<div className="manual-draft">
        <div className="panel-heading"><h4>3 · Assign each selected video to a person ({drafts.length})</h4>
          <button className="btn secondary" onClick={()=>setDrafts([])} disabled={busy}>Clear all</button></div>
        {drafts.map((item,index)=><div className="job-plan-row" key={item.team_id+item.video_group+item.block_ordinal+index}>
          <div><strong>{options?.teams.find(t=>t.id===item.team_id)?.name} · {item.video_group} · Part {item.block_ordinal}</strong>
            <label>Assigned annotator
              <select value={item.assignee_person_id} onChange={e=>changeDraftAssignee(index,e.target.value)}>
                <option value="">Choose annotator…</option>
                {options?.teams.find(t=>t.id===item.team_id)?.members.map(member=>
                  <option key={member.id} value={member.id}>{member.name}{member.native_ready?"":" · Not linked"}</option>)}
              </select>
            </label>
          </div>
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
        {dispatch==="publish"&&<label className="manual-option-checkbox">
          <input type="checkbox" checked={highPriority} onChange={e=>setHighPriority(e.target.checked)}/>
          Higher priority <span className="muted small">New jobs normally use lower priority. Enable this to publish before ordinary queued jobs.</span>
        </label>}
        <button className="btn primary" disabled={busy||drafts.some(x=>!x.assignee_person_id)}
          onClick={()=>void saveManual()}>
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
            {job.structurally_accepted_frames>0&&
              <span>{job.structurally_accepted_frames} structurally complete ·
                {" "+job.structurally_pending_frames} awaiting completion</span>}
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
            <div><span>Structurally complete</span><strong>
              {selectedJob.structurally_accepted_frames} / {selectedJob.frame_count} frames</strong></div>
            <div><span>Incomplete / awaiting submission</span><strong>
              {selectedJob.structurally_pending_frames} frames</strong></div>
            <div><span>Correction rounds</span><strong>
              {selectedJob.correction_round} · same Nightjar job</strong></div>
          </div>
          {selectedJob.review_status==="returned_incomplete"&&
            <p className="muted small">Complete images are already in the monitor review pool.
              The worker sends only the remaining images to the original annotator,
              with “Incomplete” in the Supervisely title. All rounds share this same competition job.</p>}
          {selectedJob.native_submissions?.map(binding=><div className="job-plan-row" key={binding.native_job_id}>
            <div><strong>{binding.person_name}</strong><span>Supervisely job #{binding.native_job_id}</span></div>
            <span className={"dataset-status "+binding.native_status}>
              {binding.native_status.replaceAll("_"," ")}</span>
          </div>)}
          {!selectedJob.native_submissions?.length&&<p className="muted small">No native job yet. If published, the worker will create it.</p>}
          {admin&&view==="setup"&&selectedJob.assignee_person_id&&
            !["on_review","completed","needs_correction"].includes(selectedJob.status)&&
            <div className="manual-reassignment">
              <h4>Change assigned annotator</h4>
              <p className="muted small">You may select a person from any active team. Published jobs are reassigned directly in Supervisely by the worker; no new job or video upload is created.</p>
              <select value={reassignTo} onChange={e=>setReassignTo(e.target.value)}>
                <option value="">Choose new annotator…</option>
                {assignees.filter(p=>p.native_ready&&p.id!==selectedJob.assignee_person_id).map(p=>
                  <option key={p.id} value={p.id}>{p.team_name} · {p.name}</option>)}
              </select>
              <button className="btn secondary" disabled={!reassignTo||busy}
                onClick={()=>void reassignSelected()}>Reassign job</button>
            </div>}
          {admin&&view==="setup"&&selectedJob.release_mode==="manual_hold"&&<button className="btn primary"
            disabled={busy} onClick={()=>void nativeAction(
              "/labeling/manual/jobs/"+selectedJob.id+"/publish",
              "Publish this saved video assignment to Supervisely?")}>
            <Send size={15}/> Publish selected job</button>}
        </>}
        <div className="native-operations">
          <h4>Supervisely synchronization</h4>
          <p className="muted small">Annotators submit jobs using Supervisely’s Submit/Finish workflow.
            The worker checks submitted annotation JSON frame by frame (one bird rectangle and
            exactly one visible or occluded point for each eye, beak and crown).
            Complete frames enter monitor review; the remaining images become an
            “Incomplete” correction job for the same annotator. Correction rounds do
            not create additional Nightjar competition jobs. The worker polls approximately every 12 seconds.</p>
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
      <div className="panel-heading"><div><span className="eyebrow">
        {selectedJob.video_count>1?"ASSIGNED SOURCE VIDEOS":"ASSIGNED SOURCE VIDEO"}</span>
        <h3>{[...new Set(media.items.map(item=>item.video_group).filter(Boolean))].join(" → ")||"Standalone images"}</h3>
        <p>{media.total} frames. Automatic frames were preloaded before the event. Manual duplicates use a separate Supervisely dataset.</p>
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
    {admin&&view==="setup"&&section==="automatic"&&<section className="panel manual-automatic-panel">
      <div className="panel-heading"><div><span className="eyebrow">AUTOMATIC PLANNING</span>
        <h3>Plan automatic assignments</h3>
      </div></div>

      <div className="form-grid">
        <label>Source dataset<select value={dataset} onChange={e=>setDataset(e.target.value)}>
          {!datasets.length&&<option value="">No ready datasets</option>}
          {datasets.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        <label>Distribution mode<select value={planMode} onChange={e=>setPlanMode(e.target.value as PlanMode)}>
          <option value="shared_30_v1">Shared 30-image queue</option>
          <option value="legacy">Whole videos</option>
        </select></label>
      </div>
      <p className="muted small">{planMode==="shared_30_v1"?
        "Allocate 30 frames in uploaded dataset order across the ordered videos. The uploaded frames in each video are sorted by frame number, even if sampled. Only the final dataset job may have fewer than 30 frames. The next job releases after a complete submission.":
        "Assign whole videos to eligible teams. Each video goes to one annotator across the event."}</p>
      {planMode==="legacy"&&<div className="form-grid triple">
        <label>Maximum jobs per team<input type="number" min={1} max={100}
          value={jobsPerTeam} onChange={e=>setJobsPerTeam(Number(e.target.value))}/></label>
        <label>Maximum videos per team (optional)<input type="number" min={0}
          value={videoLimit} placeholder="Unlimited"
          onChange={e=>setVideoLimit(e.target.value)}/></label>
      </div>}
      <p className="muted small">Supervisely mirror: {mirror?.state||"Unknown"} · {mirror?.uploaded_frames||0}/{mirror?.total_frames||0} frames uploaded. {mirror?.ready_for_automatic_jobs?"Upload verified.":"Wait until the complete dataset is mirrored before launching."}</p>
      {planMode==="shared_30_v1"&&dataset&&<div className="settings-note">
        <div className="panel-heading">
          <strong>Imported frame order</strong>
          <button className="btn secondary" type="button" disabled={busy}
            onClick={()=>void checkReadiness()}><RefreshCw size={14}/> Check imported frames</button>
        </div>
        {readinessError&&<p className="alert danger" role="alert">{readinessError}</p>}
        {!readiness&&!readinessError&&<p>Checking imported frame identities and order…</p>}
        {readiness&&<div>
          <p>{readiness.source_frames} frames · {readiness.source_videos} source videos ·
            {" "}{readiness.primary_jobs} primary blocks (up to 30 frames each).</p>
          <p>{readiness.source_ready?
            "Imported frame order verified (sampled source videos are supported).":
            "Automatic distribution is blocked until the imported-frame issues below are corrected."}
            {" "}{readiness.mirror_ready?
              "Supervisely upload is complete.":"Supervisely upload is not yet complete."}</p>
          {!readiness.source_ready&&<ul>
            {readiness.issues.map((issue,i)=><li key={i}>{issue}</li>)}
            {readiness.extra_issues>0&&<li>{readiness.extra_issues} other source issues</li>}
          </ul>}
        </div>}
      </div>}
      <button className="btn secondary" disabled={busy||!dataset||
        plans.some(p=>p.status==="approved")||
        (planMode==="shared_30_v1"&&Boolean(readiness&&!readiness.source_ready))}
        onClick={()=>void generate()}><UsersRound size={15}/>{planMode==="shared_30_v1"?"Create shared queue draft":"Generate draft plan"}</button>
      {created&&<div className="job-plan-summary">{"primary_blocks" in created?<>
        <strong>{created.primary_blocks} primary blocks planned</strong>
        <p>{created.unique_frames} unique source frames · {created.mixed_video_blocks} mixed-video jobs · {created.short_tails?created.short_final_frames+" frames in final short job":"all jobs contain 30 frames"} · {created.repeat_frames} repeat-eligible frames</p>
      </>:<>
        <strong>{created.job_count} jobs created in a draft</strong>
        <p>{created.assets} source frames · {created.min_frames_per_team}–{created.max_frames_per_team} frames per team</p>
      </>}</div>}
      <div className="job-plan-history"><h4>Plans in this event</h4>
        {plans.map(plan=><div className="job-plan-row" key={plan.id}>
          <div><strong>{plan.mode==="shared_30_v1"?"Shared 30-image queue":
            plan.jobs_per_team===0?"Manual assignments":"Whole-video plan"} · {plan.status}</strong>
            <span>{datasets.find(item=>item.id===plan.dataset_id)?.name||plan.dataset_id}</span>
            <span>{plan.mode==="shared_30_v1"?"30 frames per job; crosses video boundaries when necessary":plan.jobs_per_team===0?"Jobs added as needed":
              plan.jobs_per_team+" maximum jobs/team · "+(plan.redundancy===1?"No overlap":"Legacy overlap plan")}</span></div>
          {plan.status==="planned"&&<button className="btn secondary" disabled={busy}
            onClick={()=>void approve(plan)}><ClipboardCheck size={15}/> Approve</button>}
          {plan.status==="approved"&&<button className="btn primary" disabled={busy}
            onClick={()=>void nativeAction("/native/launch",
              plan.mode==="shared_30_v1"?
                "Launch this shared queue? The worker will assign available blocks to eligible annotators.":
                "Launch this plan? The worker creates one native job per individual annotator.",
              {plan_id:plan.id})}><PlayCircle size={15}/> Launch plan</button>}
          {plan.status==="running"&&(plan.mode==="shared_30_v1"||plan.jobs_per_team!==0)&&<button className="btn secondary"
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
