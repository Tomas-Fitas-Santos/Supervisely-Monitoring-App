"""Read-only cross-team monitoring statistics and provisional leaderboard.

All event monitors can inspect team aggregates. Annotation review data and
write access are intentionally handled by the team-assigned review routes.
Ranking is based on actually *scored* keypoints, not empty/unreviewed frames.
"""
import json
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import (
    CompetitionTeam,
    JobReview,
    LabelingJob,
    MediaDataset,
    MonitorAssignment,
)

router = APIRouter(prefix="/api/v1/review", tags=["team analytics"])


def team_statistics(db: Session, event_id: str) -> list[dict]:
    teams = list(db.scalars(select(CompetitionTeam).where(
        CompetitionTeam.event_id == event_id, CompetitionTeam.active.is_(True)
    ).order_by(CompetitionTeam.name, CompetitionTeam.id)))
    jobs = list(db.scalars(select(LabelingJob).where(
        LabelingJob.event_id == event_id
    )))
    reviews = list(db.scalars(select(JobReview).where(
        JobReview.event_id == event_id
    )))
    monitors = {a.team_id: a.monitor_person_id for a in db.scalars(
        select(MonitorAssignment).where(
            MonitorAssignment.event_id == event_id,
            MonitorAssignment.active.is_(True),
        )
    )}
    jobs_by_team: dict[str, list[LabelingJob]] = defaultdict(list)
    job_team = {job.id: job.team_id for job in jobs}
    reviews_by_team: dict[str, list[JobReview]] = defaultdict(list)
    for job in jobs:
        jobs_by_team[job.team_id].append(job)
    for review in reviews:
        team_id = job_team.get(review.job_id)
        if team_id:
            reviews_by_team[team_id].append(review)
    datasets = {ds.id: ds for ds in db.scalars(select(MediaDataset).where(
        MediaDataset.event_id == event_id
    ))}

    rows = []
    for team in teams:
        assigned = jobs_by_team[team.id]
        evaluated = reviews_by_team[team.id]
        accepted = [job for job in assigned if (
            job.status == "completed" and job.review_status == "accepted"
        )]
        submitted = [job for job in assigned if job.submitted_at is not None]
        timed = [job for job in submitted if (
            job.started_at is not None and job.submitted_at >= job.started_at
            and job.frame_count > 0
        )]
        duration = sum(job.submitted_at - job.started_at for job in timed)
        timed_frames = sum(job.frame_count for job in timed)
        correct = sum(row.correct_keypoints for row in evaluated)
        missing = sum(row.missing_keypoints for row in evaluated)
        incorrect = sum(row.incorrect_keypoints for row in evaluated)
        evaluated_points = correct + missing + incorrect
        total_frames = sum(job.frame_count for job in assigned)
        # Submitted frames are known from the job manifest; label-bearing
        # frames would require reviewing the actual annotation JSON.
        frames_submitted = sum(job.frame_count for job in submitted)
        synced = 0
        annotated_frames = 0
        has_annotation_counts = False
        for job in submitted:
            dataset = datasets.get(job.dataset_id)
            if dataset is None:
                continue
            manifest = (Path(dataset.root_path) / "annotations" / job.id /
                        ("round-" + str(job.correction_round)) / "manifest.json")
            if not manifest.is_file():
                continue
            synced += job.frame_count
            try:
                details = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            count = details.get("annotated_frames")
            if type(count) is int and 0 <= count <= job.frame_count:
                annotated_frames += count
                has_annotation_counts = True
        rows.append({
            "team_id": team.id, "team_name": team.name,
            "monitor_person_id": monitors.get(team.id),
            "jobs_total": len(assigned),
            "jobs_completed": len(accepted),
            "jobs_submitted": len(submitted),
            "jobs_awaiting_review": sum(j.status == "on_review" for j in assigned),
            "frames_assigned": total_frames,
            "frames_submitted": frames_submitted,
            "frames_synced": synced,
            "frames_annotated": annotated_frames if has_annotation_counts else None,
            "frames_reviewed": len({(r.job_id, r.media_id) for r in evaluated}),
            "correct_keypoints": correct, "missing_keypoints": missing,
            "incorrect_keypoints": incorrect,
            "evaluated_keypoints": evaluated_points,
            "keypoint_accuracy_pct": (
                round(100 * correct / evaluated_points, 1)
                if evaluated_points else None
            ),
            "average_seconds_per_frame": (
                round(duration / timed_frames, 2) if timed_frames else None
            ),
            "timed_frames": timed_frames,
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
        "time_definition": "Seconds from job start to submission / frames in timed jobs",
        "frames_definition": "Annotated frames contain at least one downloaded Supervisely label; historical jobs may lack this count",
        "winner_policy": "Not final until all relevant submissions are scored and accepted",
    }
