"""Recoverable Supervisely SDK publisher and polling worker.

Run as a *separate local process*:
    python -m annotatiathon.native_worker --once
    python -m annotatiathon.native_worker --loop --poll-seconds 12

The official SDK calls used here are:
  Api(server_address, token), workspace.get_info_by_id,
  project.get_list/create/get_meta/update_meta, dataset.get_list/create,
  image.get_list/upload_paths, labeling_job.get_list/create/get_info_by_id.
Jobs and datasets are removed only when an organiser explicitly confirms a
full event reset. No team-member deletion or forced job completion is performed.

Each new video job belongs to exactly one verified annotator. Two members
of a team receive separate videos and separate native jobs. Historical pair
jobs with two native bindings remain readable and can finish normally.
"""
import argparse
import json
import logging
import os
import shutil
import time
from collections import defaultdict
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from .annotation_schema import (
    ANNOTATION_CLASSES,
    AnnotationSchemaError,
    ensure_project_annotation_classes,
)
from .db import session_factory
from .events_api import seed_categories
from .full_reset_api import clear_event_data, safe_dataset_paths
from .models import (
    AuditEvent,
    CompetitionTeam,
    Event,
    LabelingJob,
    LabelingPlan,
    MediaAsset,
    MediaDataset,
    NativeJobBinding,
    NativeJobRevision,
    NativeMediaInstance,
    NativeOperation,
    NativeSourceMedia,
    Person,
    MonitorAssignment,
    SuperviselyConfig,
    epoch,
)
from .native_export_api import export_event_zip
from .native_media_pool import (
    MirrorError, assert_mirror_complete, mirror_dataset,
    source_image_name,
)
from .native_names import (
    dataset_name,
    labeling_job_name,
    legacy_dataset_name,
    legacy_project_name,
    owned_project_event_id,
    project_name,
)
from .native_operations_api import (
    can_queue_next,
    next_eligible,
    queue_publish,
    team_members,
)
from .pair_members import pair_members
from .supervisely_client import NativeAPIError, require_connection

logger = logging.getLogger(__name__)


class NativeWorkerError(Exception):
    """Stable, non-secret publishing and reconciliation error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def connect_native(config: SuperviselyConfig):
    """The optional SDK is loaded only in the worker, not the web server."""
    address, token = require_connection(config)
    try:
        import supervisely as sly
    except ImportError as exc:
        raise NativeWorkerError("sdk_missing") from exc
    return sly.Api(server_address=address, token=token)


def unique_remote(items, expected: str, kind: str):
    matches = [item for item in items if item.name == expected]
    if len(matches) > 1:
        raise NativeWorkerError(f"ambiguous_{kind}")
    return matches[0] if matches else None


def remote_project(api, config: SuperviselyConfig,
                   dataset: MediaDataset, event: Event) -> int:
    workspace = api.workspace.get_info_by_id(config.workspace_id)
    if workspace is None or workspace.team_id != config.team_id:
        raise NativeWorkerError("workspace_team_mismatch")
    name = project_name(event, dataset)
    old_name = legacy_project_name(dataset)
    if dataset.native_project_id:
        project = api.project.get_info_by_id(dataset.native_project_id)
        if (project is None or project.name not in {name, old_name}
                or project.workspace_id != config.workspace_id):
            raise NativeWorkerError("project_identity_changed")
        return project.id
    remote = list(api.project.get_list(config.workspace_id))
    matches = [p for p in remote if p.name in {name, old_name}]
    if len(matches) > 1:
        raise NativeWorkerError("ambiguous_project")
    if matches:
        return matches[0].id
    created = api.project.create(
        config.workspace_id, name, change_name_if_conflict=False,
    )
    return created.id


def job_video_group(db: Session, job: LabelingJob) -> str | None:
    if not job.media_ids:
        return None
    return db.scalar(select(MediaAsset.video_group).where(
        MediaAsset.event_id == job.event_id,
        MediaAsset.dataset_id == job.dataset_id,
        MediaAsset.id.in_(job.media_ids),
        MediaAsset.video_group.is_not(None),
    ).limit(1))


def remote_dataset(api, db: Session, project_id: int,
                   job: LabelingJob, team: CompetitionTeam) -> int:
    name = dataset_name(job, team, job_video_group(db, job))
    old_name = legacy_dataset_name(job)
    if job.native_dataset_id is not None:
        item = api.dataset.get_info_by_id(job.native_dataset_id)
        if item is None or item.name not in {name, old_name} or item.project_id != project_id:
            raise NativeWorkerError("native_dataset_identity_changed")
        return item.id
    matches = [d for d in api.dataset.get_list(project_id)
               if d.name in {name, old_name}]
    if len(matches) > 1:
        raise NativeWorkerError("ambiguous_dataset")
    if matches:
        return matches[0].id
    created = api.dataset.create(
        project_id, name, change_name_if_conflict=False,
    )
    return created.id


def media_name(asset: MediaAsset) -> str:
    return asset.id + PurePosixPath(asset.relative_path).suffix.lower()


def upload_media(db: Session, api, job: LabelingJob, ds: MediaDataset,
                 native_dataset_id: int) -> list[int]:
    assets = {a.id: a for a in db.scalars(select(MediaAsset).where(
        MediaAsset.id.in_(job.media_ids),
        MediaAsset.event_id == job.event_id,
        MediaAsset.dataset_id == job.dataset_id,
    ))}
    if len(assets) != len(job.media_ids):
        raise NativeWorkerError("missing_local_media")
    linked = {x.media_id: x for x in db.scalars(select(NativeMediaInstance).where(
        NativeMediaInstance.event_id == job.event_id,
        NativeMediaInstance.job_id == job.id,
    ))}
    remote_images = api.image.get_list(native_dataset_id)
    existing: dict[str, int] = {}
    for image in remote_images:
        if image.name in existing:
            raise NativeWorkerError("duplicate_remote_image")
        existing[image.name] = image.id
    jobs_source = (Path(ds.root_path) / "media").resolve()
    pending: list[tuple[MediaAsset, str, str]] = []
    for media_id in job.media_ids:
        asset = assets[media_id]
        name = media_name(asset)
        remote_id = existing.get(name)
        current = linked.get(media_id)
        if current:
            if current.native_dataset_id != native_dataset_id:
                raise NativeWorkerError("media_binding_dataset_changed")
            if remote_id != current.native_image_id:
                raise NativeWorkerError("remote_media_binding_missing")
            continue
        if remote_id is not None:
            db.add(NativeMediaInstance(
                event_id=job.event_id, job_id=job.id, media_id=media_id,
                native_dataset_id=native_dataset_id, native_image_id=remote_id,
            ))
            continue
        path = (jobs_source / asset.relative_path).resolve()
        if not path.is_relative_to(jobs_source) or not path.is_file():
            raise NativeWorkerError("media_path_missing")
        pending.append((asset, name, str(path)))
    db.commit()
    # A batch is journaled only after Supervisely confirms the created image IDs.
    # On crash, the next run lists remote names before uploading again.
    for start in range(0, len(pending), 24):
        batch = pending[start:start + 24]
        results = api.image.upload_paths(
            native_dataset_id,
            names=[x[1] for x in batch],
            paths=[x[2] for x in batch],
            conflict_resolution="skip",
        )
        returned = {item.name: item.id for item in results}
        if set(returned) != {x[1] for x in batch}:
            raise NativeWorkerError("remote_upload_incomplete")
        for asset, name, _path in batch:
            db.add(NativeMediaInstance(
                event_id=job.event_id, job_id=job.id, media_id=asset.id,
                native_dataset_id=native_dataset_id, native_image_id=returned[name],
            ))
        db.commit()
        existing.update(returned)
    # Preserve the exact local frame order to connect native image IDs to source paths.
    all_instances = {
        x.media_id: x.native_image_id for x in db.scalars(select(NativeMediaInstance).where(
            NativeMediaInstance.job_id == job.id,
            NativeMediaInstance.event_id == job.event_id,
        ))
    }
    if len(all_instances) != len(job.media_ids):
        raise NativeWorkerError("media_upload_not_complete")
    return [all_instances[mid] for mid in job.media_ids]


def assigned_people(db: Session, job: LabelingJob):
    """Respect historical pair jobs; new jobs name one verified person."""
    if job.assignee_person_id is None:
        return team_members(db, job)
    members, _ = pair_members(db, job.event_id, job.team_id)
    if len(members) != 2:
        raise NativeWorkerError("pair_membership_changed")
    person = next((p for p in members if p.id == job.assignee_person_id), None)
    if person is None:
        raise NativeWorkerError("assigned_member_not_in_team")
    permitted = ({"annotator"} if person.kind == "participant"
                 else {"admin", "manager", "developer"})
    if (not person.supervisely_user_id or person.membership_status != "joined"
            or not person.native_verified_at
            or (person.supervisely_role or "").lower() not in permitted):
        raise NativeWorkerError("assigned_member_unverified")
    return [person]


def process_mirror_once(db: Session, api, config: SuperviselyConfig) -> dict | None:
    """Mirror a whole source library once, before any labeling job can be issued."""
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "mirror_dataset",
        NativeOperation.status == "pending",
    ).order_by(NativeOperation.created_at, NativeOperation.id)
      .with_for_update(skip_locked=True))
    if op is None:
        db.rollback()
        return None
    op.status = "running"
    op.claimed_at = epoch()
    op.attempts += 1
    opid, dsid = op.id, op.target_id
    db.commit()
    try:
        ds = db.get(MediaDataset, dsid)
        if ds is None:
            raise MirrorError("source_dataset_not_found")
        result = mirror_dataset(db, api, config, ds)
    except Exception as exc:  # noqa: BLE001 - uncertain writes stay retryable
        db.rollback()
        failed = db.get(NativeOperation, opid)
        failed.status = "attention"
        failed.error_code = (
            exc.code if isinstance(exc, (MirrorError, NativeAPIError))
            else "mirror_remote_uncertain"
        )
        db.commit()
        logger.warning("Supervisely source mirror %s needs reconciliation (%s)",
                       opid, type(exc).__name__)
        return {"status": "attention", "operation_id": opid}
    finished = db.get(NativeOperation, opid)
    finished.status = "done"
    finished.completed_at = epoch()
    finished.error_code = None
    db.commit()
    return {"status": "done", "operation_id": opid, **result}


def source_media_for_job(db: Session, job: LabelingJob,
                         native_dataset_id: int) -> list[int]:
    """Build job-local references to mirrored images without uploading files."""
    source = {row.media_id: row for row in db.scalars(
        select(NativeSourceMedia).where(
            NativeSourceMedia.event_id == job.event_id,
            NativeSourceMedia.dataset_id == job.dataset_id,
            NativeSourceMedia.media_id.in_(job.media_ids),
        )
    )}
    if len(source) != len(job.media_ids):
        raise NativeWorkerError("job_source_frames_not_mirrored")
    mappings = {row.media_id: row for row in db.scalars(select(
        NativeMediaInstance,
    ).where(NativeMediaInstance.job_id == job.id))}
    for media_id in job.media_ids:
        remote = source[media_id]
        if remote.native_dataset_id != native_dataset_id:
            raise NativeWorkerError("source_dataset_changed")
        old = mappings.get(media_id)
        if old:
            if (old.native_image_id != remote.native_image_id
                    or old.native_dataset_id != native_dataset_id):
                raise NativeWorkerError("native_job_image_mapping_changed")
        else:
            db.add(NativeMediaInstance(
                event_id=job.event_id, job_id=job.id, media_id=media_id,
                native_dataset_id=native_dataset_id,
                native_image_id=remote.native_image_id,
            ))
    db.commit()
    return [source[media_id].native_image_id for media_id in job.media_ids]


def manual_media_dataset(api, dataset: MediaDataset, project_id: int) -> int:
    name = "Manual assignments · independent video copies"
    if dataset.native_manual_dataset_id is not None:
        ds = api.dataset.get_info_by_id(dataset.native_manual_dataset_id)
        if ds is None or ds.project_id != project_id or ds.name != name:
            raise NativeWorkerError("manual_dataset_identity_changed")
        return ds.id
    options = [item for item in api.dataset.get_list(project_id)
               if item.name == name]
    if len(options) > 1:
        raise NativeWorkerError("ambiguous_manual_media_dataset")
    ds = options[0] if options else api.dataset.create(
        project_id, name, change_name_if_conflict=False,
    )
    dataset.native_manual_dataset_id = ds.id
    return ds.id


def publish_job(db: Session, job: LabelingJob, api, config: SuperviselyConfig) -> dict:
    """Idempotent by deterministic project/dataset names and native user IDs."""
    if job.status not in {"queued", "publishing", "in_progress"}:
        raise NativeWorkerError("invalid_job_status")
    if not can_queue_next(db, job):
        raise NativeWorkerError("previous_annotator_job_not_accepted")
    plan = db.get(LabelingPlan, job.plan_id)
    event = db.get(Event, job.event_id)
    if event is None or event.status != "active" or plan is None or plan.status != "running":
        raise NativeWorkerError("plan_not_running")
    people = assigned_people(db, job)
    dataset = db.get(MediaDataset, job.dataset_id)
    if dataset is None or dataset.status != "ready":
        raise NativeWorkerError("local_dataset_not_ready")
    team = db.get(CompetitionTeam, job.team_id)
    if team is None:
        raise NativeWorkerError("missing_competition_team")
    project_id = remote_project(api, config, dataset, event)
    # All image datasets/jobs in this Supervisely project share project metadata.
    # Make sure annotation tools exist *before* uploading frames or publishing jobs.
    try:
        ensure_project_annotation_classes(api, project_id)
    except AnnotationSchemaError as exc:
        raise NativeWorkerError(exc.code) from exc
    if dataset.native_project_id is None:
        dataset.native_project_id = project_id
        db.commit()
    if job.native_dataset_id is not None and job.native_dataset_id not in {
        dataset.native_dataset_id, dataset.native_manual_dataset_id,
    }:
        # Existing pre-upgrade per-job native datasets remain readable and
        # recoverable; do not attempt to move an in-progress native job.
        remote_ds_id = remote_dataset(api, db, project_id, job, team)
        native_media = upload_media(db, api, job, dataset, remote_ds_id)
    elif job.release_mode == "automatic":
        try:
            assert_mirror_complete(db, dataset)
        except MirrorError as exc:
            raise NativeWorkerError(exc.code) from exc
        remote_ds_id = dataset.native_dataset_id
        native_media = source_media_for_job(db, job, remote_ds_id)
    else:
        remote_ds_id = manual_media_dataset(api, dataset, project_id)
        db.commit()
        native_media = upload_media(db, api, job, dataset, remote_ds_id)
    if job.native_dataset_id is None:
        job.native_dataset_id = remote_ds_id
        job.status = "publishing"
        db.commit()
    roster_ids = {p.supervisely_user_id: p for p in people}
    # One immutable dataset and one native job per assigned annotator.
    current = api.labeling_job.get_list(
        config.team_id, dataset_id=remote_ds_id, show_disabled=True,
    )
    by_user = defaultdict(list)
    for remote in current:
        if remote.dataset_id != remote_ds_id:
            raise NativeWorkerError("native_dataset_mismatch")
        by_user[remote.assigned_to_id].append(remote)
    if any(uid not in roster_ids or len(rows) != 1 for uid, rows in by_user.items()):
        raise NativeWorkerError("unexpected_existing_native_job")
    missing = [uid for uid in roster_ids if uid not in by_user]
    if missing:
        api.labeling_job.create(
            name=labeling_job_name(
                job, team, job_video_group(db, job),
                people[0].name if job.assignee_person_id else "Team pair",
            ),
            dataset_id=remote_ds_id,
            user_ids=missing,
            images_ids=native_media,
            classes_to_label=[name for name, _shape, _color, _meaning
                              in ANNOTATION_CLASSES],
            readme=(
                "Annotate the bird with a bounding box (bird) and the head "
                "landmarks as points: left_eye, right_eye, beak, crown. "
                "Use the corresponding *_occ class when a landmark is "
                "occluded. Visible and occluded points use matched color pairs."
            ),
        )
        current = api.labeling_job.get_list(
            config.team_id, dataset_id=remote_ds_id, show_disabled=True,
        )
        by_user = defaultdict(list)
        for remote in current:
            by_user[remote.assigned_to_id].append(remote)
    if set(by_user) != set(roster_ids) or any(len(rows) != 1 for rows in by_user.values()):
        raise NativeWorkerError("native_job_confirmation_failed")
    for uid, person in roster_ids.items():
        remote = by_user[uid][0]
        existing = db.scalar(select(NativeJobBinding).where(
            NativeJobBinding.event_id == job.event_id,
            NativeJobBinding.job_id == job.id,
            NativeJobBinding.person_id == person.id,
        ))
        if existing:
            if existing.native_job_id != remote.id:
                raise NativeWorkerError("native_job_identity_changed")
            existing.native_status = remote.status
            existing.last_synced_at = epoch()
        else:
            db.add(NativeJobBinding(
                event_id=job.event_id, job_id=job.id, person_id=person.id,
                native_job_id=remote.id,
                native_status=remote.status, last_synced_at=epoch(),
            ))
    if job.status in {"queued", "publishing"}:
        job.status = "in_progress"
        job.started_at = job.started_at or epoch()
    db.add(AuditEvent(
        event_id=job.event_id, action="native.job.publish",
        target_type="labeling_job", target_id=job.id,
        details={"native_dataset_id": remote_ds_id,
                 "native_job_ids": [by_user[uid][0].id for uid in roster_ids],
                 "image_count": len(native_media)},
    ))
    db.commit()
    return {"job_id": job.id, "native_jobs": len(by_user),
            "native_media": len(native_media)}


def sync_one(db: Session, api, job: LabelingJob) -> str:
    # Serialize worker polling against simultaneous monitor decisions.
    # Refresh any ORM-cached state after acquiring the PostgreSQL row lock.
    locked = db.scalar(select(LabelingJob).where(
        LabelingJob.id == job.id, LabelingJob.event_id == job.event_id,
    ).with_for_update().execution_options(populate_existing=True))
    if locked is None:
        raise NativeWorkerError("job_missing_during_sync")
    job = locked
    # A returned job is managed by the explicit native correction operation.
    # Do not treat two old 'on_review' records as a new submission.
    if job.status in {"queued", "publishing", "completed", "needs_correction"}:
        return job.status
    bindings = list(db.scalars(select(NativeJobBinding).where(
        NativeJobBinding.job_id == job.id,
        NativeJobBinding.event_id == job.event_id,
    )))
    expected_count = 1 if job.assignee_person_id else 2
    if len(bindings) != expected_count:
        raise NativeWorkerError("native_binding_count_mismatch")
    statuses = []
    for binding in bindings:
        native = api.labeling_job.get_info_by_id(binding.native_job_id)
        if native is None or native.id != binding.native_job_id:
            raise NativeWorkerError("native_job_lost")
        if (job.native_dataset_id is None
                or native.dataset_id != job.native_dataset_id):
            raise NativeWorkerError("native_job_dataset_changed")
        binding.native_status = native.status
        binding.last_synced_at = epoch()
        statuses.append(native)
    if any(info.status == "stopped" for info in statuses):
        job.status = "needs_correction"
        job.review_status = "returned"
    elif all(
        info.status in {"on_review", "completed", "review_completed"}
        and (job.assignee_person_id is not None or (
            info.images_count > 0
            and info.finished_images_count >= info.images_count
        ))
        for info in statuses
    ):
        # New single-annotator jobs trust Supervisely's submitted status.
        # Preserve the historical completion rule for old two-annotator jobs.
        job.status = "on_review"
        job.submitted_at = job.submitted_at or epoch()
    db.commit()
    return job.status


def capture_job_annotations(db: Session, api, job: LabelingJob) -> bool:
    """Copy *only annotation JSON* next to immutable local source media.

    Each source asset can have many independent pair-job annotation snapshots.
    The correction round is part of the directory name so retries cannot
    overwrite a previously reviewed revision. A manifest is published atomically.
    """
    if job.status not in {"on_review", "completed"}:
        return False
    dataset = db.get(MediaDataset, job.dataset_id)
    if dataset is None or dataset.status != "ready":
        raise NativeWorkerError("annotation_source_dataset_missing")
    destination = (
        Path(dataset.root_path) / "annotations" / job.id /
        ("round-" + str(job.correction_round))
    )
    if (destination / "manifest.json").is_file():
        return False
    if destination.exists():
        raise NativeWorkerError("annotation_snapshot_incomplete")
    assets = {
        item.id: item for item in db.scalars(select(MediaAsset).where(
            MediaAsset.dataset_id == dataset.id,
            MediaAsset.event_id == job.event_id,
            MediaAsset.id.in_(job.media_ids),
        ))
    }
    mappings = {
        item.media_id: item for item in db.scalars(select(NativeMediaInstance).where(
            NativeMediaInstance.job_id == job.id,
            NativeMediaInstance.event_id == job.event_id,
        ))
    }
    if len(assets) != len(job.media_ids) or set(mappings) != set(job.media_ids):
        raise NativeWorkerError("annotation_media_mapping_incomplete")
    if any(m.native_dataset_id != job.native_dataset_id for m in mappings.values()):
        raise NativeWorkerError("annotation_dataset_mapping_changed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir()
    captured = epoch()
    try:
        rows = []
        annotated_frames = 0
        for media_id in job.media_ids:
            asset = assets[media_id]
            native = mappings[media_id]
            annotation = api.annotation.download_json(native.native_image_id)
            if not isinstance(annotation, dict):
                raise NativeWorkerError("native_annotation_invalid")
            # Supervisely JSON uses "objects"; accept the legacy "labels"
            # field as well for snapshots created by earlier SDK versions.
            objects = annotation.get("objects")
            legacy_labels = annotation.get("labels")
            if ((isinstance(objects, list) and bool(objects)) or
                    (isinstance(legacy_labels, list) and bool(legacy_labels))):
                annotated_frames += 1
            row = {
                "source_media_id": media_id,
                "source_relative_path": asset.relative_path,
                "source_sha256": asset.sha256,
                "native_dataset_id": native.native_dataset_id,
                "native_image_id": native.native_image_id,
                "annotation": annotation,
            }
            with (temporary / (media_id + ".json")).open(
                "w", encoding="utf-8"
            ) as stream:
                json.dump(row, stream, ensure_ascii=False, separators=(",", ":"))
            rows.append({
                "source_media_id": media_id,
                "native_image_id": native.native_image_id,
                "source_relative_path": asset.relative_path,
            })
        with (temporary / "manifest.json").open("w", encoding="utf-8") as stream:
            json.dump({
                "schema": "nightjar-annotation-snapshot-v1",
                "event_id": job.event_id, "dataset_id": dataset.id,
                "job_id": job.id, "correction_round": job.correction_round,
                "captured_at": captured, "media": rows,
                "annotated_frames": annotated_frames,
                "annotation_state": "current_supervisely_snapshot",
            }, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return True


def sync_all(db: Session, api, config: SuperviselyConfig) -> dict:
    event_plans = list(db.scalars(select(LabelingPlan).where(
        LabelingPlan.status.in_(["running", "paused"]),
    )))
    changed = 0
    queued = 0
    for plan in event_plans:
        event = db.get(Event, plan.event_id)
        if event is None or event.status != "active":
            continue
        jobs = list(db.scalars(select(LabelingJob).where(
            LabelingJob.plan_id == plan.id,
            LabelingJob.event_id == plan.event_id,
        )))
        for job in jobs:
            if job.status not in {"in_progress", "on_review", "needs_correction"}:
                continue
            before = job.status
            try:
                after = sync_one(db, api, job)
            except Exception as exc:  # noqa: BLE001 - isolate failures by native job
                db.rollback()
                code = exc.code if isinstance(exc, (NativeWorkerError, NativeAPIError)) else type(exc).__name__
                logger.warning("Native status reconciliation failed for job %s (%s)", job.id, code)
                continue
            if before != after:
                changed += 1
        if plan.status == "running":
            for next_job in next_eligible(db, plan):
                if queue_publish(db, next_job).status == "pending":
                    queued += 1
            db.commit()
    # Read-only annotation sync is recoverable without new SQL queues:
    # missing snapshots are retried on the next worker poll, even after review.
    # Limit the work per poll so job-status and publishing checks remain responsive.
    snapshots = 0
    failures = 0
    for plan in event_plans:
        if snapshots >= 5:
            break
        candidates = list(db.scalars(select(LabelingJob).where(
            LabelingJob.plan_id == plan.id,
            LabelingJob.event_id == plan.event_id,
            LabelingJob.status.in_(["on_review", "completed"]),
        )))
        for candidate in candidates:
            if snapshots >= 5:
                break
            try:
                if capture_job_annotations(db, api, candidate):
                    snapshots += 1
            except Exception as exc:  # noqa: BLE001 - retried next worker poll
                failures += 1
                code = exc.code if isinstance(exc, (NativeWorkerError, NativeAPIError)) else type(exc).__name__
                logger.warning("Annotation snapshot pending retry for job %s (%s)", candidate.id, code)
    return {
        "changed_jobs": changed, "pending_operations": queued,
        "annotation_snapshots": snapshots, "annotation_sync_errors": failures,
    }


def process_once(db: Session, api, config: SuperviselyConfig) -> dict | None:
    """Claim one queued operation; a crash leaves it for explicit review, not reexecution."""
    operations = list(db.scalars(
        select(NativeOperation)
        .join(LabelingJob, LabelingJob.id == NativeOperation.target_id)
        .join(LabelingPlan, LabelingPlan.id == LabelingJob.plan_id)
        .join(Event, Event.id == NativeOperation.event_id)
        .where(
            NativeOperation.status == "pending",
            NativeOperation.kind == "publish_job",
            LabelingPlan.status == "running",
            Event.status == "active",
        ).order_by(
            NativeOperation.priority.desc(),
            NativeOperation.created_at, NativeOperation.id,
        ).with_for_update(skip_locked=True).limit(200)
    ))
    op = next((
        row for row in operations
        if can_queue_next(db, db.get(LabelingJob, row.target_id))
    ), None)
    if op is None:
        db.rollback()
        return None
    event = db.get(Event, op.event_id)
    job = db.get(LabelingJob, op.target_id)
    plan = db.get(LabelingPlan, job.plan_id) if job else None
    if (event is None or event.status != "active" or job is None
            or plan is None or plan.status != "running"):
        db.rollback()
        return None
    op.status = "running"
    op.claimed_at = epoch()
    op.attempts += 1
    opid = op.id
    db.commit()
    try:
        details = publish_job(db, job, api, config)
    except Exception as exc:  # noqa: BLE001 - remote outcome may be uncertain
        db.rollback()
        failed = db.get(NativeOperation, opid)
        failed.status = "attention"
        failed.error_code = (
            exc.code if isinstance(exc, (NativeWorkerError, NativeAPIError))
            else "remote_operation_uncertain"
        )
        db.add(AuditEvent(
            event_id=job.event_id, action="native.job.attention",
            target_type="native_operation", target_id=opid,
            details={"code": failed.error_code},
        ))
        db.commit()
        logger.warning("Native operation %s requires reconciliation (type %s)",
                       opid, type(exc).__name__)
        return {"operation_id": opid, "status": "attention"}
    finished = db.get(NativeOperation, opid)
    finished.status = "done"
    finished.completed_at = epoch()
    finished.error_code = None
    db.commit()
    return {"operation_id": opid, "status": "done", **details}


def process_correction_once(db: Session, api, config: SuperviselyConfig) -> dict | None:
    """Restart the current pair job for corrections, without advancing its queue."""
    operations = list(db.scalars(
        select(NativeOperation)
        .join(LabelingJob, LabelingJob.id == NativeOperation.target_id)
        .join(LabelingPlan, LabelingPlan.id == LabelingJob.plan_id)
        .join(Event, Event.id == NativeOperation.event_id)
        .where(
            NativeOperation.status == "pending",
            NativeOperation.kind == "restart_job",
            LabelingPlan.status.in_(["running", "paused"]),
            Event.status == "active",
        ).order_by(NativeOperation.created_at)
        .with_for_update(skip_locked=True).limit(1)
    ))
    if not operations:
        db.rollback()
        return None
    op = operations[0]
    job = db.get(LabelingJob, op.target_id)
    if job is None or job.status != "needs_correction" or job.correction_round < 1:
        op.status = "attention"
        op.error_code = "correction_state_changed"
        db.commit()
        return {"operation_id": op.id, "status": "attention"}
    op.status = "running"
    op.claimed_at = epoch()
    op.attempts += 1
    opid, jobid = op.id, job.id
    db.commit()
    try:
        people = assigned_people(db, job)
        by_person = {p.id: p for p in people}
        bindings = list(db.scalars(select(NativeJobBinding).where(
            NativeJobBinding.event_id == job.event_id,
            NativeJobBinding.job_id == job.id,
        )))
        if len(bindings) != len(people) or {b.person_id for b in bindings} != set(by_person):
            raise NativeWorkerError("correction_binding_mismatch")
        # Reconcile independently named restart jobs before any remote write,
        # including if the first remote restart succeeded but its response was lost.
        remote = api.labeling_job.get_list(
            config.team_id, dataset_id=job.native_dataset_id, show_disabled=True,
        )
        updated = []
        for binding in bindings:
            person = by_person[binding.person_id]
            title = (
                f"Nightjar correction {job.id} round {job.correction_round} "
                f"member {binding.person_id}"
            )
            matches = [
                row for row in remote
                if row.name == title and row.assigned_to_id == person.supervisely_user_id
                and row.dataset_id == job.native_dataset_id
            ]
            if len(matches) > 1:
                raise NativeWorkerError("duplicate_correction_job")
            if not matches:
                api.labeling_job.restart(
                    binding.native_job_id,
                    assignee_ids=[person.supervisely_user_id],
                    title=title,
                    complete_existing=True,
                    only_rejected_entities=False,
                )
                remote = api.labeling_job.get_list(
                    config.team_id, dataset_id=job.native_dataset_id,
                    show_disabled=True,
                )
                matches = [
                    row for row in remote
                    if row.name == title
                    and row.assigned_to_id == person.supervisely_user_id
                    and row.dataset_id == job.native_dataset_id
                ]
            if len(matches) != 1 or matches[0].id == binding.native_job_id:
                raise NativeWorkerError("correction_restart_not_confirmed")
            updated.append((binding, matches[0]))
        # One local transaction switches both bindings. Old native IDs remain
        # in a separate historical table (and remain on the remote server).
        for binding, new_job in updated:
            previous = db.scalar(select(NativeJobRevision).where(
                NativeJobRevision.native_job_id == binding.native_job_id,
            ))
            if previous is None:
                db.add(NativeJobRevision(
                    event_id=job.event_id, job_id=job.id,
                    person_id=binding.person_id,
                    native_job_id=binding.native_job_id,
                    correction_round=job.correction_round,
                ))
            binding.native_job_id = new_job.id
            binding.native_status = new_job.status
            binding.last_synced_at = epoch()
        job.status = "in_progress"
        job.review_status = "unreviewed"
        job.submitted_at = None
        job.completed_at = None
        task = db.get(NativeOperation, opid)
        task.status = "done"
        task.error_code = None
        task.completed_at = epoch()
        db.add(AuditEvent(
            event_id=job.event_id,
            action="native.job.correct_restart",
            target_type="labeling_job", target_id=jobid,
            details={
                "round": job.correction_round,
                "new_job_ids": [r.id for _, r in updated],
                "historical_job_ids": [row.native_job_id for row in
                    db.scalars(select(NativeJobRevision).where(
                        NativeJobRevision.job_id == jobid,
                    ))],
            },
        ))
        db.commit()
        return {"operation_id": opid, "status": "done", "correction_round": job.correction_round}
    except Exception as exc:  # noqa: BLE001 - native restart may succeed with lost response
        db.rollback()
        task = db.get(NativeOperation, opid)
        task.status = "attention"
        task.error_code = (
            exc.code if isinstance(exc, (NativeWorkerError, NativeAPIError))
            else "native_correction_uncertain"
        )
        db.add(AuditEvent(
            event_id=job.event_id,
            action="native.job.correction_attention",
            target_type="native_operation", target_id=opid,
            details={"error_code": task.error_code},
        ))
        db.commit()
        logger.warning("Correction operation %s requires reconciliation (%s)",
                       opid, type(exc).__name__)
        return {"operation_id": opid, "status": "attention"}


def process_export_once(db: Session, api) -> dict | None:
    """Archive a complete read-only annotation snapshot after event closure."""
    tasks = list(db.scalars(
        select(NativeOperation)
        .join(Event, Event.id == NativeOperation.event_id)
        .where(
            NativeOperation.kind == "export_event",
            NativeOperation.status == "pending",
            Event.status == "archived",
        )
        .order_by(NativeOperation.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    ))
    if not tasks:
        db.rollback()
        return None
    task = tasks[0]
    task.status = "running"
    task.claimed_at = epoch()
    task.attempts += 1
    tid = task.id
    event_id = task.event_id
    db.commit()
    try:
        result = export_event_zip(db, api, event_id)
    except Exception as exc:  # noqa: BLE001 - a remote read can fail mid-archive
        db.rollback()
        failed = db.get(NativeOperation, tid)
        failed.status = "attention"
        failed.error_code = (
            exc.code if isinstance(exc, (NativeWorkerError, NativeAPIError))
            else "annotation_export_interrupted"
        )
        db.add(AuditEvent(
            event_id=event_id, action="native.export.attention",
            target_type="native_operation", target_id=tid,
            details={"error_code": failed.error_code},
        ))
        db.commit()
        logger.warning("Native export %s requires inspection (%s)",
                       tid, type(exc).__name__)
        return {"operation_id": tid, "status": "attention"}
    finished = db.get(NativeOperation, tid)
    finished.status = "done"
    finished.completed_at = epoch()
    db.add(AuditEvent(
        event_id=event_id, action="native.export.finished",
        target_type="native_operation", target_id=tid,
        details=result,
    ))
    db.commit()
    return {"operation_id": tid, "status": "done", **result}


def process_event_reset(db: Session, api, config: SuperviselyConfig) -> dict | None:
    """Delete owned native jobs, archive their datasets/projects, then reset.

    Retries are idempotent by querying remote resources again. Standard team
    tokens cannot permanently purge archived datasets or projects, but native
    labeling jobs can be removed. Team and membership data are never touched.
    """
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "reset_event",
        NativeOperation.status == "pending",
    ).order_by(NativeOperation.created_at).with_for_update(skip_locked=True))
    if op is None:
        db.rollback()
        return None
    event = db.get(Event, op.event_id)
    if event is None or event.status != "resetting":
        op.status = "attention"
        op.error_code = "event_reset_state_changed"
        db.commit()
        return {"status": "attention", "operation_id": op.id}
    op.status = "running"
    op.claimed_at = epoch()
    op.attempts += 1
    opid, event_id = op.id, event.id
    db.commit()
    try:
        # Reset only the precise event, not the Supervisely team or workspace.
        workspace = api.workspace.get_info_by_id(config.workspace_id)
        if workspace is None or workspace.team_id != config.team_id:
            raise NativeWorkerError("reset_workspace_team_mismatch")
        source_datasets = list(db.scalars(select(MediaDataset).where(
            MediaDataset.event_id == event_id,
        )))
        expected_by_id = {
            ds.native_project_id: {
                legacy_project_name(ds),
                project_name(event, ds),
            }
            for ds in source_datasets if ds.native_project_id is not None
        }
        visible = list(api.project.get_list(config.workspace_id))
        matched = sorted(
            (p for p in visible if owned_project_event_id(p.name) == event_id),
            key=lambda p: p.id,
        )
        found_ids = {p.id for p in matched}
        # Previous Nightjar resets only archived projects. Include those
        # projects so their old native jobs are actually deleted now, even
        # if the local rows were already cleared by an older reset.
        all_projects = list(api.project.get_list_all(
            filters=[{"field": "workspaceId", "operator": "eq",
                      "value": config.workspace_id}],
            skip_exported=False,
        ))
        archived = sorted(
            (p for p in all_projects
             if p.id not in found_ids
             and p.workspace_id == config.workspace_id
             and owned_project_event_id(p.name) == event_id),
            key=lambda p: p.id,
        )
        for project in matched:
            expected = expected_by_id.get(project.id)
            if expected is not None and project.name not in expected:
                raise NativeWorkerError("reset_project_identity_conflict")
        # Check stored references before touching anything. A project renamed
        # or moved after publishing cannot be deleted by name guessing.
        for pid, names in expected_by_id.items():
            if pid in found_ids:
                continue
            remote = api.project.get_info_by_id(pid)
            if remote is not None and (
                remote.name not in names
                or remote.workspace_id != config.workspace_id
            ):
                raise NativeWorkerError("reset_linked_project_outside_scope")
            # A project previously archived is absent from active listings.
            # Job IDs remain available in the local bindings for cleanup.
        paths = safe_dataset_paths(db, event_id)
        removed_jobs = 0
        archived_datasets = 0
        archived_projects = 0

        # Native job IDs are stored locally even after their project is
        # archived. Remove explicit bindings first to eliminate historical
        # jobs that archive-only resets left in Supervisely's Jobs view.
        bindings = list(db.scalars(select(NativeJobBinding).where(
            NativeJobBinding.event_id == event_id,
        )))
        for binding in bindings:
            remote = api.labeling_job.get_info_by_id(binding.native_job_id)
            if remote is None:
                continue
            local = db.get(LabelingJob, binding.job_id)
            if (local is None or local.native_dataset_id is None
                    or remote.dataset_id != local.native_dataset_id):
                raise NativeWorkerError("reset_native_job_identity_conflict")
            api.labeling_job.remove(binding.native_job_id)
            removed_jobs += 1
            if api.labeling_job.get_info_by_id(binding.native_job_id) is not None:
                raise NativeWorkerError("reset_native_job_still_visible")

        # Archived projects are not present in the active workspace listing,
        # but their old labeling jobs can remain in the team jobs view.
        for project in archived:
            for native in list(api.labeling_job.get_list(
                config.team_id, project_id=project.id, show_disabled=True,
            )):
                if native.project_id != project.id:
                    raise NativeWorkerError("reset_archived_job_identity_conflict")
                api.labeling_job.remove(native.id)
                removed_jobs += 1
            if api.labeling_job.get_list(
                config.team_id, project_id=project.id, show_disabled=True,
            ):
                raise NativeWorkerError("reset_archived_native_jobs_still_visible")

        for project in matched:
            # This project is Nightjar-owned for the selected event, not a
            # shared Supervisely workspace project. Remove all its jobs first
            # so ordinary project archival does not retain live labeling jobs.
            for dataset in list(api.dataset.get_list(project.id)):
                if dataset.project_id != project.id:
                    raise NativeWorkerError("reset_dataset_identity_conflict")
                active_jobs = list(api.labeling_job.get_list(
                    config.team_id, dataset_id=dataset.id, show_disabled=True,
                ))
                for native in active_jobs:
                    if native.dataset_id != dataset.id:
                        raise NativeWorkerError("reset_native_job_dataset_mismatch")
                    api.labeling_job.remove(native.id)
                    removed_jobs += 1
                if api.labeling_job.get_list(
                    config.team_id, dataset_id=dataset.id, show_disabled=True,
                ):
                    raise NativeWorkerError("reset_native_jobs_still_visible")
                # Standard Supervisely token can archive datasets, not
                # permanently erase them (root instance access required).
                api.dataset.remove(dataset.id)
                archived_datasets += 1
            if api.dataset.get_list(project.id):
                raise NativeWorkerError("reset_remote_datasets_still_visible")
            api.project.remove(project.id)
            archived_projects += 1
        if any(owned_project_event_id(p.name) == event_id
               for p in api.project.get_list(config.workspace_id)):
            raise NativeWorkerError("reset_remote_projects_still_visible")
        for path in paths:
            if path.exists():
                if not path.is_dir() or path.is_symlink():
                    raise NativeWorkerError("unsafe_dataset_storage")
                shutil.rmtree(path)
        # The export ZIP belongs to this event only and may be removed too.
        from .native_export_api import archive_path
        export = archive_path(event_id)
        export.unlink(missing_ok=True)
        locked = db.scalar(select(Event).where(
            Event.id == event_id,
        ).with_for_update())
        if locked is None or locked.status != "resetting":
            raise NativeWorkerError("reset_event_identity_changed")
        clear_event_data(db, event_id)
        # Keep the restored event usable immediately for the ticket desk.
        seed_categories(db, event_id)
        # Other operations are now obsolete and must not resume.
        db.query(NativeOperation).filter(
            NativeOperation.event_id == event_id,
            NativeOperation.id != opid,
        ).delete(synchronize_session=False)
        reset = db.get(NativeOperation, opid)
        reset.status = "done"
        reset.completed_at = epoch()
        reset.error_code = None
        locked.status = "active"
        db.add(AuditEvent(
            event_id=event_id, action="event.reset.complete",
            target_type="event", target_id=event_id,
            details={"archived_projects": archived_projects,
                     "archived_datasets": archived_datasets,
                     "removed_native_jobs": removed_jobs,
                     "removed_dataset_directories": len(paths)},
        ))
        db.commit()
        return {"status": "done", "operation_id": opid,
                "archived_projects": archived_projects,
                "archived_datasets": archived_datasets,
                "removed_native_jobs": removed_jobs}
    except Exception as exc:  # noqa: BLE001 - preserve safe retry state
        db.rollback()
        failed = db.get(NativeOperation, opid)
        failed.status = "attention"
        failed.error_code = (
            exc.code if isinstance(exc, (NativeWorkerError, NativeAPIError))
            else str(exc) if isinstance(exc, ValueError) else "reset_remote_or_local_error"
        )[:70]
        db.commit()
        logger.warning("Event reset %s requires retry (type %s)",
                       opid, type(exc).__name__)
        return {"status": "attention", "operation_id": opid}


def run_worker(*, once: bool, poll_seconds: int) -> None:
    if not once and not 3 <= poll_seconds <= 300:
        raise ValueError("Polling interval must be 3-300 seconds.")
    config_isolation: tuple[int, int, str, str | None] | None = None
    api = None
    while True:
        with session_factory()() as db:
            config = db.get(SuperviselyConfig, 1)
            if (config is None or not config.verified or config.team_id is None
                    or config.workspace_id is None):
                raise NativeWorkerError("verified_team_workspace_required")
            identifier = (config.team_id, config.workspace_id, config.server_url,
                          config.token_ciphertext)
            if api is None or config_isolation != identifier:
                api = connect_native(config)
                config_isolation = identifier
            try:
                # A resetting event may still have queued publishing tasks;
                # execute the reset exclusively until it has finished.
                active_reset = db.scalar(select(NativeOperation.id).where(
                    NativeOperation.kind == "reset_event",
                    NativeOperation.status.in_(["pending", "running", "attention"]),
                ))
                if active_reset is not None:
                    reset_result = process_event_reset(db, api, config)
                    if reset_result:
                        logger.info("Nightjar reset worker: %s", reset_result)
                    if once:
                        return
                    time.sleep(poll_seconds)
                    continue
                mirror = process_mirror_once(db, api, config)
                correction = process_correction_once(db, api, config)
                operation = process_once(db, api, config)
                export = process_export_once(db, api)
                sync_result = sync_all(db, api, config)
                if mirror or correction or operation or export or sync_result["changed_jobs"] or sync_result["annotation_snapshots"]:
                    logger.info("Nightjar worker: mirror=%s correction=%s publish=%s export=%s sync=%s",
                                mirror, correction, operation, export, sync_result)
            except Exception as exc:  # noqa: BLE001 - keep long-lived worker alive
                db.rollback()
                logger.warning("Supervisely worker cycle failed (type %s); "
                               "no exception details logged.", type(exc).__name__)
        if once:
            return
        time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Nightjar Supervisely SDK worker")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true",
                       help="Process at most one publishing operation and poll statuses")
    group.add_argument("--loop", action="store_true",
                       help="Process continuously while this terminal remains open")
    parser.add_argument("--poll-seconds", type=int, default=12)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    run_worker(once=arguments.once, poll_seconds=arguments.poll_seconds)


if __name__ == "__main__":
    main()
