import { useTableSort } from "./table_sort";
import { useCallback, useEffect, useMemo, useState } from "react";
import { BarChart3, RefreshCw, ShieldCheck, Trophy } from "lucide-react";
import { api, type Auth } from "./api";

export type TeamMetric={
  team_id:string;team_name:string;monitor_person_id:string|null;
  jobs_total:number;jobs_completed:number;jobs_submitted:number;
  jobs_awaiting_review:number;frames_assigned:number;frames_submitted:number;
  frames_synced:number;frames_annotated:number|null;frames_reviewed:number;correct_keypoints:number;
  missing_keypoints:number;incorrect_keypoints:number;evaluated_keypoints:number;
  keypoint_accuracy_pct:number|null;average_seconds_per_frame:number|null;
  timed_frames:number;rank:number|null;
};
type Metrics={teams:TeamMetric[];ranking_status:string;accuracy_definition:string;
  time_definition:string;frames_definition:string;winner_policy:string};
type Monitors={monitors:{id:string;name:string;assigned_count:number}[];
  teams:{team_id:string;team_name:string;monitor_person_id:string|null}[]};
const integer=(value:number)=>new Intl.NumberFormat("en-GB").format(value);
const accuracy=(n:number|null)=>n===null?"Not yet scored":n.toFixed(1)+"%";
const time=(n:number|null)=>n===null?"Not available":n<60?n.toFixed(1)+" s":(n/60).toFixed(1)+" min";
export function TeamReviewOverview({auth,mode}:{auth:Auth;mode:"dashboard"|"rankings"}){
  const organiser=auth.staff.roles.includes("organiser");
  const [metrics,setMetrics]=useState<Metrics|null>(null);
  const [assignment,setAssignment]=useState<Monitors|null>(null);
  const [team,setTeam]=useState("");
  const [busy,setBusy]=useState("");
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [rankingPage,setRankingPage]=useState(0);
  const rankingSort=useTableSort(()=>setRankingPage(0));
  const refresh=useCallback(async()=>{
    try{
      const [m,a]=await Promise.all([
        api<Metrics>("/review/team-metrics"),
        api<Monitors>("/monitoring/assignments")
      ]);
      setMetrics(m);setAssignment(a);
      setRankingPage(current=>Math.min(current,Math.max(0,Math.ceil(m.teams.length/25)-1)));
      setTeam(old=>m.teams.some(row=>row.team_id===old)?old:(m.teams[0]?.team_id||""));
      setError("");
    }catch(e){setError((e as Error).message);}
  },[]);
  useEffect(()=>{
    void refresh();
    const timer=window.setInterval(()=>{if(!document.hidden)void refresh();},15000);
    const reconnect=()=>{if(!document.hidden)void refresh();};
    window.addEventListener("online",reconnect);
    document.addEventListener("visibilitychange",reconnect);
    return()=>{
      window.clearInterval(timer);
      window.removeEventListener("online",reconnect);
      document.removeEventListener("visibilitychange",reconnect);
    };
  },[refresh]);
  const selected=useMemo(()=>metrics?.teams.find(row=>row.team_id===team),[metrics,team]);
  async function reassign(monitorPersonId:string){
    if(!selected||!organiser)return;
    setBusy(selected.team_id);setError("");setNotice("");
    try{
      await api("/monitoring/teams/"+selected.team_id,"PUT",
        {monitor_person_id:monitorPersonId||null},auth.csrf_token);
      setNotice("Responsible monitor changed. Review permissions updated immediately.");
      await refresh();
    }catch(e){setError((e as Error).message);}
    finally{setBusy("");}
  }
  return <div className="review-analytics">
    <div className="review-analytics-header">
      <div><span className="eyebrow">{mode==="dashboard"?"TEAM PERFORMANCE":"COMPETITION STANDINGS"}</span>
        <h3>{mode==="dashboard"?"Team dashboard":"Team rankings"}</h3>
        <p className="muted">{mode==="dashboard"?
          "Every monitor can inspect every team. Figures come from synchronized jobs and saved quality reviews.":
          "Live provisional leaderboard based on reviewed keypoints, completed jobs and reviewed frames. Ties share a rank."}</p></div>
      <button className="btn secondary" onClick={()=>void refresh()}><RefreshCw size={15}/> Refresh</button>
    </div>
    {error&&<div role="alert" className="alert danger">{error}</div>}
    {notice&&<div role="status" className="alert success">{notice}</div>}
    {!metrics&&<div className="panel">Loading team statistics…</div>}
    {metrics&&mode==="dashboard"&&<>
      <section className="panel">
        <div className="review-team-toolbar">
          <label>Competition team<select value={team} onChange={e=>setTeam(e.target.value)}>
            {metrics.teams.map(row=><option key={row.team_id} value={row.team_id}>{row.team_name}</option>)}
          </select></label>
          <div className="review-monitor-assign">
            <label>Responsible reviewer / monitor
              <select value={selected?.monitor_person_id||""} disabled={!organiser||!selected||!!busy}
                onChange={e=>void reassign(e.target.value)}>
                <option value="">Unassigned</option>
                {assignment?.monitors.map(row=><option key={row.id} value={row.id}>
                  {row.name} · {row.assigned_count} teams
                </option>)}
              </select>
            </label>
            {!organiser&&<span className="muted small">Organisers can reassign teams at any time.</span>}
          </div>
        </div>
        {selected&&<div className="team-metric-grid">
          <div className="metric-tile"><span>Elapsed publish-to-submit time / frame</span><strong>{time(selected.average_seconds_per_frame)}</strong><small>{selected.timed_frames} frames with timestamps</small></div>
          <div className="metric-tile"><span>Correct keypoints</span><strong>{accuracy(selected.keypoint_accuracy_pct)}</strong><small>{integer(selected.evaluated_keypoints)} reviewed points</small></div>
          <div className="metric-tile"><span>Accepted jobs</span><strong>{selected.jobs_completed} / {selected.jobs_total}</strong><small>{selected.jobs_awaiting_review} awaiting review</small></div>
          <div className="metric-tile"><span>Frames submitted</span><strong>{integer(selected.frames_submitted)}</strong><small>{integer(selected.frames_assigned)} assigned</small></div>
          <div className="metric-tile"><span>Frames with actual annotations</span><strong>{
            selected.frames_annotated===null?"Not available":integer(selected.frames_annotated)
          }</strong><small>Frames containing downloaded Supervisely labels</small></div>
          <div className="metric-tile"><span>Annotations downloaded</span><strong>{integer(selected.frames_synced)}</strong><small>Frames in complete local snapshots</small></div>
          <div className="metric-tile"><span>Frames quality-reviewed</span><strong>{integer(selected.frames_reviewed)}</strong><small>{selected.correct_keypoints} correct · {selected.missing_keypoints} missing · {selected.incorrect_keypoints} incorrect points</small></div>
        </div>}
      </section>
      <div className="settings-note"><BarChart3 size={18}/><p><strong>Timing:</strong> {metrics.time_definition}. This includes time before the annotator starts working; it is not precise editing time per image. <strong>Accuracy:</strong> {metrics.accuracy_definition}. {metrics.frames_definition}.</p></div>
    </>}
    {metrics&&mode==="rankings"&&<>
      <section className="panel">
        <div className="ranking-intro"><Trophy size={24}/><div><h4>Provisional leaderboard</h4>
          <p>{metrics.winner_policy}. Unscored teams are not ranked yet.</p></div></div>
        <div className="table-scroll"><table className="data-table leaderboard-table">
          <thead><tr>
            {rankingSort.header("rank","Rank")}
            {rankingSort.header("team_name","Team")}
            {rankingSort.header("keypoint_accuracy_pct","Keypoint accuracy")}
            {rankingSort.header("evaluated_keypoints","Reviewed points")}
            {rankingSort.header("jobs_completed","Completed jobs")}
            {rankingSort.header("frames_reviewed","Frames reviewed")}
            {rankingSort.header("frames_submitted","Frames submitted")}
            {rankingSort.header("average_seconds_per_frame","Elapsed per frame")}
          </tr></thead>
          <tbody>{rankingSort.rows(metrics.teams,(row,key)=>row[key as keyof typeof row]).slice(rankingPage*25,(rankingPage+1)*25).map(row=><tr key={row.team_id}>
            <td><strong>{row.rank===null?"—":row.rank===1?"🥇 1":row.rank===2?"🥈 2":row.rank===3?"🥉 3":"#"+row.rank}</strong></td>
            <td><strong>{row.team_name}</strong></td>
            <td><strong>{accuracy(row.keypoint_accuracy_pct)}</strong></td>
            <td>{integer(row.evaluated_keypoints)}</td>
            <td>{row.jobs_completed}</td><td>{integer(row.frames_reviewed)}</td>
            <td>{integer(row.frames_submitted)}</td>
            <td>{time(row.average_seconds_per_frame)}</td>
          </tr>)}</tbody></table></div>
        <div className="settings-action-row">
          <button className="btn secondary" disabled={rankingPage===0}
            onClick={()=>setRankingPage(current=>Math.max(0,current-1))}>Previous teams</button>
          <button className="btn secondary" disabled={(rankingPage+1)*25>=metrics.teams.length}
            onClick={()=>setRankingPage(current=>current+1)}>Next teams</button>
        </div>
        {!metrics.teams.length&&<p className="muted">No competition teams yet.</p>}
      </section>
      <div className="settings-note"><ShieldCheck size={18}/><p>{metrics.accuracy_definition}. Rankings update when monitors save reviews. Equal accuracy, completed jobs and reviewed frames share a rank. Do not announce winners until all relevant work is reviewed.</p></div>
    </>}
  </div>;
}
