"""Pre-event Supervisely mirror and durable per-source image ID mappings.

Only source images upload before competition. Automatic jobs later reference
those image IDs, without uploading media or creating per-job datasets.
Manual jobs use separate per-assignment copies in one manual dataset.
"""
import sqlite3
import tempfile
from contextlib import closing
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
from .worker_timing import code_timing


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
        previous_revision = (existing.payload or {}).get("inventory_revision", 1)
        if previous_revision != ds.inventory_revision:
            # Never equate a completed OLD revision with newly appended media.
            # A running mirror must finish and detect this change itself.
            existing.payload = {"inventory_revision": ds.inventory_revision}
            existing.completed_at = None
            if existing.status != "running":
                existing.status = "pending"
                existing.error_code = None
                existing.claimed_at = None
        return existing
    op = NativeOperation(
        event_id=ds.event_id, kind="mirror_dataset", target_id=ds.id,
        status="pending", priority=200,
        payload={"inventory_revision": ds.inventory_revision},
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
        "inventory_revision": ds.inventory_revision,
        "mirrored_revision": ds.mirrored_revision,
        "ready_for_automatic_jobs": (
            ds.native_dataset_id is not None and state == "done"
            and ds.mirrored_revision == ds.inventory_revision
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
    # A disk-backed ephemeral index bounds Python RAM for large (100k+)
    # Supervisely image libraries. The SDK streams pages, and the SQL reader
    # fetches 512 local records at a time. Never list all assets/bindings.
    if db.scalar(select(NativeSourceMedia.id).where(
        NativeSourceMedia.dataset_id == ds.id,
        NativeSourceMedia.native_dataset_id != remote_ds.id,
    ).limit(1)):
        raise MirrorError("mirror_dataset_changed")
    root = (Path(ds.root_path) / "media").resolve()
    uploaded_count = 0
    with (
        tempfile.TemporaryDirectory(prefix="nightjar-mirror-") as temp_root,
        closing(sqlite3.connect(str(Path(temp_root) / "remote.sqlite3"))) as index,
    ):
        index.execute(
            "CREATE TABLE remote_images (name TEXT PRIMARY KEY, image_id INTEGER UNIQUE NOT NULL)"
        )
        with code_timing("mirror.remote_image_inventory"):
            images = (
                api.image.get_list_generator(remote_ds.id, batch_size=512)
                if callable(getattr(api.image, "get_list_generator", None))
                else [api.image.get_list(remote_ds.id)]  # Old test SDKs.
            )
            for remote_page in images:
                try:
                    index.executemany(
                        "INSERT INTO remote_images(name, image_id) VALUES (?, ?)",
                        ((im.name, im.id) for im in remote_page),
                    )
                except sqlite3.IntegrityError as exc:
                    raise MirrorError("duplicate_mirrored_image_names") from exc
            index.commit()

        cursor = ""
        while True:
            local_page = list(db.scalars(select(MediaAsset).where(
                MediaAsset.event_id == ds.event_id,
                MediaAsset.dataset_id == ds.id,
                MediaAsset.id > cursor,
            ).order_by(MediaAsset.id).limit(512)))
            if not local_page:
                break
            cursor = local_page[-1].id
            ids = [item.id for item in local_page]
            bound = {row.media_id: row for row in db.scalars(
                select(NativeSourceMedia).where(
                    NativeSourceMedia.dataset_id == ds.id,
                    NativeSourceMedia.media_id.in_(ids),
                )
            )}
            pending = []
            for item in local_page:
                name = source_image_name(item)
                result = index.execute(
                    "SELECT image_id FROM remote_images WHERE name = ?", (name,),
                ).fetchone()
                remote_id = int(result[0]) if result else None
                existing_binding = bound.get(item.id)
                if existing_binding is not None:
                    if (existing_binding.native_dataset_id != remote_ds.id
                            or remote_id != existing_binding.native_image_id):
                        raise MirrorError("source_image_binding_mismatch")
                    continue
                if remote_id is not None:
                    db.add(NativeSourceMedia(
                        event_id=ds.event_id, dataset_id=ds.id, media_id=item.id,
                        native_dataset_id=remote_ds.id, native_image_id=remote_id,
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
                if len(pending) == 24:
                    with code_timing("mirror.upload_image_batch"):
                        uploaded = api.image.upload_paths(
                            remote_ds.id,
                            names=[row[1] for row in pending],
                            paths=[row[2] for row in pending],
                            conflict_resolution="skip",
                        )
                    results = {im.name: im.id for im in uploaded}
                    if len(results) != len(pending) or set(results) != {row[1] for row in pending}:
                        raise MirrorError("mirror_batch_upload_incomplete")
                    for source, name, _ in pending:
                        db.add(NativeSourceMedia(
                            event_id=ds.event_id, dataset_id=ds.id, media_id=source.id,
                            native_dataset_id=remote_ds.id, native_image_id=results[name],
                        ))
                    with code_timing("mirror.persist_batch_bindings"):
                        db.commit()
                    uploaded_count += len(pending)
                    pending.clear()
            if pending:
                with code_timing("mirror.upload_image_batch"):
                    uploaded = api.image.upload_paths(
                        remote_ds.id,
                        names=[row[1] for row in pending],
                        paths=[row[2] for row in pending],
                        conflict_resolution="skip",
                    )
                results = {im.name: im.id for im in uploaded}
                if len(results) != len(pending) or set(results) != {row[1] for row in pending}:
                    raise MirrorError("mirror_batch_upload_incomplete")
                for source, name, _ in pending:
                    db.add(NativeSourceMedia(
                        event_id=ds.event_id, dataset_id=ds.id, media_id=source.id,
                        native_dataset_id=remote_ds.id, native_image_id=results[name],
                    ))
                with code_timing("mirror.persist_batch_bindings"):
                    db.commit()
                uploaded_count += len(pending)
            else:
                # Persist adopted pre-existing remote images page by page.
                db.commit()

    total = db.scalar(select(func.count(MediaAsset.id)).where(
        MediaAsset.dataset_id == ds.id,
    )) or 0
    mirrored = db.scalar(select(func.count(NativeSourceMedia.id)).where(
        NativeSourceMedia.dataset_id == ds.id,
    )) or 0
    if total != mirrored:
        raise MirrorError("source_mirror_incomplete")
    db.add(AuditEvent(
        event_id=ds.event_id, action="native.dataset.mirror.complete",
        target_type="media_dataset", target_id=ds.id,
        details={"project_id": project.id, "dataset_id": remote_ds.id,
                 "frames": mirrored, "new_uploads": uploaded_count},
    ))
    db.commit()
    return {"project_id": project.id, "native_dataset_id": remote_ds.id,
            "mirrored_frames": mirrored}
