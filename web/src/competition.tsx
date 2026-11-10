import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2, ClipboardCheck, Layers3, PlayCircle, RefreshCw, UsersRound
} from "lucide-react";
import { api, type Auth } from "./api";

type Dataset={id:string;name:string;status:string;image_count:number;
  frame_count:number;video_count:number};
type Plan={id:string;dataset_id:string;status:string;jobs_per_team:number;
  redundancy:number;video_limit:number|null;approved_at:number|null};
type Created={plan_id:string;status:string;assets:number;assignment_copies:number;
  team_counts:Record<string,number>;video_counts:Record<string,number>;
  job_count:number;min_frames_per_team:number;max_frames_per_team:number;
  morphologies:string[]};
type Job={id:string;plan_id:string;team_id:string;team_name:string;
  sequence:number;frame_count:number;video_count:number;
  morphology_counts:Record<string,number>;status:string;review_status:string;
  assigned_monitor_id:string|null;started_at:number|null;submitted_at:number|null};
type Media={id:string;relative_path:string;morphology:string;video_group:string|null;native_image_id:number|null};
type MediaPage={items:Media[];total:number;page:number;size:number};

export function CompetitionScreen({auth}:{auth:Auth}) {
  const admin=auth.staff.roles.includes("organiser");
  const [datasets,setDatasets]=useState<Dataset[]>([]);
  const [dataset,setDataset]=useState("");
  const [plans,setPlans]=useState<Plan[]>([]);
  const [jobs,setJobs]=useState<Job[]>([]);
  const [chosen,setChosen]=useState("");
  const [media,setMedia]=useState<MediaPage|null>(null);
  const [page,setPage]=useState(1);
  const [redundancy,setRedundancy]=useState(3);
  const [jobsPerTeam,setJobsPerTeam]=useState(4);
  const [videoLimit,setVideoLimit]=useState("");
  const [created,setCreated]=useState<Created|null>(null);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [busy,setBusy]=useState(false);
  const refresh=useCallback(async()=>{
    try{
      const [ds,ps,js]=await Promise.all([
        api<{items:Dataset[]}>("/datasets"),
        api<{items:Plan[]}>("/labeling/plans"),
        api<{items:Job[]}>("/labeling/jobs?limit=2000"),
      ]);
      setDatasets(ds.items.filter(d=>d.status==="ready"));setPlans(ps.items);setJobs(js.items);
      setDataset(old=>old||ds.items.find(d=>d.status==="ready")?.id||"");
      setChosen(old=>js.items.some(j=>j.id===old)?old:(js.items[0]?.id||""));
      setError("");
    }catch(e){setError((e as Error).message);}
  },[]);
  useEffect(()=>{void refresh();},[refresh]);
  useEffect(()=>{
    if(!chosen){setMedia(null);return;}
    void api<MediaPage>("/labeling/jobs/"+chosen+"/media?page="+page+"&size=50")
      .then(setMedia).catch(e=>setError((e as Error).message));
  },[chosen,page]);
  async function generate(){
    setBusy(true);setError("");setNotice("");
    try{
      const response=await api<Created>("/labeling/plans","POST",{
        dataset_id:dataset,jobs_per_team:jobsPerTeam,redundancy,
        video_limit:videoLimit.trim()?Number(videoLimit):null,
      },auth.csrf_token);
      setCreated(response);
      setNotice("A local plan with "+response.job_count+" team jobs was generated.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function approve(plan:Plan){
    if(!window.confirm(
      "Approve this event labeling plan? It locks the plan for operational use. "+
      "This does not publish it to Supervisely."
    ))return;
    setBusy(true);setError("");
    try{
      await api("/labeling/plans/"+plan.id+"/approve","POST",undefined,auth.csrf_token);
      setNotice("Labeling plan approved. Native dataset/job publishing remains a separate operation.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  const selectedJob=jobs.find(j=>j.id===chosen);
  const done=jobs.filter(j=>j.status==="completed").length;
  const started=jobs.filter(j=>["in_progress","on_review","needs_correction"].includes(j.status)).length;
  const queued=jobs.filter(j=>j.status==="queued").length;
  return <div className="page-body">
    <div className="page-heading"><div>
      <span className="eyebrow">ANNOTATIATHON · WORK ALLOCATION</span>
      <h2>Competition & Jobs</h2>
      <p className="muted">Balance unique images and intact video groups between pairs and plan repeat coverage for quality control.</p>
      </div><button className="btn secondary" onClick={()=>void refresh()}><RefreshCw size={15}/> Refresh</button></div>
    {error&&<div role="alert" className="alert danger">{error}</div>}
    {notice&&<div role="status" className="alert success">{notice}</div>}
    <div className="stat-grid compact">
      <div className="stat-card"><Layers3 size={19}/><span className="muted small">Queued jobs</span><strong>{queued}</strong></div>
      <div className="stat-card"><PlayCircle size={19}/><span className="muted small">In progress/review</span><strong>{started}</strong></div>
      <div className="stat-card"><CheckCircle2 size={19}/><span className="muted small">Completed</span><strong>{done}</strong></div>
    </div>
    <div className="content-grid competition-layout">
      {admin&&<section className="panel">
        <span className="eyebrow">ALLOCATE IMAGES TO TEAMS</span><h3>Generate a distribution plan</h3>
        <div className="form-stack">
          <label>Source dataset<select value={dataset} onChange={e=>setDataset(e.target.value)}>
            <option value="">Choose a ready dataset…</option>
            {datasets.map(ds=><option key={ds.id} value={ds.id}>
              {ds.name} ({ds.image_count+ds.frame_count} frames)
            </option>)}
          </select></label>
          <div className="form-grid"><label>Jobs per team<input type="number" min={1} max={100}
            value={jobsPerTeam} onChange={e=>setJobsPerTeam(Number(e.target.value))}/></label>
            <label>Copies across distinct teams<input type="number" min={1} max={100}
              value={redundancy} onChange={e=>setRedundancy(Number(e.target.value))}/></label></div>
          <label>Maximum video groups per team (optional)<input type="number" min={0}
            value={videoLimit} placeholder="Unlimited"
            onChange={e=>setVideoLimit(e.target.value)}/></label>
          <button className="btn primary" disabled={busy||!dataset||plans.some(p=>p.status==="approved")}
            onClick={()=>void generate()}><UsersRound size={16}/> Generate plan</button>
        </div>
        {created&&<div className="job-plan-summary">
          <strong>{created.job_count} jobs · {created.assignment_copies} asset assignments</strong>
          <p>{created.assets} original frames · Team load {created.min_frames_per_team}–{created.max_frames_per_team} frames</p>
          <p>{created.morphologies.length} morphology categories represented</p>
        </div>}
        <div className="job-plan-history"><h4>Existing plans</h4>
          {plans.map(plan=><div className="job-plan-row" key={plan.id}>
            <div><strong>{plan.status}</strong>
              <span>{plan.jobs_per_team} jobs/team · {plan.redundancy}× coverage</span></div>
            {admin&&plan.status==="planned"&&<button className="btn secondary" disabled={busy}
              onClick={()=>void approve(plan)}><ClipboardCheck size={15}/> Approve</button>}
          </div>)}
          {!plans.length&&<p className="muted small">No distribution plans yet.</p>}
        </div>
      </section>}
      <section className="panel">
        <span className="eyebrow">JOB INVENTORY</span><h3>Team workload</h3>
        <p className="muted small">Each Nightjar job represents a pair-level work unit. Native labeling jobs have independent collaboration semantics, which must be verified before live dispatch.</p>
        <div className="job-list">{jobs.map(job=><button key={job.id}
          onClick={()=>{setChosen(job.id);setPage(1);}}
          className={"job-list-row "+(chosen===job.id?"selected":"")}>
          <div><strong>{job.team_name} · Job {job.sequence}</strong>
            <span>{job.frame_count} frames · {job.video_count} videos</span></div>
          <span className={"dataset-status "+job.status}>{job.status.replaceAll("_"," ")}</span>
        </button>)}
        {!jobs.length&&<p className="muted small">Jobs will appear after creating a plan.</p>}</div>
      </section>
    </div>
    {selectedJob&&media&&<section className="panel" style={{marginTop:20}}>
      <span className="eyebrow">SELECTED PAIR JOB</span>
      <h3>{selectedJob.team_name} · Job {selectedJob.sequence}</h3>
      <p className="muted small">{media.total} assigned frames · {selectedJob.video_count} atomic video groups ·
        Review: {selectedJob.review_status}</p>
      <div className="morph-counts">{Object.entries(selectedJob.morphology_counts)
        .map(([name,total])=><span key={name}>{name}: {total}</span>)}</div>
      <div className="table-scroll"><table className="data-table"><thead><tr>
        <th>Source path</th><th>Morphology</th><th>Video group</th><th>Native ID</th>
      </tr></thead><tbody>{media.items.map(item=><tr key={item.id}>
        <td>{item.relative_path}</td><td>{item.morphology}</td>
        <td>{item.video_group||"Image"}</td><td>{item.native_image_id||"Not uploaded"}</td>
      </tr>)}</tbody></table></div>
      <div className="table-pagination"><span>Page {page} / {Math.max(1,Math.ceil(media.total/50))}</span>
        <button className="btn secondary" disabled={page<=1} onClick={()=>setPage(n=>n-1)}>Previous</button>
        <button className="btn secondary" disabled={page*50>=media.total} onClick={()=>setPage(n=>n+1)}>Next</button>
      </div>
    </section>}
  </div>;
}
