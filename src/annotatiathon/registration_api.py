"""Email-verified, roster-restricted self-registration for event monitors.

Registration never grants organiser privileges. Every new account must
correspond to an active monitor email in an active event's roster, and the
address owner must verify a short-lived code delivered through SMTP.
"""
import hashlib
import hmac
import secrets
import smtplib
import ssl
from email.message import EmailMessage

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import normalized_email
from .config import get_settings
from .db import get_db
from .models import AuditEvent, Event, Person, RegistrationChallenge, StaffAccount, epoch
from .security import password_hash

router = APIRouter(prefix="/api/v1/auth/register", tags=["staff self-registration"])
GENERIC_REPLY = {"message": "If this email is eligible for a monitor account, a verification code will be sent."}


class RequestCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def email_key(cls, value: str) -> str:
        return normalized_email(value)


class CompleteRegistration(RequestCode):
    code: str = Field(pattern=r"^\d{8}$")
    password: str = Field(min_length=12, max_length=1024)


def code_digest(email: str, code: str) -> str:
    key = get_settings().nightjar_encryption_key
    if key is None or not key.get_secret_value().strip():
        raise HTTPException(status_code=503, detail="Registration verification is not configured.")
    return hmac.new(
        key.get_secret_value().encode(), (email + "|" + code).encode(),
        hashlib.sha256,
    ).hexdigest()


def send_verification(email: str, code: str) -> None:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_sender:
        raise HTTPException(
            status_code=503,
            detail="Monitor registration email service is not yet configured.",
        )
    msg = EmailMessage()
    msg["From"] = settings.smtp_sender
    msg["To"] = email
    msg["Subject"] = "Nightjar Annotatiathon - verify your monitor account"
    msg.set_content(
        "Your one-time Nightjar monitor registration code is: " + code +
        "\n\nIt expires in 10 minutes. If you did not request this, ignore this email."
    )
    try:
        if settings.smtp_use_ssl:
            connection = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=10,
                context=ssl.create_default_context(),
            )
        else:
            connection = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
        with connection as smtp:
            if not settings.smtp_use_ssl:
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                if not settings.smtp_password:
                    raise RuntimeError("SMTP password is not configured.")
                smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503, detail="Could not send verification email. Contact the organiser."
        ) from exc


def eligible_monitor(db: Session, email: str) -> Person | None:
    """Require an organiser/CSV roster entry, never an auto-discovered native member."""
    records = db.scalars(
        select(Person).join(Event, Person.event_id == Event.id).where(
            Person.email == email, Person.kind == "monitor",
            Person.active.is_(True), Event.status == "active",
        ).order_by(Person.created_at.desc())
    )
    return next(
        (
            person for person in records
            if (person.source_fields or {}).get(
                "monitor_registration_approved",
                (person.source_fields or {}).get("source") != "supervisely_member_sync",
            ) is True
        ),
        None,
    )


@router.post("/request")
def request_registration(payload: RequestCode, db: Session = Depends(get_db)):
    monitor = eligible_monitor(db, payload.email)
    if not monitor:
        return GENERIC_REPLY
    if db.scalar(select(StaffAccount.id).where(StaffAccount.email == payload.email)):
        return GENERIC_REPLY
    now = epoch()
    challenge = db.scalar(
        select(RegistrationChallenge)
        .where(RegistrationChallenge.email == payload.email).with_for_update()
    )
    if challenge is not None and now - challenge.requested_at < 60:
        return GENERIC_REPLY
    code = f"{secrets.randbelow(100_000_000):08d}"
    digest = code_digest(payload.email, code)
    send_verification(payload.email, code)
    if challenge is None:
        challenge = RegistrationChallenge(email=payload.email, code_digest=digest,
            expires_at=now + 600, requested_at=now, attempts=0)
        db.add(challenge)
    else:
        challenge.code_digest = digest
        challenge.expires_at = now + 600
        challenge.requested_at = now
        challenge.attempts = 0
    db.commit()
    return GENERIC_REPLY


@router.post("/complete")
def complete_registration(payload: CompleteRegistration, db: Session = Depends(get_db)):
    now = epoch()
    challenge = db.scalar(
        select(RegistrationChallenge)
        .where(RegistrationChallenge.email == payload.email).with_for_update()
    )
    if (challenge is None or challenge.expires_at <= now or challenge.attempts >= 5
            or not eligible_monitor(db, payload.email)):
        raise HTTPException(status_code=400, detail="Verification code invalid or expired.")
    challenge.attempts += 1
    if not secrets.compare_digest(challenge.code_digest, code_digest(payload.email, payload.code)):
        db.commit()
        raise HTTPException(status_code=400, detail="Verification code invalid or expired.")
    if db.scalar(select(StaffAccount.id).where(StaffAccount.email == payload.email)):
        raise HTTPException(status_code=409, detail="Account already exists. Sign in.")
    roster_person = eligible_monitor(db, payload.email)
    staff = StaffAccount(
        email=payload.email, name=roster_person.name,
        password_hash=password_hash(payload.password), roles=["monitor"],
    )
    db.add(staff)
    db.delete(challenge)
    db.flush()
    db.add(AuditEvent(
        staff_id=staff.id, action="staff.monitor.self_register",
        target_type="staff", target_id=staff.id,
        details={"verified_email": True, "roles": ["monitor"]},
    ))
    db.commit()
    return {"registered": True, "message": "Your account is ready. Sign in using your email and password."}
