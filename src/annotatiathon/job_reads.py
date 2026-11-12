"""Shared, bounded job pages and exact media membership authorization."""
import base64
from collections import defaultdict
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, cast, exists, func, select, tuple_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, aliased

from .auth import AuthContext
from .models import CompetitionTeam, LabelingJob, NativeJobBinding, Person


class JobCursor(BaseModel):
    version: Literal[1] = 1
    scope: tuple[str, str | None, str | None, str | None]
    after: tuple[str, Annotated[int, Field(ge=1, le=2**31 - 1)], str]


def scoped_jobs(event_id: str, ctx: AuthContext):
    """Event monitors can inspect all teams; they no longer own assigned teams.

    Role-based access remains enforced by the calling API routes, while every
    underlying query stays isolated to the selected event.
    """
    return select(LabelingJob).where(LabelingJob.event_id == event_id)


def read_job_page(db: Session, ctx: AuthContext, event_id: str, *,
                  plan_id: str | None = None, team_id: str | None = None,
                  status: str | None = None, limit: int = 250,
                  cursor: str | None = None) -> dict:
    if not 1 <= limit <= 2000:
        raise HTTPException(status_code=422, detail="Limit must be 1-2000.")
    plan_id, team_id, status = plan_id or None, team_id or None, status or None
    query = scoped_jobs(event_id, ctx)
    for column, value in ((LabelingJob.plan_id, plan_id),
                          (LabelingJob.team_id, team_id), (LabelingJob.status, status)):
        if value is not None:
            if column is LabelingJob.status and value == "active":
                query = query.where(column.in_(["queued", "publishing", "in_progress"]))
            else:
                query = query.where(column == value)
    scope = (event_id, plan_id, team_id, status)
    ordering = (LabelingJob.team_id, LabelingJob.sequence, LabelingJob.id)
    if cursor is not None:
        try:
            if len(cursor) > 1024:
                raise ValueError("Oversized cursor")
            payload = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            position = JobCursor.model_validate_json(payload)
            if position.scope != scope:
                raise ValueError("Cursor filters changed")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid cursor for these filters.") from exc
        query = query.where(tuple_(*ordering) > tuple_(*position.after))
    assignee = aliased(Person)
    query = query.add_columns(CompetitionTeam.name, assignee.name).outerjoin(
        CompetitionTeam, and_(CompetitionTeam.id == LabelingJob.team_id,
                              CompetitionTeam.event_id == event_id),
    ).outerjoin(assignee, and_(assignee.id == LabelingJob.assignee_person_id,
                              assignee.event_id == event_id))
    rows = list(db.execute(query.order_by(*ordering).limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    submissions = defaultdict(list)
    if rows:
        bindings = select(NativeJobBinding, Person.name).outerjoin(Person, and_(
            Person.id == NativeJobBinding.person_id, Person.event_id == event_id,
        )).where(NativeJobBinding.event_id == event_id,
                 NativeJobBinding.job_id.in_([row[0].id for row in rows])).order_by(
                     NativeJobBinding.job_id, NativeJobBinding.id)
        for binding, person_name in db.execute(bindings):
            submissions[binding.job_id].append({
                "person_id": binding.person_id, "person_name": person_name or "Unknown",
                "native_job_id": binding.native_job_id, "native_status": binding.native_status,
                "last_synced_at": binding.last_synced_at,
            })
    items = [{
        "id": job.id, "plan_id": job.plan_id, "dataset_id": job.dataset_id,
        "team_id": job.team_id, "team_name": team_name or "Unknown",
        "assignee_person_id": job.assignee_person_id, "assignee_name": assignee_name,
        "release_mode": job.release_mode, "sequence": job.sequence,
        "frame_count": job.frame_count, "video_count": job.video_count,
        "morphology_counts": job.morphology_counts, "status": job.status,
        "review_status": job.review_status, "correction_round": job.correction_round,
        "annotation_snapshot_round": job.annotation_snapshot_round,
        "annotation_snapshot_frames": job.annotation_snapshot_frames,
        "native_submissions": submissions[job.id], "assigned_monitor_id": job.assigned_monitor_id,
        "started_at": job.started_at, "submitted_at": job.submitted_at, "completed_at": job.completed_at,
    } for job, team_name, assignee_name in rows]
    next_cursor = None
    if has_more:
        last = rows[-1][0]
        position = JobCursor(scope=scope, after=(last.team_id, last.sequence, last.id))
        next_cursor = base64.urlsafe_b64encode(position.model_dump_json().encode()).decode().rstrip("=")
    return {"items": items, "next_cursor": next_cursor}


def can_view_asset(db: Session, ctx: AuthContext, event_id: str, dataset_id: str, asset_id: str) -> bool:
    query = scoped_jobs(event_id, ctx).where(LabelingJob.dataset_id == dataset_id)
    if db.bind.dialect.name == "postgresql":
        membership = cast(LabelingJob.media_ids, JSONB).contains([asset_id])
    else:
        values = func.json_each(LabelingJob.media_ids).table_valued("value")
        membership = exists(select(1).select_from(values).where(values.c.value == asset_id))
    return db.scalar(query.with_only_columns(LabelingJob.id).where(membership).limit(1)) is not None
