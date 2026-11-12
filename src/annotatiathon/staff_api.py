"""Authenticated staff-role editing, independent of the attendee roster."""
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import ROLES, AuthContext, normalized_email, requires, staff_view
from .events_api import current_event
from .registration_api import monitor_can_register
from .db import get_db
from .models import AuditEvent, Person, RegistrationChallenge, StaffAccount, epoch

router = APIRouter(prefix="/api/v1/staff", tags=["staff"])


class RolesChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: list[str] = Field(min_length=1, max_length=len(ROLES))


@router.get("")
def list_staff(
    _ctx: AuthContext = Depends(requires("organiser")),
    db: Session = Depends(get_db),
):
    staff = db.scalars(select(StaffAccount).order_by(StaffAccount.name)).all()
    return {"items": [
        {**staff_view(person).model_dump(), "active": person.active} for person in staff
    ]}


@router.put("/{staff_id}/roles")
def update_staff_roles(
    staff_id: str,
    payload: RolesChange,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    if len(set(payload.roles)) != len(payload.roles) or any(
        role not in ROLES for role in payload.roles
    ):
        raise HTTPException(status_code=422, detail="Use distinct, supported staff roles.")
    # Serialize organiser role changes across requests to preserve an active organiser.
    db.execute(select(StaffAccount.id).order_by(StaffAccount.id).with_for_update()).all()
    person = db.get(StaffAccount, staff_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Staff account not found.")
    if not person.active:
        raise HTTPException(status_code=409, detail="Cannot change roles of a disabled account.")
    if "organiser" in person.roles and "organiser" not in payload.roles:
        active_staff = list(db.scalars(select(StaffAccount).where(
            StaffAccount.active.is_(True)
        )))
        if not any(
            other.id != person.id and "organiser" in other.roles for other in active_staff
        ):
            raise HTTPException(status_code=409, detail="At least one organiser must remain.")
    before = list(person.roles)
    person.roles = list(payload.roles)
    db.add(AuditEvent(
        staff_id=ctx.staff.id, action="staff.roles.update", target_type="staff",
        target_id=person.id, details={"previous": before, "new": person.roles},
    ))
    db.commit()
    return {**staff_view(person).model_dump(), "active": person.active}


class RegistrationCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)


@router.post("/registration-code")
def issue_monitor_registration_code(
    payload: RegistrationCodeRequest,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    """Create a one-time, organiser-delivered Nightjar monitor invitation.

    The plaintext code is returned once and never stored or logged.
    """
    try:
        email = normalized_email(payload.email)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    event = current_event(db, ctx, writable=True)
    person = db.scalar(select(Person).where(
        Person.event_id == event.id, Person.email == email,
        Person.kind == "monitor", Person.active.is_(True),
    ))
    if person is None or not monitor_can_register(person):
        raise HTTPException(status_code=403, detail="Monitor is not approved in this event.")
    if db.scalar(select(StaffAccount.id).where(StaffAccount.email == email)):
        raise HTTPException(status_code=409, detail="A Nightjar account already exists.")
    now = epoch()
    secret = secrets.token_urlsafe(24)
    digest = hashlib.sha256((email + "\0" + secret).encode()).hexdigest()
    challenge = db.scalar(select(RegistrationChallenge).where(
        RegistrationChallenge.email == email,
    ).with_for_update())
    if challenge is None:
        challenge = RegistrationChallenge(
            email=email, code_digest=digest, requested_at=now,
            expires_at=now + 604800, attempts=0,
        )
        db.add(challenge)
    else:
        challenge.code_digest = digest
        challenge.requested_at = now
        challenge.expires_at = now + 604800
        challenge.attempts = 0
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="staff.monitor.registration_code_issued",
        target_type="person", target_id=person.id,
        details={"email": email, "expires_at": challenge.expires_at},
    ))
    db.commit()
    return {
        "email": email, "registration_code": secret,
        "expires_at": challenge.expires_at,
    }
