"""Pre-event Supervisely mirror and durable per-source image ID mappings.

Only source images upload before competition. Automatic jobs later reference
those image IDs, without uploading media or creating per-job datasets.
Manual jobs use separate per-assignment copies in one manual dataset.
"""
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .annotation_schema import AnnotationSchemaError, ensure_project_annotation_classes
from .models import (
    AuditEvent,
    Event,
    MediaAsset,
    MediaDataset,
    NativeOperation,
    NativeSourceMedia,
    SuperviselyConfig,
    epoch,
)
from .native_names import legacy_project_name, project_name


class MirrorError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def source_image_name(asset: MediaAsset) -> str:
    return asset.id + Path(asset.relative_path).suffix.lower()


def enqueue_mirror(db: Session, ds: MediaDataset) -> NativeOperation:
    existing = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "mirror_dataset",
        NativeOperation.target_id == ds.id,
    ))
    if existing is not None:
        return existing
    op = NativeOperation(
        event_id=ds.event_id, kind="mirror_dataset", target_id=ds.id,
        status="pending", priority=200,
    )
    db.add(op)
    db.flush()
    return op


def mirror_status(db: Session, ds: MediaDataset) -> dict:
    total = ds.frame_count + ds.image_count
    uploaded = db.scalar(select(func.count(NativeSourceMedia.id)).where(
        NativeSourceMedia.dataset_id == ds.id,
    )) or 0
    operation = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "mirror_dataset",
        NativeOperation.target_id == ds.id,
    ))
    state = (operation.status if operation is not None else
             "not_started" if ds.status == "ready" else "waiting_for_local_import")
    queued_seconds = (
        max(0, epoch() - operation.created_at) if operation is not None
        and state == "pending" else None
    )
    return {
        "state": state, "uploaded_frames": uploaded, "total_frames": total,
        "ready_for_automatic_jobs": (
            ds.native_dataset_id is not None and state == "done"
            and total > 0 and uploaded == total
        ), "error_code": operation.error_code if operation else None,
        "attempts": operation.attempts if operation else 0,
        "pending_seconds": queued_seconds,
        "started_seconds_ago": (
            max(0, epoch() - operation.claimed_at)
            if operation is not None and operation.claimed_at is not None
            and state == "running" else None
        ),
    }


def assert_mirror_complete(db: Session, ds: MediaDataset) -> None:
    if not mirror_status(db, ds)["ready_for_automatic_jobs"]:
        raise MirrorError("dataset_not_mirrored_to_supervisely")


def mirror_dataset(db: Session, api, config: SuperviselyConfig,
                   ds: MediaDataset) -> dict:
    event = db.get(Event, ds.event_id)
    if ds.status != "ready" or event is None or event.status != "active":
        raise MirrorError("dataset_not_ready_for_mirror")
    workspace = api.workspace.get_info_by_id(config.workspace_id)
    if workspace is None or workspace.team_id != config.team_id:
        raise MirrorError("workspace_team_mismatch")
    new_name = project_name(event, ds)
    old_name = legacy_project_name(ds)
    if ds.native_project_id is not None:
        project = api.project.get_info_by_id(ds.native_project_id)
        if (project is None or project.workspace_id != config.workspace_id
                or project.name not in {new_name, old_name}):
            raise MirrorError("source_project_identity_changed")
    else:
        existing = [p for p in api.project.get_list(config.workspace_id)
                    if p.name in {new_name, old_name}]
        if len(existing) > 1:
            raise MirrorError("ambiguous_source_project")
        project = existing[0] if existing else api.project.create(
            config.workspace_id, new_name, change_name_if_conflict=False,
        )
        ds.native_project_id = project.id
        db.commit()
    try:
        ensure_project_annotation_classes(api, project.id)
    except AnnotationSchemaError as exc:
        raise MirrorError(exc.code) from exc
    name = "Complete video library · all species"
    if ds.native_dataset_id is not None:
        remote_ds = api.dataset.get_info_by_id(ds.native_dataset_id)
        if (remote_ds is None or remote_ds.project_id != project.id
                or remote_ds.name != name):
            raise MirrorError("source_dataset_identity_changed")
    else:
        existing = [d for d in api.dataset.get_list(project.id) if d.name == name]
        if len(existing) > 1:
            raise MirrorError("ambiguous_source_dataset")
        remote_ds = existing[0] if existing else api.dataset.create(
            project.id, name, change_name_if_conflict=False,
        )
        ds.native_dataset_id = remote_ds.id
        db.commit()
    local = list(db.scalars(select(MediaAsset).where(
        MediaAsset.event_id == ds.event_id, MediaAsset.dataset_id == ds.id,
    ).order_by(MediaAsset.relative_path)))
    remote = api.image.get_list(remote_ds.id)
    indexed = {image.name: image.id for image in remote}
    if len(indexed) != len(remote):
        raise MirrorError("duplicate_mirrored_image_names")
    bound = {row.media_id: row for row in db.scalars(select(NativeSourceMedia).where(
        NativeSourceMedia.dataset_id == ds.id,
    ))}
    if any(row.native_dataset_id != remote_ds.id for row in bound.values()):
        raise MirrorError("mirror_dataset_changed")
    root = (Path(ds.root_path) / "media").resolve()
    pending = []
    for item in local:
        name = source_image_name(item)
        if item.id in bound:
            if indexed.get(name) != bound[item.id].native_image_id:
                raise MirrorError("source_image_binding_mismatch")
            continue
        if name in indexed:
            db.add(NativeSourceMedia(
                event_id=ds.event_id, dataset_id=ds.id, media_id=item.id,
                native_dataset_id=remote_ds.id, native_image_id=indexed[name],
            ))
            continue
        if ds.upload_kind == "mounted":
            from .mounted_datasets import media_file
            try:
                path = media_file(ds, item.relative_path)
            except (OSError, HTTPException) as exc:
                raise MirrorError("mounted_frame_missing") from exc
        else:
            path = (root / item.relative_path).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise MirrorError("local_frame_missing")
        pending.append((item, name, str(path)))
    db.commit()
    for offset in range(0, len(pending), 24):
        batch = pending[offset:offset + 24]
        uploaded = api.image.upload_paths(
            remote_ds.id,
            names=[row[1] for row in batch],
            paths=[row[2] for row in batch],
            conflict_resolution="skip",
        )
        results = {item.name: item.id for item in uploaded}
        if set(results) != {row[1] for row in batch}:
            raise MirrorError("mirror_batch_upload_incomplete")
        for item, name, _ in batch:
            db.add(NativeSourceMedia(
                event_id=ds.event_id, dataset_id=ds.id, media_id=item.id,
                native_dataset_id=remote_ds.id, native_image_id=results[name],
            ))
        db.commit()
    # Verify every source frame (and not just the first queued batch) exists.
    rows = list(db.scalars(select(NativeSourceMedia).where(
        NativeSourceMedia.dataset_id == ds.id,
    )))
    if len(rows) != len(local) or len({r.media_id for r in rows}) != len(local):
        raise MirrorError("source_mirror_incomplete")
    db.add(AuditEvent(
        event_id=ds.event_id, action="native.dataset.mirror.complete",
        target_type="media_dataset", target_id=ds.id,
        details={"project_id": project.id, "dataset_id": remote_ds.id,
                 "frames": len(rows)},
    ))
    db.commit()
    return {"project_id": project.id, "native_dataset_id": remote_ds.id,
            "mirrored_frames": len(rows)}
