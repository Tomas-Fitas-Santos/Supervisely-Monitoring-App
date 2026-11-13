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
    NativeOperation,
    Person,
    ReviewSample,
    TicketLedgerEntry,
    epoch,
)
from .review_eligibility import eligible_frame_review
from .scoring_api import correct_point_types, team_rankings

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
            eligible_frame_review(),
        ).group_by(ReviewSample.team_id)
    ).all())
    reviewed = dict(db.execute(
        select(ReviewSample.team_id, func.count(ReviewSample.id))
        .join(LabelingJob, LabelingJob.id == ReviewSample.job_id)
        .where(
            ReviewSample.event_id == event_id,
            eligible_frame_review(),
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
    event = db.get(Event, event_id)
    outstanding = db.scalar(select(func.count()).select_from(NativeOperation).where(
        NativeOperation.event_id == event_id,
        NativeOperation.kind == "validate_submission",
        NativeOperation.status != "done",
    ))
    unfinished = db.scalar(select(func.count()).select_from(LabelingJob).where(
        LabelingJob.event_id == event_id,
        LabelingJob.status.in_(("queued", "publishing", "in_progress", "needs_correction")),
    ))
    return {
        "ready": event.status == "active",
        "blocking_jobs": outstanding,
        "unfinished_unscored_jobs": unfinished,
        "verified_reviews_available": fairness["ready_for_winner"],
        "official_frame_review_required": False,
        "message": (
            "Close competition to finish queued submissions and freeze standings."
            if event.status == "active" else
            "Waiting for queued submissions." if event.status == "finalizing" else
            "Competition closed."
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
        "closeout": closeout_progress(db, event),
        "ticket_totals": dict(ledger_totals),
        "generated_at": epoch(),
    }


@router.get("/report")
def report(
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    if event.status == "archived":
        snapshot = db.scalar(select(EventCloseout.snapshot_json).where(
            EventCloseout.event_id == event.id,
        ))
        if snapshot is not None and "rankings" in snapshot:
            return snapshot
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
    event_id = current_event(db, ctx).id
    event = lock_closeout_event(db, event_id)
    if payload.confirmation != "CLOSE " + event.name:
        raise HTTPException(status_code=422, detail="Type the exact event closeout phrase.")
    if event.closing_at is not None:
        return {"event_id": event.id, "status": event.status,
                "closeout": closeout_progress(db, event)}
    if event.status != "active":
        raise HTTPException(status_code=409, detail="This event is already closed.")
    event.status = "finalizing"
    event.closing_at = epoch()
    event.closing_by = ctx.staff.id
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id, action="event.closeout.start",
        target_type="event", target_id=event.id,
    ))
    db.flush()
    snapshot = finish_closeout(db, event)
    progress = closeout_progress(db, event)
    db.commit()
    return {"event_id": event.id, "status": event.status, "closeout": progress,
            **({"report": snapshot} if snapshot is not None else {})}


def lock_closeout_event(db: Session, event_id: str) -> Event:
    return db.scalars(select(Event).where(Event.id == event_id)
                      .with_for_update().execution_options(populate_existing=True)).one()


def closeout_progress(db: Session, event: Event) -> dict:
    counts = Counter()
    issues = []
    if event.closing_at is not None:
        for op in db.scalars(select(NativeOperation).where(
            NativeOperation.event_id == event.id,
            NativeOperation.kind == "validate_submission",
        ).order_by(NativeOperation.id)):
            counts[op.status] += 1
            if op.status not in {"pending", "running", "done"}:
                issues.append({"operation_id": op.id, "job_id": op.target_id,
                               "error_code": op.error_code})
    return {"cutoff_at": event.closing_at, "pending": counts["pending"],
            "running": counts["running"], "done": counts["done"], "issues": issues}


def finish_closeout(db: Session, event: Event) -> dict | None:
    """Freeze the result while holding the event lock shared by review writes."""
    if event.status != "finalizing" or db.scalar(select(NativeOperation.id).where(
        NativeOperation.event_id == event.id,
        NativeOperation.kind == "validate_submission",
        NativeOperation.status != "done",
    ).limit(1)) is not None:
        return None
    event.status = "archived"
    event.archived_at = epoch()
    db.flush()
    snapshot = event_report(db, event.id)
    rankings = team_rankings(db, event.id)
    snapshot["rankings"] = rankings
    snapshot["verified_winner_available"] = any(
        row["rank"] == 1 for row in rankings["items"]
    )
    db.add(EventCloseout(
        event_id=event.id, snapshot_json=snapshot, closed_by=event.closing_by,
    ))
    db.add(AuditEvent(
        staff_id=event.closing_by, event_id=event.id, action="event.closeout",
        target_type="event", target_id=event.id,
    ))
    return snapshot


def finish_pending_closeouts(db: Session) -> None:
    event_ids = list(db.scalars(select(Event.id).where(Event.status == "finalizing")))
    for event_id in event_ids:
        finish_closeout(db, lock_closeout_event(db, event_id))
        db.commit()


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
