"""Read-only reviewer contribution rankings, including historical saved reviews."""
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import Integer, case, distinct, func, literal, select, union_all
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import JobReview, MediaAsset, ReviewSample, StaffAccount, epoch
from .scoring_api import frame_points

router = APIRouter(prefix="/api/v1/rankings", tags=["reviewer rankings"])


def monitor_rankings(db: Session, event_id: str, period: str = "all") -> dict:
    now = epoch()
    start = int(datetime.fromtimestamp(now, ZoneInfo("Europe/Lisbon")).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).timestamp()) if period == "today" else None
    # Reviewer work remains credited even if a later correction supersedes the
    # submission or the staff account is deactivated. Team prize eligibility
    # is a different calculation and must not erase completed reviewer work.
    shared_scope = [
        ReviewSample.event_id == event_id, MediaAsset.event_id == event_id,
        ReviewSample.completed_at.is_not(None), ReviewSample.decision.is_not(None),
        ReviewSample.reviewer_id.is_not(None),
    ]
    saved_score = ReviewSample.decision["frame_points"].as_integer()
    shared = select(
        ReviewSample.reviewer_id.label("reviewer_id"), ReviewSample.media_id.label("media_id"),
        MediaAsset.kind.label("kind"), ReviewSample.completed_at.label("completed_at"),
        case((saved_score.between(0, 5), saved_score)).label("score"),
    ).join(MediaAsset, MediaAsset.id == ReviewSample.media_id).where(*shared_scope)
    legacy = select(
        JobReview.reviewer_id, JobReview.media_id, MediaAsset.kind, JobReview.reviewed_at,
        literal(None, Integer),  # Legacy reviews used an incompatible scoring rubric.
    ).join(MediaAsset, MediaAsset.id == JobReview.media_id).where(
        JobReview.event_id == event_id, MediaAsset.event_id == event_id,
    )
    reviews = union_all(shared, legacy).subquery()
    scope = [reviews.c.completed_at >= start] if start is not None else []
    images = func.count(distinct(reviews.c.media_id))
    query = select(
        StaffAccount.id, StaffAccount.name, images.label("images"),
        func.count().label("completed_reviews"),
        func.coalesce(func.sum(reviews.c.score), 0).label("score_total"),
        func.count(reviews.c.score).label("scored_reviews"),
        func.count(distinct(case((reviews.c.kind == "video_frame", reviews.c.media_id)))).label("video_frames"),
        func.count(distinct(case((reviews.c.kind == "image", reviews.c.media_id)))).label("still_images"),
        func.max(reviews.c.completed_at).label("last_reviewed_at"),
    ).select_from(reviews).join(StaffAccount, StaffAccount.id == reviews.c.reviewer_id).where(*scope).group_by(
        StaffAccount.id, StaffAccount.name,
    )
    items = [{"reviewer_id": row.id, "name": row.name, "reviewed_images": row.images,
              "completed_reviews": row.completed_reviews, "video_frames": row.video_frames,
              "still_images": row.still_images, "last_reviewed_at": row.last_reviewed_at,
              "scored_reviews": row.scored_reviews, "_score_total": row.score_total}
             for row in db.execute(query)]
    # Older completed reviews predate the saved frame_points field. Recover their
    # original 0–5 grade from immutable verdicts without rewriting review history.
    historical_scope = [*shared_scope, saved_score.is_(None)]
    if start is not None:
        historical_scope.append(ReviewSample.completed_at >= start)
    historical = select(ReviewSample.reviewer_id, ReviewSample.decision, ReviewSample.snapshot).join(
        MediaAsset, MediaAsset.id == ReviewSample.media_id,
    ).where(*historical_scope)
    by_reviewer = {row["reviewer_id"]: row for row in items}
    for reviewer_id, decision, snapshot in db.execute(historical):
        verdicts = decision.get("verdicts") if isinstance(decision, dict) else None
        annotation = snapshot.get("annotation") if isinstance(snapshot, dict) else None
        objects = annotation.get("objects", annotation.get("labels")) if isinstance(annotation, dict) else None
        if (reviewer_id in by_reviewer and isinstance(verdicts, list) and isinstance(objects, list)
                and all(isinstance(v, dict) for v in verdicts)
                and all(isinstance(obj, dict) for obj in objects)):
            row = by_reviewer[reviewer_id]
            row["_score_total"] += frame_points(decision, snapshot)
            row["scored_reviews"] += 1
    for row in items:
        total = row.pop("_score_total")
        row["average_review_score"] = total / row["scored_reviews"] if row["scored_reviews"] else None
    items.sort(key=lambda row: (-row["reviewed_images"], row["name"].casefold(), row["reviewer_id"]))
    previous = None
    rank = 0
    for position, row in enumerate(items, 1):
        if row["reviewed_images"] != previous:
            rank = position
        row["rank"] = rank
        previous = row["reviewed_images"]
    total_images = db.scalar(select(func.count(distinct(reviews.c.media_id))).where(*scope)) or 0
    return {"items": items, "period": period, "period_start": start, "generated_at": now,
            "reviewer_count": len(items), "reviewed_images": total_images,
            "completed_reviews": sum(row["completed_reviews"] for row in items),
            "counting_policy": "One image per reviewer, including historical completed reviews. "
                               "Repeat reviews do not increase the image count. Equal counts share a rank."}


@router.get("/monitors")
def rankings(period: Literal["all", "today"] = "all",
             ctx: AuthContext = Depends(requires("organiser", "monitor")),
             db: Session = Depends(get_db)):
    return monitor_rankings(db, current_event(db, ctx).id, period)
