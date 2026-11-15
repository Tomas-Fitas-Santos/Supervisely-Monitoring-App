"""Read-only source coverage and measured completion forecasts; no SDK calls."""
from datetime import date, datetime, time, timedelta
from math import ceil
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import LabelingJob, MediaAsset, MediaDataset, NativeJobBinding, ReviewSample, epoch

router = APIRouter(prefix="/api/v1/labeling", tags=["annotation statistics"])
LISBON = ZoneInfo("Europe/Lisbon")


def event_window(event, now, preview_date=None):
    if event.start_at is not None and event.end_at is not None:
        return event.start_at, event.end_at, "configured"
    local = datetime.fromtimestamp(now, LISBON)
    day = preview_date or (local.date() - timedelta(days=1) if local.hour < 7 else local.date())
    start = datetime.combine(day, time(19), LISBON)
    end = datetime.combine(day + timedelta(days=1), time(7), LISBON)
    return int(start.timestamp()), int(end.timestamp()), "preview"


def annotation_statistics(db, event, *, now=None, preview_date=None):
    now = epoch() if now is None else now
    start, end, schedule_source = event_window(event, now, preview_date)
    elapsed = max(0, min(now, end) - start)
    remaining_seconds = max(0, end - max(now, start))
    # Query only one timestamp/flags per source ID. Annotation JSON/image bytes
    # never leave the database for this dashboard, even for 100k-frame datasets.
    validated = select(
        ReviewSample.media_id,
        case((func.count(ReviewSample.snapshot["validated_at"].as_integer()) == func.count(),
              func.min(ReviewSample.snapshot["validated_at"].as_integer())), else_=None).label("validated_at"),
        func.max(case((ReviewSample.completed_at.is_not(None), 1), else_=0)).label("reviewed"),
    ).where(
        ReviewSample.event_id == event.id, ReviewSample.validated_complete.is_(True),
    ).group_by(ReviewSample.media_id).cte("validated_sources").prefix_with("MATERIALIZED", dialect="postgresql")
    # Deduplicate first, then look up source primary keys. Scope through the
    # owning dataset: a redundant asset.event_id predicate can make PostgreSQL
    # rescan the entire fresh import per review when event statistics are stale.
    completed = select(
        validated.c.media_id, MediaAsset.dataset_id, MediaAsset.kind,
        validated.c.validated_at, validated.c.reviewed,
    ).join(MediaAsset, MediaAsset.id == validated.c.media_id).join(
        MediaDataset, MediaDataset.id == MediaAsset.dataset_id,
    ).where(
        MediaDataset.event_id == event.id,
        MediaDataset.status == "ready", MediaAsset.kind.in_(("video_frame", "image")),
    ).subquery()
    # Inventory is limited to indexed datasets; duplicate paths in separate
    # datasets are separate source identities, exactly as they are for jobs.
    coverage = db.execute(select(
        MediaDataset.id, MediaDataset.name, MediaAsset.kind,
        func.count().label("total"),
    ).join(MediaAsset, MediaAsset.dataset_id == MediaDataset.id)
        .where(
            MediaDataset.event_id == event.id, MediaDataset.status == "ready",
            MediaAsset.event_id == event.id, MediaAsset.kind.in_(("video_frame", "image")),
        ).group_by(MediaDataset.id, MediaDataset.name, MediaAsset.kind)
        .order_by(MediaDataset.name, MediaDataset.id)).mappings().all()
    # Keep the inventory separate from the grouped completions. An outer join
    # can choose a quadratic nested loop before fresh imports are ANALYZEd.
    summaries = {(r.dataset_id, r.kind): r for r in db.execute(select(
        completed.c.dataset_id, completed.c.kind, func.count().label("complete"),
        func.sum(completed.c.reviewed).label("reviewed"),
        func.sum(case((completed.c.validated_at.is_(None), 1), else_=0)).label("undated"),
    ).group_by(completed.c.dataset_id, completed.c.kind))}
    kinds = {kind: {"total": 0, "complete": 0, "reviewed": 0} for kind in ("video_frame", "image")}
    datasets = {}
    undated = 0
    for row in coverage:
        summary = summaries.get((row["id"], row["kind"]))
        values = {"total": row["total"], "complete": summary.complete if summary else 0,
                  "reviewed": summary.reviewed if summary else 0}
        ds = datasets.setdefault(row["id"], {"id": row["id"], "name": row["name"],
            "total": 0, "complete": 0, "reviewed": 0, "video_frames": 0, "images": 0})
        for key in ("total", "complete", "reviewed"):
            kinds[row["kind"]][key] += values[key]
            ds[key] += values[key]
        ds["video_frames" if row["kind"] == "video_frame" else "images"] += row["total"]
        undated += summary.undated if summary else 0
    total = sum(row["total"] for row in kinds.values())
    complete = sum(row["complete"] for row in kinds.values())
    reviewed = sum(row["reviewed"] for row in kinds.values())
    # Aggregate by exact recorded time, then bin in Python. At most one small
    # row per validation batch timestamp; old snapshots without time are not
    # guessed from job submission times, which change across corrections.
    timings = db.execute(select(completed.c.validated_at, func.count()).where(
        completed.c.validated_at >= start, completed.c.validated_at <= now,
        completed.c.validated_at < end,
    ).group_by(completed.c.validated_at)).all()
    in_window = sum(count for _, count in timings)
    recent_start = max(start, min(now, end) - 3600)
    recent = sum(count for timestamp, count in timings if timestamp > recent_start)
    recent_seconds = max(0, min(now, end) - recent_start)
    recent_rate = recent * 3600 / recent_seconds if recent_seconds else None
    average_rate = in_window * 3600 / elapsed if elapsed else None
    forecast_status = ("before_start" if now < start else "ended" if now >= end
                       else "warming_up" if elapsed < 900 else "no_observations" if not in_window
                       else "available")
    projected = (min(total, complete + int((recent_rate or 0) * remaining_seconds / 3600))
                 if forecast_status == "available" else None)
    hourly = []
    cursor = start
    bucket_seconds = max(3600, ceil((end - start) / (96 * 3600)) * 3600)
    while cursor < end:
        stop = min(cursor + bucket_seconds, end)
        hourly.append({"start_at": cursor, "end_at": stop,
                       "complete": sum(count for timestamp, count in timings if cursor <= timestamp < stop),
                       "future": cursor > now})
        cursor = stop
    jobs = dict(db.execute(select(LabelingJob.status, func.count()).where(
        LabelingJob.event_id == event.id,
    ).group_by(LabelingJob.status)).all())
    assignments, annotators = db.execute(select(
        func.coalesce(func.sum(LabelingJob.frame_count), 0),
        func.count(func.distinct(case((LabelingJob.status.in_(("in_progress", "needs_correction")),
                                      LabelingJob.assignee_person_id)))),
    ).where(LabelingJob.event_id == event.id)).one()
    last_sync = db.scalar(select(func.max(NativeJobBinding.last_synced_at)).where(
        NativeJobBinding.event_id == event.id,
    ))
    return {
        "event_id": event.id, "generated_at": now, "last_native_sync": last_sync,
        "schedule": {"start_at": start, "end_at": end, "source": schedule_source,
                     "timezone": "Europe/Lisbon", "elapsed_seconds": elapsed,
                     "remaining_seconds": remaining_seconds},
        "totals": {"total": total, "complete": complete, "reviewed": reviewed,
                   "remaining": total - complete, "awaiting_review": complete - reviewed,
                   "percent": round(100 * complete / total, 1) if total else 0,
                   "undated_completions": undated},
        "kinds": kinds, "datasets": list(datasets.values()), "jobs": jobs,
        "frame_assignments": assignments, "active_annotators": annotators,
        "forecast": {"status": forecast_status, "projected_complete": projected,
                     "recent_per_hour": round(recent_rate, 1) if recent_rate is not None else None,
                     "average_per_hour": round(average_rate, 1) if average_rate is not None else None,
                     "completed_in_window": in_window, "recent_completions": recent,
                     "required_per_hour": round((total - complete) * 3600 / remaining_seconds, 1)
                     if remaining_seconds else None},
        "hourly": hourly,
    }


@router.get("/annotation-statistics")
def get_annotation_statistics(
    event_date: date | None = None,
    ctx: AuthContext = Depends(requires("organiser", "monitor", "observer", "activity")),
    db: Session = Depends(get_db),
):
    return annotation_statistics(db, current_event(db, ctx), preview_date=event_date)
