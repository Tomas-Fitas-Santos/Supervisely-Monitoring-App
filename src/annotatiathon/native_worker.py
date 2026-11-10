"""Recoverable Supervisely SDK publisher and polling worker.

Run as a *separate local process*:
    python -m annotatiathon.native_worker --once
    python -m annotatiathon.native_worker --loop --poll-seconds 12

The official SDK calls used here are:
  Api(server_address, token), workspace.get_info_by_id,
  project.get_list/create, dataset.get_list/create,
  image.get_list/upload_paths, labeling_job.get_list/create/get_info_by_id.
No project/job deletion, role mutation or forced job completion is performed.

A pair receives two native LabelingJob records over a single job-specific
dataset. Its media is isolated from every *other pair job*. The SDK does not
promise simultaneous collaborative editing; that behavior requires live proof.
"""
import argparse
import logging
import time
from collections import defaultdict
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import session_factory
from .models import (
    AuditEvent,
    Event,
    LabelingJob,
    LabelingPlan,
    MediaAsset,
    MediaDataset,
    NativeJobBinding,
    NativeJobRevision,
    NativeMediaInstance,
    NativeOperation,
    SuperviselyConfig,
    epoch,
)
from .native_export_api import export_event_zip
from .native_operations_api import (
    can_queue_next,
    next_eligible,
    queue_publish,
    team_members,
)
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


def remote_project(api, config: SuperviselyConfig, dataset: MediaDataset) -> int:
    workspace = api.workspace.get_info_by_id(config.workspace_id)
    if workspace is None or workspace.team_id != config.team_id:
        raise NativeWorkerError("workspace_team_mismatch")
    name = "Nightjar-" + dataset.event_id + "-" + dataset.id
    if dataset.native_project_id:
        project = api.project.get_info_by_id(dataset.native_project_id)
        if project is None or project.name != name:
            raise NativeWorkerError("project_identity_changed")
        return project.id
    existing = unique_remote(
        api.project.get_list(config.workspace_id), name, "project"
    )
    if existing is None:
        existing = api.project.create(
            config.workspace_id, name, change_name_if_conflict=False,
        )
    return existing.id


def remote_dataset(api, project_id: int, job: LabelingJob) -> int:
    name = "Nightjar-" + job.id
    if job.native_dataset_id is not None:
        item = api.dataset.get_info_by_id(job.native_dataset_id)
        if item is None or item.name != name or item.project_id != project_id:
            raise NativeWorkerError("native_dataset_identity_changed")
        return item.id
    existing = unique_remote(
        api.dataset.get_list(project_id), name, "dataset"
    )
    if existing is None:
        existing = api.dataset.create(
            project_id, name, change_name_if_conflict=False,
        )
    return existing.id


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


def publish_job(db: Session, job: LabelingJob, api, config: SuperviselyConfig) -> dict:
    """Idempotent by deterministic project/dataset names and native user IDs."""
    if job.status not in {"queued", "publishing", "in_progress"}:
        raise NativeWorkerError("invalid_job_status")
    if job.sequence > 1 and not can_queue_next(db, job):
        raise NativeWorkerError("previous_pair_job_not_accepted")
    plan = db.get(LabelingPlan, job.plan_id)
    event = db.get(Event, job.event_id)
    if event is None or event.status != "active" or plan is None or plan.status != "running":
        raise NativeWorkerError("plan_not_running")
    people = team_members(db, job)
    dataset = db.get(MediaDataset, job.dataset_id)
    if dataset is None or dataset.status != "ready":
        raise NativeWorkerError("local_dataset_not_ready")
    project_id = remote_project(api, config, dataset)
    if dataset.native_project_id is None:
        dataset.native_project_id = project_id
        db.commit()
    remote_ds_id = remote_dataset(api, project_id, job)
    if job.native_dataset_id is None:
        job.native_dataset_id = remote_ds_id
        job.status = "publishing"
        db.commit()
    native_media = upload_media(db, api, job, dataset, remote_ds_id)
    roster_ids = {p.supervisely_user_id: p for p in people}
    # Same project and job-specific dataset, two independent Supervisely job rows.
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
            name="Nightjar pair " + job.id,
            dataset_id=remote_ds_id,
            user_ids=missing,
            images_ids=native_media,
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
    # A returned job is managed by the explicit native correction operation.
    # Do not treat two old 'on_review' records as a new submission.
    if job.status in {"queued", "publishing", "completed", "needs_correction"}:
        return job.status
    bindings = list(db.scalars(select(NativeJobBinding).where(
        NativeJobBinding.job_id == job.id,
        NativeJobBinding.event_id == job.event_id,
    )))
    if len(bindings) != 2:
        return job.status
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
        and info.images_count > 0
        and info.finished_images_count >= info.images_count
        for info in statuses
    ):
        job.status = "on_review"
        job.submitted_at = job.submitted_at or epoch()
    db.commit()
    return job.status


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
            except Exception:  # noqa: BLE001 - isolation of remote status errors
                db.rollback()
                logger.warning("Native status reconciliation requires inspection for job %s", job.id)
                continue
            if before != after:
                changed += 1
        if plan.status == "running":
            for next_job in next_eligible(db, plan):
                if queue_publish(db, next_job).status == "pending":
                    queued += 1
            db.commit()
    return {"changed_jobs": changed, "pending_operations": queued}


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
        ).order_by(NativeOperation.created_at).with_for_update(skip_locked=True).limit(200)
    ))
    op = next((
        row for row in operations
        if (db.get(LabelingJob, row.target_id).sequence == 1
            or can_queue_next(db, db.get(LabelingJob, row.target_id)))
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
        people = team_members(db, job)
        by_person = {p.id: p for p in people}
        bindings = list(db.scalars(select(NativeJobBinding).where(
            NativeJobBinding.event_id == job.event_id,
            NativeJobBinding.job_id == job.id,
        )))
        if len(bindings) != 2 or {b.person_id for b in bindings} != set(by_person):
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


def run_worker(*, once: bool, poll_seconds: int) -> None:
    if not once and not 3 <= poll_seconds <= 300:
        raise ValueError("Polling interval must be 3-300 seconds.")
    config_isolation: tuple[int, int, str] | None = None
    api = None
    while True:
        with session_factory()() as db:
            config = db.get(SuperviselyConfig, 1)
            if (config is None or not config.verified or config.team_id is None
                    or config.workspace_id is None):
                raise NativeWorkerError("verified_team_workspace_required")
            identifier = (config.team_id, config.workspace_id, config.server_url)
            if api is None or config_isolation != identifier:
                api = connect_native(config)
                config_isolation = identifier
            try:
                correction = process_correction_once(db, api, config)
                operation = process_once(db, api, config)
                export = process_export_once(db, api)
                sync_result = sync_all(db, api, config)
                if correction or operation or export or sync_result["changed_jobs"]:
                    logger.info("Nightjar worker: correction=%s publish=%s export=%s sync=%s",
                                correction, operation, export, sync_result)
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
