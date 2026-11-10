"""Explicit event launch and auditable Supervisely publishing operations.

HTTP requests enqueue durable tasks. Only a separate SDK worker contacts the
remote write API. No artificial/demo jobs or remote mutations on page load.
"""
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .credentials import decrypt_token
from .db import get_db
from .events_api import current_event
from .models import (
    AuditEvent,
    CompetitionTeam,
    LabelingJob,
    LabelingPlan,
    NativeJobBinding,
    NativeOperation,
    Person,
    SuperviselyConfig,
)
from .pair_members import pair_members

router = APIRouter(prefix="/api/v1/native", tags=["Supervisely dispatch"])


def native_ready(db: Session) -> SuperviselyConfig:
    config = db.get(SuperviselyConfig, 1)
    if (config is None or not config.verified or config.team_id is None
            or config.workspace_id is None):
        raise HTTPException(
            status_code=409,
            detail="Verify a live Supervisely team AND workspace in Settings first.",
        )
    if not config.token_ciphertext:
        raise HTTPException(
            status_code=422,
            detail="An organiser must configure the Supervisely API token in Settings.",
        )
    try:
        decrypt_token(config.token_ciphertext)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Saved Supervisely integration credential is unavailable.",
        ) from exc
    return config


def team_members(db: Session, job: LabelingJob) -> list[Person]:
    people, kind = pair_members(db, job.event_id, job.team_id)
    if len(people) != 2:
        raise HTTPException(
            status_code=409,
            detail="Each team must contain exactly two active roster members.",
        )
    for person in people:
        role = (person.supervisely_role or "").lower()
        allowed = (role == "annotator" if kind == "participants"
                   else role in {"admin", "manager", "developer"})
        if (not person.supervisely_user_id or person.membership_status != "joined"
                or person.native_verified_at is None or not allowed):
            raise HTTPException(
                status_code=409,
                detail="Both people must have verified native memberships with "
                       "roles appropriate to their pair type.",
            )
    ids = [p.supervisely_user_id for p in people]
    if len(set(ids)) != 2:
        raise HTTPException(status_code=409, detail="Native user IDs must be distinct.")
    return people


def queue_publish(db: Session, job: LabelingJob) -> NativeOperation:
    existing = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "publish_job",
        NativeOperation.target_id == job.id,
    ))
    if existing:
        return existing
    op = NativeOperation(
        event_id=job.event_id, kind="publish_job", target_id=job.id,
        status="pending",
    )
    db.add(op)
    db.flush()
    return op


def can_queue_next(db: Session, job: LabelingJob) -> bool:
    """Release automatic jobs sequentially per annotator, not per team."""
    if job.release_mode == "manual_released":
        return True
    if job.release_mode == "manual_hold":
        return False
    earlier = list(db.scalars(select(LabelingJob).where(
        LabelingJob.event_id == job.event_id,
        LabelingJob.plan_id == job.plan_id,
        LabelingJob.team_id == job.team_id,
        LabelingJob.assignee_person_id == job.assignee_person_id,
        LabelingJob.sequence < job.sequence,
    )))
    return all(
        x.status == "completed" and x.review_status == "accepted"
        for x in earlier
    )


def next_eligible(db: Session, plan: LabelingPlan) -> list[LabelingJob]:
    jobs = list(db.scalars(select(LabelingJob).where(
        LabelingJob.event_id == plan.event_id,
        LabelingJob.plan_id == plan.id,
    ).order_by(LabelingJob.team_id, LabelingJob.sequence)))
    first_queued: dict[tuple[str, str | None], LabelingJob] = {}
    for job in jobs:
        if job.status == "queued" and job.release_mode == "automatic":
            first_queued.setdefault((job.team_id, job.assignee_person_id), job)
    return [job for job in first_queued.values() if can_queue_next(db, job)]


class LaunchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str


@router.post("/launch")
def launch_event(
    payload: LaunchInput,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    native_ready(db)
    plan = db.scalar(select(LabelingPlan).where(
        LabelingPlan.event_id == event.id,
        LabelingPlan.id == payload.plan_id,
    ).with_for_update())
    if plan is None:
        raise HTTPException(status_code=404, detail="Labeling plan not found.")
    if plan.status not in {"approved", "running"}:
        raise HTTPException(status_code=409, detail="Approve the labeling plan first.")
    jobs = list(db.scalars(select(LabelingJob).where(
        LabelingJob.event_id == event.id, LabelingJob.plan_id == plan.id,
    ).order_by(LabelingJob.team_id, LabelingJob.sequence)))
    if not jobs:
        raise HTTPException(status_code=409, detail="Plan contains no jobs.")
    first = next_eligible(db, plan)
    if not first:
        raise HTTPException(status_code=409, detail="No eligible first jobs are available.")
    for job in first:
        team = db.scalar(select(CompetitionTeam).where(
            CompetitionTeam.id == job.team_id,
            CompetitionTeam.event_id == event.id,
            CompetitionTeam.active.is_(True),
        ))
        if not team:
            raise HTTPException(status_code=409, detail="A competition team is inactive.")
        team_members(db, job)
    created = 0
    for job in first:
        if job.status == "queued":
            task = queue_publish(db, job)
            if task.status == "pending":
                created += 1
    plan.status = "running"
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id, action="labeling.plan.launch",
        target_type="labeling_plan", target_id=plan.id,
        details={"first_jobs": len(first), "publish_pending": created},
    ))
    db.commit()
    return {
        "status": plan.status, "plan_id": plan.id,
        "first_jobs": len(first), "pending_operations": created,
        "remote_writes_queued": True,
        "note": "The separate Supervisely worker performs the remote writes.",
    }


@router.post("/plans/{plan_id}/pause")
def pause_release(
    plan_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    plan = db.scalar(select(LabelingPlan).where(
        LabelingPlan.id == plan_id, LabelingPlan.event_id == event.id,
    ).with_for_update())
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")
    if plan.status not in {"running", "paused"}:
        raise HTTPException(status_code=409, detail="Only launched plans can be paused.")
    plan.status = "paused"
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id, action="labeling.plan.pause",
        target_type="labeling_plan", target_id=plan.id,
    ))
    db.commit()
    return {"status": plan.status, "remote_jobs_not_stopped": True}


@router.post("/plans/{plan_id}/resume")
def resume_release(
    plan_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    plan = db.scalar(select(LabelingPlan).where(
        LabelingPlan.id == plan_id, LabelingPlan.event_id == event.id,
    ).with_for_update())
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")
    if plan.status not in {"paused", "running"}:
        raise HTTPException(status_code=409, detail="Launch the plan first.")
    plan.status = "running"
    queued = 0
    for job in next_eligible(db, plan):
        if queue_publish(db, job).status == "pending":
            queued += 1
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id, action="labeling.plan.resume",
        target_type="labeling_plan", target_id=plan.id,
        details={"queued": queued},
    ))
    db.commit()
    return {"status": plan.status, "queued": queued}


@router.get("/overview")
def native_overview(
    ctx: AuthContext = Depends(requires(
        "organiser", "monitor", "observer", "activity"
    )),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    plans = list(db.scalars(select(LabelingPlan).where(
        LabelingPlan.event_id == event.id
    )))
    ops = list(db.scalars(select(NativeOperation).where(
        NativeOperation.event_id == event.id
    ).order_by(NativeOperation.created_at.desc()).limit(1000)))
    bindings = list(db.scalars(select(NativeJobBinding).where(
        NativeJobBinding.event_id == event.id,
    )))
    status_counts = Counter(o.status for o in ops)
    return {
        "plans": [{
            "id": plan.id, "status": plan.status,
            "dataset_id": plan.dataset_id,
        } for plan in plans],
        "operations": [{
            "id": op.id, "kind": op.kind, "target_id": op.target_id,
            "status": op.status, "attempts": op.attempts,
            "error_code": op.error_code,
            "created_at": op.created_at, "completed_at": op.completed_at,
        } for op in ops],
        "operation_counts": dict(status_counts),
        "native_bindings": len(bindings),
        "bindings_by_status": dict(Counter(b.native_status for b in bindings)),
        "last_sync": max((b.last_synced_at or 0 for b in bindings), default=None),
    }


@router.post("/operations/{operation_id}/retry")
def retry_operation(
    operation_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.event_id == event.id,
        NativeOperation.id == operation_id,
    ).with_for_update())
    if op is None:
        raise HTTPException(status_code=404, detail="Operation not found.")
    if op.status not in {"blocked", "attention"}:
        raise HTTPException(status_code=409, detail="Only blocked operations can be retried.")
    job = db.scalar(select(LabelingJob).where(
        LabelingJob.id == op.target_id, LabelingJob.event_id == event.id
    ))
    if job is None:
        raise HTTPException(status_code=404, detail="Associated job not found.")
    plan = db.scalar(select(LabelingPlan).where(
        LabelingPlan.id == job.plan_id, LabelingPlan.event_id == event.id
    ))
    if (plan is None or
            (plan.status not in {"running", "paused"} if op.kind == "restart_job"
             else plan.status != "running")):
        raise HTTPException(
            status_code=409,
            detail="Job operation cannot be retried in the current plan state.",
        )
    if op.kind not in {"publish_job", "restart_job"}:
        raise HTTPException(status_code=409, detail="Unsupported job operation.")
    op.status = "pending"
    op.error_code = None
    op.claimed_at = None
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="native.operation.retry", target_type="native_operation",
        target_id=op.id, details={"prior_attempts": op.attempts},
    ))
    db.commit()
    return {"id": op.id, "status": op.status}
