"""Separate dataset queues with video assignments taking priority over images."""
from fastapi import HTTPException
from sqlalchemy import func, select

from .models import (
    AuditEvent,
    BalancedVideoRun,
    Event,
    LabelingPlan,
    MediaDataset,
    WorkBlock,
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


def video_queue_exhausted(db, parent):
    """Assigned video work may remain in progress, correction, or review."""
    if parent.status != "running" or parent.allocation_mode not in VIDEO_MODES:
        return False
    source = db.get(MediaDataset, parent.dataset_id)
    if source is None or not source.frame_count:
        return False
    if parent.allocation_mode == "balanced_30_v1":
        rows = BalancedVideoRun
        remaining = rows.next_offset < rows.frame_count
    else:
        rows = WorkBlock
        remaining = rows.job_id.is_(None)
    total = db.scalar(select(func.sum(rows.frame_count)).where(rows.plan_id == parent.id)) or 0
    return total == source.frame_count and db.scalar(select(rows.id).where(
        rows.plan_id == parent.id, remaining,
    ).limit(1)) is None


def activate_following_plan(db, parent):
    """Caller holds the parent plan lock, before any annotator/team locks.

    No commit: the caller atomically activates and optionally claims image work.
    This permits the next free annotator to cross queues in the same worker poll.
    """
    child = db.scalar(select(LabelingPlan).where(
        LabelingPlan.after_plan_id == parent.id,
        LabelingPlan.status.in_(["waiting", "running"]),
    ).with_for_update().execution_options(populate_existing=True))
    if child is None or child.event_id != parent.event_id or not video_queue_exhausted(db, parent):
        return None
    if child.status == "waiting":
        dataset = db.get(MediaDataset, child.dataset_id)
        if (dataset is None or dataset.status != "ready"
                or not mirror_status(db, dataset)["ready_for_automatic_jobs"]):
            return None
        child.status = "running"
        db.add(AuditEvent(event_id=child.event_id, action="labeling.plan.sequence.start",
                          target_type="labeling_plan", target_id=child.id,
                          details={"after_plan_id": parent.id, "trigger": "video_queue_exhausted"}))
        db.flush()
    return child


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
        if parent is not None:
            activate_following_plan(db, parent)
        db.commit()
