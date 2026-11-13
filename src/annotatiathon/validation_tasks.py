"""Durable, fenced submission-validation tasks.

Each task is a reusable NativeOperation(kind=validate_submission, target=job.id).
No live native jobs are created here. Workers may read Supervisely annotations
concurrently, but each job/round is exclusively leased and completed once.
Publisher/correction writes are deliberately NOT lease-retried.
"""
import logging
import os
import threading
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .db import session_factory
from .models import (
    Event,
    LabelingJob,
    LabelingPlan,
    NativeJobBinding,
    NativeOperation,
    epoch,
)
from .worker_timing import milestone

logger = logging.getLogger(__name__)
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 15
MAX_ATTEMPTS = 5


def enqueue_validation(db: Session, job: LabelingJob) -> bool:
    """Schedule once per native job/round, in the caller's transaction.

    Can be called on each status poll. Existing completed/active rounds never
    requeue; a later correction round reuses the same durable task row.
    """
    if job.status not in {"on_review", "completed"}:
        return False
    event = db.scalar(select(Event).where(Event.id == job.event_id)
                      .with_for_update().execution_options(populate_existing=True))
    if event is None or event.status != "active":
        return False
    binding = db.scalar(select(NativeJobBinding).where(
        NativeJobBinding.event_id == job.event_id,
        NativeJobBinding.job_id == job.id,
        NativeJobBinding.person_id == job.assignee_person_id,
    )) if job.assignee_person_id else None
    if job.assignee_person_id is not None and binding is None:
        return False
    target = {
        "correction_round": job.correction_round,
        "native_job_id": binding.native_job_id if binding else None,
    }
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "validate_submission",
        NativeOperation.target_id == job.id,
    ).with_for_update())
    if op is None:
        db.add(NativeOperation(
            event_id=job.event_id, kind="validate_submission",
            target_id=job.id, status="pending", payload=target, priority=80,
            next_attempt_at=epoch(),
        ))
        return True
    if op.payload == target:
        return False
    if op.status == "running":
        # Inflight old round must complete/reconcile before a different
        # correction round can be submitted; never overwrite its lease.
        return False
    op.payload = target
    op.status = "pending"
    op.attempts = 0
    op.claimed_at = None
    op.completed_at = None
    op.error_code = None
    op.lease_owner = None
    op.lease_until = None
    op.next_attempt_at = epoch()
    return True


def claim_validation(db: Session) -> tuple[str, str, str] | None:
    """Claim exactly one runnable task with row-level SKIP LOCKED."""
    now = epoch()
    op = db.scalar(select(NativeOperation).join(Event, Event.id == NativeOperation.event_id).where(
        Event.status.in_(("active", "finalizing")),
        NativeOperation.kind == "validate_submission",
        NativeOperation.status == "pending",
        or_(NativeOperation.next_attempt_at.is_(None),
            NativeOperation.next_attempt_at <= now),
    ).order_by(
        NativeOperation.priority.desc(), NativeOperation.created_at,
        NativeOperation.id,
    ).limit(1).with_for_update(skip_locked=True, of=NativeOperation))
    if op is None:
        db.rollback()
        return None
    job = db.get(LabelingJob, op.target_id)
    event = db.get(Event, op.event_id)
    plan = db.get(LabelingPlan, job.plan_id) if job else None
    if (job is None or event is None or event.status not in {"active", "finalizing"}
            or plan is None or plan.status not in {"running", "paused"}
            or job.status not in {"on_review", "completed"}
            or (op.payload or {}).get("correction_round") != job.correction_round):
        op.status = "attention"
        op.error_code = "validation_submission_state_changed"
        op.completed_at = now
        db.commit()
        return None
    token = uuid4().hex
    op.status = "running"
    op.claimed_at = now
    op.next_attempt_at = None
    op.lease_owner = token
    op.lease_until = now + LEASE_SECONDS
    op.attempts += 1
    db.commit()
    return op.id, job.id, token


def renew_lease(task_id: str, token: str) -> bool:
    """Heartbeat through an independent SQLAlchemy session."""
    with session_factory()() as db:
        changed = db.execute(update(NativeOperation).where(
            NativeOperation.id == task_id,
            NativeOperation.kind == "validate_submission",
            NativeOperation.status == "running",
            NativeOperation.lease_owner == token,
        ).values(lease_until=epoch() + LEASE_SECONDS)).rowcount
        db.commit()
    return bool(changed)


def _heartbeat(task_id: str, token: str, stop: threading.Event,
               lease_lost: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            if not renew_lease(task_id, token):
                lease_lost.set()
                return
        except Exception:  # noqa: BLE001 - failed heartbeat must fail closed
            lease_lost.set()
            logger.warning("Validation heartbeat failed; worker must not finalize %s", task_id)
            return


def finish_validation(db: Session, task_id: str, token: str, *,
                      failure: Exception | None = None) -> bool:
    """Fence completion and bound retry backoff to a maximum of five attempts."""
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.id == task_id,
        NativeOperation.kind == "validate_submission",
    ).with_for_update())
    if op is None or op.status != "running" or op.lease_owner != token:
        db.rollback()
        return False
    op.lease_owner = None
    op.lease_until = None
    if failure is None:
        op.status = "done"
        op.error_code = None
        op.completed_at = epoch()
    else:
        op.error_code = type(failure).__name__[:70]
        if op.attempts >= MAX_ATTEMPTS:
            op.status = "attention"
            op.completed_at = epoch()
        else:
            op.status = "pending"
            # Deterministic task-specific jitter prevents a thundering herd.
            jitter = int(op.id[:2], 16) % 7 if all(
                char in "0123456789abcdef" for char in op.id[:2].lower()
            ) else 3
            op.next_attempt_at = epoch() + min(300, 5 * (2 ** op.attempts) + jitter)
    db.commit()
    return True


def recover_expired_validations(db: Session, *, limit: int = 10) -> int:
    """Retry abandoned read-only validation; never auto-repeat native writes."""
    now = epoch()
    tasks = list(db.scalars(select(NativeOperation).where(
        NativeOperation.kind == "validate_submission",
        NativeOperation.status == "running",
        NativeOperation.lease_until < now,
    ).order_by(NativeOperation.lease_until).limit(limit)
        .with_for_update(skip_locked=True, of=NativeOperation)))
    for op in tasks:
        op.lease_owner = None
        op.lease_until = None
        op.error_code = "validation_lease_expired"
        if op.attempts >= MAX_ATTEMPTS:
            op.status = "attention"
            op.completed_at = now
        else:
            op.status = "pending"
            op.next_attempt_at = now + 5
    db.commit()
    return len(tasks)


def process_validation_once(db: Session, api, config) -> dict | None:
    """Read, validate and ingest a single job's annotation JSON.

    SDK/DB clients and sessions are worker-local. A completion attempt from
    an expired owner is fenced out; immutable round manifests make retry
    idempotent even after a crash between file write and SQL commit.
    """
    task = claim_validation(db)
    if task is None:
        return None
    task_id, job_id, token = task
    milestone("validation_started", job_id=job_id, operation_id=task_id)
    stop = threading.Event()
    lease_lost = threading.Event()
    thread = threading.Thread(
        target=_heartbeat, args=(task_id, token, stop, lease_lost),
        name="nightjar-validation-heartbeat", daemon=True,
    )
    thread.start()
    problem = None
    completed = False
    next_assignment_id = None
    try:
        from .native_worker import capture_job_annotations
        job = db.get(LabelingJob, job_id)
        if job is None or job.status not in {"on_review", "completed"}:
            raise ValueError("validation_target_changed")
        task_row = db.get(NativeOperation, task_id)
        payload = task_row.payload or {}
        if job.correction_round != payload.get("correction_round"):
            raise ValueError("validation_round_changed")
        if job.assignee_person_id:
            binding = db.scalar(select(NativeJobBinding).where(
                NativeJobBinding.job_id == job.id,
                NativeJobBinding.event_id == job.event_id,
                NativeJobBinding.person_id == job.assignee_person_id,
            ))
            if binding is None or binding.native_job_id != payload.get("native_job_id"):
                raise ValueError("validation_native_job_changed")
        capture_job_annotations(db, api, job)
        if lease_lost.is_set():
            raise RuntimeError("validation_lease_lost")
        # Finish validation and publish-queue insertion in ONE transaction.
        # If this allocation fails, roll back the accepted sample writes and
        # retry safely from the immutable per-round annotation manifest.
        if job.status == "on_review" and job.release_mode in {
            "shared_primary", "shared_repeat",
        } and job.assignee_person_id:
            plan = db.get(LabelingPlan, job.plan_id)
            if plan and plan.status == "running":
                from .work_queue import claim_primary_block
                next_job = claim_primary_block(
                    db, plan.id, job.team_id, job.assignee_person_id,
                    include_repeats=False,
                )
                if next_job is not None:
                    next_assignment_id = next_job.id
        completed = True
    except Exception as exc:
        db.rollback()
        problem = exc
        if os.environ.get("BENCHMARK_ANNOTATORS") == "300":
            logger.exception("Synthetic validation task %s failed", task_id)
        else:
            logger.warning("Validation task %s failed (%s)",
                           task_id, type(exc).__name__)
    finally:
        stop.set()
        thread.join(timeout=2)

    if lease_lost.is_set():
        db.rollback()
        milestone("validation_lease_lost", job_id=job_id, operation_id=task_id)
        return {"operation_id": task_id, "status": "lease_lost"}
    if not finish_validation(db, task_id, token, failure=problem):
        milestone("validation_lease_lost", job_id=job_id, operation_id=task_id)
        return {"operation_id": task_id, "status": "lease_lost"}
    if not completed:
        milestone("validation_failed", job_id=job_id, operation_id=task_id)
        return {"operation_id": task_id, "status": "retry_or_attention"}
    milestone("validation_completed", job_id=job_id, operation_id=task_id)
    if next_assignment_id is not None:
        milestone("next_job_queued", job_id=job_id, operation_id=next_assignment_id)
        milestone("publish_queued", job_id=next_assignment_id)
        return {"operation_id": task_id, "status": "done",
                "next_assignment_queued": True}
    return {"operation_id": task_id, "status": "done"}
