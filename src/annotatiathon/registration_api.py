"""Roster-approved monitor registration with organiser-issued one-time codes.

Existing staff accounts sign in unchanged. This workflow does not require
SMTP or a Supervisely API token from a new monitor.
"""
import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import normalized_email, same_origin
from .db import get_db
from .models import (
    AuditEvent,
    Event,
    Person,
    RegistrationChallenge,
    StaffAccount,
    epoch,
)
from .security import password_hash

router = APIRouter(prefix="/api/v1/auth/register", tags=["staff self-registration"])


class MonitorAccountRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)
    registration_code: str | None = Field(default=None, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize(cls, value: str) -> str:
        return normalized_email(value)


def monitor_can_register(person: Person) -> bool:
    """Allow roster monitors and verified native managers unless denied explicitly.

    A verified Supervisely member must already be an active SQL roster
    monitor and a confirmed member of the organiser's team. Registration
    still creates ONLY a Nightjar account, not a Supervisely identity.
    """
    if person.kind != "monitor" or not person.active:
        return False
    fields = person.source_fields or {}
    if "monitor_registration_approved" in fields:
        return fields["monitor_registration_approved"] is True
    if fields.get("source") != "supervisely_member_sync":
        return True
    return (
        person.membership_status == "joined"
        and person.supervisely_user_id is not None
        and person.native_verified_at is not None
        and person.supervisely_role in {"admin", "manager"}
    )


def eligible_monitor(db: Session, email: str) -> Person | None:
    """Find an active SQL monitor, including verified native team managers.

    Password login for existing staff never calls this function. This
    allowlist applies only to *initial creation* of a Nightjar account.
    """
    records = db.scalars(
        select(Person).join(Event, Person.event_id == Event.id).where(
            Person.email == email,
            Person.kind == "monitor",
            Person.active.is_(True),
            Event.status == "active",
        ).order_by(Person.created_at.desc())
    )
    return next(
        (
            person for person in records
            if monitor_can_register(person)
        ), None,
    )


@router.post("", status_code=201)
def create_monitor_account(
    payload: MonitorAccountRegistration,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a Nightjar monitor login with an organiser-issued code.

    Email ownership is not inferred from an approved roster address alone.
    Organisers must deliver the code to the intended monitor privately.
    """
    same_origin(request)
    # Previously created accounts are never re-registered when event/roster
    # configuration changes. Direct people to their existing login.
    if db.scalar(select(StaffAccount.id).where(StaffAccount.email == payload.email)):
        raise HTTPException(status_code=409, detail="A Nightjar account already exists. Sign in.")
    person = eligible_monitor(db, payload.email)
    if person is None:
        raise HTTPException(
            status_code=403,
            detail="This email is not on the approved monitor roster. Contact the organiser.",
        )
    challenge = db.scalar(select(RegistrationChallenge).where(
        RegistrationChallenge.email == payload.email,
    ).with_for_update())
    if (challenge is None or challenge.expires_at < epoch()
            or challenge.attempts >= 5):
        raise HTTPException(
            status_code=403,
            detail="Ask the organiser for an active one-time registration code.",
        )
    digest = hashlib.sha256(
        (payload.email + "\0" + (payload.registration_code or "")).encode(),
    ).hexdigest()
    if not hmac.compare_digest(digest, challenge.code_digest):
        challenge.attempts += 1
        db.commit()
        raise HTTPException(status_code=403, detail="Invalid registration code.")

    staff = StaffAccount(
        email=payload.email,
        name=person.name,
        password_hash=password_hash(payload.password),
        roles=["monitor"],
    )
    db.add(staff)
    try:
        db.flush()
        db.add(AuditEvent(
            staff_id=staff.id,
            event_id=person.event_id,
            action="staff.monitor.self_register",
            target_type="staff",
            target_id=staff.id,
            details={
                "roster_person_id": person.id,
                "matched_approved_roster_email": True,
                "email_ownership_verified": False,
                "organiser_registration_code_verified": True,
                "roles": ["monitor"],
                "supervisely_linked": False,
            },
        ))
        db.delete(challenge)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A Nightjar account already exists. Sign in.",
        ) from exc
    return {
        "registered": True,
        "message": "Nightjar account created using a private organiser code. Link Supervisely separately.",
    }
