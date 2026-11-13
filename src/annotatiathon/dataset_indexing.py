"""Durable local imports: short HTTP requests, crash recovery, bounded progress I/O."""
import hashlib
import json
import logging
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select, text

from .models import DatasetStagedFile, Event, MediaDataset, NativeOperation, epoch

logger = logging.getLogger(__name__)
UPLOAD_INDEX = "index_uploaded_dataset"


def require_active_import_event(db, ds):
    """Only hold the shared event mutation lock during the final short commit."""
    event = db.scalar(select(Event).where(Event.id == ds.event_id)
        .with_for_update(key_share=True).execution_options(populate_existing=True))
    if event is None or event.status != "active":
        raise HTTPException(409, "Dataset event became read-only during indexing.")


def require_idle_imports(db, event_id):
    if db.scalar(select(MediaDataset.id).where(
        MediaDataset.event_id == event_id, MediaDataset.status == "indexing",
    ).limit(1)):
        raise HTTPException(409, "Wait for local dataset indexing to finish before resetting the event.")


class IndexProgress:
    """Atomic snapshot outside the inventory transaction; at most one write/second.

    This uses the shared private media volume, so every API replica sees progress
    without exposing partially validated inventory or committing it prematurely.
    """
    def __init__(self, ds):
        self.path = Path(ds.root_path) / "index-progress.json"
        self.started = epoch()
        self.last_write = 0.0
        self.values = {"phase": "starting", "files": 0, "checked_frames": 0,
                       "started_at": self.started}

    def __call__(self, *, force=False, **values):
        self.values.update(values)
        now = time.monotonic()
        if not force and now - self.last_write < 1:
            return
        self.values["updated_at"] = epoch()
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.values), encoding="utf-8")
        temporary.replace(self.path)
        self.last_write = now


def read_progress(ds):
    try:
        return json.loads((Path(ds.root_path) / "index-progress.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None


def queue_uploaded_index(db, ds):
    from .datasets_api import dataset_view

    if ds.upload_kind not in {"folder", "zip"}:
        raise HTTPException(409, "This dataset uses mounted indexing.")
    if ds.status in {"ready", "indexing"}:
        return dataset_view(ds)
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == UPLOAD_INDEX, NativeOperation.target_id == ds.id,
    ))
    if ds.status not in {"uploading", "interrupted"} and not (
        ds.status == "failed" and op is not None and op.status == "attention"
    ):
        raise HTTPException(409, "Dataset cannot be indexed in this state.")
    total, incomplete = db.execute(select(
        func.count(DatasetStagedFile.id),
        func.count(DatasetStagedFile.id).filter(
            DatasetStagedFile.received_bytes != DatasetStagedFile.expected_bytes),
    ).where(DatasetStagedFile.dataset_id == ds.id)).one()
    if not total or incomplete:
        raise HTTPException(409, "Some upload files are still incomplete.")
    if op is None:
        op = NativeOperation(event_id=ds.event_id, kind=UPLOAD_INDEX,
                             target_id=ds.id, priority=200)
        db.add(op)
    op.status, op.error_code, op.claimed_at, op.completed_at = "pending", None, None, None
    ds.status, ds.error_code = "indexing", None
    db.commit()
    return dataset_view(ds)


@contextmanager
def operation_lock(db, operation_id):
    """A dedicated PostgreSQL session lock survives inventory commits.

    A crashed process releases its connection/lock. No elapsed-time guess can
    cause two healthy workers to decode or clean the same dataset concurrently.
    SQLite is only used for single-process unit tests.
    """
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        yield True
        return
    key = int.from_bytes(hashlib.sha256(operation_id.encode()).digest()[:8],
                         "big", signed=True)
    with bind.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                except Exception:
                    connection.invalidate()
                    raise


def clean_derived(ds):
    """Only private output of an uncommitted import; staging/source stays intact."""
    from .datasets_api import get_settings

    original = Path(ds.root_path)
    root = original.resolve()
    expected = Path(get_settings().local_data_dir).resolve() / ds.event_id / ds.id
    if root != expected or original.is_symlink():
        raise ValueError("unsafe_dataset_cleanup_path")
    for name in ("media", "source_videos", "provenance"):
        target = root / name
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise ValueError("unsafe_dataset_cleanup_path")
        if target.exists():
            shutil.rmtree(target)
    (root / "media").mkdir(exist_ok=True)


def process_index_once(db, kind=UPLOAD_INDEX):
    # Skip locked candidates, including a still-running worker in another process.
    ids = list(db.scalars(select(NativeOperation.id).where(
        NativeOperation.kind == kind,
        NativeOperation.status.in_(("pending", "running")),
    ).order_by(NativeOperation.created_at, NativeOperation.id)))
    db.rollback()
    for op_id in ids:
        with operation_lock(db, op_id) as acquired:
            if not acquired:
                continue
            op = db.scalar(select(NativeOperation).where(
                NativeOperation.id == op_id,
            ).with_for_update(skip_locked=True).execution_options(populate_existing=True))
            if op is None or op.status not in {"pending", "running"}:
                db.rollback()
                continue
            ds_id = op.target_id
            op.status, op.claimed_at = "running", epoch()
            op.attempts += 1
            db.commit()
            ds = db.get(MediaDataset, ds_id)
            progress = IndexProgress(ds) if ds else None
            try:
                if ds is not None and ds.status == "ready":
                    finished = db.get(NativeOperation, op_id)
                    finished.status, finished.completed_at = "done", epoch()
                    db.commit()
                    return {"status": "done", "operation_id": op_id, "dataset_id": ds_id}
                if ds is None or ds.status != "indexing":
                    raise ValueError("dataset_not_indexable")
                event = db.get(Event, ds.event_id)
                if event is None or event.status != "active":
                    raise ValueError("dataset_event_not_active")
                # The inventory is atomic; a crash rolls it back. Remove only
                # abandoned derived output before rebuilding from retained input.
                clean_derived(ds)
                progress(force=True)
                if kind == UPLOAD_INDEX:
                    from .datasets_api import index_uploaded_dataset
                    index_uploaded_dataset(db, ds, progress)
                else:
                    from .mounted_datasets import index_mounted_dataset
                    index_mounted_dataset(db, ds, progress)
                finished = db.get(NativeOperation, op_id)
                finished.status, finished.completed_at, finished.error_code = "done", epoch(), None
                db.commit()
                progress(phase="done", force=True)
                return {"status": "done", "operation_id": op_id, "dataset_id": ds_id}
            except Exception as exc:
                db.rollback()
                # An importer may have committed 'ready' immediately before a
                # process interruption. Never remove a committed media inventory.
                ds = db.get(MediaDataset, ds_id)
                if ds and ds.status == "ready":
                    finished = db.get(NativeOperation, op_id)
                    finished.status, finished.completed_at = "done", epoch()
                    db.commit()
                    return {"status": "done", "operation_id": op_id, "dataset_id": ds_id}
                logger.exception("Local indexing failed for dataset %s", ds_id)
                code = "mounted_index_failed" if kind != UPLOAD_INDEX else "upload_index_failed"
                if ds:
                    clean_derived(ds)
                    ds.status, ds.error_code = "failed", code
                failed = db.get(NativeOperation, op_id)
                failed.status, failed.error_code = "attention", code
                db.commit()
                if progress:
                    progress(phase="failed", force=True)
                return {"status": "attention", "operation_id": op_id,
                        "dataset_id": ds_id, "error": type(exc).__name__}
    return None
