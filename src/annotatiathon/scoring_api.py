"""Versioned rubric and rankings from completed submitted-frame reviews."""
from collections import Counter

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import AuditEvent, CompetitionTeam, LabelingJob, ReviewRubric, ReviewSample

router = APIRouter(prefix="/api/v1", tags=["review scoring"])


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visible_keypoint: int = Field(default=1, ge=0, le=100)
    occluded_keypoint: int = Field(default=3, ge=0, le=100)
    box: int = Field(default=1, ge=0, le=100)
    wrong_class: int = Field(default=1, ge=0, le=100)
    wrong_placement: int = Field(default=2, ge=0, le=100)
    sabotage: int = Field(default=3, ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_visible_keypoint_name(cls, value):
        if isinstance(value, dict) and "keypoint" in value:
            value = dict(value)
            value.setdefault("visible_keypoint", value.pop("keypoint"))
        return value


def resolved_weights(stored: dict | None) -> dict[str, int]:
    """Read old versioned rubrics without changing or deleting historical data.

    Legacy 'keypoint' is the visible-keypoint weight. Occluded keypoints
    receive their new default unless the organiser explicitly publishes it.
    """
    values = dict(stored or {})
    if "visible_keypoint" not in values and "keypoint" in values:
        values["visible_keypoint"] = values["keypoint"]
    values.pop("keypoint", None)
    return Weights(**values).model_dump()


def correct_point_types(decision: dict, snapshot: dict) -> tuple[int, int]:
    """Recover visible/occluded counts for both new and historical reviews."""
    if "correct_visible_keypoints" in decision and "correct_occluded_keypoints" in decision:
        return (int(decision["correct_visible_keypoints"]),
                int(decision["correct_occluded_keypoints"]))
    # Historical decisions did not store point subtypes. Recover from the
    # original immutable per-object verdicts when available.
    annotation = snapshot.get("annotation") or {}
    objects = annotation.get("objects", annotation.get("labels", []))
    verdicts = decision.get("verdicts")
    if isinstance(verdicts, list) and isinstance(objects, list) and len(verdicts) == len(objects):
        visible = 0
        occluded = 0
        for verdict in verdicts:
            if not isinstance(verdict, dict) or not verdict.get("correct"):
                continue
            index = verdict.get("object_index")
            if not isinstance(index, int) or not 0 <= index < len(objects):
                break
            obj = objects[index]
            if not isinstance(obj, dict):
                break
            title = obj.get("classTitle", "")
            if obj.get("geometryType") == "point" or title in {
                "left_eye", "left_eye_occ", "right_eye", "right_eye_occ",
                "beak", "beak_occ", "crown", "crown_occ",
            }:
                if str(title).endswith("_occ"):
                    occluded += 1
                else:
                    visible += 1
        else:
            if visible + occluded == int(decision.get("correct_keypoints", 0)):
                return visible, occluded
    return int(decision.get("correct_keypoints", 0)), 0


def score_verdict(decision: dict, snapshot: dict, weights: dict[str, int]) -> int:
    """Apply the event's current rubric to immutable per-object verdicts."""
    visible, occluded = correct_point_types(decision, snapshot)
    return (
        visible * weights["visible_keypoint"]
        + occluded * weights["occluded_keypoint"]
        + int(decision.get("correct_boxes", 0)) * weights["box"]
        - sum(int(count) * weights.get(reason, 0)
              for reason, count in decision.get("errors", {}).items())
    )


def latest_rubric(db: Session, event_id: str):
    return db.scalar(select(ReviewRubric).where(ReviewRubric.event_id == event_id)
                     .order_by(ReviewRubric.version.desc()).limit(1))


@router.get("/scoring/rubric")
def get_rubric(ctx: AuthContext = Depends(requires("organiser", "monitor")),
               db: Session = Depends(get_db)):
    rubric = latest_rubric(db, current_event(db, ctx).id)
    return {"version": rubric.version if rubric else 0,
            "weights": resolved_weights(rubric.weights) if rubric else Weights().model_dump()}


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
    current_rubric = latest_rubric(db, event.id)
    current_weights = resolved_weights(current_rubric.weights) if current_rubric else Weights().model_dump()
    teams = {
        team_id: {"team_id": team_id, "team_name": name, "correct_keypoints": 0,
                  "correct_visible_keypoints": 0, "correct_occluded_keypoints": 0,
                  "correct_boxes": 0, "weighted_score": 0, "reviewed_frames": 0,
                  "available_frames": 0, "missing_objects": 0, "errors": Counter()}
        for team_id, name in db.execute(select(CompetitionTeam.id, CompetitionTeam.name)
                                        .where(CompetitionTeam.event_id == event.id))
    }
    for team_id, decision, snapshot in db.execute(select(
        ReviewSample.team_id, ReviewSample.decision, ReviewSample.snapshot,
    ).join(LabelingJob, LabelingJob.id == ReviewSample.job_id)
     .where(ReviewSample.event_id == event.id,
            ReviewSample.correction_round == LabelingJob.correction_round,
            LabelingJob.status.in_(("on_review", "completed")))):
        row = teams[team_id]
        row["available_frames"] += 1
        if decision is not None:
            row["reviewed_frames"] += 1
            for key in ("correct_keypoints", "correct_boxes"):
                row[key] += decision[key]
            row["errors"].update(decision["errors"])
            row["missing_objects"] += len(decision["missing_classes"])
            visible, occluded = correct_point_types(decision, snapshot)
            row["correct_visible_keypoints"] += visible
            row["correct_occluded_keypoints"] += occluded
            # Apply the latest published rubric uniformly to every team.
            # Historical decisions and their original rubric IDs are immutable.
            row["weighted_score"] += score_verdict(decision, snapshot, current_weights)
    for row in teams.values():
        row["correct_objects"] = row["correct_keypoints"] + row["correct_boxes"]
    items = sorted(teams.values(), key=lambda row: (
        -row["weighted_score"], -row["correct_objects"], row["team_id"],
    ))
    for position, row in enumerate(items, start=1):
        row["rank"] = position
    return {
        "items": items,
        "rubric_version": current_rubric.version if current_rubric else 0,
        "weighted_scoring_policy": "Primary competition rank is total weighted correct annotation points minus configured incorrect-annotation penalties; the current rubric applies uniformly to all completed verdicts",
        "submission_policy": "Only the latest submitted correction round per job counts after review",
    }
