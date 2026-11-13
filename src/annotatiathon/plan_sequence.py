"""Durable video-first sequencing, gated on complete validated submissions."""
from fastapi import HTTPException
from sqlalchemy import func, select

from .models import (
    AuditEvent,
    BalancedVideoRun,
    Event,
    LabelingJob,
    LabelingPlan,
    MediaAsset,
    MediaDataset,
    ReviewSample,
    WorkBlock,
    WorkPacket,
)
from .native_media_pool import mirror_status

VIDEO_MODES = {"shared_30_v1", "balanced_30_v1"}


def video_predecessor(db, event_id, dataset_id, plan_id):
    parent = db.scalar(select(LabelingPlan).where(
        LabelingPlan.id == plan_id, LabelingPlan.event_id == event_id,
    ).with_for_update())
    if (parent is None or parent.dataset_id == dataset_id
            or parent.allocation_mode not in VIDEO_MODES
            or parent.status not in {"planned", "approved", "running", "paused"}):
        raise HTTPException(409, "Choose an existing video queue plan in this event, using a different dataset.")
    source = db.get(MediaDataset, parent.dataset_id)
    if source is None or source.image_count or not source.frame_count:
        raise HTTPException(409, "The first plan must use a video-only dataset.")
    if parent.allocation_mode == "balanced_30_v1":
        planned = db.scalar(select(func.sum(BalancedVideoRun.frame_count)).where(
            BalancedVideoRun.plan_id == parent.id,
        )) or 0
    else:
        planned = db.scalar(select(func.sum(WorkBlock.frame_count)).where(
            WorkBlock.plan_id == parent.id,
        )) or 0
    if planned != source.frame_count:
        raise HTTPException(409, "The video plan must cover every video frame before an image plan can follow it. Resolve excluded footage first.")
    return parent


def video_submissions_complete(db, parent):
    """Never use queue exhaustion or remote Submit alone as the handoff signal."""
    if parent.status != "running":
        return False
    total = db.scalar(select(func.count(MediaAsset.id)).where(
        MediaAsset.dataset_id == parent.dataset_id, MediaAsset.kind == "video_frame",
    )) or 0
    if not total:
        return False
    if db.scalar(select(LabelingJob.id).outerjoin(
        WorkPacket, WorkPacket.job_id == LabelingJob.id,
    ).where(
        LabelingJob.plan_id == parent.id, LabelingJob.release_mode == "shared_primary",
        WorkPacket.submitted_at.is_(None),
    ).limit(1)):
        return False
    # Distinct source IDs prevent duplicate submissions from counting as coverage.
    accepted = db.scalar(select(func.count(func.distinct(ReviewSample.media_id))).join(
        LabelingJob, LabelingJob.id == ReviewSample.job_id,
    ).join(MediaAsset, MediaAsset.id == ReviewSample.media_id).where(
        LabelingJob.plan_id == parent.id, LabelingJob.release_mode == "shared_primary",
        ReviewSample.validated_complete.is_(True),
        MediaAsset.dataset_id == parent.dataset_id, MediaAsset.kind == "video_frame",
    )) or 0
    return accepted == total


def activate_waiting_plans(db):
    """One atomic transition; restart-safe and safe with concurrent status workers."""
    waiting = list(db.scalars(select(LabelingPlan.id).where(
        LabelingPlan.status == "waiting", LabelingPlan.after_plan_id.is_not(None),
    )))
    for plan_id in waiting:
        candidate = db.get(LabelingPlan, plan_id)
        if candidate is None:
            db.rollback()
            continue
        event = db.scalar(select(Event).where(Event.id == candidate.event_id)
                          .with_for_update(key_share=True).execution_options(populate_existing=True))
        if event is None or event.status != "active":
            db.rollback()
            continue
        parent = db.scalar(select(LabelingPlan).where(
            LabelingPlan.id == candidate.after_plan_id,
        ).with_for_update().execution_options(populate_existing=True))
        plan = db.scalar(select(LabelingPlan).where(LabelingPlan.id == plan_id)
                         .with_for_update().execution_options(populate_existing=True))
        if (plan.status != "waiting" or parent is None
                or parent.event_id != plan.event_id or not video_submissions_complete(db, parent)):
            db.rollback()
            continue
        dataset = db.get(MediaDataset, plan.dataset_id)
        if (dataset is None or dataset.status != "ready"
                or not mirror_status(db, dataset)["ready_for_automatic_jobs"]):
            db.rollback()
            continue
        plan.status = "running"
        db.add(AuditEvent(event_id=plan.event_id, action="labeling.plan.sequence.start",
                          target_type="labeling_plan", target_id=plan.id,
                          details={"after_plan_id": parent.id}))
        db.commit()
