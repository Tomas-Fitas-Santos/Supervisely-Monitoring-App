import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2, ClipboardCheck, Image, RefreshCw, RotateCcw, ShieldCheck
} from "lucide-react";
import { api, type Auth } from "./api";

type Job={id:string;team_name:string;sequence:number;status:string;review_status:string;
  dataset_id:string;frame_count:number;video_count:number;assigned_monitor_id:string|null;
  correction_round:number;assignee_person_id:string|null;assignee_name:string|null;
  native_submissions:{person_id:string;person_name:string;native_job_id:number;
    native_status:string;last_synced_at:number|null}[];};
type Media={id:string;relative_path:string;morphology:string;video_group:string|null};
type MediaPage={items:Media[];total:number;page:number;size:number};
type Review={id:string;media_id:string;verdict:"good"|"bad"|"incomplete";
  correct_keypoints:number;missing_keypoints:number;incorrect_keypoints:number;
  visible_keypoints:number;occluded_keypoints:number;
  temporal_consistency:boolean|null;notes:string;keypoint_score:number;reviewed_at:number};
type AnnotationSnapshot={
  source_media_id:string;source_relative_path:string;source_sha256:string;
  native_dataset_id:number;native_image_id:number;annotation:Record<string,unknown>
};
type Reviews={count:number;keypoint_score:number;reviewed_frames:number;
  total_frames:number;items:Review[];status:string;review_status:string;
  annotation_snapshot_ready:boolean};

export function QualityScreen({auth}:{auth:Auth}) {
  const canReview=auth.staff.roles.some(role=>role==="organiser"||role==="monitor");
  const [jobs,setJobs]=useState<Job[]>([]);
  const [jobId,setJobId]=useState("");
  const [filter,setFilter]=useState<"review"|"all"|"active"|"completed">("review");
  const [media,setMedia]=useState<MediaPage|null>(null);
  const [reviews,setReviews]=useState<Reviews|null>(null);
  const [page,setPage]=useState(1);
  const [assetId,setAssetId]=useState("");
  const [annotation,setAnnotation]=useState<AnnotationSnapshot|null>(null);
  const [verdict,setVerdict]=useState<"good"|"bad"|"incomplete">("good");
  const [correct,setCorrect]=useState(0);
  const [missing,setMissing]=useState(0);
  const [incorrect,setIncorrect]=useState(0);
  const [visible,setVisible]=useState(0);
  const [occluded,setOccluded]=useState(0);
  const [temporal,setTemporal]=useState("unknown");
  const [notes,setNotes]=useState("");
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [busy,setBusy]=useState(false);
  const refresh=useCallback(async()=>{
    try{
      const response=await api<{items:Job[]}>("/labeling/jobs?limit=2000");
      setJobs(response.items);
      setJobId(current=>response.items.some(j=>j.id===current)?current:
        (response.items.find(j=>["submitted","on_review","needs_correction"].includes(j.status))?.id||response.items[0]?.id||""));
      setError("");
    }catch(e){setError((e as Error).message);}
  },[]);
  const loadReviews=useCallback(async()=>{
    if(!jobId){setReviews(null);setMedia(null);return;}
    try{
      const [r,m]=await Promise.all([
        api<Reviews>("/review/jobs/"+jobId),
        api<MediaPage>("/labeling/jobs/"+jobId+"/media?page="+page+"&size=50")
      ]);
      setReviews(r);setMedia(m);
      setAssetId(old=>m.items.some(x=>x.id===old)?old:(m.items[0]?.id||""));
    }catch(e){setError((e as Error).message);}
  },[jobId,page]);
  useEffect(()=>{void refresh();},[refresh]);
  useEffect(()=>{void loadReviews();},[loadReviews]);
  useEffect(()=>{
    if(!jobId)return;
    const timer=window.setInterval(()=>{
      if(!document.hidden)void loadReviews();
    },10000);
    return()=>window.clearInterval(timer);
  },[jobId,loadReviews]);
  useEffect(()=>{
    const timer=window.setInterval(()=>{if(!document.hidden)void refresh();},10000);
    return()=>window.clearInterval(timer);
  },[refresh]);
  const selected=jobs.find(j=>j.id===jobId);
  const reviewJobs=jobs.filter(j=>j.status==="on_review");
  const filteredJobs=jobs.filter(j=>filter==="all"||
    (filter==="review"&&j.status==="on_review")||
    (filter==="active"&&["queued","publishing","in_progress"].includes(j.status))||
    (filter==="completed"&&j.status==="completed"));
  const asset=media?.items.find(a=>a.id===assetId);
  const stored=reviews?.items.find(r=>r.media_id===assetId);
  useEffect(()=>{
    let valid=true;
    setAnnotation(null);
    if(jobId&&assetId){
      void api<AnnotationSnapshot>("/review/jobs/"+jobId+"/annotations/"+assetId)
        .then(result=>{if(valid)setAnnotation(result);})
        .catch(()=>{if(valid)setAnnotation(null);});
    }
    return()=>{valid=false;};
  },[jobId,assetId,selected?.correction_round,reviews?.annotation_snapshot_ready]);
  useEffect(()=>{
    setVerdict(stored?.verdict||"good");
    setCorrect(stored?.correct_keypoints||0);
    setMissing(stored?.missing_keypoints||0);
    setIncorrect(stored?.incorrect_keypoints||0);
    setVisible(stored?.visible_keypoints||0);
    setOccluded(stored?.occluded_keypoints||0);
    setTemporal(stored?.temporal_consistency===null||stored===undefined?
      "unknown":stored.temporal_consistency?"consistent":"inconsistent");
    setNotes(stored?.notes||"");
  },[assetId,stored?.id,stored?.reviewed_at]);
  const reviewable=selected&&["on_review","submitted","needs_correction"].includes(selected.status);
  async function saveFrame(){
    if(!selected||!asset)return;
    setBusy(true);setError("");setNotice("");
    try{
      await api("/review/jobs/"+selected.id+"/frames","PUT",{
        media_id:asset.id,verdict,correct_keypoints:correct,
        missing_keypoints:missing,incorrect_keypoints:incorrect,
        visible_keypoints:visible,occluded_keypoints:occluded,
        temporal_consistency:temporal==="unknown"?null:temporal==="consistent",notes,
      },auth.csrf_token);
      setNotice("Frame scoring saved.");await loadReviews();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function decide(choice:"accept"|"return_for_correction"){
    if(!selected)return;
    if(!window.confirm(choice==="accept"?
      "Accept this annotator's video job in Nightjar?":"Return this video job for correction in Nightjar?"))return;
    setBusy(true);setError("");setNotice("");
    try{
      await api("/review/jobs/"+selected.id+"/decision","POST",{
        decision:choice,notes,
      },auth.csrf_token);
      setNotice(choice==="accept"?
        "Video review accepted. The next job for this annotator may now be released.":
        "Video returned. The worker will prepare a correction job for the same annotator.");
      await refresh();await loadReviews();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  return <div className="page-body">
    <div className="page-heading"><div><span className="eyebrow">MONITORING · QUALITY CONTROL</span>
      <h2>Review & Scoring</h2>
      <p className="muted">Inspect each person's submitted video, synchronized annotations and frame-level quality. One native submission per new job.</p>
    </div><button className="btn secondary" onClick={()=>{void refresh();void loadReviews();}}>
      <RefreshCw size={15}/> Refresh jobs & annotations</button></div>
    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}
    <div className="content-grid reviews-layout">
      <section className="panel">
        <span className="eyebrow">SUBMISSIONS · {reviewJobs.length} AWAITING REVIEW</span>
        <h3>Choose an annotator's video</h3>
        <label className="review-filter">Show jobs<select value={filter}
          onChange={e=>setFilter(e.target.value as typeof filter)}>
          <option value="review">Ready for review ({reviewJobs.length})</option>
          <option value="active">Queue and in progress</option>
          <option value="completed">Accepted</option>
          <option value="all">Every job</option>
        </select></label>
        <div className="review-jobs">{filteredJobs.map(job=><button key={job.id}
          className={"job-list-row "+(job.id===jobId?"selected":"")}
          onClick={()=>{setJobId(job.id);setPage(1);setAssetId("");}}>
          <div><strong>{job.team_name} · {job.assignee_name||"Historical pair"}</strong>
            <span>{job.video_count} video(s) · {job.frame_count} frames · Job {job.sequence}</span>
          </div>
          <span className={"dataset-status "+job.status}>{job.status.replaceAll("_"," ")}</span>
        </button>)}
        {!filteredJobs.length&&<p className="muted small">No jobs in this category. Submitted jobs appear here after the worker synchronizes their native status.</p>}</div>
      </section>
      <section className="panel">
        <span className="eyebrow">SELECTED JOB · LIVE SUPERVISELY STATUS</span>
        <h3>{selected?selected.team_name+" · "+(selected.assignee_name||"Historical pair job"):"No job selected"}</h3>
        {selected&&<div className="submission-summary">
          <div className="review-status-strip">
            <div><span>Nightjar state</span><strong>{selected.status.replaceAll("_"," ")}</strong></div>
            <div><span>Annotation JSON</span><strong>{reviews?.annotation_snapshot_ready?
              "Synced to local storage":"Not received yet"}</strong></div>
            <div><span>Video frames</span><strong>{selected.frame_count}</strong></div>
          </div>
          {(selected.native_submissions||[]).map(member=><div className="job-plan-row"
            key={member.native_job_id}>
            <div><strong>{member.person_name}</strong>
              <span>Supervisely job #{member.native_job_id} · Updated {member.last_synced_at?
                new Date(member.last_synced_at*1000).toLocaleString():"not yet"}</span></div>
            <span className={"dataset-status "+member.native_status}>
              {member.native_status.replaceAll("_"," ")}</span>
          </div>)}
          {!selected.native_submissions?.length&&
            <p className="muted small">The worker has not yet published this job into Supervisely.</p>}
          {selected.status==="on_review"&&!reviews?.annotation_snapshot_ready&&
            <p className="advisory">Supervisely submission detected. Waiting for the worker to download the annotation JSON. The page checks again automatically.</p>}
          {selected.status==="needs_correction"&&
            <p className="muted small">Returned for correction · Round {selected.correction_round}. The worker will restart only this person's native job.</p>}
        </div>}
        {reviews&&<div className="review-stats">
          <span><strong>{reviews.reviewed_frames} / {reviews.total_frames}</strong> frames reviewed</span>
          <span><strong>{reviews.keypoint_score}</strong> keypoint score</span>
        </div>}
        <p className="muted small">Scoring: +1 correct keypoint, −1 missing keypoint, 0 incorrect. Occlusion and temporal consistency are recorded separately.</p>
        {reviewable&&canReview&&<div className="review-actions">
          <button className="btn secondary" disabled={busy||!reviews?.count||!reviews?.annotation_snapshot_ready}
            onClick={()=>void decide("return_for_correction")}><RotateCcw size={14}/> Return for correction</button>
          <button className="btn primary" disabled={busy||!reviews?.count||!reviews?.annotation_snapshot_ready||selected?.status!=="on_review"}
            onClick={()=>void decide("accept")}><CheckCircle2 size={14}/> Accept video</button>
        </div>}
        {!reviewable&&selected&&<p className="muted small">Review opens after the assigned participant submits this job in Supervisely. You do not need the other team member to submit a different video.</p>}
      </section>
    </div>
    {selected&&media&&<section className="panel" style={{marginTop:20}}>
      <div className="review-media-heading"><div><span className="eyebrow">FRAME INSPECTION</span>
        <h3>{asset?.video_group||"Frame inspection"}</h3>
      </div><div className="review-navigation">
          <span>{media.total} frames</span>
          <button className="btn secondary" disabled={!assetId||media.items.findIndex(m=>m.id===assetId)<=0}
            onClick={()=>{const i=media.items.findIndex(m=>m.id===assetId);if(i>0)setAssetId(media.items[i-1].id);}}>Previous frame</button>
          <button className="btn secondary" disabled={!assetId||media.items.findIndex(m=>m.id===assetId)>=media.items.length-1}
            onClick={()=>{const i=media.items.findIndex(m=>m.id===assetId);if(i>=0&&i<media.items.length-1)setAssetId(media.items[i+1].id);}}>Next frame</button>
        </div></div>
      <div className="review-media-layout">
        <div className="review-media-items">
          {media.items.map(m=><button key={m.id} onClick={()=>setAssetId(m.id)}
            className={"review-media-row "+(m.id===assetId?"selected":"")}>
            <Image size={15}/><span>{m.relative_path}</span>
            {reviews?.items.some(r=>r.media_id===m.id)&&<CheckCircle2 size={15}/>}
          </button>)}
          <div className="table-pagination"><span>Page {page}</span>
            <button className="btn secondary" disabled={page<=1}
              onClick={()=>{setPage(n=>n-1);setAssetId("");}}>Previous</button>
            <button className="btn secondary" disabled={page*50>=media.total}
              onClick={()=>{setPage(n=>n+1);setAssetId("");}}>Next</button></div>
        </div>
        <div className="review-media-detail">
          {asset&&<><div className="review-frame-title"><strong>{asset.relative_path}</strong>
            <small>{asset.video_group||"Stand-alone image"}</small></div>
          <img className="review-preview" alt={"Bird frame "+asset.relative_path}
            src={"/api/v1/review/datasets/"+selected.dataset_id+"/assets/"+asset.id+"/image"}/>
          <div className="annotation-inspector">
            <strong>Supervisely annotation</strong>
            {annotation?
              <>
                <p className="muted small">Downloaded from Supervisely · Native image #{annotation.native_image_id} · {Array.isArray(annotation.annotation.labels)?annotation.annotation.labels.length:0} label(s)</p>
                <details><summary>Inspect annotation JSON for this frame</summary>
                  <pre>{JSON.stringify(annotation.annotation,null,2)}</pre>
                </details>
              </>:
              <p className="muted small">Awaiting annotation synchronization from the returned labeling job.
                The original local frame remains available for review.</p>}
          </div>
          {canReview&&reviewable&&reviews?.annotation_snapshot_ready&&<div className="review-entry">
            <label>Verdict<select value={verdict} onChange={e=>setVerdict(
              e.target.value as "good"|"bad"|"incomplete")}>
              <option value="good">Good</option><option value="bad">Incorrect</option>
              <option value="incomplete">Missing/incomplete</option></select></label>
            <div className="review-score-grid">
              {([
                ["Correct",correct,setCorrect],["Missing",missing,setMissing],
                ["Incorrect",incorrect,setIncorrect],["Visible",visible,setVisible],
                ["Occluded",occluded,setOccluded],
              ] as const).map(([label,value,set])=><label key={label}>{label}
                <input type="number" min={0} max={10000} value={value}
                  onChange={e=>set(Number(e.target.value))}/></label>)}
            </div>
            <label>Temporal consistency<select value={temporal}
              onChange={e=>setTemporal(e.target.value)}>
              <option value="unknown">Not assessed</option>
              <option value="consistent">Consistent</option>
              <option value="inconsistent">Inconsistent</option></select></label>
            <label>Monitor notes<textarea rows={3} value={notes} maxLength={4000}
              onChange={e=>setNotes(e.target.value)}/></label>
            <button className="btn primary" disabled={busy} onClick={()=>void saveFrame()}>
              <ClipboardCheck size={15}/> Save frame review</button>
          </div>}</>}
        </div>
      </div>
    </section>}
    <div className="settings-note" style={{marginTop:18}}><ShieldCheck size={18}/>
      <p>Nightjar's review scores and decisions are local audit records. The connected Supervisely instance remains the source of truth for native annotations and job states.</p>
    </div>
  </div>;
}
