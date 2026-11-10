"""Organiser-only event reset. Remote archival precedes local deletion.

This resets the selected event, never the Supervisely team/members or other
Nightjar events. Existing roster identities, team pairs, monitor assignments,
and Nightjar sign-ins stay in place; attendance, jobs, datasets, tickets,
activities and operational histories are reset.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .config import get_settings
from .db import get_db
from .events_api import current_event
from .models import (
    AuditEvent, Base, CompetitionTeam, Event, LabelingJob, MediaDataset,
    NativeOperation, Person, SuperviselyConfig,
)

router = APIRouter(prefix="/api/v1/settings/reset", tags=["reset"])

# Exclude the following from the event-data deletion. The event retains its
# identity so monitors/teams keep their existing references and accounts.
PRESERVED = frozenset({
    "events", "people", "competition_teams", "monitor_pair_members",
    "monitor_assignments", "audit_events", "native_operations",
})


def reset_operation(db: Session, event_id: str):
    return db.scalar(select(NativeOperation).where(
        NativeOperation.event_id == event_id,
        NativeOperation.kind == "reset_event",
        NativeOperation.target_id == event_id,
    ))


def reset_preview(db: Session, event: Event) -> dict:
    datasets = list(db.scalars(select(MediaDataset).where(
        MediaDataset.event_id == event.id,
    )))
    config = db.get(SuperviselyConfig, 1)
    jobs = db.scalar(select(func.count(LabelingJob.id)).where(
        LabelingJob.event_id == event.id,
    )) or 0
    preserved_teams = db.scalar(select(func.count(CompetitionTeam.id)).where(
        CompetitionTeam.event_id == event.id,
    )) or 0
    preserved_people = db.scalar(select(func.count(Person.id)).where(
        Person.event_id == event.id,
    )) or 0
    operation = reset_operation(db, event.id)
    return {
        "event_id": event.id, "event_name": event.name,
        "event_status": event.status,
        "preserved_teams": preserved_teams, "preserved_people": preserved_people,
        "datasets": len(datasets), "jobs": jobs,
        "known_native_projects": sorted({
            ds.native_project_id for ds in datasets if ds.native_project_id is not None
        }),
        "workspace_id": config.workspace_id if config else None,
        "team_id": config.team_id if config else None,
        "remote_scope": "Nightjar projects for this event in the configured workspace",
        "remote_action": "archive_projects",
        "operation": None if not operation else {
            "status": operation.status, "error_code": operation.error_code,
            "attempts": operation.attempts,
        },
        "preserved": [
            "Supervisely team and all joined members",
            "Other Supervisely projects and workspaces",
            "Nightjar team and participant/monitor rosters",
            "Nightjar staff logins and Supervisely token",
            "Team membership and monitor assignments",
        ],
        "cleared": [
            "Event datasets, uploaded frames, downloaded annotations",
            "Nightjar and Supervisely labeling jobs under Nightjar projects",
            "Plans, reviews, tickets, activities, attendance, results and exports",
        ],
        "confirmation_phrase": "RESET " + event.name,
    }


@router.get("/preview")
def preview(
    ctx: AuthContext = Depends(requires("organiser")),
    db: Session = Depends(get_db),
):
    return reset_preview(db, current_event(db, ctx))


class ConfirmReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str


@router.post("/request", status_code=202)
def request_reset(
    payload: ConfirmReset,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    if payload.confirmation != "RESET " + event.name:
        raise HTTPException(status_code=422, detail="Type the exact reset confirmation phrase.")
    config = db.get(SuperviselyConfig, 1)
    if not config or not config.verified or not config.token_ciphertext:
        raise HTTPException(
            status_code=409, detail="Verified organiser Supervisely connection required."
        )
    if not config.workspace_id or not config.team_id:
        raise HTTPException(status_code=409, detail="Supervisely workspace/team required.")
    previous = reset_operation(db, event.id)
    if previous is not None and previous.status != "done":
        raise HTTPException(
            status_code=409, detail="A reset is already underway; inspect its status."
        )
    # The event lock serializes against any event-scoped job or upload mutation.
    # 'resetting' blocks new mutations via current_event(writable=True).
    event.status = "resetting"
    operation = previous or NativeOperation(
        event_id=event.id, kind="reset_event", target_id=event.id,
        status="pending",
    )
    operation.status = "pending"
    operation.error_code = None
    operation.claimed_at = None
    operation.completed_at = None
    db.add(operation)
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="event.reset.requested", target_type="event", target_id=event.id,
        details={"scope": "current_event", "archive_remote_projects": True},
    ))
    db.commit()
    return {"status": "pending", "operation_id": operation.id,
            "message": "Worker will archive Nightjar projects, then clear event data."}


@router.post("/retry", status_code=202)
def retry_reset(
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    operation = reset_operation(db, event.id)
    if not operation or event.status != "resetting":
        raise HTTPException(status_code=409, detail="No reset is waiting for retry.")
    if operation.status != "attention":
        raise HTTPException(status_code=409, detail="Only failed resets may be retried.")
    operation.status = "pending"
    operation.error_code = None
    operation.claimed_at = None
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="event.reset.retry", target_type="event", target_id=event.id,
        details={"attempts": operation.attempts},
    ))
    db.commit()
    return {"status": "pending", "operation_id": operation.id}


def safe_dataset_paths(db: Session, event_id: str) -> list[Path]:
    base = Path(get_settings().local_data_dir).resolve()
    paths = []
    for ds in db.scalars(select(MediaDataset).where(
        MediaDataset.event_id == event_id,
    )):
        raw = Path(ds.root_path)
        # A reset must never traverse symlinks, the storage root, or paths
        # outside the dedicated private media directory.
        if raw.is_symlink():
            raise ValueError("unsafe_dataset_storage")
        path = raw.resolve()
        if path == base or not path.is_relative_to(base):
            raise ValueError("unsafe_dataset_storage")
        paths.append(path)
    return paths


def clear_event_data(db: Session, event_id: str) -> None:
    """Delete dependents before parents, preserving all roster/identity tables."""
    # Stage records lack event_id; delete via dataset IDs before their parents.
    datasets = list(db.scalars(select(MediaDataset.id).where(
        MediaDataset.event_id == event_id,
    )))
    if datasets:
        stage = Base.metadata.tables["dataset_staged_files"]
        db.execute(delete(stage).where(stage.c.dataset_id.in_(datasets)))
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in PRESERVED or table.name == "dataset_staged_files":
            continue
        if "event_id" in table.c:
            db.execute(delete(table).where(table.c.event_id == event_id))
    # Keep valid membership/registration identities, remove all attendance.
    for person in db.scalars(select(Person).where(Person.event_id == event_id)):
        person.check_in_at = None
        person.table_number = None
    for team in db.scalars(select(CompetitionTeam).where(
        CompetitionTeam.event_id == event_id,
    )):
        team.table_number = None
