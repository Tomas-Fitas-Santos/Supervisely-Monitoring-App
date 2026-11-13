"""Explicit revision-fenced morphology-balanced preparation and 30-frame claims.

No dataset scanning at dispatch: each approved run holds a contiguous ordinal
range, and claims fetch at most 30 immutable frame IDs from indexed PostgreSQL.
"""
import json
import os
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .datasets_api import get_dataset
from .db import get_db, session_factory
from .ecomorphology_reference import GROUPS, suggested_classification
from .events_api import current_event
from .models import (
    AuditEvent,
    BalancedVideoRun,
    DatasetAppendBatch,
    DatasetFolderClassification,
    DatasetPreparedQueue,
    Event,
    LabelingJob,
    LabelingPlan,
    MediaAsset,
    MediaDataset,
    MediaVideo,
    NativeOperation,
    ReviewSample,
    WorkPacket,
    epoch,
)

router = APIRouter(prefix="/api/v1", tags=["balanced automatic distribution"])
OPEN_BATCHES = ("uploading", "indexing", "ready_to_commit")


def local_inventory_ready(db: Session, ds: MediaDataset) -> bool:
    if ds.status != "ready":
        return False
    return db.scalar(select(DatasetAppendBatch.id).where(
        DatasetAppendBatch.dataset_id == ds.id,
        DatasetAppendBatch.status.in_(OPEN_BATCHES),
    ).limit(1)) is None


def prepared_view(db: Session, ds: MediaDataset) -> dict:
    prepared = db.get(DatasetPreparedQueue, ds.id)
    current = bool(prepared and
                   prepared.inventory_revision == ds.inventory_revision and
                   prepared.configuration_revision == ds.configuration_revision)
    return {
        "dataset_id": ds.id, "inventory_revision": ds.inventory_revision,
        "configuration_revision": ds.configuration_revision,
        "inventory_ready": local_inventory_ready(db, ds),
        "status": prepared.state if prepared and current else
                  "stale" if prepared else "not_prepared",
        "current": current,
        "summary": prepared.summary if current and prepared else None,
    }


@router.get("/datasets/{dataset_id}/balanced-preparation")
def get_balanced_preparation(
    dataset_id: str, ctx: AuthContext = Depends(requires("organiser", "monitor", "observer")),
    db: Session = Depends(get_db),
):
    ds = get_dataset(db, current_event(db, ctx).id, dataset_id)
    return prepared_view(db, ds)


@router.post("/datasets/{dataset_id}/balanced-preparation", status_code=202)
def request_balanced_preparation(
    dataset_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    if not local_inventory_ready(db, ds):
        raise HTTPException(409, "Finish upload and indexing before preparing the queue.")
    if db.scalar(select(LabelingJob.id).where(
        LabelingJob.dataset_id == ds.id,
    ).limit(1)):
        raise HTTPException(409, "The dataset has jobs already assigned.")
    current = db.get(DatasetPreparedQueue, ds.id)
    if (current is not None and current.state == "prepared"
            and current.inventory_revision == ds.inventory_revision
            and current.configuration_revision == ds.configuration_revision):
        return prepared_view(db, ds)
    if current is None:
        current = DatasetPreparedQueue(
            dataset_id=ds.id, event_id=ds.event_id,
            inventory_revision=ds.inventory_revision,
            configuration_revision=ds.configuration_revision,
            state="pending", summary={}, runs=[],
        )
        db.add(current)
    else:
        current.inventory_revision = ds.inventory_revision
        current.configuration_revision = ds.configuration_revision
        current.state = "pending"
        current.summary = {}
        current.runs = []
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "prepare_balanced",
        NativeOperation.target_id == ds.id,
    ).with_for_update())
    if op is None:
        op = NativeOperation(
            event_id=event.id, kind="prepare_balanced", target_id=ds.id,
            priority=170, status="pending",
        )
        db.add(op)
    elif op.status == "running":
        raise HTTPException(409, "Queue preparation is already running.")
    else:
        op.status = "pending"
        op.error_code = None
        op.claimed_at = None
        op.completed_at = None
    op.payload = {"inventory_revision": ds.inventory_revision,
                  "configuration_revision": ds.configuration_revision}
    db.add(AuditEvent(
        event_id=event.id, staff_id=ctx.staff.id,
        action="balanced.prepare.request", target_type="media_dataset",
        target_id=ds.id, details=op.payload,
    ))
    db.commit()
    return prepared_view(db, ds)


def _build_prepared_inventory(db: Session, ds: MediaDataset) -> tuple[list[dict], dict]:
    """Only metadata and indexed aggregate queries; never read media files."""
    grouped = {
        video_id: (int(count), low, high)
        for video_id, count, low, high in db.execute(
            select(
                MediaAsset.video_id, func.count(MediaAsset.id),
                func.min(MediaAsset.frame_index), func.max(MediaAsset.frame_index),
            ).where(
                MediaAsset.dataset_id == ds.id,
                MediaAsset.video_id.is_not(None),
            ).group_by(MediaAsset.video_id)
        )
    }
    overrides = {
        row.source_folder: row for row in db.scalars(
            select(DatasetFolderClassification).where(
                DatasetFolderClassification.dataset_id == ds.id,
            )
        )
    }
    supply = {group: {"group": group, "frames": 0, "videos": 0,
                      "species": set()} for group in GROUPS}
    supply["Unclassified"] = {"group": "Unclassified", "frames": 0,
                              "videos": 0, "species": set()}
    runs, excluded = [], Counter()
    skipped = []
    for video in db.scalars(select(MediaVideo).where(
        MediaVideo.dataset_id == ds.id,
    ).order_by(MediaVideo.video_group, MediaVideo.id)):
        count, low, high = grouped.get(video.id, (0, None, None))
        species = video.video_group.split("/")[0]
        override = overrides.get(species)
        proposed, _ = suggested_classification(species)
        group = (override.ecomorphology if override else proposed) or "Unclassified"
        reason = None
        if any("_sampled" in part.lower() for part in video.video_group.split("/")):
            reason = "sampled_variant"
        elif video.density not in {"full", "declared_full"}:
            reason = "not_full_density"
        elif video.identity_status != "complete":
            reason = "source_identity_unverified"
        elif any("inferred" in note.lower() for note in (video.identity_notes or [])):
            reason = "inferred_ordinals"
        elif not count or low is None or high is None or high - low + 1 != count:
            reason = "nonconsecutive_ordinals"
        elif count != video.frame_count:
            reason = "frame_inventory_mismatch"
        elif group not in GROUPS:
            reason = "unclassified_species"
        if reason:
            excluded[reason] += 1
            if len(skipped) < 40:
                skipped.append({"video": video.video_group, "reason": reason})
            continue
        runs.append({
            "video_id": video.id, "video_group": video.video_group,
            "source_folder": species, "morphology_group": group,
            "first_ordinal": int(low), "frame_count": count,
        })
        supply[group]["frames"] += count
        supply[group]["videos"] += 1
        supply[group]["species"].add(species)
    # The approved payload has one row per video, not 100,000 frame IDs.
    summary = {
        "eligible_frames": sum(r["frame_count"] for r in runs),
        "eligible_videos": len(runs),
        "groups": [
            {"group": group, "frames": data["frames"],
             "videos": data["videos"], "species": len(data["species"])}
            for group, data in supply.items()
        ],
        "exclusions": dict(excluded), "excluded_examples": skipped,
        "final_short_frames": sum(r["frame_count"] for r in runs) % 30,
    }
    return runs, summary


def process_preparation_once(db: Session) -> dict | None:
    """Credential-free local indexing lane. One fenced SQL transaction publishes draft."""
    cutoff = epoch() - 3600
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "prepare_balanced",
        or_(
            NativeOperation.status == "pending",
            (NativeOperation.status == "running") & (
                NativeOperation.claimed_at < cutoff),
        ),
    ).order_by(NativeOperation.created_at, NativeOperation.id)
        .with_for_update(skip_locked=True))
    if op is None:
        db.rollback()
        return None
    op.status = "running"
    op.claimed_at = epoch()
    op.attempts += 1
    op_id, ds_id = op.id, op.target_id
    requested = dict(op.payload or {})
    db.commit()
    try:
        ds = db.get(MediaDataset, ds_id)
        if ds is None or not local_inventory_ready(db, ds):
            raise ValueError("inventory_not_ready")
        runs, summary = _build_prepared_inventory(db, ds)
        current = db.scalar(select(MediaDataset).where(
            MediaDataset.id == ds_id,
        ).with_for_update().execution_options(populate_existing=True))
        if (current.inventory_revision != requested.get("inventory_revision")
                or current.configuration_revision != requested.get("configuration_revision")
                or not local_inventory_ready(db, current)):
            raise ValueError("preparation_revision_stale")
        record = db.get(DatasetPreparedQueue, ds_id)
        if (record is None or record.inventory_revision != current.inventory_revision
                or record.configuration_revision != current.configuration_revision):
            raise ValueError("prepared_draft_changed")
        record.runs = runs
        record.summary = summary
        record.state = "prepared"
        record.updated_at = epoch()
        finished = db.get(NativeOperation, op_id)
        finished.status = "done"
        finished.completed_at = epoch()
        finished.error_code = None
        db.commit()
        return {"status": "done", "operation_id": op_id, "videos": len(runs)}
    except Exception as exc:  # noqa: BLE001 - retain durable preparation failure status
        db.rollback()
        op = db.get(NativeOperation, op_id)
        if op:
            op.status = "attention"
            op.error_code = "balanced_preparation_failed"
        record = db.get(DatasetPreparedQueue, ds_id)
        if record and record.state == "pending":
            record.state = "attention"
        db.commit()
        return {"status": "attention", "operation_id": op_id,
                "error_type": type(exc).__name__}


def install_prepared_runs(db: Session, plan: LabelingPlan, ds: MediaDataset) -> int:
    record = db.get(DatasetPreparedQueue, ds.id)
    if (record is None or record.state != "prepared"
            or record.inventory_revision != ds.inventory_revision
            or record.configuration_revision != ds.configuration_revision
            or not local_inventory_ready(db, ds)
            or not record.runs):
        raise HTTPException(409, "Prepare the current complete dataset before creating this plan.")
    for i, row in enumerate(record.runs):
        db.add(BalancedVideoRun(
            event_id=plan.event_id, dataset_id=ds.id, plan_id=plan.id,
            video_id=row["video_id"], video_group=row["video_group"],
            source_folder=row["source_folder"],
            morphology_group=row["morphology_group"],
            first_ordinal=row["first_ordinal"], frame_count=row["frame_count"],
            selection_order=i,
        ))
    return len(record.runs)


def validate_prepared_plan(db: Session, plan: LabelingPlan) -> None:
    ds = db.get(MediaDataset, plan.dataset_id)
    if (ds is None or ds.status != "ready"
            or not local_inventory_ready(db, ds)
            or plan.prepared_revision != ds.inventory_revision):
        raise HTTPException(409, "Balanced plan is stale; prepare and create a new plan.")
    record = db.get(DatasetPreparedQueue, ds.id)
    if (record is None or record.state != "prepared"
            or record.inventory_revision != ds.inventory_revision
            or record.configuration_revision != ds.configuration_revision):
        raise HTTPException(409, "Morphology mapping changed; prepare queue again.")


def allocate_balanced_claim(db: Session, plan: LabelingPlan, event: Event,
                            team_id: str, person_id: str) -> LabelingJob | None:
    """Called ONLY after team, individual, open-packet and plan row locks."""
    from .native_operations_api import queue_publish

    runs = list(db.scalars(select(BalancedVideoRun).where(
        BalancedVideoRun.plan_id == plan.id,
    ).order_by(BalancedVideoRun.selection_order, BalancedVideoRun.id)))
    if not runs:
        return None
    finish_window = int(os.environ.get("NIGHTJAR_BALANCED_FINISH_WINDOW_SECONDS", "3600"))
    sealed = event.end_at is not None and epoch() >= event.end_at - max(finish_window, 0)
    remaining_total = sum(row.frame_count - row.next_offset for row in runs)
    if not remaining_total:
        return None

    # Started, unfinished tails have priority. Unopened groups are chosen by
    # projected COMPLETE-video supply so scarce groups get dispatched first.
    projected = Counter()
    opened_species = Counter()
    for row in runs:
        if row.next_offset:
            projected[row.morphology_group] += row.frame_count
            opened_species[(row.morphology_group, row.source_folder)] += 1
    selected = []
    slots = 30
    tails = sorted(
        (row for row in runs if 0 < row.next_offset < row.frame_count),
        key=lambda row: (row.frame_count - row.next_offset,
                         row.selection_order, row.id),
    )
    for row in tails:
        if not slots:
            break
        count = min(slots, row.frame_count - row.next_offset)
        selected.append((row, row.next_offset, count))
        slots -= count
    while slots and not sealed:
        selected_ids = {row.id for row, _, _ in selected}
        unopened = [row for row in runs if row.next_offset == 0
                    and row.id not in selected_ids]
        if not unopened:
            break
        row = min(unopened, key=lambda x: (
            projected[x.morphology_group],
            opened_species[(x.morphology_group, x.source_folder)],
            x.morphology_group, x.source_folder,
            x.selection_order, x.id,
        ))
        count = min(slots, row.frame_count)
        selected.append((row, 0, count))
        slots -= count
        projected[row.morphology_group] += row.frame_count
        opened_species[(row.morphology_group, row.source_folder)] += 1
    if not selected:
        return None
    # Under the finish-window seal, a short tail is permitted only when all
    # eligible unassigned frames have been exhausted, never because one video
    # lacks 30 frames.
    frame_ids = []
    morph = Counter()
    for row, offset, size in selected:
        first = row.first_ordinal + offset
        assets = list(db.scalars(select(MediaAsset).where(
            MediaAsset.dataset_id == plan.dataset_id,
            MediaAsset.video_id == row.video_id,
            MediaAsset.frame_index >= first,
            MediaAsset.frame_index < first + size,
        ).order_by(MediaAsset.frame_index)))
        if (len(assets) != size or any(
                asset.frame_index != first + index
                for index, asset in enumerate(assets))):
            raise ValueError("Balanced run ordinals changed after approval.")
        frame_ids.extend(asset.id for asset in assets)
        morph.update(asset.morphology for asset in assets)
    if len(frame_ids) != len(set(frame_ids)) or len(frame_ids) > 30:
        raise ValueError("Balanced claim must use distinct frames and at most 30.")
    for row, offset, size in selected:
        if row.next_offset != offset:
            raise ValueError("Concurrent balanced cursor change.")
        row.next_offset += size
    seq = db.scalar(select(func.max(LabelingJob.sequence)).where(
        LabelingJob.plan_id == plan.id, LabelingJob.team_id == team_id,
    )) or 0
    job = LabelingJob(
        event_id=plan.event_id, dataset_id=plan.dataset_id, plan_id=plan.id,
        team_id=team_id, assignee_person_id=person_id, sequence=seq + 1,
        release_mode="shared_primary", media_ids=frame_ids,
        frame_count=len(frame_ids),
        video_count=len({row.video_id for row, _, _ in selected}),
        morphology_counts=dict(morph),
    )
    db.add(job)
    db.flush()
    db.add(WorkPacket(job_id=job.id, event_id=plan.event_id, person_id=person_id))
    queue_publish(db, job)
    return job


def record_balanced_acceptance(db: Session, job: LabelingJob, accepted: list[str]) -> None:
    if not accepted or job.release_mode != "shared_primary":
        return
    plan = db.get(LabelingPlan, job.plan_id)
    if plan is None or plan.allocation_mode != "balanced_30_v1":
        return
    by_video = Counter(db.scalars(select(MediaAsset.video_id).where(
        MediaAsset.id.in_(accepted), MediaAsset.dataset_id == plan.dataset_id,
    )))
    if sum(by_video.values()) != len(accepted):
        raise ValueError("Accepted frame identity is incomplete.")
    for row in db.scalars(select(BalancedVideoRun).where(
        BalancedVideoRun.plan_id == plan.id,
        BalancedVideoRun.video_id.in_(by_video),
    ).with_for_update()):
        row.accepted_count += by_video.pop(row.video_id)
        if row.accepted_count > row.frame_count:
            raise ValueError("A balanced source video has been double-counted.")
    if by_video:
        raise ValueError("Accepted frame lacks an approved balanced video.")


@router.get("/labeling/plans/{plan_id}/balanced-progress")
def balanced_progress(
    plan_id: str, ctx: AuthContext = Depends(requires("organiser", "monitor", "observer")),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    plan = db.scalar(select(LabelingPlan).where(
        LabelingPlan.id == plan_id, LabelingPlan.event_id == event.id,
        LabelingPlan.allocation_mode == "balanced_30_v1",
    ))
    if not plan:
        raise HTTPException(404, "Balanced plan not found.")
    rows = list(db.scalars(select(BalancedVideoRun).where(
        BalancedVideoRun.plan_id == plan.id,
    ).order_by(BalancedVideoRun.morphology_group, BalancedVideoRun.video_group)))
    groups = {g: {"group": g, "planned": 0, "assigned": 0,
                  "validated": 0, "usable": 0, "videos_complete": 0} for g in GROUPS}
    incomplete = []
    for run in rows:
        group = groups[run.morphology_group]
        group["planned"] += run.frame_count
        group["assigned"] += run.next_offset
        group["validated"] += run.accepted_count
        complete = run.accepted_count == run.frame_count and run.next_offset == run.frame_count
        if complete:
            group["usable"] += run.frame_count
            group["videos_complete"] += 1
        elif run.next_offset:
            incomplete.append({
                "video": run.video_group, "group": run.morphology_group,
                "assigned": run.next_offset, "validated": run.accepted_count,
                "missing": run.frame_count - run.accepted_count,
            })
    return {"plan_id": plan.id, "groups": list(groups.values()),
            "unfinished_videos": incomplete[:500],
            "extra_unfinished": max(0, len(incomplete) - 500)}


@router.get("/labeling/plans/{plan_id}/balanced-manifest.jsonl")
def balanced_manifest(
    plan_id: str, ctx: AuthContext = Depends(requires("organiser", "monitor", "observer")),
    db: Session = Depends(get_db),
):
    from fastapi.responses import StreamingResponse
    event = current_event(db, ctx)
    plan = db.scalar(select(LabelingPlan).where(
        LabelingPlan.id == plan_id, LabelingPlan.event_id == event.id,
        LabelingPlan.allocation_mode == "balanced_30_v1",
    ))
    if plan is None:
        raise HTTPException(404, "Balanced plan not found.")
    def rows():
        with session_factory()() as session:
            runs = session.scalars(select(BalancedVideoRun).where(
                BalancedVideoRun.plan_id == plan_id,
            ).order_by(BalancedVideoRun.video_group))
            for run in runs:
                complete = run.accepted_count == run.frame_count
                yield json.dumps({
                    "type": "video", "video": run.video_group,
                    "group": run.morphology_group, "species": run.source_folder,
                    "frames": run.frame_count, "validated": run.accepted_count,
                    "usable": bool(complete),
                    "missing": run.frame_count - run.accepted_count,
                }) + "\n"
                if not complete:
                    continue
                assets = session.execute(select(
                    MediaAsset.id, MediaAsset.frame_index, MediaAsset.sha256,
                    ReviewSample.id, LabelingJob.review_status,
                ).join(
                    ReviewSample, ReviewSample.media_id == MediaAsset.id,
                ).join(
                    LabelingJob, LabelingJob.id == ReviewSample.job_id,
                ).where(
                    MediaAsset.video_id == run.video_id,
                    LabelingJob.plan_id == plan_id,
                    ReviewSample.validated_complete.is_(True),
                ).order_by(MediaAsset.frame_index).execution_options(yield_per=200))
                for asset_id, ordinal, sha, snapshot_id, review_status in assets:
                    yield json.dumps({
                        "type": "frame", "source_video": run.video_group,
                        "group": run.morphology_group, "species": run.source_folder,
                        "ordinal": ordinal, "source_media_id": asset_id,
                        "source_sha256": sha, "snapshot_ref": snapshot_id,
                        "review_status": review_status,
                    }) + "\n"
    return StreamingResponse(rows(), media_type="application/x-ndjson",
                             headers={"Content-Disposition": "attachment; filename=balanced-collection.jsonl"})
