import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type React from "react";
import {
  CheckCircle2, ChevronRight, ClipboardCheck, PauseCircle, PlayCircle,
  RefreshCw, RotateCcw, ZoomIn, ZoomOut
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
type Weights={
  visible_keypoint:number;occluded_keypoint:number;box:number;
  wrong_class:number;wrong_placement:number;sabotage:number;
};
type Rubric={version:number;weights:Weights};
type Ranking={rank:number;team_id:string;team_name:string;
  correct_keypoints:number;correct_visible_keypoints:number;
  correct_occluded_keypoints:number;correct_boxes:number;correct_objects:number;
  reviewed_frames:number;available_frames:number;weighted_score:number;
  missing_objects:number;errors:Record<string,number>};
const emptyWeights:Weights={
  visible_keypoint:1,occluded_keypoint:3,box:1,
  wrong_class:1,wrong_placement:2,sabotage:3
};
const fieldLabels:Record<keyof Weights,string>={
  visible_keypoint:"Correct visible keypoint (+ points)",
  occluded_keypoint:"Correct occluded keypoint (+ points)",
  box:"Correct bird bounding box (+ points)",
  wrong_class:"Wrong keypoint class (− points)",
  wrong_placement:"Wrong keypoint placement (− points)",
  sabotage:"Sabotage / no serious attempt (− points)"
};
const reasons:{value:Reason;label:string}[]=[
  {value:"wrong_class",label:"Wrong keypoint class"},
  {value:"wrong_placement",label:"Wrong keypoint placement"},
  {value:"sabotage",label:"Sabotage / no serious placement attempt"},
];
const fmt=(n:number)=>new Intl.NumberFormat().format(n);
const isLeaseConflict=(error:unknown)=>/claim expired|not owned by this claim|already completed/i
  .test((error as Error).message||"");

export function SharedReviewScreen({auth}:{auth:Auth}){
  const organiser=auth.staff.roles.includes("organiser");
  const [tab,setTab]=useState<"review"|"rankings"|"scoring">("review");
  const [claim,setClaim]=useState<ReviewClaim|null>(null);
  const activeClaimRef=useRef<ReviewClaim|null>(null);
  const [status,setStatus]=useState<QueueStatus|null>(null);
  const [rankings,setRankings]=useState<Ranking[]>([]);
  const [rubric,setRubric]=useState<Rubric|null>(null);
  const [weights,setWeights]=useState<Weights>(emptyWeights);
  const [verdicts,setVerdicts]=useState<Record<number,VerdictDraft>>({});
  const [missingClasses,setMissingClasses]=useState<string[]>([]);
  const [zoom,setZoom]=useState(1);
  const [selectedObject,setSelectedObject]=useState<number|null>(null);
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
    if(shapeOf(obj)==="rectangle")return true;
    if(!value.reason)return false;
    return value.reason!=="wrong_class"||
      Boolean(value.corrected_class&&value.corrected_class!==obj.classTitle);
  }).length;
  const allGraded=Boolean(claim)&&graded===objects.length;

  const refreshStatus=useCallback(async()=>{
    const result=await api<QueueStatus>("/review-queue/status");
    setStatus(result);
  },[]);
  const refreshRankings=useCallback(async()=>{
    const result=await api<{items:Ranking[]}>("/rankings/teams");
    setRankings(result.items);
  },[]);

  useEffect(()=>{
    void refreshStatus().catch(e=>setError((e as Error).message));
    const interval=window.setInterval(()=>{
      if(!document.hidden)void refreshStatus().catch(()=>undefined);
    },15000);
    return()=>window.clearInterval(interval);
  },[refreshStatus]);
  useEffect(()=>{
    if(tab!=="rankings")return;
    void refreshRankings().catch(e=>setError((e as Error).message));
    const interval=window.setInterval(()=>{
      if(!document.hidden)void refreshRankings().catch(()=>undefined);
    },15000);
    return()=>window.clearInterval(interval);
  },[tab,refreshRankings]);
  useEffect(()=>{
    if(tab!=="scoring")return;
    let active=true;
    void api<Rubric>("/scoring/rubric").then(value=>{
      if(active){setRubric(value);setWeights(value.weights);}
    }).catch(e=>{if(active)setError((e as Error).message);});
    return()=>{active=false;};
  },[tab]);

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
    setClaim(next);setVerdicts({});setMissingClasses([]);setZoom(1);setSelectedObject(null);
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

  async function saveRubric(e:React.FormEvent){
    e.preventDefault();
    if(!organiser||busy)return;
    setBusy(true);setError("");setNotice("");
    try{
      const result=await api<Rubric>("/scoring/rubric","PUT",weights,auth.csrf_token);
      setRubric(result);setWeights(result.weights);
      setNotice("Scoring rubric saved as version "+result.version+
        ". Historical verdicts remain unchanged; leaderboard weighted scores are recalculated consistently with this version.");
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }

  const pointClasses=(claim?.classes||[]).filter(c=>c.shape==="point");
  return <div className="page-body shared-review-workspace">
    <div className="page-heading">
      <div><span className="eyebrow">EVENT OPERATIONS · SHARED QUALITY REVIEW</span>
        <h2>Monitoring & Review</h2>
        <p className="muted">Every monitor draws randomly from the same pool of submitted frames. More submitted frames means more opportunities to earn verified correct-annotation points. Claims are exclusive.</p>
      </div>
      <button className="btn secondary" disabled={busy}
        onClick={()=>{void refreshStatus();if(tab==="rankings")void refreshRankings();}}>
        <RefreshCw size={15}/> Refresh
      </button>
    </div>

    <nav className="workspace-subtabs" aria-label="Monitoring workspaces">
      <button className={tab==="review"?"active":""} onClick={()=>setTab("review")}>Review frames</button>
      <button className={tab==="rankings"?"active":""} onClick={()=>setTab("rankings")}>Team rankings</button>
      <button className={tab==="scoring"?"active":""} onClick={()=>setTab("scoring")}>Scoring rubric</button>
    </nav>

    {error&&<div className="alert danger" role="alert">{error}</div>}
    {notice&&<div className="alert success" role="status">{notice}</div>}

    {tab==="review"&&<>
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
        <>
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
            <div className="shared-review-zoom-viewport" role="region" aria-label="Zoomable annotated bird image" tabIndex={0}>
              <div className="shared-review-scalable" style={{width:(zoom*100)+"%"}}>
                <AnnotationFrame key={claim.review_id} src={claim.image_url}
                  alt={"Frame "+claim.source_filename} annotation={claim.annotation}
                  selectedIndex={selectedObject} onSelectObject={setSelectedObject}/>
              </div>
            </div>
          </section>

          <section className="panel shared-review-grade-panel">
            <div className="shared-review-grade-heading">
              <div><span className="eyebrow">INDIVIDUAL OBJECT REVIEW</span>
                <h3>Review each annotation</h3>
                <p className="muted">Select a numbered marker on the image or a row below.
                  Grade each object separately; incorrect keypoints require a reason.</p>
              </div><strong>{graded} / {objects.length} graded</strong>
            </div>
            {!objects.length&&<div className="settings-note">No annotated points or boxes in this submitted frame. Confirm any missing landmark classes below before saving.</div>}
            <div className="shared-review-object-list">
              {objects.map((obj,index)=>{
                const shape=shapeOf(obj),draft=verdicts[index];
                const classOptions=(claim.classes||[]).filter(c=>c.shape===shape&&c.name!==obj.classTitle);
                return <div className={"shared-review-object-row "+(selectedObject===index?"selected":"")}
                  key={index}>
                  <div className="shared-review-object-label">
                    <span className="soft-pill">{shape==="point"?"Keypoint":shape==="rectangle"?"Bounding box":"Unknown"}</span>
                    <button className="shared-review-object-select" type="button"
                      aria-pressed={selectedObject===index}
                      onClick={()=>setSelectedObject(index)}>
                      <strong>{index+1}. {obj.classTitle||"Unlabelled object"}</strong>
                      <small>{selectedObject===index?"Selected on image":"Highlight on image"}</small>
                    </button>
                  </div>
                  <div className="shared-review-verdict-buttons" role="group" aria-label={"Classification for "+(obj.classTitle||"object")+" "+(index+1)}>
                    <button type="button" className={"btn "+(draft?.correct===true?"primary":"secondary")}
                      disabled={busy||!shape}
                      onClick={()=>{setSelectedObject(index);
                        mark(index,{correct:true,reason:null,corrected_class:""});}}>
                      <CheckCircle2 size={15}/> Correct
                    </button>
                    <button type="button" className={"btn "+(draft?.correct===false?"danger-outline":"secondary")}
                      disabled={busy||!shape}
                      onClick={()=>{setSelectedObject(index);
                        mark(index,{correct:false,reason:null,corrected_class:""});}}>
                      Incorrect
                    </button>
                  </div>
                  {draft?.correct===false&&shape==="point"&&
                    <div className="shared-review-reason">
                      <label>Why is the keypoint incorrect?
                        <select value={draft.reason||""} onChange={e=>mark(index,{
                          correct:false,reason:e.target.value as Reason||null,corrected_class:""
                        })}>
                          <option value="">Choose reason…</option>
                          {reasons.map(reason=><option key={reason.value} value={reason.value}>{reason.label}</option>)}
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
                  {draft?.correct===false&&shape==="rectangle"&&
                    <span className="muted small">Bounding box counted as incorrect.</span>}
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
        </>}
    </>}

    {tab==="rankings"&&<section className="panel shared-review-rankings">
      <div className="shared-review-grade-heading"><div>
        <span className="eyebrow">EVENT RESULTS · WEIGHTED ANNOTATION POINTS</span>
        <h3>Team leaderboard</h3>
        <p className="muted">Teams rank by their total verified annotation points: visible and occluded
          keypoints and correct bounding boxes earn different weights; incorrect annotations
          incur the organiser-configured penalties. More submitted frames can mean more points.</p>
      </div><button className="btn secondary" onClick={()=>void refreshRankings()}>
        <RefreshCw size={15}/> Update</button></div>
      <div className="table-scroll"><table className="data-table">
        <thead><tr><th>Rank</th><th>Team</th><th>Visible points</th>
          <th>Occluded points</th><th>Correct boxes</th><th>Correct annotations</th>
          <th>Reviewed / Available</th><th>Errors (class / placement / sabotage)</th>
          <th>Competition score</th></tr></thead>
        <tbody>{rankings.map(team=><tr key={team.team_id}>
          <td><strong>#{team.rank}</strong></td><td><strong>{team.team_name}</strong></td>
          <td>{fmt(team.correct_visible_keypoints)}</td>
          <td>{fmt(team.correct_occluded_keypoints)}</td>
          <td>{fmt(team.correct_boxes)}</td>
          <td>{fmt(team.correct_objects)}</td>
          <td>{fmt(team.reviewed_frames)} / {fmt(team.available_frames)}</td>
          <td>{fmt(team.errors?.wrong_class||0)} / {fmt(team.errors?.wrong_placement||0)} / {fmt(team.errors?.sabotage||0)}</td>
          <td><strong>{fmt(team.weighted_score)}</strong></td>
        </tr>)}</tbody>
      </table>{!rankings.length&&<div className="empty-table">
        No competition results available yet.</div>}</div>
      <p className="muted small">Only completed frame reviews contribute verified object counts. Pending submitted frames are excluded until reviewed.
        Ranking calculations and historic reviews are kept on the server.</p>
    </section>}

    {tab==="scoring"&&<section className="panel shared-review-scoring">
      <div className="shared-review-grade-heading"><div>
        <span className="eyebrow">SCORING · VERSIONED RUBRIC</span>
        <h3>Annotation scoring</h3>
        <p className="muted">Only event organisers (admins) can change scoring weights here.
          All monitors can view them. Saved rubric versions apply uniformly to every
          reviewed submission, including earlier completed reviews.</p>
      </div><span className="soft-pill">Version {rubric?.version??"—"}</span></div>
      <form className="form-stack" onSubmit={e=>void saveRubric(e)}>
        <div className="shared-review-weights">
          {(Object.keys(fieldLabels) as (keyof Weights)[]).map(key=><label key={key}>
            {fieldLabels[key]}
            <input type="number" required min={0} max={100} step={1}
              disabled={!organiser||busy} value={weights[key]}
              onChange={e=>setWeights(old=>({...old,[key]:Number(e.target.value)}))}/>
          </label>)}
        </div>
        <p className="muted small">Competition score = correct visible keypoints × visible weight
          + correct occluded keypoints × occluded weight + correct bounding boxes × box weight
          − wrong-class, wrong-placement and sabotage penalties. Defaults: +1 visible,
          +3 occluded, +1 box; penalties −1/−2/−3 respectively (configurable).
          Highest total weighted score wins, not the highest percentage of correct images.</p>
        {organiser&&<button type="submit" className="btn primary" disabled={busy}>
          Save scoring configuration</button>}
      </form>
    </section>}
  </div>;
}
