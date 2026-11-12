"""Versioned rubric and rankings from completed submitted-frame reviews."""
from collections import Counter

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import AuditEvent, CompetitionTeam, ReviewRubric, ReviewSample

router = APIRouter(prefix="/api/v1", tags=["review scoring"])


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keypoint: int = Field(default=1, ge=0, le=100)
    box: int = Field(default=1, ge=0, le=100)
    wrong_class: int = Field(default=0, ge=0, le=100)
    wrong_placement: int = Field(default=0, ge=0, le=100)
    sabotage: int = Field(default=0, ge=0, le=100)


def latest_rubric(db: Session, event_id: str):
    return db.scalar(select(ReviewRubric).where(ReviewRubric.event_id == event_id)
                     .order_by(ReviewRubric.version.desc()).limit(1))


@router.get("/scoring/rubric")
def get_rubric(ctx: AuthContext = Depends(requires("organiser", "monitor")),
               db: Session = Depends(get_db)):
    rubric = latest_rubric(db, current_event(db, ctx).id)
    return {"version": rubric.version if rubric else 0,
            "weights": rubric.weights if rubric else Weights().model_dump()}


@router.put("/scoring/rubric")
def set_rubric(weights: Weights,
               ctx: AuthContext = Depends(requires("organiser", mutation=True)),
               db: Session = Depends(get_db)):
    event = current_event(db, ctx, writable=True)
    previous = latest_rubric(db, event.id)
    rubric = ReviewRubric(event_id=event.id, version=previous.version + 1 if previous else 1,
                          weights=weights.model_dump())
    db.add(rubric)
    db.flush()
    db.add(AuditEvent(event_id=event.id, staff_id=ctx.staff.id, action="scoring.rubric.update",
                      target_type="review_rubric", target_id=rubric.id,
                      details={"version": rubric.version}))
    db.commit()
    return {"version": rubric.version, "weights": rubric.weights}


@router.get("/rankings/teams")
def rankings(ctx: AuthContext = Depends(requires("organiser", "monitor")),
             db: Session = Depends(get_db)):
    event = current_event(db, ctx)
    teams = {
        team_id: {"team_id": team_id, "team_name": name, "correct_keypoints": 0,
                  "correct_boxes": 0, "weighted_score": 0, "reviewed_frames": 0,
                  "available_frames": 0, "missing_objects": 0, "errors": Counter()}
        for team_id, name in db.execute(select(CompetitionTeam.id, CompetitionTeam.name)
                                        .where(CompetitionTeam.event_id == event.id))
    }
    for team_id, decision in db.execute(select(ReviewSample.team_id, ReviewSample.decision)
                                        .where(ReviewSample.event_id == event.id,
                                               ReviewSample.correction_round == 0)):
        row = teams[team_id]
        row["available_frames"] += 1
        if decision is not None:
            row["reviewed_frames"] += 1
            for key in ("correct_keypoints", "correct_boxes", "weighted_score"):
                row[key] += decision[key]
            row["errors"].update(decision["errors"])
            row["missing_objects"] += len(decision["missing_classes"])
    for row in teams.values():
        row["correct_objects"] = row["correct_keypoints"] + row["correct_boxes"]
    items = sorted(teams.values(), key=lambda row: (
        -row["correct_objects"], -row["weighted_score"], row["team_id"],
    ))
    for position, row in enumerate(items, start=1):
        row["rank"] = position
    return {"items": items}
