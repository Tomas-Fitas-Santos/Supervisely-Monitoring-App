"""Read-only event report/export and controlled final closeout with preserved records."""
import csv
import io
import json
from collections import Counter
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import (
    AuditEvent,
    CompetitionTeam,
    Event,
    EventCloseout,
    JobReview,
    LabelingJob,
    MediaAsset,
    MediaDataset,
    Person,
    ReviewSample,
    TicketLedgerEntry,
    epoch,
)
from .scoring_api import correct_point_types

router = APIRouter(prefix="/api/v1/closeout", tags=["event reports"])
VIEWERS = ("organiser", "monitor", "observer")


def complete_submission(job: LabelingJob) -> bool:
    """Official single-annotator jobs complete when all JSON frames are captured.

    Legacy jobs still require the historical whole-job acceptance decision.
    """
    if job.status == "completed":
        return True
    return (
        job.assignee_person_id is not None
        and job.release_mode in {
            "shared_primary", "shared_repeat", "manual_released", "manual_hold",
        }
        and job.status == "on_review"
        and job.annotation_snapshot_round == job.correction_round
        and job.annotation_snapshot_frames == job.frame_count
    )


def frame_review_fairness(db: Session, event_id: str) -> dict:
    """Report random-pool review coverage, not an equal-team scoring quota.

    Keep this historical function and report key for older API consumers.
    Only current, complete, eligible native submission rounds are counted.
    """
    teams = list(db.execute(select(CompetitionTeam.id, CompetitionTeam.name).where(
        CompetitionTeam.event_id == event_id,
        CompetitionTeam.active.is_(True),
    ).order_by(CompetitionTeam.name)))
    available = dict(db.execute(
        select(ReviewSample.team_id, func.count(ReviewSample.id))
        .join(LabelingJob, LabelingJob.id == ReviewSample.job_id)
        .where(
            ReviewSample.event_id == event_id,
            ReviewSample.correction_round == LabelingJob.correction_round,
            LabelingJob.status.in_(("on_review", "completed")),
        ).group_by(ReviewSample.team_id)
    ).all())
    reviewed = dict(db.execute(
        select(ReviewSample.team_id, func.count(ReviewSample.id))
        .join(LabelingJob, LabelingJob.id == ReviewSample.job_id)
        .where(
            ReviewSample.event_id == event_id,
            ReviewSample.correction_round == LabelingJob.correction_round,
            LabelingJob.status.in_(("on_review", "completed")),
            ReviewSample.completed_at.is_not(None),
            ReviewSample.decision.is_not(None),
        ).group_by(ReviewSample.team_id)
    ).all())
    per_team = [
        {"team_id": team_id, "team_name": name,
         "available_frames": int(available.get(team_id, 0)),
         "reviewed_frames": int(reviewed.get(team_id, 0))}
        for team_id, name in teams
    ]
    total_reviewed = sum(row["reviewed_frames"] for row in per_team)
    return {
        "teams": per_team,
        "total_available_frames": sum(row["available_frames"] for row in per_team),
        "total_reviewed_frames": total_reviewed,
        "ready_for_winner": total_reviewed > 0,
        "policy": (
            "Random review from all available submitted frames; no equal-team quota. "
            "Only verified correct keypoints and bounding boxes count toward ranking."
        ),
    }


def closeout_readiness(db: Session, event_id: str, fairness: dict) -> dict:
    """The API and organiser UI use one definitive pre-closeout gate."""
    jobs = list(db.scalars(select(LabelingJob).where(
        LabelingJob.event_id == event_id, LabelingJob.status != "cancelled",
    )))
    frame_counts = dict(db.execute(
        select(ReviewSample.job_id, func.count(ReviewSample.id))
        .join(LabelingJob, LabelingJob.id == ReviewSample.job_id)
        .where(
            ReviewSample.event_id == event_id,
            ReviewSample.correction_round == LabelingJob.correction_round,
        ).group_by(ReviewSample.job_id)
    ).all())
    blocking = []
    unfinished = 0
    official_modes = {"shared_primary", "shared_repeat", "manual_released", "manual_hold"}
    official_frame_jobs = any(
        job.assignee_person_id is not None and job.release_mode in official_modes
        for job in jobs
    )
    for job in jobs:
        if job.assignee_person_id is not None and job.release_mode in official_modes:
            if job.status in {"on_review", "completed"}:
                # A native submission already marked complete must be copied
                # back as annotation JSON before the official result is frozen.
                if (job.annotation_snapshot_round != job.correction_round
                        or frame_counts.get(job.id, 0) != job.frame_count):
                    blocking.append(job.id)
            else:
                # Queued, in-progress, held and automatically returned
                # incomplete jobs are not scored. They cannot block event end.
                unfinished += 1
        elif job.status != "completed" or job.review_status != "accepted":
            blocking.append(job.id)
    return {
        "ready": not blocking and (
            not official_frame_jobs or fairness["ready_for_winner"]
        ),
        "blocking_jobs": len(blocking),
        "unfinished_unscored_jobs": unfinished,
        "verified_reviews_available": fairness["ready_for_winner"],
        "official_frame_review_required": official_frame_jobs,
        "message": (
            f"{len(blocking)} submitted/legacy jobs still require synced annotation JSON or acceptance."
            if blocking else
            "Cannot finalize: no completed annotation reviews exist yet."
            if official_frame_jobs and not fairness["ready_for_winner"] else
            f"Finalization available. {unfinished} unfinished jobs will remain unscored; "
            "the winner uses total verified weighted annotation points."
        ),
    }


def event_report(db: Session, event_id: str) -> dict:
    event = db.get(Event, event_id)
    people = db.scalars(select(Person).where(
        Person.event_id == event_id, Person.active.is_(True),
    )).all()
    jobs = db.scalars(select(LabelingJob).where(
        LabelingJob.event_id == event_id,
    )).all()
    reviewed = db.scalars(select(JobReview).where(
        JobReview.event_id == event_id
    )).all()
    datasets = db.scalars(select(MediaDataset).where(
        MediaDataset.event_id == event_id
    )).all()
    team_count = db.scalar(select(func.count(CompetitionTeam.id)).where(
        CompetitionTeam.event_id == event_id, CompetitionTeam.active.is_(True),
    )) or 0
    category_counts = Counter()
    for name, count in db.execute(select(
        MediaAsset.morphology, func.count(MediaAsset.id),
    ).where(MediaAsset.event_id == event_id).group_by(MediaAsset.morphology)):
        category_counts[name] = int(count)
    ledger_totals: Counter[str] = Counter()
    for kind, total in db.execute(select(
        TicketLedgerEntry.kind, func.sum(TicketLedgerEntry.quantity),
    ).where(TicketLedgerEntry.event_id == event_id).group_by(TicketLedgerEntry.kind)):
        ledger_totals[kind] = int(total)
    statuses = Counter(j.status for j in jobs)
    complete = sum(complete_submission(job) for job in jobs)
    fairness = frame_review_fairness(db, event_id)
    return {
        "event_id": event_id, "event_name": event.name, "event_status": event.status,
        "people": {
            "registered": len(people),
            "participants": sum(p.kind == "participant" for p in people),
            "monitors": sum(p.kind == "monitor" for p in people),
            "checked_in": sum(p.check_in_at is not None for p in people),
        },
        "teams": team_count,
        "datasets": {
            "count": len(datasets),
            "images": sum(d.image_count for d in datasets),
            "video_frames": sum(d.frame_count for d in datasets),
            "videos": sum(d.video_count for d in datasets),
            "morphology_counts": dict(category_counts),
        },
        "labeling": {
            "jobs": len(jobs), "status_counts": dict(statuses),
            "frames_assigned": sum(j.frame_count for j in jobs),
            "jobs_accepted": complete,
            "jobs_needing_action": sum(
                not complete_submission(job) and job.status != "cancelled"
                for job in jobs
            ),
        },
        "reviews": {
            "frames_reviewed": len(reviewed),
            "good": sum(r.verdict == "good" for r in reviewed),
            "bad": sum(r.verdict == "bad" for r in reviewed),
            "incomplete": sum(r.verdict == "incomplete" for r in reviewed),
            "keypoint_score": sum(
                r.correct_keypoints-r.missing_keypoints for r in reviewed
            ),
            "temporal_consistency_issues": sum(
                r.temporal_consistency is False for r in reviewed
            ),
        },
        "review_fairness": fairness,
        "closeout_readiness": closeout_readiness(db, event_id, fairness),
        "ticket_totals": dict(ledger_totals),
        "generated_at": epoch(),
    }


@router.get("/report")
def report(
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    return event_report(db, event.id)


class CloseoutConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str


@router.post("/finalize")
def finalize_event(
    payload: CloseoutConfirmation,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    if payload.confirmation != "CLOSE " + event.name:
        raise HTTPException(status_code=422, detail="Type the exact event closeout phrase.")
    existing = db.scalar(select(EventCloseout).where(
        EventCloseout.event_id == event.id
    ))
    if existing:
        raise HTTPException(status_code=409, detail="This event is already closed.")
    report_data = event_report(db, event.id)
    readiness = report_data["closeout_readiness"]
    if not readiness["ready"]:
        raise HTTPException(status_code=409, detail=readiness["message"])
    item = EventCloseout(
        event_id=event.id, snapshot_json=report_data, closed_by=ctx.staff.id,
    )
    db.add(item)
    event.status = "archived"
    event.archived_at = epoch()
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="event.closeout", target_type="event", target_id=event.id,
        details={
            "jobs": report_data["labeling"]["jobs"],
            "frames_reviewed": sum(
                item["reviewed_frames"]
                for item in report_data["review_fairness"]["teams"]
            ),
            "review_fairness": report_data["review_fairness"],
            "closeout_id": item.id,
        },
    ))
    db.commit()
    return {
        "event_id": event.id, "status": "archived",
        "closeout_id": item.id, "report": report_data,
    }


def cell(value) -> str:
    text = "" if value is None else str(value)
    # Avoid CSV spreadsheet formula injection when attendee data is opened in Excel.
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def export_rows(db: Session, event_id: str, section: str) -> tuple[list[str], list[list]]:
    if section == "people":
        headers = ["id", "name", "email", "role", "team_id", "checked_in_at",
                   "table_number", "membership_status", "supervisely_login"]
        teams = {t.id: t.table_number for t in db.scalars(select(CompetitionTeam).where(
            CompetitionTeam.event_id == event_id
        ))}
        rows = [
            [p.id, p.name, p.email, p.kind, p.team_id, p.check_in_at,
             teams.get(p.team_id) if p.team_id else p.table_number,
             p.membership_status, p.supervisely_login]
            for p in db.scalars(select(Person).where(Person.event_id == event_id))
        ]
    elif section == "jobs":
        headers = ["id", "plan_id", "team_id", "sequence", "status",
                   "review_status", "frame_count", "video_count",
                   "started_at", "submitted_at", "completed_at"]
        rows = [
            [j.id, j.plan_id, j.team_id, j.sequence, j.status,
             j.review_status, j.frame_count, j.video_count,
             j.started_at, j.submitted_at, j.completed_at]
            for j in db.scalars(select(LabelingJob).where(
                LabelingJob.event_id == event_id
            ))
        ]
    elif section == "reviews":
        headers = ["job_id", "media_id", "verdict", "correct", "missing",
                   "incorrect", "visible", "occluded", "temporal_consistency",
                   "notes", "reviewer_id", "reviewed_at"]
        rows = [
            [r.job_id, r.media_id, r.verdict, r.correct_keypoints,
             r.missing_keypoints, r.incorrect_keypoints,
             r.visible_keypoints, r.occluded_keypoints,
             r.temporal_consistency, r.notes, r.reviewer_id, r.reviewed_at]
            for r in db.scalars(select(JobReview).where(JobReview.event_id == event_id))
        ]
    elif section == "frame_reviews":
        headers = [
            "job_id", "team_id", "media_id", "correction_round",
            "latest_round", "reviewer_id", "reviewed_at",
            "correct_keypoints", "correct_visible_keypoints",
            "correct_occluded_keypoints", "correct_boxes", "weighted_score_at_review",
            "incorrect_reasons_json", "missing_classes_json",
            "verdicts_json", "source_snapshot_sha256",
        ]
        samples = db.scalars(
            select(ReviewSample).where(ReviewSample.event_id == event_id)
            .order_by(ReviewSample.job_id, ReviewSample.correction_round,
                      ReviewSample.media_id)
        ).all()
        rounds = dict(db.execute(
            select(LabelingJob.id, LabelingJob.correction_round)
            .where(LabelingJob.event_id == event_id)
        ).all())
        rows = []
        for sample in samples:
            decision = sample.decision if sample.completed_at is not None else None
            decision = decision if isinstance(decision, dict) else {}
            visible, occluded = correct_point_types(decision, sample.snapshot)
            rows.append([
                sample.job_id, sample.team_id, sample.media_id,
                sample.correction_round,
                sample.correction_round == rounds.get(sample.job_id),
                sample.reviewer_id if sample.completed_at else None,
                sample.completed_at,
                decision.get("correct_keypoints"),
                visible if decision else None, occluded if decision else None,
                decision.get("correct_boxes"), decision.get("weighted_score"),
                json.dumps(decision.get("errors", {}), ensure_ascii=False),
                json.dumps(decision.get("missing_classes", []), ensure_ascii=False),
                json.dumps(decision.get("verdicts", []), ensure_ascii=False),
                sample.snapshot.get("sha256"),
            ])
    elif section == "media":
        headers = ["id", "dataset_id", "relative_path", "morphology",
                   "kind", "video_group", "sha256", "native_image_id"]
        rows = [
            [m.id, m.dataset_id, m.relative_path, m.morphology,
             m.kind, m.video_group, m.sha256, m.native_image_id]
            for m in db.scalars(select(MediaAsset).where(MediaAsset.event_id == event_id))
        ]
    else:
        raise HTTPException(status_code=404, detail="Unknown export section.")
    return headers, rows


@router.get("/exports/{section}")
def export_csv(
    section: Literal["people", "jobs", "reviews", "frame_reviews", "media"],
    ctx: AuthContext = Depends(requires("organiser", "monitor")),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    headers, rows = export_rows(db, event.id, section)

    def generate():
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerow(headers)
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        for row in rows:
            writer.writerow([cell(value) for value in row])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    return StreamingResponse(
        generate(), media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="nightjar-{section}-{event.id}.csv"',
            "Cache-Control": "private, no-store",
        },
    )
