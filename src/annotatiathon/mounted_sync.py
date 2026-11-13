"""Explicit, durable, additive synchronization of a mounted dataset."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .dataset_append import ensure_append_allowed
from .dataset_indexing import IndexProgress, operation_lock
from .datasets_api import get_dataset
from .db import get_db
from .events_api import current_event
from .models import (
    AuditEvent,
    DatasetAppendBatch,
    LabelingPlan,
    MediaDataset,
    NativeOperation,
    epoch,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/datasets", tags=["mounted synchronization"])
KIND = "sync_mounted_dataset"


class SyncStatus(BaseModel):
    state: str
    error: str | None = None
    new_frames: int = 0
    new_images: int = 0
    new_videos: int = 0
    existing_files: int = 0
    inventory_revision: int | None = None


def sync_view(op):
    payload = op.payload or {} if op else {}
    return SyncStatus(state=op.status if op else "not_started", **{
        key: payload[key] for key in SyncStatus.model_fields if key != "state" and key in payload
    })


def ensure_sync_allowed(db, ds):
    ensure_append_allowed(db, ds)
    if db.scalar(select(LabelingPlan.id).where(
        LabelingPlan.dataset_id == ds.id, LabelingPlan.status == "planned",
    ).limit(1)):
        raise HTTPException(409, "Synchronize all additions before creating a labeling plan.")


@router.get("/{dataset_id}/mounted/sync", response_model=SyncStatus)
def sync_status(dataset_id: str, ctx: AuthContext = Depends(requires("organiser")),
                db: Session = Depends(get_db)):
    ds = get_dataset(db, current_event(db, ctx).id, dataset_id)
    return sync_view(db.scalar(select(NativeOperation).where(
        NativeOperation.kind == KIND, NativeOperation.target_id == ds.id)))


@router.post("/{dataset_id}/mounted/sync", status_code=202, response_model=SyncStatus)
def queue_sync(dataset_id: str,
               ctx: AuthContext = Depends(requires("organiser", mutation=True)),
               db: Session = Depends(get_db)):
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    if ds.upload_kind != "mounted":
        raise HTTPException(409, "Only mounted folders support Sync additions.")
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == KIND, NativeOperation.target_id == ds.id))
    if op and op.status in {"pending", "running"}:
        return sync_view(op)
    ensure_sync_allowed(db, ds)
    if db.scalar(select(DatasetAppendBatch.id).where(
        DatasetAppendBatch.dataset_id == ds.id,
        DatasetAppendBatch.status.in_(("uploading", "indexing", "ready_to_commit", "failed")),
    ).limit(1)):
        raise HTTPException(409, "Complete or discard the current append batch before synchronizing.")
    if op is None:
        op = NativeOperation(event_id=event.id, kind=KIND, target_id=ds.id, priority=200)
        db.add(op)
    op.status, op.payload, op.error_code = "pending", {}, None
    op.claimed_at, op.completed_at = None, None
    # Existing rows remain committed, but planning/deletion cannot race the scan.
    ds.status = "indexing"
    db.add(AuditEvent(event_id=event.id, staff_id=ctx.staff.id,
        action="dataset.mounted.sync.request", target_type="media_dataset", target_id=ds.id))
    db.commit()
    return sync_view(op)


def process_sync_once(db: Session):
    ids = list(db.scalars(select(NativeOperation.id).where(
        NativeOperation.kind == KIND, NativeOperation.status.in_(("pending", "running")),
    ).order_by(NativeOperation.created_at, NativeOperation.id)))
    db.rollback()
    for operation_id in ids:
        with operation_lock(db, operation_id) as acquired:
            if not acquired:
                continue
            op = db.scalar(select(NativeOperation).where(NativeOperation.id == operation_id)
                .with_for_update(skip_locked=True).execution_options(populate_existing=True))
            if op is None or op.status not in {"pending", "running"}:
                db.rollback()
                continue
            dsid = op.target_id
            op.status, op.claimed_at = "running", epoch()
            op.attempts += 1
            db.commit()
            ds = db.scalar(select(MediaDataset).where(MediaDataset.id == dsid)
                .with_for_update().execution_options(populate_existing=True))
            progress = IndexProgress(ds) if ds else None
            try:
                if ds is None or ds.upload_kind != "mounted" or ds.status != "indexing":
                    raise ValueError("mounted_dataset_not_syncable")
                ds.status = "ready"
                ensure_sync_allowed(db, ds)
                progress(phase="synchronizing", force=True)
                from .mounted_datasets import index_mounted_dataset
                result = index_mounted_dataset(db, ds, progress, additions=True)
                op = db.get(NativeOperation, operation_id)
                op.status, op.payload, op.completed_at = "done", result, epoch()
                op.error_code = None
                db.add(AuditEvent(event_id=ds.event_id, action="dataset.mounted.sync.commit",
                    target_type="media_dataset", target_id=ds.id, details=result))
                db.commit()
                progress(phase="done", force=True)
                return {"status": "done", "dataset_id": dsid, **result}
            except Exception as exc:
                db.rollback()
                logger.exception("Mounted synchronization failed for %s", dsid)
                op = db.get(NativeOperation, operation_id)
                op.status, op.error_code = "attention", "mounted_sync_failed"
                detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                op.payload = {"error": detail[:500]}
                ds = db.get(MediaDataset, dsid)
                if ds:
                    ds.status = "ready"  # Retain the last committed inventory and all bindings.
                db.commit()
                if progress:
                    progress(phase="sync_failed", force=True)
                return {"status": "attention", "dataset_id": dsid}
    return None
