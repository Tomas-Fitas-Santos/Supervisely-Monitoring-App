"""Audited, idempotent admission, kept separate from Supervisely login."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import AuditEvent, CompetitionTeam, Person, epoch

router = APIRouter(prefix="/api/v1/attendance", tags=["attendance"])


class AttendanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: str
    checked_in: bool
    checked_in_at: int | None
    changed: bool
    table_number: int | None
    team_id: str | None


class CheckInInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table_number: int | None = Field(default=None, ge=1, le=10000)


class TableChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table_number: int = Field(ge=1, le=10000)


@router.get("/summary")
def attendance_summary(
    _ctx: AuthContext = Depends(requires("organiser", "reception", "monitor", "observer", "distribution", "activity")),
    db: Session = Depends(get_db),
):
    event = current_event(db, _ctx)
    registered = db.scalar(select(func.count(Person.id)).where(
        Person.active.is_(True), Person.event_id == event.id
    )) or 0
    present = db.scalar(select(func.count(Person.id)).where(
        Person.event_id == event.id, Person.active.is_(True), Person.check_in_at.is_not(None)
    )) or 0
    return {"registered": registered, "present": present, "not_arrived": registered - present}


def change_check_in(
    db: Session, person_id: str, staff_id: str, event_id: str, check_in: bool,
    requested_table: int | None = None,
) -> AttendanceResult:
    person = db.scalar(select(Person).where(
        Person.id == person_id, Person.event_id == event_id,
    ).with_for_update())
    if person is None or not person.active:
        raise HTTPException(status_code=404, detail="Active attendee not found.")
    team = None
    if person.kind == "participant" and person.team_id is not None:
        team = db.scalar(select(CompetitionTeam).where(
            CompetitionTeam.id == person.team_id,
            CompetitionTeam.event_id == event_id,
            CompetitionTeam.active.is_(True),
        ).with_for_update())
        if team is None:
            raise HTTPException(status_code=409, detail="Competition team is inactive.")
    table = team.table_number if team is not None else person.table_number
    existing = person.check_in_at is not None
    if check_in and not existing:
        if table is None:
            if requested_table is None and team is not None:
                raise HTTPException(
                    status_code=422,
                    detail="Assign the competition team a table number at first arrival.",
                )
            if requested_table is not None:
                if team is not None:
                    team.table_number = requested_table
                else:
                    person.table_number = requested_table
                table = requested_table
        elif requested_table is not None and requested_table != table:
            raise HTTPException(
                status_code=409,
                detail="The team already has a different table. Correct its table separately.",
            )
    changed = existing != check_in
    if changed:
        previous = person.check_in_at
        person.check_in_at = epoch() if check_in else None
        db.add(AuditEvent(
            staff_id=staff_id, event_id=event_id,
            action="attendance.check_in" if check_in else "attendance.reverse",
            target_type="person", target_id=person.id,
            details={
                "prior_check_in_at": previous, "new_check_in_at": person.check_in_at,
                "table_number": table, "team_id": person.team_id,
            },
        ))
        db.commit()
    return AttendanceResult(
        person_id=person.id, checked_in=person.check_in_at is not None,
        checked_in_at=person.check_in_at, changed=changed,
        table_number=table, team_id=person.team_id,
    )


@router.put("/teams/{team_id}/table")
def change_team_table(
    team_id: str, payload: TableChange,
    ctx: AuthContext = Depends(requires("organiser", "reception", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    team = db.scalar(select(CompetitionTeam).where(
        CompetitionTeam.id == team_id,
        CompetitionTeam.event_id == event.id,
        CompetitionTeam.active.is_(True),
    ).with_for_update())
    if team is None:
        raise HTTPException(status_code=404, detail="Active competition team not found.")
    before = team.table_number
    if before != payload.table_number:
        team.table_number = payload.table_number
        db.add(AuditEvent(
            staff_id=ctx.staff.id, event_id=event.id,
            action="attendance.team_table.change",
            target_type="competition_team", target_id=team.id,
            details={"previous": before, "new": payload.table_number},
        ))
        db.commit()
    return {"team_id": team.id, "table_number": team.table_number,
            "changed": before != payload.table_number}


@router.post("/{person_id}/check-in", response_model=AttendanceResult)
def check_in(
    person_id: str,
    payload: CheckInInput | None = None,
    ctx: AuthContext = Depends(requires("organiser", "reception", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    return change_check_in(
        db, person_id, ctx.staff.id, event.id, True,
        payload.table_number if payload else None,
    )


@router.post("/{person_id}/reverse", response_model=AttendanceResult)
def reverse_check_in(
    person_id: str,
    ctx: AuthContext = Depends(requires("organiser", "reception", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    return change_check_in(db, person_id, ctx.staff.id, event.id, False)
