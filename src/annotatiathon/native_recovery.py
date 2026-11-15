"""Expose abandoned remote writes for reconciliation, never replay them blindly."""
from functools import wraps

from sqlalchemy import select

from .dataset_indexing import operation_lock
from .models import AuditEvent, NativeOperation

GUARDED_WRITE = "native-write-session-lock-v1"
JOB_WRITE_KINDS = ("publish_job", "restart_job", "reassign_job")


def guarded_native_write(handler):
    """Hold ownership across commits and SDK calls, not just the initial claim.

    The caller holds the operation row lock. A crashed process releases this
    PostgreSQL session lock; elapsed time alone never makes a write retryable.
    Mark only operations executed by this protocol, leaving older workers alone.
    """
    @wraps(handler)
    def guarded(db, api, config, operation):
        with operation_lock(db, operation.id) as acquired:
            if not acquired:
                db.rollback()
                return None
            operation.lease_owner = GUARDED_WRITE
            return handler(db, api, config, operation)
    return guarded


def recover_abandoned_job_writes(db, *, limit=100):
    """Make interrupted writes retryable through the existing organiser flow.

    No SDK calls and no changes to jobs, assignments, reviews, or datasets.
    Retry uses each writer's existing remote-identity reconciliation. Unknown
    results require attention because a remote write might have succeeded.
    """
    candidates = list(db.scalars(select(NativeOperation.id).where(
        NativeOperation.kind.in_(JOB_WRITE_KINDS),
        NativeOperation.status == "running",
        NativeOperation.lease_owner == GUARDED_WRITE,
    ).order_by(NativeOperation.claimed_at, NativeOperation.id).limit(limit)))
    db.rollback()
    recovered = 0
    for operation_id in candidates:
        with operation_lock(db, operation_id) as acquired:
            if not acquired:
                continue
            op = db.scalar(select(NativeOperation).where(
                NativeOperation.id == operation_id,
                NativeOperation.kind.in_(JOB_WRITE_KINDS),
                NativeOperation.status == "running",
                NativeOperation.lease_owner == GUARDED_WRITE,
            ).with_for_update(skip_locked=True).execution_options(populate_existing=True))
            if op is None:
                db.rollback()
                continue
            op.status = "attention"
            op.error_code = "worker_interrupted_reconcile_required"
            op.lease_owner = None
            op.lease_until = None
            db.add(AuditEvent(
                event_id=op.event_id, action="native.operation.interrupted",
                target_type="native_operation", target_id=op.id,
                details={"kind": op.kind, "attempts": op.attempts},
            ))
            db.commit()
            recovered += 1
    return recovered
