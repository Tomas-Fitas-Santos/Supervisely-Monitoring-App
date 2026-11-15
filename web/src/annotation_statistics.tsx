import { useCallback, useEffect, useRef, useState } from "react";
import { BarChart3, CheckCircle2, Clock3, Image, TrendingUp, Video } from "lucide-react";
import { api } from "./api";
import { RefreshButton } from "./refresh_button";
import { formatLisbonInput, formatLisbonTime } from "./event_time";

type Counts={total:number;complete:number;reviewed:number};
type Statistics={generated_at:number;last_native_sync:number|null;
  schedule:{start_at:number;end_at:number;source:"configured"|"preview";timezone:string;
    elapsed_seconds:number;remaining_seconds:number};
  totals:Counts&{remaining:number;awaiting_review:number;percent:number;undated_completions:number};
  kinds:Record<"video_frame"|"image",Counts>;
  datasets:(Counts&{id:string;name:string;video_frames:number;images:number})[];
  jobs:Record<string,number>;frame_assignments:number;active_annotators:number;
  forecast:{status:string;projected_complete:number|null;recent_per_hour:number|null;
    average_per_hour:number|null;completed_in_window:number;recent_completions:number;required_per_hour:number|null};
  hourly:{start_at:number;end_at:number;complete:number;future:boolean}[]};
const number=(value:number|null)=>value===null?"—":value.toLocaleString(undefined,{maximumFractionDigits:1});
const clock=(value:number)=>new Intl.DateTimeFormat("en-GB",{timeZone:"Europe/Lisbon",hour:"2-digit",minute:"2-digit"}).format(value*1000);
const duration=(seconds:number)=>`${Math.floor(seconds/3600)}h ${Math.floor(seconds%3600/60)}m`;
const forecastMessages:Record<string,string>={
  before_start:"The estimate starts after the event begins.",
  warming_up:"Gathering the first 15 minutes of event activity.",
  no_observations:"Waiting for timed, complete annotations during this event window.",
  ended:"This event window has ended. Current completion totals are shown below.",
};

export function AnnotationStatistics(){
  const [data,setData]=useState<Statistics|null>(null);
  const [eventDate,setEventDate]=useState("");
  const [error,setError]=useState("");
  const [loading,setLoading]=useState(false);
  const requestId=useRef(0);
  const refresh=useCallback(async()=>{
    const current=++requestId.current;
    try{
      setLoading(true);
      const result=await api<Statistics>("/labeling/annotation-statistics"+
        (eventDate?"?event_date="+encodeURIComponent(eventDate):""));
      if(current===requestId.current){
        setData(result);setError("");
        // Keep an overnight preview anchored to its original date after 07:00.
        if(result.schedule.source==="preview"&&!eventDate)
          setEventDate(formatLisbonInput(result.schedule.start_at).slice(0,10));
      }
      return true;
    }catch(e){if(current===requestId.current)setError((e as Error).message);return false;}
    finally{if(current===requestId.current)setLoading(false);}
  },[eventDate]);
  useEffect(()=>{
    let alive=true;
    let timer:number|undefined;
    const poll=async(initial=false)=>{
      if(initial||!document.hidden)await refresh();
      if(alive)timer=window.setTimeout(()=>void poll(),30000);
    };
    void poll(true);
    return ()=>{alive=false;requestId.current++;window.clearTimeout(timer);};
  },[refresh]);
  if(!data)return <section className="panel" aria-busy={loading}>
    {error?<><p role="alert" className="alert danger">{error}</p><RefreshButton refresh={refresh}>Retry statistics</RefreshButton></>:
      <p role="status">Loading annotation statistics…</p>}</section>;
  const {totals,forecast,schedule}=data;
  const maximum=Math.max(1,...data.hourly.map(h=>h.complete));
  const previewDay=formatLisbonInput(schedule.start_at).slice(0,10);
  return <div className="annotation-statistics" aria-busy={loading}>
    <div className="annotation-stats-toolbar">
      <div><h3><BarChart3 size={19}/> Frames & images</h3>
        <p className="muted small">Unique source coverage · updated {clock(data.generated_at)} · refreshes every 30 seconds</p></div>
      <RefreshButton refresh={refresh}>Refresh statistics</RefreshButton>
    </div>
    {error&&<p role="alert" className="alert danger">Statistics could not refresh. Showing the last successful update: {error}</p>}
    <section className="annotation-forecast" aria-label="End-of-event forecast">
      <div>
        <span className="eyebrow">END-OF-EVENT ESTIMATE · {clock(schedule.end_at)} LISBON</span>
        <strong className="annotation-projection">{number(forecast.projected_complete)}</strong>
        <p>complete frames / images out of {number(totals.total)}</p>
        <p className="small">{forecast.projected_complete===null?forecastMessages[forecast.status]:
          `If the recent rate continues: ${number(forecast.recent_per_hour)} complete per hour.`}</p>
      </div>
      <div className="annotation-forecast-schedule">
        <strong><Clock3 size={16}/> {formatLisbonTime(schedule.start_at)} → {formatLisbonTime(schedule.end_at)}</strong>
        <p>{duration(schedule.elapsed_seconds)} elapsed · {duration(schedule.remaining_seconds)} remaining</p>
        {schedule.source==="preview"&&<>
          <label>Event date for forecast preview
            <input type="date" value={eventDate||previewDay} onChange={e=>setEventDate(e.target.value)}/>
          </label>
          <p className="small">No saved event schedule. Preview uses 19:00 to 07:00 the following day; it does not change event settings.</p>
        </>}
        <p className="small">Estimate uses up to the last hour of recorded complete annotations and cannot exceed the indexed inventory. It is a projection, not a guarantee.</p>
      </div>
    </section>
    <div className="stat-grid annotation-stat-cards">
      <div className="stat-card"><CheckCircle2 size={19}/><span className="muted small">Complete annotations</span>
        <strong>{number(totals.complete)}</strong><span>{totals.percent}% of source inventory</span></div>
      <div className="stat-card"><Image size={19}/><span className="muted small">Still to complete</span>
        <strong>{number(totals.remaining)}</strong><span>Includes unassigned and unfinished work</span></div>
      <div className="stat-card"><CheckCircle2 size={19}/><span className="muted small">Reviewed source images</span>
        <strong>{number(totals.reviewed)}</strong><span>{number(totals.awaiting_review)} complete, not yet reviewed</span></div>
      <div className="stat-card"><TrendingUp size={19}/><span className="muted small">Recent completions / hour</span>
        <strong>{number(forecast.recent_per_hour)}</strong><span>Event average: {number(forecast.average_per_hour)} / hour</span></div>
    </div>
    <section className="panel annotation-coverage-panel">
      <div className="panel-heading"><div><h3>Source coverage</h3><p>Each source image counts once, even across correction rounds and repeat jobs.</p></div></div>
      <progress aria-label="Overall source completion" max={totals.total||1} value={totals.complete}/>
      <div className="annotation-kind-grid">
        {(["video_frame","image"] as const).map(kind=>{
          const row=data.kinds[kind];
          return <div key={kind}>{kind==="video_frame"?<Video size={18}/>:<Image size={18}/>}
            <strong>{kind==="video_frame"?"Video frames":"Standalone images"}</strong>
            <p>{number(row.complete)} / {number(row.total)} complete</p>
            <span className="muted small">{number(row.reviewed)} reviewed</span>
          </div>;
        })}
      </div>
      <p className="muted small">Complete means the required bird box and head landmarks passed structural validation. Monitor review assesses quality separately. Unsubmitted drafts in Supervisely are not counted.</p>
      {totals.undated_completions>0&&<p className="alert warning">{number(totals.undated_completions)} historical completions have no recorded completion time. They count toward coverage, but are excluded from rates and the activity chart.</p>}
    </section>
    <section className="panel">
      <div className="panel-heading"><div><h3>Completion activity</h3><p>New unique completions recorded during the event window. Validation delays can shift activity later.</p></div></div>
      <div className="annotation-hourly-chart" role="list" aria-label="Completions by time interval">
        {data.hourly.map(hour=><div role="listitem" className="annotation-hour" key={hour.start_at}
          aria-label={`${clock(hour.start_at)} to ${clock(hour.end_at)}: ${hour.future?"upcoming":number(hour.complete)+" complete"}`}>
          <span className="small">{hour.future?"—":number(hour.complete)}</span>
          <div className="annotation-hour-track"><div style={{height:`${hour.complete/maximum*100}%`}}/></div>
          <span className="muted small">{clock(hour.start_at)}</span>
        </div>)}
      </div>
      <div className="annotation-operating-stats">
        <p><strong>{number(data.active_annotators)}</strong> annotators with work in progress or correction</p>
        <p><strong>{number(forecast.required_per_hour)}</strong> completions / hour needed to finish all inventory</p>
        <p><strong>{number(data.frame_assignments)}</strong> frame assignments, including repeat jobs</p>
        <p><strong>{number(data.jobs.needs_correction||0)}</strong> jobs awaiting correction</p>
      </div>
    </section>
    <section className="panel">
      <div className="panel-heading"><div><h3>By dataset</h3><p>Video and image datasets keep their own plans and job queues.</p></div></div>
      <div className="table-scroll"><table className="data-table">
        <thead><tr><th>Dataset</th><th>Video frames</th><th>Images</th><th>Complete</th><th>Remaining</th><th>Reviewed</th><th>Coverage</th></tr></thead>
        <tbody>{data.datasets.map(ds=><tr key={ds.id}><td>{ds.name}</td><td>{number(ds.video_frames)}</td>
          <td>{number(ds.images)}</td><td>{number(ds.complete)}</td><td>{number(ds.total-ds.complete)}</td>
          <td>{number(ds.reviewed)}</td><td>{ds.total?Math.round(ds.complete/ds.total*100):0}%</td></tr>)}</tbody>
      </table></div>
      {!data.datasets.length&&<p className="muted">No indexed frames or images in this event yet.</p>}
      <p className="muted small">Last Supervisely job sync: {data.last_native_sync?formatLisbonTime(data.last_native_sync):"No sync recorded"}. A disconnected worker delays these figures; it does not imply lost annotations.</p>
    </section>
  </div>;
}
