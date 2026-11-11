"""Read-only event report/export and controlled final closeout with preserved records."""
import csv
import io
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
    TicketLedgerEntry,
    epoch,
)

router = APIRouter(prefix="/api/v1/closeout", tags=["event reports"])
VIEWERS = ("organiser", "monitor", "observer")


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
            "jobs_accepted": statuses["completed"],
            "jobs_needing_action": sum(
                status not in {"completed", "cancelled"} for status in (
                    j.status for j in jobs
                )
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
    if report_data["labeling"]["jobs_needing_action"]:
        raise HTTPException(
            status_code=409,
            detail="Some labeling jobs are not accepted. Resolve them before closeout.",
        )
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
            "frames_reviewed": report_data["reviews"]["frames_reviewed"],
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
    section: Literal["people", "jobs", "reviews", "media"],
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
