import { useTableSort } from "./table_sort";
import { confirmAction } from "./dialogs";
import { useCallback, useEffect, useState } from "react";
import { Archive, BarChart3, CheckCircle2, Download, RefreshCw, ShieldAlert } from "lucide-react";
import { api, type Auth } from "./api";

type NativeExport={operation_id:string|null;status:string;error_code:string|null;
  attempts:number;ready_to_download:boolean;size_bytes:number|null};
type TeamRank={team_id:string;team_name:string;rank:number|null;correct_keypoints:number;
  correct_visible_keypoints:number;correct_occluded_keypoints:number;
  correct_boxes:number;correct_objects:number;estimated_points:number|null;
  reviewed_frames:number;available_frames:number;missing_objects:number;
  errors:Record<string,number>};
type ReviewQueueCounts={pending:number;active:number;completed:number};
type Report={
  event_id:string;event_name:string;event_status:string;
  people:{registered:number;participants:number;monitors:number;checked_in:number};
  teams:number;
  datasets:{count:number;images:number;videos:number;video_frames:number;
    morphology_counts:Record<string,number>};
  labeling:{jobs:number;status_counts:Record<string,number>;frames_assigned:number;
    jobs_accepted:number;jobs_needing_action:number};
  reviews:{frames_reviewed:number;good:number;bad:number;incomplete:number;
    keypoint_score:number;temporal_consistency_issues:number};
  closeout_readiness:{ready:boolean;blocking_jobs:number;unfinished_unscored_jobs:number;
    verified_reviews_available:boolean;message:string};
  ticket_totals:Record<string,number>;generated_at:number;
};
export function CloseoutScreen({auth}:{auth:Auth}) {
  const organiser=auth.staff.roles.includes("organiser");
  const [report,setReport]=useState<Report|null>(null);
  const [nativeExport,setNativeExport]=useState<NativeExport|null>(null);
  const [rankings,setRankings]=useState<TeamRank[]>([]);
  const rankingSort=useTableSort();
  const [reviewCounts,setReviewCounts]=useState<ReviewQueueCounts|null>(null);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [confirmation,setConfirmation]=useState("");
  const [busy,setBusy]=useState(false);
  const refresh=useCallback(async()=>{
    try{
      const [result,exportState,teamRanks,counts]=await Promise.all([
        api<Report>("/closeout/report"),
        api<NativeExport>("/native/export"),
        api<{items:TeamRank[]}>("/rankings/teams"),
        api<ReviewQueueCounts>("/review-queue/status")
      ]);
      setReport(result);setNativeExport(exportState);setRankings(teamRanks.items);
      setReviewCounts(counts);setError("");
    }
    catch(e){setError((e as Error).message);}
  },[]);
  useEffect(()=>{void refresh();},[refresh]);
  useEffect(()=>{
    const timer=window.setInterval(()=>{if(!document.hidden)void refresh();},12000);
    return()=>window.clearInterval(timer);
  },[refresh]);
  async function finalize(){
    if(!report||confirmation!=="CLOSE "+report.event_name)return;
    if(!await confirmAction(
      "Final confirmation: archive this event and freeze operational data? "+
      "Existing annotations and native Supervisely jobs will not be deleted."
    ))return;
    setBusy(true);setError("");
    try{
      await api("/closeout/finalize","POST",{confirmation},auth.csrf_token);
      setNotice("Event closed, archived and snapshotted. Previous records remain accessible.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  async function nativeExportAction(retry:boolean){
    if(!report||!await confirmAction(retry?
      "Retry an interrupted read-only native annotation export?":
      "Queue a read-only native annotation snapshot? This can take considerable time "+
      "for a large dataset and requires the separate SDK worker."))return;
    setBusy(true);setError("");setNotice("");
    try{
      await api("/native/export/"+(retry?"retry":"prepare"),"POST",undefined,
        auth.csrf_token);
      setNotice("Export task queued. The separate SDK worker will download annotation JSON.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  const phrase=report?"CLOSE "+report.event_name:"";
  return <div className="page-body closeout-workspace">
    <div className="page-heading"><div><span className="eyebrow">EVENT OPERATIONS · TEAM RESULTS</span>
      <h2>Results & Rankings</h2>
      <p className="muted">Teams rank by estimated annotation points from the proportional review queue. Organisers can export records and close the event.</p>
      </div><button className="btn secondary" onClick={()=>void refresh()}>
        <RefreshCw size={15}/> Refresh</button></div>
    {error&&<div role="alert" className="alert danger">{error}</div>}
    {notice&&<div role="status" className="alert success">{notice}</div>}
    {report&&<>
      <div className="stat-grid compact">
        <div className="stat-card"><BarChart3 size={19}/><span className="muted small">Participants checked in</span>
          <strong>{report.people.checked_in}/{report.people.participants}</strong></div>
        <div className="stat-card"><CheckCircle2 size={19}/><span className="muted small">Complete submissions</span>
          <strong>{report.labeling.jobs_accepted}/{report.labeling.jobs}</strong></div>
        <div className="stat-card"><Archive size={19}/><span className="muted small">Shared-queue reviews completed</span>
          <strong>{reviewCounts?.completed??"—"}</strong></div>
      </div>
      <section className="panel event-ranking-panel">
        <div className="review-analytics-header">
          <div><span className="eyebrow">PROPORTIONAL REVIEW LEADERBOARD</span>
            <h3>Team rankings</h3>
            <p className="muted">Estimated points = eligible submitted frames × average review score (0–5).</p>
          </div><BarChart3 size={27}/></div>
        <div className="table-scroll"><table className="data-table">
          <thead><tr>
            {rankingSort.header("rank","Rank")}
            {rankingSort.header("team_name","Team")}
            {rankingSort.header("correct_objects","Correct Annotations")}
            {rankingSort.header("reviewed_frames","Reviewed / total")}
            {rankingSort.header("errors","Errors (class / placement / sabotage)")}
            {rankingSort.header("estimated_points","Estimated points")}
          </tr></thead>
          <tbody>{rankingSort.rows(rankings,(team,key)=>key==="errors"
            ? (team.errors?.wrong_class??0)+(team.errors?.wrong_placement??0)+(team.errors?.sabotage??0)
            : team[key as keyof Omit<typeof team,"errors">]).map(team=><tr key={team.team_id}>
            <td><strong>{team.rank===null?"—":"#"+team.rank}</strong></td>
            <td><strong>{team.team_name}</strong></td>
            <td><strong>{team.correct_objects.toLocaleString()}</strong></td>
            <td>{team.reviewed_frames} / {team.available_frames}</td>
            <td title="Class / placement / sabotage">{team.errors?.wrong_class||0} / {team.errors?.wrong_placement||0} / {team.errors?.sabotage||0}</td>
            <td><strong>{team.estimated_points?.toLocaleString(undefined,{maximumFractionDigits:2})??"Awaiting review"}</strong></td>
          </tr>)}</tbody>
        </table>{!rankings.length&&<div className="empty-table">Rankings will appear after reviewed frames are available.</div>}</div>
        <p className="muted small">Completed reviews determine each team’s average score.
          The submitted frame count includes eligible frames still awaiting review.</p>
      </section>
      <div className="content-grid closeout-layout">
        <section className="panel">
          <span className="eyebrow">EVENT SUMMARY</span><h3>{report.event_name}</h3>
          <div className="report-rows">
            <div><span>Competition teams</span><strong>{report.teams}</strong></div>
            <div><span>Local datasets</span><strong>{report.datasets.count}</strong></div>
            <div><span>Unique images</span><strong>{report.datasets.images}</strong></div>
            <div><span>Video groups</span><strong>{report.datasets.videos}</strong></div>
            <div><span>Video frames</span><strong>{report.datasets.video_frames}</strong></div>
            <div><span>Assigned frames incl. overlap</span><strong>{report.labeling.frames_assigned}</strong></div>
            <div><span>Reviews marked good</span><strong>{report.reviews.good}</strong></div>
            <div><span>Reviews marked bad / incomplete</span>
              <strong>{report.reviews.bad+report.reviews.incomplete}</strong></div>
            <div><span>Keypoint score</span><strong>{report.reviews.keypoint_score}</strong></div>
            <div><span>Temporal consistency concerns</span>
              <strong>{report.reviews.temporal_consistency_issues}</strong></div>
          </div>
          <h4>Job status distribution</h4>
          <div className="morph-counts">{Object.entries(report.labeling.status_counts).map(
            ([status,n])=><span key={status}>{status.replaceAll("_"," ")}: {n}</span>
          )}</div>
          <h4 style={{marginTop:15}}>Morphology counts</h4>
          <div className="morph-counts">{Object.entries(report.datasets.morphology_counts).map(
            ([name,n])=><span key={name}>{name}: {n}</span>
          )}</div>
        </section>
        <section className="panel">
          <span className="eyebrow">OFFICIAL EVENT EXPORTS</span><h3>Download CSV records</h3>
          <p className="muted small">Exports contain participant identities and annotation review history; store them securely.</p>
          <div className="export-links">{([
            ["people","People, teams and admission"],["jobs","All pair job progress"],
            ["frame_reviews","Official frame reviews and object verdicts"],
            ["reviews","Historical whole-job reviews"],
            ["media","Media inventory and hashes"],
          ] as const).map(([key,label])=><a key={key} className="btn secondary"
            href={"/api/v1/closeout/exports/"+key} download>
            <Download size={15}/>{label}</a>)}</div>
          <div className="native-export-controls" style={{marginTop:20}}>
            <span className="eyebrow">SUPERVISELY ANNOTATION SNAPSHOT</span>
            <h4>Full and curated native JSON</h4>
            <p className="muted small">A separate export from CSV. The archive contains
              current annotation JSON for every recorded native image copy,
              full and curated source mapping manifests, and no media bytes or historical versions.</p>
            <p className="muted small">Status: <strong>{nativeExport?.status||"not_requested"}</strong>
              {nativeExport?.error_code?" · "+nativeExport.error_code:""}</p>
            {organiser&&report.event_status==="archived"&&nativeExport?.status==="not_requested"&&
              <button className="btn primary" disabled={busy}
                onClick={()=>void nativeExportAction(false)}>
                <Download size={15}/> Prepare native annotation export</button>}
            {organiser&&nativeExport&&["attention","blocked"].includes(nativeExport.status)&&
              <button className="btn secondary" disabled={busy}
                onClick={()=>void nativeExportAction(true)}>
                <RefreshCw size={14}/> Retry export</button>}
            {nativeExport?.ready_to_download&&<a className="btn primary"
              href="/api/v1/native/export/download" download>
              <Download size={15}/> Download annotation ZIP
              {nativeExport.size_bytes!==null?
                " ("+(nativeExport.size_bytes/1e6).toFixed(1)+" MB)":""}</a>}
            {nativeExport&&["pending","running"].includes(nativeExport.status)&&
              <p className="muted small">Export in progress. This screen updates automatically.</p>}
          </div>
          <div className="settings-note" style={{marginTop:18}}><ShieldAlert size={18}/>
            <p>Nightjar exports are not substitutes for native Supervisely annotation exports. Native media, annotations and labeling jobs are never deleted here.</p></div>
        </section>
      </div>
      {auth.staff.roles.includes("organiser")&&<section className="panel closeout-confirm" style={{marginTop:20}}>
        <span className="eyebrow">ARCHIVE CURRENT EVENT</span><h3>Finalize & close event</h3>
        <p className="muted">Archiving preserves all existing people, job, ticket, dataset and activity records. Create a new event for another competition.</p>
        {!report.closeout_readiness.ready?<div className="advisory"><ShieldAlert size={18}/>
          {report.closeout_readiness.message}</div>:
          <div className="settings-note"><CheckCircle2 size={17}/>
            <p>Completed submissions have their annotation JSON captured and some reviews are verified.
              {report.closeout_readiness.unfinished_unscored_jobs>0?
                " "+report.closeout_readiness.unfinished_unscored_jobs+
                " unfinished jobs remain unscored.":""}
              Equal reviewed-frame counts are not required for finalisation.</p></div>}
        <label>Type <strong>{phrase}</strong> to confirm
          <input value={confirmation} onChange={e=>setConfirmation(e.target.value)}
            placeholder={phrase} autoComplete="off" spellCheck={false}/></label>
        <button className="btn danger-outline" disabled={busy||
          report.event_status==="archived"||!report.closeout_readiness.ready||
          confirmation!==phrase} onClick={()=>void finalize()}>
          <Archive size={16}/> Archive & finalize event</button>
      </section>}
    </>}
  </div>;
}
