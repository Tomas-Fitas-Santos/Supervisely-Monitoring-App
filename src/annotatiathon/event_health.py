"""Organiser-only operational observations; never retries or modifies work."""
import json
import os
import shutil
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .config import get_settings
from .db import get_db
from .events_api import current_event
from .models import LabelingJob, MediaAsset, MediaDataset, NativeJobBinding, NativeOperation, epoch
from .mounted_datasets import media_file
from .worker_health import health_directory, worker_status

router = APIRouter(prefix="/api/v1", tags=["event health"])
DEFAULT_WORKERS = {"publish": 4, "validation": 3, "correction": 3, "status": 1,
                   "recovery": 1, "bulk": 1, "mirror": 1, "indexer": 1}


def queue_health(db, event_id, now):
    op = NativeOperation
    ready_at = case((op.next_attempt_at > op.created_at, op.next_attempt_at), else_=op.created_at)
    pending = op.status == "pending"
    ready = pending & (ready_at <= now)
    running = op.status == "running"
    finished = (op.status == "done") & (op.completed_at >= now-900) & (op.completed_at <= now)
    rows = db.execute(select(
        op.kind,
        func.sum(case((ready, 1), else_=0)).label("ready"),
        func.sum(case((pending & (ready_at > now), 1), else_=0)).label("scheduled"),
        func.sum(case((running, 1), else_=0)).label("running"),
        func.sum(case((op.status.in_(("attention", "blocked", "failed")), 1), else_=0)).label("attention"),
        func.min(case((ready, ready_at))).label("oldest_ready"),
        func.min(case((running, op.claimed_at))).label("oldest_running"),
        func.sum(case((running & (op.lease_until < now), 1), else_=0)).label("expired_leases"),
        func.sum(case((finished, 1), else_=0)).label("finished_15m"),
        func.avg(case((finished & (op.claimed_at <= op.completed_at),
                       op.completed_at-op.claimed_at))).label("average_run_seconds"),
    ).where(op.event_id == event_id).group_by(op.kind)).mappings()
    result = []
    for row in rows:
        value = dict(row)
        wait = max(0, now-row["oldest_ready"]) if row["oldest_ready"] is not None else 0
        run = max(0, now-row["oldest_running"]) if row["oldest_running"] is not None else 0
        long_threshold = 1800 if any(word in row["kind"] for word in ("index", "mirror", "export", "append", "reset", "prepare")) else 300
        severity = ("critical" if row["attention"] or wait >= 600 else "warning"
                    if wait >= 120 or row["ready"] >= 20 or run >= long_threshold or row["expired_leases"] else "ok")
        result.append({**value, "wait_seconds": wait, "run_seconds": run, "state": severity,
                       "estimated_drain_seconds": round(row["ready"]*900/row["finished_15m"])
                       if row["ready"] and row["finished_15m"] else None})
    return result


def backup_health(now):
    try:
        path = health_directory() / "backup.json"
        if path.stat().st_size > 4096:
            raise ValueError("oversized receipt")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        completed = int(datetime.fromisoformat(data["completed_utc"]).timestamp())
        if completed > now + 60:
            raise ValueError("future backup")
        return {"state": "warning" if data.get("last_attempt_failed") or now-completed > 900 else "ok",
                "completed_at": completed, "restore_tested": bool(data.get("restore_tested")),
                "independent_storage": "unverified", "last_attempt_failed": bool(data.get("last_attempt_failed"))}
    except (OSError, ValueError, KeyError, TypeError):
        return {"state": "unknown", "completed_at": None, "restore_tested": False,
                "independent_storage": "unverified", "last_attempt_failed": False}


def event_health(db, event, *, now=None):
    now = epoch() if now is None else now
    alerts = []

    def alert(level, title, action):
        alerts.append({"level": level, "title": title, "action": action})

    expected = DEFAULT_WORKERS
    configured = os.getenv("NIGHTJAR_EXPECTED_WORKERS")
    if configured:
        try:
            expected = json.loads(configured)
            if not isinstance(expected, dict) or not expected or any(
                k not in DEFAULT_WORKERS or type(v) is not int or not 1 <= v <= 32 for k, v in expected.items()
            ):
                raise ValueError("invalid expected fleet")
        except (ValueError, TypeError):
            expected = DEFAULT_WORKERS
            alert("warning", "Worker expectations are misconfigured", "Check NIGHTJAR_EXPECTED_WORKERS; displaying the standard live fleet.")
    workers, unreadable = worker_status(now, expected)
    if unreadable:
        alert("warning", "Some worker telemetry could not be read", "Check the shared media volume and permissions. Missing telemetry is not proof of lost work.")
    for worker in workers:
        if worker["state"] != "ok":
            alert("warning", f"{worker['role']}: {worker['reporting']}/{worker['expected']} recently reporting",
                  "Inspect this worker's logs and current operation. A long cycle may be legitimate; do not recreate its jobs.")
    queues = queue_health(db, event.id, now)
    for queue in queues:
        if queue["state"] != "ok":
            alert(queue["state"], f"{queue['kind']}: queue needs attention",
                  "Open Jobs & worker queue for details. Reconcile uncertain writes through Supervisely synchronization; never clear assignments to unblock them.")
    # Event-local bindings that still require polling; one fresh job must not
    # hide other jobs whose statuses have stopped updating.
    bindings = db.execute(select(func.count(), func.min(NativeJobBinding.last_synced_at),
        func.sum(case((NativeJobBinding.last_synced_at.is_(None), 1), else_=0))).join(
        LabelingJob, LabelingJob.id == NativeJobBinding.job_id).where(
        NativeJobBinding.event_id == event.id,
        LabelingJob.status.in_(("in_progress", "on_review", "needs_correction")),
    )).one()
    sync = {"active_bindings": bindings[0], "oldest_sync": bindings[1], "never_synced": bindings[2] or 0}
    if bindings[0] and (bindings[2] or bindings[1] is None or now-bindings[1] > 180):
        alert("warning", "Some active jobs have stale Supervisely status", "Check the status worker and Supervisely connectivity. Submissions may be waiting to be detected.")
    datasets = []
    for ds in db.scalars(select(MediaDataset).where(MediaDataset.event_id == event.id)):
        if ds.status != "ready":
            datasets.append({"id": ds.id, "name": ds.name, "state": "unknown", "checked": 0,
                             "missing": 0, "import_status": ds.status})
            continue
        sample = db.scalars(select(MediaAsset.relative_path).where(
            MediaAsset.dataset_id == ds.id, MediaAsset.kind.in_(("image", "video_frame")),
        ).order_by(MediaAsset.id).limit(10)).all()
        missing = 0
        for relative in sample:
            try:
                media_file(ds, relative)
            except (OSError, ValueError, HTTPException):
                missing += 1
        datasets.append({"id": ds.id, "name": ds.name, "state": "critical" if missing else "ok" if sample else "unknown",
                         "checked": len(sample), "missing": missing, "import_status": ds.status})
        if missing:
            alert("critical", f"{ds.name}: source images unavailable",
                  "Restore the original source mount/path. Do not reupload or delete the dataset. This is a sample check, not a full integrity audit.")
    try:
        disk = shutil.disk_usage(get_settings().local_data_dir)
        storage = {"state": "critical" if disk.free < 2*1024**3 or disk.free/disk.total < .05
                   else "warning" if disk.free < 10*1024**3 or disk.free/disk.total < .1 else "ok",
                   "free_bytes": disk.free, "total_bytes": disk.total}
    except OSError:
        storage = {"state": "unknown", "free_bytes": None, "total_bytes": None}
    if storage["state"] != "ok":
        alert("critical" if storage["state"] == "critical" else "warning", "Media storage needs checking",
              "Check host and Docker database-volume free space before more uploads. Never remove event media or database volumes to free space.")
    backup = backup_health(now)
    if backup["state"] != "ok":
        alert("warning", "No recent successful backup receipt", "Check the backup process. Existing older backups may exist; this page does not scan or restore them.")
    return {"event_id": event.id, "generated_at": now, "database": "reachable",
            "state": "critical" if any(a["level"] == "critical" for a in alerts) else "warning" if alerts else "ok",
            "alerts": alerts, "workers": workers, "queues": queues, "sync": sync,
            "datasets": datasets, "storage": storage, "backup": backup}


@router.get("/event-health")
def get_event_health(ctx: AuthContext = Depends(requires("organiser")), db: Session = Depends(get_db)):
    return event_health(db, current_event(db, ctx))
