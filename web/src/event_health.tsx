import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, AlertTriangle, Clock3, Database, ShieldCheck } from "lucide-react";
import { api } from "./api";
import { RefreshButton } from "./refresh_button";
import { formatLisbonTime } from "./event_time";

type Queue={kind:string;ready:number;scheduled:number;running:number;attention:number;wait_seconds:number;
  run_seconds:number;expired_leases:number;finished_15m:number;average_run_seconds:number|null;
  estimated_drain_seconds:number|null;state:string};
type Health={generated_at:number;state:string;database:string;
  alerts:{level:string;title:string;action:string}[];
  workers:{role:string;expected:number;reporting:number;errors:number;state:string;last_seen:number|null;longest_unfinished_seconds:number}[];
  queues:Queue[];sync:{active_bindings:number;oldest_sync:number|null;never_synced:number};
  datasets:{id:string;name:string;state:string;checked:number;missing:number;import_status:string}[];
  storage:{state:string;free_bytes:number|null;total_bytes:number|null};
  backup:{state:string;completed_at:number|null;restore_tested:boolean;last_attempt_failed:boolean}};
const age=(seconds:number|null)=>seconds===null?"—":seconds<60?`${Math.round(seconds)}s`:
  seconds<3600?`${Math.floor(seconds/60)}m ${Math.round(seconds%60)}s`:`${Math.floor(seconds/3600)}h ${Math.floor(seconds%3600/60)}m`;
const stamp=(value:number|null)=>value===null?"Not recorded":formatLisbonTime(value);
const title=(value:string)=>value.replaceAll("_"," ");

export function EventHealth(){
  const [data,setData]=useState<Health|null>(null);
  const [error,setError]=useState("");
  const [loading,setLoading]=useState(false);
  const [growth,setGrowth]=useState<Record<string,number>>({});
  const [interval,setInterval]=useState<number|null>(null);
  const previous=useRef<Health|null>(null);
  const active=useRef(false);
  const mounted=useRef(false);
  const refresh=useCallback(async()=>{
    if(active.current)return;
    active.current=true;setLoading(true);
    try{
      const result=await api<Health>("/event-health");
      if(!mounted.current)return;
      const old=previous.current;
      setGrowth(old?Object.fromEntries(result.queues.map(q=>[q.kind,q.ready+q.running-
        (old.queues.find(v=>v.kind===q.kind)?.ready||0)-(old.queues.find(v=>v.kind===q.kind)?.running||0)])):{});
      setInterval(old?result.generated_at-old.generated_at:null);
      previous.current=result;setData(result);setError("");
      return true;
    }catch(e){if(mounted.current)setError((e as Error).message);return false;}
    finally{active.current=false;if(mounted.current)setLoading(false);}
  },[]);
  useEffect(()=>{
    mounted.current=true;
    let alive=true;
    let timer:number|undefined;
    const poll=async(initial=false)=>{
      if(initial||!document.hidden)await refresh();
      if(alive)timer=window.setTimeout(()=>void poll(),20000);
    };
    void poll(true);
    return()=>{alive=false;mounted.current=false;window.clearTimeout(timer);};
  },[refresh]);
  if(!data)return <section className="panel">{error?<>
    <p className="alert danger" role="alert">Health checks unavailable: {error}. This does not establish whether jobs are safe or lost.</p>
    <RefreshButton refresh={refresh}>Retry health checks</RefreshButton></>:<p role="status">Checking event health…</p>}</section>;
  return <div className="event-health" aria-busy={loading}>
    <div className="annotation-stats-toolbar"><div><h3><Activity size={20}/> Event health</h3>
      <p className="muted small">Updated {stamp(data.generated_at)} · refreshes every 20 seconds</p></div>
      <RefreshButton refresh={refresh} disabled={loading}>Refresh health</RefreshButton></div>
    {error&&<p className="alert danger" role="alert">Refresh failed. These are older observations: {error}</p>}
    <section className={`health-summary health-${data.state}`}>
      <ShieldCheck size={25}/><div><h3>{data.state==="ok"?"No alerts in the available checks":
        data.state==="critical"?"Event needs attention":"Checks need attention"}</h3>
      <p>Queues and source checks apply to the selected event. Worker and storage checks cover the shared host.</p></div>
    </section>
    {!!data.alerts.length&&<section className="panel"><h3><AlertTriangle size={18}/> Actionable alerts</h3>
      <div className="health-alerts">{data.alerts.map((a,i)=><div className={`health-alert health-${a.level}`} key={i}>
        <strong>{a.title}</strong><p>{a.action}</p></div>)}</div></section>}
    <section className="panel"><div className="panel-heading"><div><h3>Queue delays & congestion</h3>
      <p>Ready work excludes scheduled retries. Yellow: waiting ≥2m, ≥20 ready tasks or a long-running operation. Red: waiting ≥10m or intervention required.</p></div></div>
      <div className="table-scroll"><table className="data-table"><thead><tr>
        <th>Queue</th><th>Ready</th><th>Scheduled</th><th>Running</th><th>Needs action</th><th>Oldest ready wait</th>
        <th>Longest run</th><th>Finished / 15m</th><th>Avg. run</th><th>Est. ready drain</th><th>Backlog change</th>
      </tr></thead><tbody>{data.queues.map(q=><tr key={q.kind}>
        <td><span className={`health-indicator health-${q.state}`}>{title(q.kind)}</span></td>
        <td>{q.ready}</td><td>{q.scheduled}</td><td>{q.running}</td><td>{q.attention}</td>
        <td>{age(q.wait_seconds)}</td><td>{age(q.run_seconds)}{q.expired_leases>0&&<small> · {q.expired_leases} expired leases</small>}</td>
        <td>{q.finished_15m}</td><td>{age(q.average_run_seconds)}</td><td>{age(q.estimated_drain_seconds)}</td>
        <td>{growth[q.kind]===undefined?"—":`${growth[q.kind]>0?"+":""}${growth[q.kind]}`}</td>
      </tr>)}</tbody></table></div>
      {!data.queues.length&&<p className="muted">No queued operations in this event yet.</p>}
      <p className="muted small">Backlog change compares ready + running work{interval!==null?` over the last ${age(interval)}`:" between refreshes"}.
        Drain estimates assume no new arrivals and the same completion rate as the last 15 minutes; they are unavailable without recent completions.
        Counts describe operations, not frames. Reused validation tasks show their latest completion only.</p>
      <p className="muted small">Long runs are flagged after 5 minutes (30 minutes for imports, mirroring, exports and resets).
        These observations never cancel, retry or reassign jobs automatically.</p>
    </section>
    <section className="panel"><h3>Worker activity</h3><p className="muted small">Reporting means a cycle was observed within 90 seconds; it does not prove Supervisely is reachable.
      Long-running calls remain visible. Missing telemetry may also mean the new worker version has not been deployed.</p>
      <div className="table-scroll"><table className="data-table"><thead><tr><th>Role</th><th>Reporting / expected</th><th>Cycle errors</th><th>Last report</th><th>Unfinished cycle</th></tr></thead>
        <tbody>{data.workers.map(w=><tr key={w.role}><td><span className={`health-indicator health-${w.state}`}>{title(w.role)}</span></td>
          <td>{w.reporting} / {w.expected}</td><td>{w.errors}</td><td>{stamp(w.last_seen)}</td><td>{age(w.longest_unfinished_seconds)}</td></tr>)}</tbody></table></div>
    </section>
    <div className="health-card-grid">
      <section className="panel"><h3><Clock3 size={18}/> Supervisely status</h3><p>{data.sync.active_bindings} active job bindings · {data.sync.never_synced} not yet synced</p>
        <p>Oldest active-job sync: {stamp(data.sync.oldest_sync)}</p><p className="muted small">Stale after 3 minutes. This reads saved sync results; opening this page makes no Supervisely API calls.</p></section>
      <section className="panel"><h3><Database size={18}/> Media storage</h3>
        <p>{data.storage.free_bytes===null?"Free space unavailable":`${(data.storage.free_bytes/1024**3).toFixed(1)} GiB free`}</p>
        <p className="muted small">Media filesystem only. Docker's separate database volume and Windows host capacity must also be checked.</p></section>
      <section className="panel"><h3>Backup receipt</h3><p>Last successful snapshot: {stamp(data.backup.completed_at)}</p>
        <p>{data.backup.last_attempt_failed?"Latest attempt failed.":""} {data.backup.restore_tested?"Receipt records a restore test.":"This snapshot has not been marked restore-tested."}</p>
        <p className="muted small">Receipt is stale after 15 minutes. Independent storage is unverified. A receipt does not establish current archive integrity or protect mounted originals.</p></section>
      <section className="panel"><h3>Local services</h3><p>API responding · database {data.database}</p>
        <p className="muted small">Public tunnel availability is not tested from inside the app. Check the public hostname from a separate device.</p></section>
    </div>
    <section className="panel"><h3>Dataset source availability</h3><p className="muted small">Checks up to 10 indexed images per ready dataset through the same source resolver used by the app. This does not certify every file.</p>
      <div className="table-scroll"><table className="data-table"><thead><tr><th>Dataset</th><th>Import</th><th>Checked</th><th>Unavailable</th></tr></thead>
        <tbody>{data.datasets.map(d=><tr key={d.id}><td><span className={`health-indicator health-${d.state}`}>{d.name}</span></td>
          <td>{d.import_status}</td><td>{d.checked}</td><td>{d.missing}</td></tr>)}</tbody></table></div>
    </section>
  </div>;
}
