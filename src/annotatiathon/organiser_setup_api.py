"""Email-verified setup and password recovery for allowlisted app staff.

A known address alone never grants account access. No Supervisely identity or
event-roster role is consulted by this flow.
"""
import hashlib
import hmac
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .auth import normalized_email, same_origin
from .config import get_settings
from .db import get_db
from .models import AuditEvent, RegistrationChallenge, StaffAccount, StaffSession, epoch
from .organiser_access import access_roles
from .security import password_hash

router = APIRouter(prefix="/api/v1/auth/setup", tags=["staff account setup"])
LINK_SECONDS = 20 * 60
REQUEST_COOLDOWN_SECONDS = 60
GENERIC_MESSAGE = "If this address is approved, a secure account setup link has been emailed."


class SetupRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def normalize(cls, value: str) -> str:
        return normalized_email(value)


class SetupComplete(BaseModel):
    email: str
    token: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=12, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize(cls, value: str) -> str:
        return normalized_email(value)


def _mail_configuration():
    settings = get_settings()
    base = settings.nightjar_public_url.strip().rstrip("/")
    url = urlsplit(base)
    if ((settings.app_env == "production" and url.scheme != "https") or
        url.scheme not in {"https", "http"} or not url.hostname or
        url.username or url.password or url.query or url.fragment or
        not settings.nightjar_smtp_host or not settings.nightjar_smtp_from):
        raise HTTPException(
            status_code=503,
            detail="Account email delivery is not configured. Contact an administrator.",
        )
    return settings, base


def _send_link(email: str, token: str) -> None:
    settings, base = _mail_configuration()
    # Fragment is never sent as part of the HTTP request to the public web server.
    url = base + "/#nightjar_setup=" + quote(token, safe="") + "&email=" + quote(email, safe="")
    msg = EmailMessage()
    msg["Subject"] = "Nightjar: secure account setup"
    msg["From"] = settings.nightjar_smtp_from
    msg["To"] = email
    msg.set_content(
        "Use this link to create or recover your Nightjar organiser account:\n\n"
        + url + "\n\nThe link expires in 20 minutes and works once. "
        "If you did not request it, ignore this email.\n"
    )
    try:
        if settings.nightjar_smtp_port == 465:
            client = smtplib.SMTP_SSL(
                settings.nightjar_smtp_host, settings.nightjar_smtp_port,
                timeout=10, context=ssl.create_default_context(),
            )
        else:
            client = smtplib.SMTP(
                settings.nightjar_smtp_host, settings.nightjar_smtp_port, timeout=10,
            )
        with client as smtp:
            if settings.nightjar_smtp_port != 465:
                smtp.starttls(context=ssl.create_default_context())
            if settings.nightjar_smtp_user:
                if settings.nightjar_smtp_password is None:
                    raise ValueError("SMTP password missing")
                smtp.login(
                    settings.nightjar_smtp_user,
                    settings.nightjar_smtp_password.get_secret_value(),
                )
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        # Never leak SMTP credentials, tokens, or external error messages.
        raise HTTPException(
            status_code=503, detail="Setup email could not be delivered. Contact an administrator.",
        ) from exc


@router.post("/request")
def request_account_link(
    payload: SetupRequest, request: Request, db: Session = Depends(get_db),
):
    same_origin(request)
    roles = access_roles(payload.email)
    if not roles:
        return {"message": GENERIC_MESSAGE}
    # Do not issue a token when the deployment cannot deliver an email.
    _mail_configuration()
    now = epoch()
    challenge = db.scalar(
        select(RegistrationChallenge)
        .where(RegistrationChallenge.email == payload.email)
        .with_for_update()
    )
    if challenge is not None and now - challenge.requested_at < REQUEST_COOLDOWN_SECONDS:
        return {"message": GENERIC_MESSAGE}
    token = secrets.token_urlsafe(36)
    digest = hashlib.sha256((payload.email + "\0" + token).encode()).hexdigest()
    if challenge is None:
        challenge = RegistrationChallenge(
            email=payload.email, code_digest=digest,
            requested_at=now, expires_at=now + LINK_SECONDS, attempts=0,
        )
        db.add(challenge)
    else:
        challenge.code_digest = digest
        challenge.requested_at = now
        challenge.expires_at = now + LINK_SECONDS
        challenge.attempts = 0
    db.flush()
    _send_link(payload.email, token)
    db.add(AuditEvent(
        action="staff.setup_link.request", target_type="staff", target_id=None,
        details={"email": payload.email, "channel": "smtp"},
    ))
    db.commit()
    return {"message": GENERIC_MESSAGE}


@router.post("/complete")
def complete_account_setup(
    payload: SetupComplete, request: Request, db: Session = Depends(get_db),
):
    same_origin(request)
    roles = access_roles(payload.email)
    if not roles:
        raise HTTPException(status_code=403, detail="This address has no Nightjar app access.")
    challenge = db.scalar(
        select(RegistrationChallenge)
        .where(RegistrationChallenge.email == payload.email)
        .with_for_update()
    )
    digest = hashlib.sha256((payload.email + "\0" + payload.token).encode()).hexdigest()
    if (challenge is None or challenge.expires_at < epoch()
            or challenge.attempts >= 5
            or not hmac.compare_digest(digest, challenge.code_digest)):
        if challenge is not None:
            challenge.attempts += 1
            db.commit()
        raise HTTPException(status_code=403, detail="Setup link is invalid or expired. Request a new link.")
    staff = db.scalar(
        select(StaffAccount)
        .where(StaffAccount.email == payload.email)
        .with_for_update()
    )
    if staff is None:
        staff = StaffAccount(
            email=payload.email, name=payload.email.split("@", 1)[0],
            roles=roles, password_hash=password_hash(payload.password),
            active=True,
        )
        db.add(staff)
        db.flush()
    else:
        staff.roles = roles
        staff.active = True
        staff.password_hash = password_hash(payload.password)
        staff.login_failures = 0
        staff.locked_until = None
        db.execute(delete(StaffSession).where(StaffSession.staff_id == staff.id))
    db.add(AuditEvent(
        action="staff.account.email_verified_setup", target_type="staff",
        target_id=staff.id, details={"roles": roles, "verified_delivery": "email_link"},
    ))
    db.delete(challenge)
    db.commit()
    return {"registered": True, "message": "Your Nightjar account is ready. Sign in."}
