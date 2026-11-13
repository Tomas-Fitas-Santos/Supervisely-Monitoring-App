"""Retryable rehearsal cleanup; source images and dataset records are never deleted."""
import shutil
from collections import defaultdict
from contextlib import contextmanager

from sqlalchemy import delete, select, text, union

from . import models as m
from .events_api import seed_categories
from .full_reset_api import DATASET_OPERATIONS, clear_event_data, safe_dataset_paths
from .native_names import legacy_project_name, project_name

# Session-level locks survive the worker's transaction commits. A reset waits
# for every in-flight SDK cycle, including writes whose response is uncertain.
# A process crash releases the lock, so a replacement reset worker can resume.
RESET_LOCK = 724_913_208


@contextmanager
def worker_reset_barrier(engine, *, reset=False):
    if engine.dialect.name != "postgresql":
        yield True
        return
    suffix = "" if reset else "_shared"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        acquired = connection.scalar(text(f"SELECT pg_try_advisory_lock{suffix}(:key)"), {"key": RESET_LOCK})
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text(f"SELECT pg_advisory_unlock{suffix}(:key)"), {"key": RESET_LOCK})


def checkpoint(db, operation, **values):
    operation.payload = {**(operation.payload or {}), **values}
    db.commit()


def clean_preserving_datasets(db, api, config, operation):
    """Called under the exclusive worker barrier, with the event locked for work.

    Remote ownership is checked before deletion. Local deletion is last and
    transactional. Retrying an acknowledged or uncertain annotation clear is
    safe: it uploads the same empty JSON, never image bytes.
    """
    event = db.get(m.Event, operation.event_id, populate_existing=True)
    if event is None or event.status != "resetting":
        raise ValueError("clean_event_not_locked")
    event_id = event.id
    workspace = api.workspace.get_info_by_id(config.workspace_id)
    if workspace is None or workspace.team_id != config.team_id:
        raise ValueError("clean_workspace_team_mismatch")
    datasets = list(db.scalars(select(m.MediaDataset).where(m.MediaDataset.event_id == event_id)))
    paths = safe_dataset_paths(db, event_id)
    for root in paths:
        annotations = root / "annotations"
        if annotations.is_symlink() or annotations.resolve().parent != root:
            raise ValueError("unsafe_annotation_storage")
    projects = set()
    remote_datasets = set()
    for dataset in datasets:
        if dataset.native_project_id is None:
            continue
        project = api.project.get_info_by_id(dataset.native_project_id)
        if (project is None or project.workspace_id != config.workspace_id
                or project.name not in {project_name(event, dataset), legacy_project_name(dataset)}):
            raise ValueError("clean_source_project_identity_changed")
        projects.add(project.id)
        listed = list(api.dataset.get_list(project.id))
        if any(d.project_id != project.id for d in listed):
            raise ValueError("clean_remote_dataset_identity_changed")
        ids = {d.id for d in listed}
        if any(did is not None and did not in ids for did in (
                dataset.native_dataset_id, dataset.native_manual_dataset_id)):
            raise ValueError("clean_source_dataset_missing")
        remote_datasets.update(ids)

    # Include correction revisions and uncertain remote creations by listing
    # the exact owned projects; never use team-wide deletion or guessed IDs.
    native_jobs = {}
    for project_id in sorted(projects):
        for job in api.labeling_job.get_list(config.team_id, project_id=project_id, show_disabled=True):
            if job.project_id != project_id or job.dataset_id not in remote_datasets:
                raise ValueError("clean_remote_job_identity_changed")
            native_jobs[job.id] = job
    for model in (m.NativeJobBinding, m.NativeJobRevision):
        for binding in db.scalars(select(model).where(model.event_id == event_id)):
            remote = api.labeling_job.get_info_by_id(binding.native_job_id)
            if remote is not None and remote.id not in native_jobs:
                raise ValueError("clean_linked_job_outside_owned_projects")

    checkpoint(db, operation, phase="removing_jobs")
    removed = (operation.payload or {}).get("removed_jobs", 0)
    for job_id in sorted(native_jobs):
        api.labeling_job.remove(job_id)
        removed += 1
    for project_id in sorted(projects):
        if api.labeling_job.get_list(config.team_id, project_id=project_id, show_disabled=True):
            raise ValueError("clean_native_jobs_still_visible")
    checkpoint(db, operation, phase="clearing_annotations", removed_jobs=removed)

    # UNION deduplicates automatic-job instances pointing at source images.
    # Clear in bounded batches and retain the cursor after every successful
    # batch. No re-upload, re-index, source-ID replacement, or project archival.
    selections = []
    for model in (m.NativeSourceMedia, m.NativeMediaInstance):
        selections.append(select(
            model.native_image_id.label("image_id"), model.native_dataset_id.label("dataset_id"),
            m.MediaAsset.frame_width.label("width"), m.MediaAsset.frame_height.label("height"),
        ).join(m.MediaAsset, m.MediaAsset.id == model.media_id).where(model.event_id == event_id))
    images = union(*selections).subquery()
    cursor = (operation.payload or {}).get("annotation_cursor", 0)
    cleared = (operation.payload or {}).get("cleared_annotations", 0)
    while True:
        rows = db.execute(select(images).where(images.c.image_id > cursor)
                          .order_by(images.c.image_id).limit(200)).all()
        if not rows:
            break
        groups = defaultdict(list)
        for row in rows:
            if row.dataset_id not in remote_datasets:
                raise ValueError("clean_image_outside_owned_datasets")
            groups[row.dataset_id].append(row)
        for dataset_id, batch in groups.items():
            ids = [row.image_id for row in batch]
            infos = {info.id: info for info in api.image.get_list(dataset_id, filters=[
                {"field": "id", "operator": "in", "value": ids}])}
            if set(infos) != set(ids):
                raise ValueError("clean_mapped_image_missing")
            annotations = []
            for row in batch:
                width = row.width or getattr(infos[row.image_id], "width", None)
                height = row.height or getattr(infos[row.image_id], "height", None)
                if not width or not height:
                    raise ValueError("clean_image_dimensions_missing")
                annotations.append({"description": "", "tags": [], "objects": [],
                                    "size": {"width": width, "height": height}})
            api.annotation.upload_jsons(ids, annotations)
        cursor = rows[-1].image_id
        cleared += len(rows)
        checkpoint(db, operation, annotation_cursor=cursor, cleared_annotations=cleared)

    checkpoint(db, operation, phase="clearing_local_activity")
    for root in paths:
        annotations = root / "annotations"
        if annotations.exists():
            if annotations.is_symlink() or annotations.resolve().parent != root:
                raise ValueError("unsafe_annotation_storage")
            shutil.rmtree(annotations)
    from .native_export_api import archive_path
    archive_path(event_id).unlink(missing_ok=True)
    locked = db.scalar(select(m.Event).where(m.Event.id == event_id)
                       .with_for_update(key_share=True).execution_options(populate_existing=True))
    if locked is None or locked.status != "resetting":
        raise ValueError("clean_event_state_changed")
    clear_event_data(db, event_id, preserve_datasets=True)
    seed_categories(db, event_id)
    db.execute(delete(m.NativeOperation).where(
        m.NativeOperation.event_id == event_id, m.NativeOperation.id != operation.id,
        m.NativeOperation.kind.not_in(DATASET_OPERATIONS),
    ).execution_options(synchronize_session=False))
    locked.status = "active"
    locked.closing_at = locked.closing_by = None
    locked.last_native_job_number = 0
    operation.status, operation.completed_at, operation.error_code = "done", m.epoch(), None
    operation.payload = {**operation.payload, "phase": "done"}
    db.add(m.AuditEvent(event_id=event_id, action="event.clean.complete", target_type="event",
                       target_id=event_id, details={"preserved_datasets": len(datasets),
                       "removed_native_jobs": removed, "cleared_annotations": cleared}))
    db.commit()
    return {"status": "done", "operation_id": operation.id, "preserved_datasets": len(datasets),
            "removed_native_jobs": removed, "cleared_annotations": cleared}
