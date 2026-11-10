"""Audited, idempotent admission, kept separate from Supervisely login."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import AuditEvent, Person, epoch

router = APIRouter(prefix="/api/v1/attendance", tags=["attendance"])


class AttendanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: str
    checked_in: bool
    checked_in_at: int | None
    changed: bool


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


def change_check_in(db: Session, person_id: str, staff_id: str,
                    event_id: str, check_in: bool) -> AttendanceResult:
    person = db.scalar(select(Person).where(Person.id == person_id).with_for_update())
    if person is None or person.event_id != event_id or not person.active:
        raise HTTPException(status_code=404, detail="Active attendee not found.")
    existing = person.check_in_at is not None
    changed = existing != check_in
    if changed:
        previous = person.check_in_at
        person.check_in_at = epoch() if check_in else None
        db.add(AuditEvent(
            staff_id=staff_id, event_id=event_id,
            action="attendance.check_in" if check_in else "attendance.reverse",
            target_type="person", target_id=person.id,
            details={"prior_check_in_at": previous, "new_check_in_at": person.check_in_at},
        ))
        db.commit()
    return AttendanceResult(
        person_id=person.id, checked_in=person.check_in_at is not None,
        checked_in_at=person.check_in_at, changed=changed,
    )


@router.post("/{person_id}/check-in", response_model=AttendanceResult)
def check_in(
    person_id: str,
    ctx: AuthContext = Depends(requires("organiser", "reception", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    return change_check_in(db, person_id, ctx.staff.id, event.id, True)


@router.post("/{person_id}/reverse", response_model=AttendanceResult)
def reverse_check_in(
    person_id: str,
    ctx: AuthContext = Depends(requires("organiser", "reception", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    return change_check_in(db, person_id, ctx.staff.id, event.id, False)
