"""Email-verified, roster-restricted self-registration for event monitors.

Registration never grants organiser privileges. A new account must match
an active approved monitor in the event roster; the person must prove
control of that exact email using a short-lived one-time code.
"""
import hashlib
import hmac
import secrets
import smtplib
import ssl
from email.message import EmailMessage

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, normalized_email, requires, same_origin
from .config import get_settings
from .db import get_db
from .models import AuditEvent, Event, Person, RegistrationChallenge, StaffAccount, epoch
from .security import password_hash

router = APIRouter(prefix="/api/v1/auth/register", tags=["staff self-registration"])
settings_router = APIRouter(
    prefix="/api/v1/settings/registration-email",
    tags=["monitor registration configuration"],
)
GENERIC_REPLY = {
    "message": (
        "If this address belongs to an approved monitor without an account, "
        "a verification code will be emailed. Check your inbox and spam folder."
    ),
    "resend_after_seconds": 60,
}
MAX_CODE_ATTEMPTS = 5
CODE_SECONDS = 600
RESEND_SECONDS = 60


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


def smtp_ready() -> bool:
    """Expose only readiness; never expose SMTP credentials to the browser."""
    settings = get_settings()
    return bool(
        settings.smtp_host and settings.smtp_sender
        and (not settings.smtp_username or settings.smtp_password)
    )


def code_digest(email: str, code: str) -> str:
    key = get_settings().nightjar_encryption_key
    if key is None or not key.get_secret_value().strip():
        raise HTTPException(status_code=503, detail="Registration verification is not configured.")
    return hmac.new(
        key.get_secret_value().encode(), (email + "|" + code).encode(),
        hashlib.sha256,
    ).hexdigest()


def send_mail(email: str, subject: str, body: str) -> None:
    """Send server-side mail over TLS; never log passwords or verification codes."""
    settings = get_settings()
    if not smtp_ready():
        raise HTTPException(
            status_code=503,
            detail="Registration email is not configured. Contact the event organiser.",
        )
    msg = EmailMessage()
    msg["From"] = settings.smtp_sender
    msg["To"] = email
    msg["Subject"] = subject
    msg.set_content(body)
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
                smtp.login(
                    settings.smtp_username, settings.smtp_password.get_secret_value(),
                )
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not send the email using the configured SMTP service. Contact the organiser.",
        ) from exc


def send_verification(email: str, code: str) -> None:
    send_mail(
        email,
        "Nightjar Annotatiathon — verify your monitor email",
        "Your one-time Nightjar monitor registration code is: " + code
        + "\n\nThis code expires in 10 minutes and can be used only once."
        + "\nIf you didn't request it, ignore this email.",
    )


def eligible_monitor(db: Session, email: str) -> Person | None:
    """Explicitly approved event roster, never arbitrary native team members."""
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
            and (person.membership_status != "joined"
                 or person.supervisely_role == "manager"
                 or person.supervisely_role == "admin")
        ),
        None,
    )


@router.get("/status")
def registration_status():
    """Show whether email verification can be used, without exposing a roster."""
    return {
        "email_verification_available": smtp_ready(),
        "verification_code_minutes": CODE_SECONDS // 60,
        "resend_after_seconds": RESEND_SECONDS,
    }


@router.post("/request")
def request_registration(
    payload: RequestCode, request: Request, db: Session = Depends(get_db),
):
    same_origin(request)
    if not smtp_ready():
        raise HTTPException(
            status_code=503,
            detail="Registration email delivery is not configured. Contact the organiser.",
        )
    monitor = eligible_monitor(db, payload.email)
    # Unknown/ineligible/existing accounts all receive the same public reply.
    if monitor is None:
        return GENERIC_REPLY
    if db.scalar(select(StaffAccount.id).where(StaffAccount.email == payload.email)):
        return GENERIC_REPLY
    now = epoch()
    challenge = db.scalar(
        select(RegistrationChallenge)
        .where(RegistrationChallenge.email == payload.email).with_for_update()
    )
    if challenge is not None and now - challenge.requested_at < RESEND_SECONDS:
        return GENERIC_REPLY
    code = f"{secrets.randbelow(100_000_000):08d}"
    digest = code_digest(payload.email, code)
    try:
        send_verification(payload.email, code)
    except HTTPException:
        # An SMTP outage must not reveal which public email addresses are
        # approved event monitors. Organisers can diagnose using email test.
        return GENERIC_REPLY
    if challenge is None:
        db.add(RegistrationChallenge(
            email=payload.email, code_digest=digest,
            expires_at=now + CODE_SECONDS, requested_at=now, attempts=0,
        ))
    else:
        challenge.code_digest = digest
        challenge.expires_at = now + CODE_SECONDS
        challenge.requested_at = now
        challenge.attempts = 0
    db.commit()
    return GENERIC_REPLY


@router.post("/complete")
def complete_registration(
    payload: CompleteRegistration, request: Request, db: Session = Depends(get_db),
):
    same_origin(request)
    now = epoch()
    challenge = db.scalar(
        select(RegistrationChallenge)
        .where(RegistrationChallenge.email == payload.email).with_for_update()
    )
    if (challenge is None or challenge.expires_at <= now
            or challenge.attempts >= MAX_CODE_ATTEMPTS):
        raise HTTPException(status_code=400, detail="Verification code invalid or expired.")
    person = eligible_monitor(db, payload.email)
    if person is None:
        raise HTTPException(
            status_code=403,
            detail="Monitor registration is not approved. Contact the organiser.",
        )
    challenge.attempts += 1
    if not secrets.compare_digest(
        challenge.code_digest, code_digest(payload.email, payload.code),
    ):
        db.commit()
        raise HTTPException(status_code=400, detail="Verification code invalid or expired.")
    if db.scalar(select(StaffAccount.id).where(StaffAccount.email == payload.email)):
        raise HTTPException(status_code=409, detail="Account already exists. Sign in.")
    staff = StaffAccount(
        email=payload.email, name=person.name,
        password_hash=password_hash(payload.password), roles=["monitor"],
    )
    db.add(staff)
    db.delete(challenge)
    db.flush()
    db.add(AuditEvent(
        staff_id=staff.id, event_id=person.event_id, action="staff.monitor.self_register",
        target_type="staff", target_id=staff.id,
        details={"verified_email": True, "roster_person_id": person.id, "roles": ["monitor"]},
    ))
    db.commit()
    return {
        "registered": True,
        "message": "Monitor account created and email verified. You can now sign in.",
    }


@settings_router.get("")
def email_delivery_status(
    _ctx: AuthContext = Depends(requires("organiser")),
):
    settings = get_settings()
    return {
        "configured": smtp_ready(),
        "sender": settings.smtp_sender if smtp_ready() else None,
        "port": settings.smtp_port if smtp_ready() else None,
        "ssl": settings.smtp_use_ssl if smtp_ready() else None,
    }


@settings_router.post("/test")
def test_registration_email(
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    """Send a harmless connectivity test only to the signed-in organiser."""
    send_mail(
        ctx.staff.email,
        "Nightjar — monitor registration email test",
        "Nightjar email delivery is working. This is a configuration test,"
        " not a registration verification code.\n",
    )
    db.add(AuditEvent(
        staff_id=ctx.staff.id, action="registration_email.test_sent",
        target_type="staff", target_id=ctx.staff.id,
        details={"recipient": "signed_in_organiser", "smtp_configured": True},
    ))
    db.commit()
    return {"sent": True, "message": "Test email submitted to the SMTP service."}
