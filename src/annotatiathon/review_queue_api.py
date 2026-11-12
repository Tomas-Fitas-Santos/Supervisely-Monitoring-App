"""One immutable review candidate per submitted frame, with shared monitor leases."""
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from .annotation_schema import annotation_class_view
from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import (
    CompetitionTeam,
    LabelingJob,
    MediaAsset,
    MediaDataset,
    Person,
    ReviewRubric,
    ReviewSample,
    epoch,
    identifier,
)
from .review_eligibility import eligible_frame_review
from .scoring_api import (
    PRIZE_RELEASE_MODES,
    Weights,
    frame_points,
    latest_rubric,
    resolved_weights,
    score_verdict,
)

router = APIRouter(prefix="/api/v1/review-queue", tags=["frame review"])
LEASE_SECONDS = 180
read_access = requires("organiser", "monitor")
write_access = requires("organiser", "monitor", mutation=True)


def ingest_sample(db: Session, job: LabelingJob, root: Path, *,
                  media_ids: list[str] | None = None,
                  validated: bool = False) -> None:
    """Make all completed per-frame snapshots available to event monitors.

    Existing completed decisions and claims are never mutated.  A job's
    source-media identity and correction round make retries idempotent, even
    when the worker re-processes an already published snapshot manifest.
    Historical two-person native jobs are handled by their legacy review API.
    """
    if job.assignee_person_id is None or not job.media_ids:
        return
    if len(set(job.media_ids)) != len(job.media_ids):
        raise ValueError("Duplicate source frames in submitted job")
    existing = set(db.scalars(select(ReviewSample.media_id).where(
        ReviewSample.job_id == job.id,
    ))) if validated else set(db.scalars(select(ReviewSample.media_id).where(
        ReviewSample.job_id == job.id,
        ReviewSample.correction_round == job.correction_round,
    )))
    # Do not use job arrival time or the frame's video position to choose
    # reviews. Every eligible submitted frame joins one event-wide random pool.
    for media_id in (media_ids if media_ids is not None else job.media_ids):
        if media_id in existing:
            continue
        raw = (root / f"{media_id}.json").read_bytes()
        snapshot = json.loads(raw)
        if snapshot["source_media_id"] != media_id:
            raise ValueError("Submitted frame source does not match its snapshot")
        if not isinstance(snapshot.get("annotation"), dict):
            raise TypeError("Submitted frame does not contain annotation JSON")
        snapshot["sha256"] = hashlib.sha256(raw).hexdigest()
        snapshot["selection_reason"] = "submitted_frame"
        db.add(ReviewSample(
            event_id=job.event_id, job_id=job.id, team_id=job.team_id,
            media_id=media_id, correction_round=job.correction_round,
            validated_complete=validated, snapshot=snapshot,
        ))


class Lease(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_token: UUID


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_index: int = Field(ge=0)
    correct: bool
    reason: Literal["wrong_class", "wrong_placement", "sabotage"] | None = None
    corrected_class: str | None = None


class Completion(Lease):
    verdicts: list[Verdict]
    missing_classes: list[str] = Field(default_factory=list, max_length=1000)
    action: Literal["next", "pause"] = "pause"


def lock_event(db, ctx):
    return current_event(db, ctx, writable=True)


def claim_view(db, sample):
    job = db.get(LabelingJob, sample.job_id)
    person = db.get(Person, job.assignee_person_id)
    rubric = db.get(ReviewRubric, sample.rubric_id) if sample.rubric_id else None
    return {
        "review_id": sample.id, "claim_token": sample.claim_token,
        "expires_at": sample.expires_at, "source_media_id": sample.media_id,
        "annotation_snapshot_id": sample.id, "snapshot_revision": sample.snapshot["sha256"],
        "source_filename": Path(sample.snapshot["source_relative_path"]).name,
        "team_name": db.get(CompetitionTeam, sample.team_id).name,
        "annotator_name": person.name, "correction_round": sample.correction_round,
        "selection_reason": sample.snapshot["selection_reason"],
        "image_url": f"/api/v1/review-queue/{sample.id}/image",
        "annotation": sample.snapshot["annotation"], "classes": annotation_class_view(),
        "rubric_version": rubric.version if rubric else 0,
    }


def claim(db, event_id, reviewer_id):
    now = epoch()
    rows = ReviewSample
    scope = rows.event_id == event_id
    current_submission = (eligible_frame_review(),)
    active = (rows.completed_at.is_(None), rows.expires_at > now)
    existing = db.scalar(select(rows).join(
        LabelingJob, LabelingJob.id == rows.job_id,
    ).where(scope, *current_submission, rows.reviewer_id == reviewer_id, *active))
    if existing:
        return claim_view(db, existing)
    available = rows.completed_at.is_(None) & or_(rows.expires_at.is_(None), rows.expires_at <= now)
    query = select(rows).join(
        LabelingJob, LabelingJob.id == rows.job_id,
    ).where(scope, *current_submission, available)
    # Count leases as allocated reviews. The event lock serializes allocation
    # so simultaneous monitors see each other's claims before choosing a team.
    allocated = or_(rows.completed_at.is_not(None), rows.expires_at > now)
    submitted = func.count()
    reviewed = func.sum(case((allocated, 1), else_=0))
    teams = db.execute(select(rows.team_id, submitted, reviewed).join(
        LabelingJob, LabelingJob.id == rows.job_id,
    ).where(scope, *current_submission, LabelingJob.release_mode.in_(PRIZE_RELEASE_MODES))
        .group_by(rows.team_id)).all()
    total_submitted = sum(count for _, count, _ in teams)
    next_slot = sum(count for _, _, count in teams) + 1
    eligible = [(team_id, count, assigned) for team_id, count, assigned in teams if count > assigned]
    if eligible:
        # Compare deficits using integers to avoid floating point ties.
        team_id, _, _ = max(eligible, key=lambda team: (
            next_slot * team[1] - total_submitted * team[2], team[0],
        ))
        query = query.where(rows.team_id == team_id, LabelingJob.release_mode.in_(PRIZE_RELEASE_MODES))
    pivot = identifier()
    sample = db.scalar(
        query.where(rows.random_key >= pivot).order_by(rows.random_key).limit(1)
        .with_for_update(skip_locked=True, of=rows)
    )
    if sample is None:
        sample = db.scalar(
            query.order_by(rows.random_key).limit(1)
            .with_for_update(skip_locked=True, of=rows)
        )
    if sample is None:
        return None
    rubric = latest_rubric(db, event_id)
    sample.rubric_id = rubric.id if rubric else None
    sample.reviewer_id = reviewer_id
    sample.claim_token = identifier()
    sample.expires_at = now + LEASE_SECONDS
    db.flush()
    return claim_view(db, sample)


def owned(db, event_id, review_id, reviewer_id, token=None, *, allow_expired=False):
    sample = db.get(ReviewSample, review_id)
    if sample is None or sample.event_id != event_id:
        raise HTTPException(404, "Review sample not found.")
    job = db.get(LabelingJob, sample.job_id)
    if (job is None
            or (sample.validated_complete and (
                job.status not in {"on_review", "completed"}
                or job.annotation_snapshot_frames != job.frame_count))
            or (not sample.validated_complete and (
                sample.correction_round != job.correction_round
                or job.status not in {"on_review", "completed"}))):
        raise HTTPException(409, "This frame belongs to an incomplete or superseded submission.")
    if sample.reviewer_id != reviewer_id or (token is not None and sample.claim_token != str(token)):
        raise HTTPException(409, "Review is not owned by this claim.")
    if not allow_expired and sample.completed_at is None and (sample.expires_at or 0) <= epoch():
        raise HTTPException(409, "Review claim expired.")
    return sample


@router.post("/claim-next")
def claim_next(ctx: AuthContext = Depends(write_access), db: Session = Depends(get_db)):
    event = lock_event(db, ctx)
    review = claim(db, event.id, ctx.staff.id)
    db.commit()
    return {"review": review}


@router.post("/{review_id}/heartbeat")
def heartbeat(review_id: str, payload: Lease,
              ctx: AuthContext = Depends(write_access), db: Session = Depends(get_db)):
    event = lock_event(db, ctx)
    sample = owned(db, event.id, review_id, ctx.staff.id, payload.claim_token)
    if sample.completed_at is not None:
        raise HTTPException(409, "Review already completed.")
    sample.expires_at = epoch() + LEASE_SECONDS
    db.commit()
    return {"expires_at": sample.expires_at}


@router.post("/{review_id}/release")
def release(review_id: str, payload: Lease,
            ctx: AuthContext = Depends(write_access), db: Session = Depends(get_db)):
    event = lock_event(db, ctx)
    sample = owned(db, event.id, review_id, ctx.staff.id, payload.claim_token, allow_expired=True)
    if sample.completed_at is None:
        sample.expires_at = 0
    db.commit()
    return {"released": True}


@router.post("/{review_id}/complete")
def complete(review_id: str, payload: Completion,
             ctx: AuthContext = Depends(write_access), db: Session = Depends(get_db)):
    event = lock_event(db, ctx)
    sample = owned(db, event.id, review_id, ctx.staff.id, payload.claim_token)
    if sample.completed_at is not None:
        successor_id = sample.decision.get("next_review_id")
        successor = db.get(ReviewSample, successor_id) if successor_id else None
        if (successor is not None and successor.reviewer_id == ctx.staff.id
                and successor.claim_token == sample.decision.get("next_claim_token")
                and successor.completed_at is None and successor.expires_at > epoch()):
            return {"completed": True, "review": claim_view(db, successor)}
        return {"completed": True, "review": None}
    annotation = sample.snapshot["annotation"]
    objects = annotation.get("objects", annotation.get("labels", []))
    if sorted(item.object_index for item in payload.verdicts) != list(range(len(objects))):
        raise HTTPException(422, "Supply exactly one verdict for every annotation object.")
    classes = {item["name"]: item["shape"] for item in annotation_class_view()}
    if any(name not in classes for name in payload.missing_classes):
        raise HTTPException(422, "Missing items must use a known annotation class.")
    rubric = db.get(ReviewRubric, sample.rubric_id) if sample.rubric_id else None
    weights = resolved_weights(rubric.weights) if rubric else Weights().model_dump()
    correct = Counter()
    errors = Counter()
    for verdict in payload.verdicts:
        obj = objects[verdict.object_index]
        shape = obj.get("geometryType") or classes.get(obj.get("classTitle"))
        if shape not in {"rectangle", "point"}:
            raise HTTPException(422, "Unsupported object geometry.")
        if verdict.correct:
            if verdict.reason or verdict.corrected_class:
                raise HTTPException(422, "Correct objects cannot include correction details.")
            correct[shape] += 1
            if shape == "point":
                # Project class identity distinguishes visible and occluded.
                if str(obj.get("classTitle", "")).endswith("_occ"):
                    correct["occluded_point"] += 1
                else:
                    correct["visible_point"] += 1
        else:
            if verdict.reason == "wrong_class":
                if (classes.get(verdict.corrected_class) != shape
                        or verdict.corrected_class == obj.get("classTitle")):
                    raise HTTPException(422, "Choose a different class with the same geometry.")
            elif verdict.corrected_class or (shape == "point" and not verdict.reason):
                raise HTTPException(422, "Invalid or missing correction reason.")
            errors[verdict.reason or "incorrect_box"] += 1
    decision = {
        "verdicts": [item.model_dump() for item in payload.verdicts],
        "missing_classes": payload.missing_classes,
        "correct_keypoints": correct["point"],
        "correct_visible_keypoints": correct["visible_point"],
        "correct_occluded_keypoints": correct["occluded_point"],
        "correct_boxes": correct["rectangle"],
        "errors": dict(errors),
    }
    decision["frame_points"] = frame_points(decision, sample.snapshot)
    decision["weighted_score"] = score_verdict(decision, sample.snapshot, weights)
    sample.decision = decision
    sample.completed_at = epoch()
    db.flush()
    next_review = claim(db, event.id, ctx.staff.id) if payload.action == "next" else None
    if next_review:
        sample.decision = {**sample.decision, "next_review_id": next_review["review_id"],
                           "next_claim_token": next_review["claim_token"]}
    db.commit()
    return {"completed": True, "review": next_review}


@router.get("/status")
def status(ctx: AuthContext = Depends(read_access), db: Session = Depends(get_db)):
    now = epoch()
    event = current_event(db, ctx)
    state = case((ReviewSample.completed_at.is_not(None), "completed"),
                 (ReviewSample.expires_at > now, "active"), else_="pending").label("state")
    counts = Counter()
    teams = {}
    for team, name, key, count in db.execute(
        select(ReviewSample.team_id, CompetitionTeam.name, state, func.count())
        .join(CompetitionTeam, CompetitionTeam.id == ReviewSample.team_id)
        .join(LabelingJob, LabelingJob.id == ReviewSample.job_id)
        .where(ReviewSample.event_id == event.id,
               eligible_frame_review())
        .group_by(ReviewSample.team_id, CompetitionTeam.name, state)
    ):
        counts[key] += count
        teams.setdefault(team, {"team_id": team, "team_name": name,
                                "pending": 0, "active": 0, "completed": 0})[key] = count
    return {**{key: counts[key] for key in ("pending", "active", "completed")},
            "teams": list(teams.values())}


@router.get("/{review_id}/image")
def image(review_id: str, ctx: AuthContext = Depends(read_access), db: Session = Depends(get_db)):
    event = current_event(db, ctx)
    sample = owned(db, event.id, review_id, ctx.staff.id)
    asset = db.get(MediaAsset, sample.media_id)
    dataset = db.get(MediaDataset, asset.dataset_id)
    root = (Path(dataset.root_path) / "media").resolve()
    path = (root / asset.relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Local image missing.")
    return FileResponse(path, headers={"Cache-Control": "private, no-store"})
