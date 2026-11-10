"""Event-specific annotation review with auditable per-frame quality scores."""
import json
import mimetypes
from collections import Counter
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .datasets_api import get_dataset
from .db import get_db
from .events_api import current_event
from .models import (
    AuditEvent,
    JobReview,
    LabelingJob,
    MediaAsset,
    MediaDataset,
    MonitorAssignment,
    NativeJobBinding,
    NativeOperation,
    Person,
    epoch,
)

router = APIRouter(prefix="/api/v1/review", tags=["quality review"])
VIEWERS = ("organiser", "monitor", "observer")
REVIEWERS = ("organiser", "monitor")


def get_job(db: Session, event_id: str, job_id: str,
            lock: bool = False) -> LabelingJob:
    q = select(LabelingJob).where(
        LabelingJob.id == job_id, LabelingJob.event_id == event_id
    )
    if lock:
        q = q.with_for_update()
    job = db.scalar(q)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not in current event.")
    return job


def verify_monitor(db: Session, ctx: AuthContext, job: LabelingJob) -> None:
    if "organiser" in ctx.staff.roles:
        return
    person = db.scalar(select(Person).where(
        Person.event_id == job.event_id,
        Person.email == ctx.staff.email,
        Person.active.is_(True),
        Person.kind == "monitor",
    ))
    if not person:
        raise HTTPException(
            status_code=403, detail="Monitoring account is not linked to an event monitor."
        )
    link = db.scalar(select(MonitorAssignment).where(
        MonitorAssignment.event_id == job.event_id,
        MonitorAssignment.team_id == job.team_id,
        MonitorAssignment.monitor_person_id == person.id,
        MonitorAssignment.active.is_(True),
    ))
    if link is None:
        raise HTTPException(status_code=403, detail="Team is assigned to another monitor.")


@router.get("/jobs/{job_id}/annotations/{media_id}")
def read_native_annotation(
    job_id: str, media_id: str,
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    """Read the latest completed local annotation snapshot for an assigned frame."""
    event = current_event(db, ctx)
    job = get_job(db, event.id, job_id)
    if "monitor" in ctx.staff.roles and "organiser" not in ctx.staff.roles:
        verify_monitor(db, ctx, job)
    if media_id not in job.media_ids:
        raise HTTPException(status_code=404, detail="Frame not assigned to this job.")
    ds = db.get(MediaDataset, job.dataset_id)
    if ds is None or ds.event_id != event.id:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    snapshot = (
        Path(ds.root_path) / "annotations" / job.id /
        ("round-" + str(job.correction_round))
    )
    # Manifest exists only after the full round has been published atomically.
    if not (snapshot / "manifest.json").is_file():
        raise HTTPException(
            status_code=404, detail="Submitted annotations are still syncing."
        )
    file = snapshot / (media_id + ".json")
    if not file.is_file():
        raise HTTPException(status_code=404, detail="Frame annotation not yet available.")
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail="Local annotation snapshot is unreadable.") from exc


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_id: str
    verdict: Literal["good", "bad", "incomplete"]
    correct_keypoints: int = Field(default=0, ge=0, le=10000)
    missing_keypoints: int = Field(default=0, ge=0, le=10000)
    incorrect_keypoints: int = Field(default=0, ge=0, le=10000)
    visible_keypoints: int = Field(default=0, ge=0, le=10000)
    occluded_keypoints: int = Field(default=0, ge=0, le=10000)
    temporal_consistency: bool | None = None
    notes: str = Field(default="", max_length=4000)


def review_view(review: JobReview) -> dict:
    return {
        "id": review.id, "media_id": review.media_id, "job_id": review.job_id,
        "verdict": review.verdict,
        "correct_keypoints": review.correct_keypoints,
        "missing_keypoints": review.missing_keypoints,
        "incorrect_keypoints": review.incorrect_keypoints,
        "visible_keypoints": review.visible_keypoints,
        "occluded_keypoints": review.occluded_keypoints,
        "temporal_consistency": review.temporal_consistency,
        "notes": review.notes, "reviewer_id": review.reviewer_id,
        "reviewed_at": review.reviewed_at,
        # Default keypoint metric follows the explicitly stated example weights:
        # correct +1, missing -1, incorrect 0. Visibility is tracked separately.
        "keypoint_score": review.correct_keypoints - review.missing_keypoints,
    }


@router.get("/jobs/{job_id}")
def list_reviews(
    job_id: str,
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    job = get_job(db, event.id, job_id)
    if "monitor" in ctx.staff.roles and "organiser" not in ctx.staff.roles:
        verify_monitor(db, ctx, job)
    items = db.scalars(select(JobReview).where(
        JobReview.event_id == event.id, JobReview.job_id == job.id
    ).order_by(JobReview.reviewed_at.desc())).all()
    verdicts = Counter(x.verdict for x in items)
    return {
        "job_id": job.id, "status": job.status,
        "review_status": job.review_status, "count": len(items),
        "reviewed_frames": len(items), "total_frames": job.frame_count,
        "verdicts": dict(verdicts),
        "keypoint_score": sum(x.correct_keypoints-x.missing_keypoints for x in items),
        "items": [review_view(x) for x in items],
    }


@router.put("/jobs/{job_id}/frames")
def review_frame(
    job_id: str, payload: ReviewInput,
    ctx: AuthContext = Depends(requires(*REVIEWERS, mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    job = get_job(db, event.id, job_id, lock=True)
    verify_monitor(db, ctx, job)
    if job.status not in ("on_review", "submitted", "needs_correction"):
        raise HTTPException(
            status_code=409,
            detail="Only submitted jobs may be reviewed. Sync native status first.",
        )
    if payload.media_id not in job.media_ids:
        raise HTTPException(status_code=422, detail="Media is not assigned to this job.")
    asset = db.scalar(select(MediaAsset).where(
        MediaAsset.id == payload.media_id, MediaAsset.event_id == event.id,
        MediaAsset.dataset_id == job.dataset_id,
    ))
    if not asset:
        raise HTTPException(status_code=404, detail="Assigned media not found.")
    item = db.scalar(select(JobReview).where(
        JobReview.job_id == job.id, JobReview.media_id == asset.id
    ))
    if item is None:
        item = JobReview(
            event_id=event.id, job_id=job.id, media_id=asset.id,
            reviewer_id=ctx.staff.id,
        )
        db.add(item)
    for key in type(payload).model_fields:
        if key != "media_id":
            setattr(item, key, getattr(payload, key))
    item.reviewer_id = ctx.staff.id
    item.reviewed_at = epoch()
    job.review_status = "in_progress"
    db.flush()
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="job.review.frame", target_type="job_review", target_id=item.id,
        details={
            "job_id": job.id, "media_id": asset.id,
            "verdict": item.verdict, "score": item.correct_keypoints-item.missing_keypoints,
        },
    ))
    db.commit()
    return review_view(item)


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accept", "return_for_correction"]
    notes: str = Field(default="", max_length=4000)


@router.post("/jobs/{job_id}/decision")
def decide_review(
    job_id: str, payload: ReviewDecision,
    ctx: AuthContext = Depends(requires(*REVIEWERS, mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    job = get_job(db, event.id, job_id, lock=True)
    verify_monitor(db, ctx, job)
    if job.status != "on_review":
        raise HTTPException(
            status_code=409,
            detail="Wait for both native annotator submissions before deciding a review.",
        )
    reviewed = list(db.scalars(select(JobReview).where(
        JobReview.job_id == job.id, JobReview.event_id == event.id,
    )))
    if not reviewed:
        raise HTTPException(status_code=409, detail="Score at least one frame first.")
    if payload.decision == "accept" and any(
        r.verdict != "good" for r in reviewed
    ):
        raise HTTPException(
            status_code=409,
            detail="Bad/incomplete reviewed frames remain. Correct them or return this job.",
        )
    job.status = ("completed" if payload.decision == "accept"
                  else "needs_correction")
    job.review_status = ("accepted" if payload.decision == "accept"
                         else "returned")
    native_correction_queued = False
    if payload.decision == "accept":
        job.completed_at = epoch()
    else:
        job.correction_round += 1
        bindings = list(db.scalars(select(NativeJobBinding).where(
            NativeJobBinding.event_id == event.id,
            NativeJobBinding.job_id == job.id,
        )))
        if len(bindings) == 2:
            previous = db.scalar(select(NativeOperation).where(
                NativeOperation.kind == "restart_job",
                NativeOperation.target_id == job.id,
            ).with_for_update())
            if previous is not None:
                if previous.status not in {"done", "attention", "blocked"}:
                    raise HTTPException(
                        status_code=409,
                        detail="An earlier correction operation is still in progress.",
                    )
                previous.status = "pending"
                previous.error_code = None
                previous.claimed_at = None
            else:
                db.add(NativeOperation(
                    event_id=event.id, kind="restart_job",
                    target_id=job.id, status="pending",
                ))
            native_correction_queued = True
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="job.review.accept" if payload.decision == "accept"
        else "job.review.return", target_type="labeling_job",
        target_id=job.id, details={
            "reviewed_frames": len(reviewed), "notes": payload.notes,
            "remote_status_not_changed": True,
            "correction_round": job.correction_round,
            "native_correction_queued": native_correction_queued,
        },
    ))
    db.commit()
    return {
        "id": job.id, "status": job.status, "review_status": job.review_status,
        "reviewed_frames": len(reviewed),
        "native_mutations": False,
        "native_correction_queued": native_correction_queued,
        "correction_round": job.correction_round,
    }


@router.get("/datasets/{dataset_id}/assets/{asset_id}/image")
def view_media(
    dataset_id: str, asset_id: str,
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    dataset = get_dataset(db, event.id, dataset_id)
    asset = db.scalar(select(MediaAsset).where(
        MediaAsset.id == asset_id, MediaAsset.event_id == event.id,
        MediaAsset.dataset_id == dataset.id,
    ))
    if asset is None:
        raise HTTPException(status_code=404, detail="Image not found.")
    media_base = (Path(dataset.root_path) / "media").resolve()
    target = (media_base / asset.relative_path).resolve()
    if not target.is_relative_to(media_base) or not target.is_file():
        raise HTTPException(status_code=404, detail="Local image missing.")
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=mime,
                        headers={"Cache-Control": "private, no-store"})
