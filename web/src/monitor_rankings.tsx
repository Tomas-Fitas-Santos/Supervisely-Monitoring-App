import { useCallback, useEffect, useState } from "react";
import { Trophy } from "lucide-react";
import { api } from "./api";
import { RefreshButton } from "./refresh_button";

type MonitorRank={reviewer_id:string;name:string;rank:number;reviewed_images:number;
  completed_reviews:number;video_frames:number;still_images:number;last_reviewed_at:number;
  average_review_score:number|null;scored_reviews:number};
type Rankings={items:MonitorRank[];reviewer_count:number;reviewed_images:number;
  completed_reviews:number;counting_policy:string};
const count=(value:number)=>value.toLocaleString();

export function MonitorRankings({currentStaffId}:{currentStaffId:string}){
  const [period,setPeriod]=useState<"all"|"today">("all");
  const [data,setData]=useState<Rankings|null>(null);
  const [error,setError]=useState("");
  const refresh=useCallback(async(signal?:AbortSignal)=>{
    try{
      const result=await api<Rankings>("/rankings/monitors?period="+period);
      if(!signal?.aborted){setData(result);setError("");}
    }catch(e){if(!signal?.aborted)setError((e as Error).message);return false;}
  },[period]);
  useEffect(()=>{
    const controller=new AbortController();
    setData(null);
    void refresh(controller.signal);
    const timer=window.setInterval(()=>{
      if(!document.hidden)void refresh(controller.signal);
    },15000);
    return()=>{controller.abort();window.clearInterval(timer);};
  },[refresh]);
  return <section className="panel event-ranking-panel">
    <div className="review-analytics-header">
      <div><span className="eyebrow">REVIEW CONTRIBUTIONS</span>
        <h3><Trophy size={19}/> Monitor rankings</h3>
        <p className="muted">Ranked by distinct images reviewed. Includes reviews already completed in this event.</p>
      </div><RefreshButton refresh={()=>refresh()}>Refresh monitors</RefreshButton>
    </div>
    <div className="filter-bar"><label>Review period<select value={period}
      onChange={e=>setPeriod(e.target.value as typeof period)}>
      <option value="all">All event reviews</option>
      <option value="today">Today · Lisbon time</option>
    </select></label></div>
    {error&&<p className="alert danger" role="alert">{error}</p>}
    {data&&<>
      <p className="muted small">{count(data.reviewer_count)} reviewers · {count(data.reviewed_images)} distinct images
        {" · "}{count(data.completed_reviews)} completed reviews</p>
      <div className="table-scroll"><table className="data-table">
        <thead><tr><th>Rank</th><th>Monitor</th><th>Images reviewed</th><th>Video frames</th>
          <th>Still images</th><th>Completed reviews</th><th>Average review score</th><th>Last review · Lisbon</th></tr></thead>
        <tbody>{data.items.map(row=><tr key={row.reviewer_id}>
          <td><strong>#{row.rank}</strong></td>
          <td><strong>{row.name}</strong>{row.reviewer_id===currentStaffId&&
            <span className="soft-pill" style={{marginLeft:8}}>You</span>}</td>
          <td><strong>{count(row.reviewed_images)}</strong></td><td>{count(row.video_frames)}</td>
          <td>{count(row.still_images)}</td><td>{count(row.completed_reviews)}</td>
          <td title={`${count(row.scored_reviews??0)} scored reviews`}>
            {row.average_review_score==null?"—":`${row.average_review_score.toFixed(2)} / 5`}</td>
          <td>{new Date(row.last_reviewed_at*1000).toLocaleString(undefined,{timeZone:"Europe/Lisbon"})}</td>
        </tr>)}</tbody>
      </table>{!data.items.length&&<div className="empty-table">No completed reviews in this period.</div>}</div>
      <p className="muted small">{data.counting_policy} Opened or released frames do not count.
        Completed reviews include repeat reviews; images reviewed counts each image once per reviewer.
        {" "}Average review score uses the same 0–5 score as team results, across all scored reviews in the selected period,
        including repeat reviews. Historical reviews without a recoverable 0–5 score are excluded from the average.</p>
    </>}
    {!data&&!error&&<p className="muted" role="status">Loading monitor rankings…</p>}
  </section>;
}
