"""Read-only cross-team monitoring statistics and provisional leaderboard.

All event monitors can inspect team aggregates. Annotation review data and
write access are intentionally handled by the team-assigned review routes.
Ranking is based on actually *scored* keypoints, not empty/unreviewed frames.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import (
    CompetitionTeam,
    JobReview,
    LabelingJob,
    MonitorAssignment,
)

router = APIRouter(prefix="/api/v1/review", tags=["team analytics"])


def team_statistics(db: Session, event_id: str) -> list[dict]:
    teams = list(db.scalars(select(CompetitionTeam).where(
        CompetitionTeam.event_id == event_id, CompetitionTeam.active.is_(True)
    ).order_by(CompetitionTeam.name, CompetitionTeam.id)))
    submitted = LabelingJob.submitted_at.is_not(None)
    accepted = and_(LabelingJob.status == "completed", LabelingJob.review_status == "accepted")
    timed = and_(submitted, LabelingJob.started_at.is_not(None),
                 LabelingJob.submitted_at >= LabelingJob.started_at, LabelingJob.frame_count > 0)
    snapshot = and_(submitted, LabelingJob.annotation_snapshot_round == LabelingJob.correction_round)
    jobs = {row["team_id"]: row for row in db.execute(select(
        LabelingJob.team_id,
        func.count().label("jobs_total"),
        func.sum(case((accepted, 1), else_=0)).label("jobs_completed"),
        func.sum(case((submitted, 1), else_=0)).label("jobs_submitted"),
        func.sum(case((LabelingJob.status == "on_review", 1), else_=0)).label("jobs_awaiting_review"),
        func.sum(LabelingJob.frame_count).label("frames_assigned"),
        func.sum(case((submitted, LabelingJob.frame_count), else_=0)).label("frames_submitted"),
        func.sum(case((timed, LabelingJob.submitted_at - LabelingJob.started_at), else_=0)).label("duration"),
        func.sum(case((timed, LabelingJob.frame_count), else_=0)).label("timed_frames"),
        func.coalesce(func.sum(case((snapshot, LabelingJob.annotation_snapshot_frames), else_=0)), 0).label("frames_synced"),
        func.coalesce(func.sum(case((snapshot, LabelingJob.annotation_snapshot_annotated_frames), else_=0)), 0).label("annotated"),
        func.count(case((snapshot, LabelingJob.annotation_snapshot_annotated_frames))).label("known_counts"),
    ).where(LabelingJob.event_id == event_id).group_by(LabelingJob.team_id)).mappings()}
    frames = select(
        LabelingJob.team_id, JobReview.job_id, JobReview.media_id,
        func.sum(JobReview.correct_keypoints).label("correct"),
        func.sum(JobReview.missing_keypoints).label("missing"),
        func.sum(JobReview.incorrect_keypoints).label("incorrect"),
    ).join(LabelingJob, LabelingJob.id == JobReview.job_id).where(
        LabelingJob.event_id == event_id, JobReview.event_id == event_id,
    ).group_by(LabelingJob.team_id, JobReview.job_id, JobReview.media_id).subquery()
    scores = {row["team_id"]: row for row in db.execute(select(
        frames.c.team_id, func.count().label("reviewed"),
        func.sum(frames.c.correct).label("correct"), func.sum(frames.c.missing).label("missing"),
        func.sum(frames.c.incorrect).label("incorrect"),
    ).group_by(frames.c.team_id)).mappings()}
    monitors = dict(db.execute(select(MonitorAssignment.team_id, MonitorAssignment.monitor_person_id).where(
        MonitorAssignment.event_id == event_id, MonitorAssignment.active.is_(True),
    )).all())
    rows = []
    for team in teams:
        job = jobs.get(team.id, {})
        score = scores.get(team.id, {})
        correct, missing, incorrect = (score.get(key, 0) for key in ("correct", "missing", "incorrect"))
        evaluated = correct + missing + incorrect
        timed_frames = job.get("timed_frames", 0)
        rows.append({
            "team_id": team.id, "team_name": team.name,
            "monitor_person_id": monitors.get(team.id),
            **{key: job.get(key, 0) for key in (
                "jobs_total", "jobs_completed", "jobs_submitted", "jobs_awaiting_review",
                "frames_assigned", "frames_submitted", "frames_synced", "timed_frames")},
            "frames_annotated": job.get("annotated", 0) if job.get("known_counts", 0) else None,
            "frames_reviewed": score.get("reviewed", 0),
            "correct_keypoints": correct, "missing_keypoints": missing,
            "incorrect_keypoints": incorrect, "evaluated_keypoints": evaluated,
            "keypoint_accuracy_pct": round(100 * correct / evaluated, 1) if evaluated else None,
            "average_seconds_per_frame": round(job.get("duration", 0) / timed_frames, 2) if timed_frames else None,
        })
    # Provisional order: scored accuracy, then accepted work and reviewed
    # frames. Teams with no evaluated keypoints stay unranked at the bottom.
    rows.sort(key=lambda r: (
        r["keypoint_accuracy_pct"] is None,
        -(r["keypoint_accuracy_pct"] or 0),
        -r["jobs_completed"],
        -r["frames_reviewed"],
        r["team_name"].casefold(), r["team_id"],
    ))
    place = 0
    previous_score = None
    for i, row in enumerate(rows, start=1):
        score = row["keypoint_accuracy_pct"]
        if score is None:
            row["rank"] = None
        else:
            # Same ranked metrics share a rank; team name is just tie ordering.
            key = (score, row["jobs_completed"], row["frames_reviewed"])
            if key != previous_score:
                place = i
                previous_score = key
            row["rank"] = place
    return rows


@router.get("/team-metrics")
def team_metrics(
    _ctx: AuthContext = Depends(requires("organiser", "monitor", "observer")),
    db: Session = Depends(get_db),
):
    event = current_event(db, _ctx)
    return {
        "teams": team_statistics(db, event.id),
        "ranking_status": "provisional",
        "accuracy_definition": "Correct / (correct + missing + incorrect) reviewed keypoints",
        "time_definition": "Seconds from Nightjar publishing the job to the detected Supervisely submission / frames; includes any waiting time",
        "frames_definition": "Annotated frames contain at least one downloaded Supervisely label; historical jobs may lack this count",
        "winner_policy": "Not final until all relevant submissions are scored and accepted",
    }
