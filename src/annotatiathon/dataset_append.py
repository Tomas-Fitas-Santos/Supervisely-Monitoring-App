"""Append complete videos to a committed dataset without touching prior media.

Chunks are isolated per append batch; only the dedicated local indexer commits
new inventory rows. The shared remote dataset remains the same.
"""
import shutil
from collections import Counter
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .config import get_settings
from .datasets_api import (
    MAX_CHUNK_BYTES, VIDEO_EXTENSIONS, copy_and_hash, extract_video_frames,
    get_dataset, root_dir, safe_relative, sha256_file, staged_sources,
)
from .events_api import current_event
from .db import get_db
from .models import (
    AuditEvent, DatasetAppendBatch, DatasetAppendFile, DatasetPreparedQueue,
    LabelingJob, LabelingPlan, MediaAsset, MediaVideo, NativeOperation, epoch,
)
from .native_media_pool import enqueue_mirror

router = APIRouter(prefix="/api/v1/datasets", tags=["dataset append"])
OPEN = ("uploading", "indexing", "ready_to_commit")


class AppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_kind: Literal["zip", "folder"]


def batch_view(batch: DatasetAppendBatch) -> dict:
    return {
        "id": batch.id, "dataset_id": batch.dataset_id, "status": batch.status,
        "upload_kind": batch.upload_kind, "received_bytes": batch.source_bytes,
        "started_revision": batch.revision_at_start,
        "error_code": batch.error_code, "created_at": batch.created_at,
    }


def _batch(db: Session, ds: MediaDataset, batch_id: str, *, lock=False) -> DatasetAppendBatch:
    stmt = select(DatasetAppendBatch).where(
        DatasetAppendBatch.id == batch_id,
        DatasetAppendBatch.dataset_id == ds.id,
        DatasetAppendBatch.event_id == ds.event_id,
    )
    if lock:
        stmt = stmt.with_for_update()
    batch = db.scalar(stmt)
    if batch is None:
        raise HTTPException(404, "Append batch not found.")
    return batch


def _append_root(ds: MediaDataset, batch: DatasetAppendBatch) -> Path:
    root = root_dir(ds)
    candidate = root / "append" / batch.id
    if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
        raise HTTPException(409, "Invalid batch staging root.")
    return candidate


def ensure_append_allowed(db: Session, ds: MediaDataset) -> None:
    if ds.status != "ready":
        raise HTTPException(409, "Finish indexing the dataset before appending.")
    # Never change a source set that any active or historical job may reference.
    if db.scalar(select(LabelingJob.id).where(
        LabelingJob.dataset_id == ds.id,
    ).limit(1)):
        raise HTTPException(409, "Dataset has assigned jobs and cannot be expanded.")
    if db.scalar(select(LabelingPlan.id).where(
        LabelingPlan.dataset_id == ds.id,
        LabelingPlan.status.in_(["approved", "running", "paused"]),
    ).limit(1)):
        raise HTTPException(409, "Revoke approval or use a new dataset before appending.")


@router.get("/{dataset_id}/append-batches")
def list_append_batches(
    dataset_id: str, ctx: AuthContext = Depends(requires("organiser")),
    db: Session = Depends(get_db),
):
    ds = get_dataset(db, current_event(db, ctx).id, dataset_id)
    batches = list(db.scalars(select(DatasetAppendBatch).where(
        DatasetAppendBatch.dataset_id == ds.id,
    ).order_by(DatasetAppendBatch.created_at.desc(), DatasetAppendBatch.id.desc())))
    return {"items": [batch_view(b) for b in batches], "inventory_revision": ds.inventory_revision}


@router.post("/{dataset_id}/append-batches", status_code=201)
def create_append_batch(
    dataset_id: str, payload: AppendRequest,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    ensure_append_allowed(db, ds)
    if db.scalar(select(DatasetAppendBatch.id).where(
        DatasetAppendBatch.dataset_id == ds.id,
        DatasetAppendBatch.status.in_(OPEN),
    ).limit(1)):
        raise HTTPException(409, "Complete or discard the current append batch first.")
    batch = DatasetAppendBatch(
        event_id=event.id, dataset_id=ds.id, upload_kind=payload.upload_kind,
        status="uploading", source_bytes=0, revision_at_start=ds.inventory_revision,
        created_by=ctx.staff.id,
    )
    db.add(batch)
    db.flush()
    path = _append_root(ds, batch) / "stage"
    path.mkdir(parents=True, exist_ok=False)
    db.add(AuditEvent(
        event_id=event.id, staff_id=ctx.staff.id, action="dataset.append.begin",
        target_type="dataset_append_batch", target_id=batch.id,
        details={"dataset_id": ds.id, "upload_kind": batch.upload_kind},
    ))
    db.commit()
    return batch_view(batch)


@router.get("/{dataset_id}/append-batches/{batch_id}")
def append_status(
    dataset_id: str, batch_id: str,
    ctx: AuthContext = Depends(requires("organiser")),
    db: Session = Depends(get_db),
):
    ds = get_dataset(db, current_event(db, ctx).id, dataset_id)
    batch = _batch(db, ds, batch_id)
    files = list(db.scalars(select(DatasetAppendFile).where(
        DatasetAppendFile.batch_id == batch.id,
    ).order_by(DatasetAppendFile.relative_path)))
    return {**batch_view(batch), "files": [{
        "path": f.relative_path, "received_bytes": f.received_bytes,
        "expected_bytes": f.expected_bytes,
    } for f in files]}


@router.put("/{dataset_id}/append-batches/{batch_id}/chunk")
async def append_chunk(
    dataset_id: str, batch_id: str, request: Request,
    relative_path: str = Query(min_length=1, max_length=1024),
    offset: int = Query(ge=0), total: int = Query(ge=0),
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    batch = _batch(db, ds, batch_id, lock=True)
    if batch.status != "uploading":
        raise HTTPException(409, "Batch is not accepting uploads.")
    if batch.revision_at_start != ds.inventory_revision:
        raise HTTPException(409, "Dataset inventory changed; discard stale batch.")
    rel = safe_relative(relative_path)
    if batch.upload_kind == "zip" and str(rel) != "source.zip":
        raise HTTPException(422, "ZIP append accepts source.zip only.")
    if batch.upload_kind == "folder" and len(rel.parts) < 2:
        raise HTTPException(422, "Select species/video folders.")
    limits = get_settings()
    if (total > limits.max_dataset_bytes or
            ds.source_bytes + batch.source_bytes + total > limits.max_dataset_bytes + total):
        raise HTTPException(413, "Dataset exceeds its configured byte limit.")
    row = db.scalar(select(DatasetAppendFile).where(
        DatasetAppendFile.batch_id == batch.id,
        DatasetAppendFile.relative_path == str(rel),
    ).with_for_update())
    if row is None:
        if offset:
            raise HTTPException(409, "New file must start at offset zero.")
        row = DatasetAppendFile(
            batch_id=batch.id, relative_path=str(rel),
            expected_bytes=total, received_bytes=0,
        )
        db.add(row)
        db.flush()
    elif row.expected_bytes != total or row.received_bytes != offset:
        raise HTTPException(409, "Chunk offset/size differs from the saved upload.")
    target = _append_root(ds, batch) / "stage" / str(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or (target.stat().st_size if target.exists() else 0) != offset:
        raise HTTPException(409, "Unsafe or inconsistent on-disk upload offset.")
    written = 0
    try:
        with target.open("r+b" if target.exists() else "wb") as out:
            out.seek(offset)
            async for chunk in request.stream():
                written += len(chunk)
                if (written > MAX_CHUNK_BYTES or offset + written > total
                        or ds.source_bytes + batch.source_bytes + written >
                           limits.max_dataset_bytes):
                    raise HTTPException(413, "Chunk exceeds limits.")
                out.write(chunk)
    except Exception:
        if target.exists():
            with target.open("r+b") as out:
                out.truncate(offset)
        raise
    row.received_bytes += written
    batch.source_bytes += written
    batch.last_activity_at = epoch()
    db.commit()
    return {"received_bytes": row.received_bytes,
            "expected_bytes": total, "complete": row.received_bytes == total}


@router.post("/{dataset_id}/append-batches/{batch_id}/finish", status_code=202)
def finish_append(
    dataset_id: str, batch_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    batch = _batch(db, ds, batch_id, lock=True)
    ensure_append_allowed(db, ds)
    if batch.status != "uploading" or batch.revision_at_start != ds.inventory_revision:
        raise HTTPException(409, "Batch is not ready for indexing.")
    files = list(db.scalars(select(DatasetAppendFile).where(
        DatasetAppendFile.batch_id == batch.id,
    )))
    if not files or any(f.received_bytes != f.expected_bytes for f in files):
        raise HTTPException(409, "Finish uploading all batch files first.")
    if len(files) > get_settings().max_dataset_assets:
        raise HTTPException(413, "Too many batch files.")
    batch.status = "indexing"
    op = NativeOperation(
        event_id=event.id, kind="append_dataset", target_id=batch.id,
        status="pending", priority=150,
    )
    db.add(op)
    db.commit()
    return {**batch_view(batch), "operation_id": op.id}


@router.post("/{dataset_id}/append-batches/{batch_id}/retry", status_code=202)
def retry_append(
    dataset_id: str, batch_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    batch = _batch(db, ds, batch_id, lock=True)
    ensure_append_allowed(db, ds)
    if batch.status != "failed" or batch.revision_at_start != ds.inventory_revision:
        raise HTTPException(409, "Only a failed batch on the same revision can be retried.")
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "append_dataset", NativeOperation.target_id == batch.id,
    ).with_for_update())
    if op is None:
        raise HTTPException(409, "Append operation is missing.")
    batch.status = "indexing"
    batch.error_code = None
    op.status = "pending"
    op.error_code = None
    op.claimed_at = None
    db.commit()
    return batch_view(batch)


@router.delete("/{dataset_id}/append-batches/{batch_id}")
def discard_append(
    dataset_id: str, batch_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    batch = _batch(db, ds, batch_id, lock=True)
    if batch.status not in {"uploading", "failed"}:
        raise HTTPException(409, "Only an uncommitted idle batch can be discarded.")
    staging = _append_root(ds, batch)
    if staging.exists():
        shutil.rmtree(staging)
    batch.status = "discarded"
    db.commit()
    return batch_view(batch)


def _index_batch(db: Session, ds: MediaDataset, batch: DatasetAppendBatch) -> dict:
    """Build batch-only files before atomically exposing the new inventory."""
    from .frame_extraction import extract_full_video
    from .frame_import import index_supplied_frames

    staged = _append_root(ds, batch)
    media_root = root_dir(ds) / "media"
    media_batch = media_root / "batches" / batch.id
    if media_batch.exists():
        shutil.rmtree(media_batch)
    media_batch.mkdir(parents=True)
    files = list(db.scalars(select(DatasetAppendFile).where(
        DatasetAppendFile.batch_id == batch.id,
    ).order_by(DatasetAppendFile.relative_path)))
    source_proxy = SimpleNamespace(root_path=str(staged), upload_kind=batch.upload_kind)
    existing_groups = set(db.scalars(select(MediaVideo.video_group).where(
        MediaVideo.dataset_id == ds.id,
    )))
    source_groups = set()
    frame_groups = set()
    videos = {}
    manifests = {}
    kinds = Counter()
    asset_rows = []
    settings = get_settings()

    def video_for(group: str, policy: str) -> MediaVideo:
        if group in existing_groups:
            raise HTTPException(409, f"Video already exists in dataset: {group}.")
        if group not in videos:
            v = MediaVideo(
                event_id=ds.event_id, dataset_id=ds.id,
                video_group=group, extraction_policy=policy,
            )
            db.add(v)
            db.flush()
            videos[group] = v
        return videos[group]

    def add_frame(path: Path, species: str, group: str | None, kind: str,
                  ordinal=None, pts=None, width=None, height=None):
        digest, size = sha256_file(path)
        if ds.image_count + ds.frame_count + sum(kinds.values()) + 1 > settings.max_dataset_assets:
            raise HTTPException(413, "Dataset would exceed its asset limit.")
        rel = path.relative_to(media_root).as_posix()
        video = videos.get(group)
        if video is not None:
            video.frame_count += 1
            video.extracted_bytes += size
        asset_rows.append(MediaAsset(
            event_id=ds.event_id, dataset_id=ds.id, relative_path=rel,
            morphology=species, kind=kind, video_group=group,
            video_id=video.id if video else None,
            frame_index=ordinal, presentation_timestamp=pts,
            frame_width=width, frame_height=height,
            sha256=digest, size_bytes=size,
        ))
        kinds[kind] += 1
        if len(asset_rows) >= 500:
            db.add_all(asset_rows)
            db.flush()
            asset_rows.clear()

    for rel, stream in staged_sources(source_proxy, files):
        parts = PurePosixPath(rel).parts
        species = parts[1]
        suffix = PurePosixPath(rel).suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            title = PurePosixPath(parts[2]).stem
            group = f"{species}/{title}"
            if group in source_groups or group in frame_groups:
                raise HTTPException(409, f"Repeated video title: {group}.")
            source_groups.add(group)
            source = staged / "source_videos" / species / parts[2]
            digest, _ = copy_and_hash(stream, source)
            video = video_for(group, ds.extraction_policy)
            video.source_sha256 = digest
            video.source_relative_path = source.relative_to(root_dir(ds)).as_posix()
            video.identity_status = "complete"
            video.missing_frame_count = 0
            out = media_batch / "videos" / species / title
            if ds.extraction_policy == "all_frames":
                extraction = extract_full_video(
                    source, out,
                    max_frames=settings.max_dataset_assets - ds.frame_count - ds.image_count
                               - sum(kinds.values()),
                )
                video.density = "full"
                video.time_base = extraction.time_base
                video.frame_rate = extraction.frame_rate
                video.source_frame_count = len(extraction.frames)
                for frame in extraction.frames:
                    add_frame(frame.path, species, group, "video_frame",
                              frame.ordinal, frame.pts, frame.width, frame.height)
            else:
                video.density = "sampled"
                video.identity_status = "unknown"
                video.missing_frame_count = None
                for frame in extract_video_frames(source, out):
                    add_frame(frame, species, group, "video_frame")
        elif parts[-1] == "frames.json":
            if len(parts) != 4:
                raise HTTPException(422, "frames.json must belong to a video folder.")
            group = f"{species}/{parts[2]}"
            if group in source_groups:
                raise HTTPException(409, f"Repeated video title: {group}.")
            video_for(group, "supplied_unknown")
            path = staged / "provenance" / species / parts[2] / "frames.json"
            copy_and_hash(stream, path)
            manifests[group] = path
            frame_groups.add(group)
        else:
            kind = "image" if parts[0] == "images" else "video_frame"
            group = f"{species}/{parts[2]}" if kind == "video_frame" else None
            if group:
                if group in source_groups:
                    raise HTTPException(409, f"Repeated video title: {group}.")
                video_for(group, "supplied_unknown")
                frame_groups.add(group)
            dest = media_batch / rel
            copy_and_hash(stream, dest)
            add_frame(dest, species, group, kind)
    db.add_all(asset_rows)
    db.flush()
    if not kinds:
        raise HTTPException(422, "Append batch contains no media.")
    for group, video in videos.items():
        if video.extraction_policy == "supplied_unknown":
            index_supplied_frames(db, video, media_root, manifests.get(group),
                                  strict=ds.extraction_policy == "all_frames")
    if ds.source_bytes + batch.source_bytes > settings.max_dataset_bytes:
        raise HTTPException(413, "Dataset would exceed its byte limit.")
    ds.source_bytes += batch.source_bytes
    ds.image_count += kinds["image"]
    ds.frame_count += kinds["video_frame"]
    ds.video_count += len(videos)
    ds.inventory_revision += 1
    batch.status = "committed"
    batch.committed_at = epoch()
    batch.error_code = None
    prepared = db.get(DatasetPreparedQueue, ds.id)
    if prepared:
        prepared.state = "stale"
    enqueue_mirror(db, ds)
    db.add(AuditEvent(
        event_id=ds.event_id, staff_id=batch.created_by,
        action="dataset.append.commit", target_type="dataset_append_batch",
        target_id=batch.id, details={
            "revision": ds.inventory_revision,
            "new_frames": kinds["video_frame"], "new_videos": len(videos),
        },
    ))
    return {"new_frames": kinds["video_frame"], "new_videos": len(videos),
            "inventory_revision": ds.inventory_revision}


def process_append_once(db: Session) -> dict | None:
    """Claim and commit one batch; local I/O only, no Supervisely credentials."""
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "append_dataset", NativeOperation.status == "pending",
    ).order_by(NativeOperation.created_at, NativeOperation.id)
        .with_for_update(skip_locked=True))
    if op is None:
        db.rollback()
        return None
    op.status = "running"
    op.claimed_at = epoch()
    op.attempts += 1
    op_id, batch_id = op.id, op.target_id
    db.commit()
    batch = db.get(DatasetAppendBatch, batch_id)
    try:
        if batch is None or batch.status != "indexing":
            raise ValueError("append_batch_not_indexable")
        ds = db.scalar(select(MediaDataset).where(
            MediaDataset.id == batch.dataset_id,
        ).with_for_update().execution_options(populate_existing=True))
        ensure_append_allowed(db, ds)
        if ds.inventory_revision != batch.revision_at_start:
            raise ValueError("append_revision_stale")
        result = _index_batch(db, ds, batch)
        finished = db.get(NativeOperation, op_id)
        finished.status = "done"
        finished.completed_at = epoch()
        finished.error_code = None
        db.commit()
        # Only this batch's private staging is removed, never prior media.
        shutil.rmtree(_append_root(ds, batch) / "stage", ignore_errors=True)
        return {"status": "done", "operation_id": op_id, **result}
    except Exception as exc:
        db.rollback()
        failed = db.get(NativeOperation, op_id)
        if failed:
            failed.status = "attention"
            failed.error_code = "append_import_failed"
        batch = db.get(DatasetAppendBatch, batch_id)
        if batch:
            batch.status = "failed"
            batch.error_code = "append_import_failed"
            db.commit()
            ds = db.get(MediaDataset, batch.dataset_id)
            # Only uncommitted batch-owned derived files may be removed.
            target = root_dir(ds) / "media" / "batches" / batch.id
            if target.exists() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
        return {"status": "attention", "operation_id": op_id,
                "error_type": type(exc).__name__}
