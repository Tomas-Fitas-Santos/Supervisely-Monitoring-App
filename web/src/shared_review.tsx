import { RefreshButton } from "./refresh_button";
import { useTableSort } from "./table_sort";
import { useEffect, useMemo, useRef, useState } from "react";
import { singleFlight } from "./single_flight";
import {
  CheckCircle2, ChevronRight, ClipboardCheck, PauseCircle, PlayCircle,
  RotateCcw, ZoomIn, ZoomOut
} from "lucide-react";
import { api, type Auth } from "./api";
import { AnnotationFrame } from "./annotation_overlay";

type Reason="wrong_class"|"wrong_placement"|"sabotage";
type ObjectShape="rectangle"|"point";
type AnnotationClass={name:string;shape:string;color:string};
type AnnotationObject={classTitle?:string;geometryType?:string};
type ReviewClaim={
  review_id:string;claim_token:string;expires_at:number;
  source_filename:string;team_name:string;annotator_name:string;
  selection_reason:string;correction_round:number;image_url:string;
  annotation:Record<string,unknown>;classes:AnnotationClass[];
  rubric_version:number;
};
type QueueStatus={pending:number;active:number;completed:number;
  teams:{team_id:string;team_name:string;pending:number;active:number;completed:number}[]};
type VerdictDraft={correct:boolean;reason:Reason|null;corrected_class:string};
type Ranking={rank:number|null;team_id:string;team_name:string;
  correct_keypoints:number;correct_visible_keypoints:number;
  correct_occluded_keypoints:number;correct_boxes:number;correct_objects:number;
  reviewed_frames:number;available_frames:number;
  estimated_points:number|null;average_frame_points:number|null;coverage_pct:number;
  submission_share_pct:number;review_share_pct:number;
  missing_objects:number;errors:Record<string,number>};
const reasons:{value:Reason;label:string}[]=[
  {value:"wrong_class",label:"Wrong keypoint class"},
  {value:"wrong_placement",label:"Wrong keypoint placement"},
  {value:"sabotage",label:"Sabotage / no serious placement attempt"},
];
const boxReasons=([
  {value:"wrong_placement",label:"Placement mistake"},
  {value:"sabotage",label:"Sabotage / no serious attempt"},
] satisfies typeof reasons);
const fmt=(n:number)=>new Intl.NumberFormat(undefined,{maximumFractionDigits:2}).format(n);
const isLeaseConflict=(error:unknown)=>/claim expired|not owned by this claim|already completed/i
  .test((error as Error).message||"");

export function SharedReviewScreen({auth}:{auth:Auth}){
  const [tab,setTab]=useState<"review"|"rankings">("review");
  const [claim,setClaim]=useState<ReviewClaim|null>(null);
  const activeClaimRef=useRef<ReviewClaim|null>(null);
  const [status,setStatus]=useState<QueueStatus|null>(null);
  const [rankings,setRankings]=useState<Ranking[]>([]);
  const rankingSort=useTableSort();
  const [verdicts,setVerdicts]=useState<Record<number,VerdictDraft>>({});
  const [missingClasses,setMissingClasses]=useState<string[]>([]);
  const [zoom,setZoom]=useState(1);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [paused,setPaused]=useState(false);

  const objects=useMemo(()=>{
    const raw=claim?.annotation.objects??claim?.annotation.labels;
    return Array.isArray(raw)?raw as AnnotationObject[]:[];
  },[claim]);
  const shapeOf=(item:AnnotationObject):ObjectShape|null=>{
    const geometry=item.geometryType??claim?.classes.find(c=>c.name===item.classTitle)?.shape;
    return geometry==="point"||geometry==="rectangle"?geometry:null;
  };
  const graded=objects.filter((obj,index)=>{
    const value=verdicts[index];
    if(!value||!shapeOf(obj))return false;
    if(value.correct)return true;
    if(shapeOf(obj)==="rectangle")return value.reason==="wrong_placement"||value.reason==="sabotage";
    if(!value.reason)return false;
    return value.reason!=="wrong_class"||
      Boolean(value.corrected_class&&value.corrected_class!==obj.classTitle);
  }).length;
  const allGraded=Boolean(claim)&&graded===objects.length;
  const pointClasses=(claim?.classes||[]).filter(c=>c.shape==="point");

  const refreshStatus=useMemo(()=>singleFlight(async()=>{
    const result=await api<QueueStatus>("/review-queue/status");
    setStatus(result);
  }),[]);
  const refreshRankings=useMemo(()=>singleFlight(async()=>{
    const result=await api<{items:Ranking[]}>("/rankings/teams");
    setRankings(result.items);
  }),[]);

  useEffect(()=>{
    if(!document.hidden)void refreshStatus().catch(e=>setError((e as Error).message));
    const interval=window.setInterval(()=>{
      if(!document.hidden)void refreshStatus().catch(()=>undefined);
    },15000);
    return()=>window.clearInterval(interval);
  },[refreshStatus]);
  useEffect(()=>{
    if(tab!=="rankings")return;
    if(!document.hidden)void refreshRankings().catch(e=>setError((e as Error).message));
    const interval=window.setInterval(()=>{
      if(!document.hidden)void refreshRankings().catch(()=>undefined);
    },15000);
    return()=>window.clearInterval(interval);
  },[tab,refreshRankings]);

  // Leases renew while the claimed frame remains on this browser.
  // If the browser closes unexpectedly, the backend reclaims it after expiry.
  useEffect(()=>{
    if(!claim)return;
    const reviewId=claim.review_id,token=claim.claim_token;
    const interval=window.setInterval(()=>{
      void api<{expires_at:number}>("/review-queue/"+reviewId+"/heartbeat",
        "POST",{claim_token:token},auth.csrf_token).then(result=>{
        setClaim(current=>current?.review_id===reviewId?
          {...current,expires_at:result.expires_at}:current);
      }).catch(e=>{
        if(activeClaimRef.current?.review_id!==reviewId)return;
        if(isLeaseConflict(e)){
          setActiveReview(null);
          setError("This frame lease has expired or was reassigned. Your unsaved grades were not submitted; request the next available frame.");
          void refreshStatus().catch(()=>undefined);
        }else{
          setError("Review lease: "+(e as Error).message);
        }
      });
    },45000);
    return()=>window.clearInterval(interval);
  },[claim?.review_id,claim?.claim_token,auth.csrf_token]);

  function setActiveReview(next:ReviewClaim|null){
    activeClaimRef.current=next;
    setClaim(next);setVerdicts({});setMissingClasses([]);setZoom(1);
  }

  async function claimNext(){
    if(busy||claim)return;
    setBusy(true);setError("");setNotice("");
    try{
      const result=await api<{review:ReviewClaim|null}>(
        "/review-queue/claim-next","POST",undefined,auth.csrf_token);
      setActiveReview(result.review);
      setPaused(false);
      if(!result.review)setNotice("No reviewable frames are available yet. Try again after the worker syncs a submission.");
      await refreshStatus();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }

  async function release(){
    if(busy||!claim)return;
    setBusy(true);setError("");setNotice("");
    try{
      await api("/review-queue/"+claim.review_id+"/release","POST",
        {claim_token:claim.claim_token},auth.csrf_token);
      setActiveReview(null);setPaused(true);
      setNotice("Frame returned to the shared queue without a review.");
      await refreshStatus();
    }catch(e){
      if(isLeaseConflict(e)){
        setActiveReview(null);
        setError("This frame is no longer assigned to you. Your unsaved grades were not submitted. You can request another frame.");
        void refreshStatus().catch(()=>undefined);
      }else setError((e as Error).message);
    }
    finally{setBusy(false);}
  }

  function mark(index:number,draft:VerdictDraft){
    setVerdicts(current=>({...current,[index]:draft}));
  }

  async function complete(action:"next"|"pause"){
    if(!claim||busy)return;
    if(!allGraded){
      setError("Classify every bounding box and keypoint before finalising this frame.");
      return;
    }
    setBusy(true);setError("");setNotice("");
    try{
      const payload={
        claim_token:claim.claim_token,
        verdicts:objects.map((_obj,index)=>{
          const v=verdicts[index];
          return {object_index:index,correct:v.correct,
            ...(v.correct?{}:{
              ...(v.reason?{reason:v.reason}:{}),
              ...(v.reason==="wrong_class"?{corrected_class:v.corrected_class}:{})
            })};
        }),
        missing_classes:missingClasses,action
      };
      const response=await api<{completed:boolean;review:ReviewClaim|null}>(
        "/review-queue/"+claim.review_id+"/complete","POST",payload,auth.csrf_token);
      setActiveReview(response.review);
      setPaused(action==="pause");
      setNotice(action==="pause"?
        "Frame saved. Reviewing paused until you choose Next frame.":
        response.review?"Review saved. Next frame claimed.":"Review saved. Queue currently empty.");
      await refreshStatus();
    }catch(e){
      if(isLeaseConflict(e)){
        setActiveReview(null);
        setError("Review lease expired or was reassigned before your verdicts could be saved. Request a new frame.");
        void refreshStatus().catch(()=>undefined);
      }else setError((e as Error).message);
    }
    finally{setBusy(false);}
  }

  return <div className="page-body shared-review-workspace">
    <div className="page-heading">
      <div><span className="eyebrow">EVENT OPERATIONS · SHARED QUALITY REVIEW</span>
        <h2>Monitoring & Review</h2>
        <p className="muted">Only reviews saved before the leaderboard freezes count. Review allocation follows each team's share of submitted frames. Each monitor receives a random available frame from the selected team, with exclusive claims.</p>
      </div>
      <RefreshButton disabled={busy} onError={e=>setError((e as Error).message)}
        refresh={async()=>{
          setError("");
          await Promise.all([refreshStatus(), ...(tab==="rankings"?[refreshRankings()]:[])]);
        }}>Refresh</RefreshButton>
    </div>

    <nav className="workspace-subtabs" aria-label="Monitoring workspaces">
      <button className={tab==="review"?"active":""} onClick={()=>setTab("review")}>Review frames</button>
      <button className={tab==="rankings"?"active":""} onClick={()=>setTab("rankings")}>Team rankings</button>
    </nav>

    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}

    {tab==="review"&&<div className={"shared-review-session"+(claim?" has-frame":"")}>
      <div className="stat-grid compact shared-review-stat-grid">
        <div className="stat-card"><span className="muted small">Ready frames</span><strong>{fmt(status?.pending??0)}</strong></div>
        <div className="stat-card"><span className="muted small">Being reviewed</span><strong>{fmt(status?.active??0)}</strong></div>
        <div className="stat-card"><span className="muted small">Reviewed frames</span><strong>{fmt(status?.completed??0)}</strong></div>
      </div>
      {!claim?
        <section className="panel shared-review-empty">
          <ClipboardCheck size={35}/>
          <h3>{paused?"Reviewing paused":"Ready to review a frame?"}</h3>
          <p className="muted">Choose a random eligible frame from all submitted jobs, regardless of team.
            Frames already claimed by another monitor are excluded.</p>
          <button className="btn primary large" disabled={busy} onClick={()=>void claimNext()}>
            <PlayCircle size={18}/>{busy?"Claiming…":paused?"Resume · Next frame":"Get next frame"}
          </button>
          <p className="muted small">The worker queues every frame from complete submitted jobs. Random pool selection lets teams with more submitted frames receive more reviews without any equal-team quota.</p>
        </section>:
        <div className="shared-review-split">
          <div className="shared-review-image-column">
          <section className="shared-review-frame-heading">
            <div><span className="eyebrow">CURRENT FRAME</span>
              <h3>{claim.source_filename}</h3>
              <p className="muted">{claim.team_name} · {claim.annotator_name}</p>
            </div>
            <div className="shared-review-zoom-controls" role="group" aria-label="Image zoom">
              <button className="btn secondary" disabled={zoom<=1}
                onClick={()=>setZoom(z=>Math.max(1,Math.round((z-.25)*100)/100))}
                aria-label="Zoom out"><ZoomOut size={15}/></button>
              <span>{Math.round(zoom*100)}%</span>
              <button className="btn secondary" disabled={zoom>=3}
                onClick={()=>setZoom(z=>Math.min(3,Math.round((z+.25)*100)/100))}
                aria-label="Zoom in"><ZoomIn size={15}/></button>
              <button className="btn secondary" disabled={zoom===1}
                onClick={()=>setZoom(1)}><RotateCcw size={14}/> Reset</button>
            </div>
          </section>
          <section className="panel shared-review-canvas-panel">
            <div className="shared-review-zoom-viewport" role="region"
              aria-label="Annotated bird image. Zoom in and drag to inspect details.">
              <AnnotationFrame key={claim.review_id} src={claim.image_url}
                alt={"Frame "+claim.source_filename} annotation={claim.annotation}
                zoom={zoom}/>
            </div>
            {zoom>1&&<p className="muted small shared-review-zoom-hint">
              Drag the enlarged image to inspect details. Zoom and panning stay inside the viewer.
            </p>}
          </section>
          </div>

          <section className="panel shared-review-grade-panel">
            <div className="shared-review-grade-heading">
              <div><span className="eyebrow">INDIVIDUAL OBJECT REVIEW</span>
                <h3>Review each annotation</h3>
                <p className="muted">Use the legend above to show or hide each label class.
                  Grade every annotation using the rows below; incorrect annotations require a reason.</p>
              </div><strong>{graded} / {objects.length} graded</strong>
            </div>
            {!objects.length&&<div className="settings-note">No annotated points or boxes in this submitted frame. Check missing landmark classes below before saving.</div>}
            <div className="shared-review-object-list">
              {objects.map((obj,index)=>{
                const shape=shapeOf(obj),draft=verdicts[index];
                const classOptions=(claim.classes||[]).filter(c=>c.shape===shape&&c.name!==obj.classTitle);
                return <div className="shared-review-object-row" key={index}>
                  <div className="shared-review-object-label">
                    <span className="soft-pill">{shape==="point"?"Keypoint":shape==="rectangle"?"Bounding box":"Unknown"}</span>
                    <strong>{index+1}. {obj.classTitle||"Unlabelled object"}</strong>
                  </div>
                  <div className="shared-review-verdict-buttons" role="group" aria-label={"Classification for "+(obj.classTitle||"object")+" "+(index+1)}>
                    <button type="button" className={"btn "+(draft?.correct===true?"primary":"secondary")}
                      disabled={busy||!shape}
                      onClick={()=>mark(index,{correct:true,reason:null,corrected_class:""})}>
                      <CheckCircle2 size={15}/> Correct
                    </button>
                    <button type="button" className={"btn "+(draft?.correct===false?"danger-outline":"secondary")}
                      disabled={busy||!shape}
                      onClick={()=>mark(index,{correct:false,reason:null,corrected_class:""})}>
                      Incorrect
                    </button>
                  </div>
                  {draft?.correct===false&&shape&&
                    <div className="shared-review-reason">
                      <label>{shape==="rectangle"?"Why is the bounding box incorrect?":"Why is the keypoint incorrect?"}
                        <select value={draft.reason||""} onChange={e=>mark(index,{
                          correct:false,reason:e.target.value as Reason||null,corrected_class:""
                        })}>
                          <option value="">Choose reason…</option>
                          {(shape==="rectangle"?boxReasons:reasons).map(reason=><option key={reason.value} value={reason.value}>{reason.label}</option>)}
                        </select>
                      </label>
                      {draft.reason==="wrong_class"&&<label>Correct keypoint class
                        <select required value={draft.corrected_class}
                          onChange={e=>mark(index,{...draft,corrected_class:e.target.value})}>
                          <option value="">Choose the correct class…</option>
                          {classOptions.map(c=><option key={c.name} value={c.name}>{c.name}</option>)}
                        </select>
                      </label>}
                    </div>}
                  {!shape&&<span className="alert danger">Unsupported geometry; please report this annotation.</span>}
                </div>;
              })}
            </div>
            <details className="shared-review-missing">
              <summary>Missing keypoints / classes ({missingClasses.length})</summary>
              <p className="muted small">Optional: record landmark classes missing from this frame.
                They are tracked separately from submitted incorrect objects.</p>
              <div className="shared-review-missing-list">
                {pointClasses.map(c=><label key={c.name}>
                  <input type="checkbox" checked={missingClasses.includes(c.name)}
                    onChange={e=>setMissingClasses(v=>e.target.checked?[...v,c.name]:v.filter(x=>x!==c.name))}/>
                  {c.name}
                </label>)}
              </div>
            </details>
            <div className="shared-review-completion">
              <button className="btn secondary" disabled={busy} onClick={()=>void release()}>
                Release unreviewed frame
              </button>
              <div className="shared-review-completion-actions">
                <button className="btn secondary" disabled={busy||!allGraded}
                  onClick={()=>void complete("pause")}><PauseCircle size={17}/> Complete & Pause</button>
                <button className="btn primary" disabled={busy||!allGraded}
                  onClick={()=>void complete("next")}><CheckCircle2 size={17}/>
                  Complete & Next <ChevronRight size={16}/>
                </button>
              </div>
            </div>
          </section>
        </div>}
    </div>}

    {tab==="rankings"&&<section className="panel shared-review-rankings">
      <div className="shared-review-grade-heading"><div>
        <span className="eyebrow">EVENT RESULTS · ESTIMATED PRIZE POINTS</span>
        <h3>Team leaderboard</h3>
        <p className="muted">Prize scoring counts up to five distinct correct anatomical labels per reviewed frame:
          bird box, left eye, right eye, beak and crown. Visible and occluded variants each count as one.
          Estimated points = eligible automatic-primary submitted frames × average reviewed prize points (0–5).
          Incorrect labels earn zero; the legacy weighted quality rubric does not affect prize ranks.
          Review allocation follows submission share, and frames are randomized within each team.</p>
      </div><RefreshButton refresh={async()=>{setError("");await refreshRankings();}}
        onError={e=>setError((e as Error).message)}>Update</RefreshButton></div>
      <div className="table-scroll"><table className="data-table">
        <thead><tr>
            {rankingSort.header("rank","Rank")}
            {rankingSort.header("team_name","Team")}
            {rankingSort.header("correct_objects","Correct Annotations")}
            {rankingSort.header("reviewed_frames","Reviewed / total")}
            {rankingSort.header("coverage_pct","Coverage")}
            {rankingSort.header("submission_share_pct","Submission share")}
            {rankingSort.header("review_share_pct","Review share")}
            {rankingSort.header("average_frame_points","Average / 5")}
            {rankingSort.header("errors","Errors (class / placement / sabotage)")}
            {rankingSort.header("estimated_points","Estimated points")}
          </tr></thead>
        <tbody>{rankingSort.rows(rankings,(team,key)=>key==="errors"
            ? (team.errors?.wrong_class??0)+(team.errors?.wrong_placement??0)+(team.errors?.sabotage??0)
            : team[key as keyof Omit<typeof team,"errors">]).map(team=><tr key={team.team_id}>
          <td><strong>{team.rank===null?"—":"#"+team.rank}</strong></td><td><strong>{team.team_name}</strong></td>
          <td>{fmt(team.correct_objects)}</td>
          <td>{fmt(team.reviewed_frames)} / {fmt(team.available_frames)}</td>
          <td>{fmt(team.coverage_pct)}%</td><td>{fmt(team.submission_share_pct)}%</td>
          <td>{fmt(team.review_share_pct)}%</td>
          <td>{team.average_frame_points===null?"—":fmt(team.average_frame_points)}</td>
          <td title="Class / placement / sabotage">{fmt(team.errors?.wrong_class||0)} / {fmt(team.errors?.wrong_placement||0)} / {fmt(team.errors?.sabotage||0)}</td>
          <td><strong>{team.estimated_points===null?"Awaiting review":fmt(team.estimated_points)}</strong></td>
        </tr>)}</tbody>
      </table>{!rankings.length&&<div className="empty-table">
        No competition results available yet.</div>}</div>
      <p className="muted small">A team’s coverage is its completed reviews divided by its eligible submissions.
        The estimated score becomes available after the first review.</p>
    </section>}

  </div>;
}
