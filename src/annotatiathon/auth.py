"""First-party staff authentication. Supervisely tokens are unrelated to staff login."""
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import DEFAULT_EVENT_ID, AuditEvent, Event, Person, StaffAccount, StaffSession
from .security import DUMMY_HASH, password_matches, random_token, token_digest

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
SESSION_SECONDS = 12 * 60 * 60
ROLES = frozenset({"organiser", "monitor", "reception", "activity", "distribution", "observer"})


def normalized_email(email: str) -> str:
    value = email.strip().lower()
    if len(value) > 320 or "@" not in value or value.startswith("@") or value.endswith("@"):
        raise ValueError("A valid email address is required.")
    return value


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize(cls, value: str) -> str:
        return normalized_email(value)


class StaffView(BaseModel):
    id: str
    name: str
    email: str
    roles: list[str]


class AuthView(BaseModel):
    staff: StaffView
    csrf_token: str


@dataclass(frozen=True)
class AuthContext:
    staff: StaffAccount
    session: StaffSession


def staff_view(staff: StaffAccount) -> StaffView:
    return StaffView(id=staff.id, email=staff.email, name=staff.name, roles=staff.roles)


def same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    parsed = urlsplit(origin)
    host = request.headers.get("host", "").lower()
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != host:
        raise HTTPException(status_code=403, detail="Cross-origin action denied.")


def monitor_roster_access(db: Session, staff: StaffAccount, event_id: str | None) -> bool:
    """Require monitor-eligible roster status when a roster identity exists.

    Organisers retain admin privileges. Manually provisioned staff without a
    matching roster record remain supported for legacy operational roles;
    self-registered monitors always have an approved roster entry.
    """
    if "organiser" in staff.roles or "monitor" not in staff.roles:
        return True
    rows = list(db.scalars(select(Person).where(
        Person.email == staff.email,
        Person.event_id == (event_id or DEFAULT_EVENT_ID),
    )))
    if not rows:
        return True
    return any(
        p.kind == "monitor" and p.active
        and (p.source_fields or {}).get(
            "monitor_registration_approved",
            (p.source_fields or {}).get("source") != "supervisely_member_sync",
        ) is True
        and (p.membership_status != "joined" or p.supervisely_role == "manager")
        for p in rows
    )


def get_context(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    token = request.cookies.get("annotatiathon_session", "")
    if not token:
        raise HTTPException(status_code=401, detail="Sign in required.")
    session = db.get(StaffSession, token_digest(token))
    if session is None or session.expires_at <= int(time.time()):
        raise HTTPException(status_code=401, detail="Session expired.")
    staff = db.get(StaffAccount, session.staff_id)
    if staff is None or not staff.active:
        raise HTTPException(status_code=401, detail="Account unavailable.")
    if not monitor_roster_access(db, staff, session.event_id):
        raise HTTPException(status_code=403, detail="Monitor access is not approved for this event.")
    return AuthContext(staff=staff, session=session)


def require_csrf(
    request: Request, ctx: AuthContext = Depends(get_context)
) -> AuthContext:
    same_origin(request)
    provided = request.headers.get("x-csrf-token", "")
    if not provided or not secrets.compare_digest(provided, ctx.session.csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")
    return ctx


def requires(*roles: str, mutation: bool = False):
    def permission(
        ctx: AuthContext = Depends(require_csrf if mutation else get_context),
    ) -> AuthContext:
        if not any(role in ctx.staff.roles for role in roles):
            raise HTTPException(status_code=403, detail="Not authorised for this operation.")
        return ctx

    return permission


@router.post("/login", response_model=AuthView)
def login(payload: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    same_origin(request)
    staff = db.scalar(select(StaffAccount).where(StaffAccount.email == payload.email))
    now = int(time.time())
    if staff is None:
        password_matches(DUMMY_HASH, payload.password)
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    if not staff.active or (staff.locked_until and staff.locked_until > now):
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    if not password_matches(staff.password_hash, payload.password):
        staff.login_failures += 1
        if staff.login_failures >= 5:
            staff.locked_until = now + 15 * 60
            staff.login_failures = 0
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    staff.login_failures = 0
    staff.locked_until = None
    token = random_token()
    csrf = random_token()
    latest_event = db.scalar(select(Event).where(Event.status == "active").order_by(
        Event.created_at.desc(), Event.name.desc()
    ))
    if not monitor_roster_access(db, staff, latest_event.id if latest_event else DEFAULT_EVENT_ID):
        raise HTTPException(status_code=403, detail="Monitor access is not approved for this event.")
    db.add(StaffSession(
        id=token_digest(token), staff_id=staff.id,
        event_id=latest_event.id if latest_event is not None else DEFAULT_EVENT_ID,
        csrf_token=csrf,
        expires_at=now + SESSION_SECONDS,
    ))
    db.add(AuditEvent(staff_id=staff.id, action="staff.login", target_type="staff",
                      target_id=staff.id))
    db.commit()
    response.set_cookie(
        "annotatiathon_session", token, httponly=True,
        secure=get_settings().app_env == "production",
        samesite="lax", max_age=SESSION_SECONDS, path="/",
    )
    return AuthView(staff=staff_view(staff), csrf_token=csrf)


@router.get("/me", response_model=AuthView)
def me(ctx: AuthContext = Depends(get_context)):
    return AuthView(staff=staff_view(ctx.staff), csrf_token=ctx.session.csrf_token)


@router.post("/logout", status_code=204)
def logout(
    response: Response, ctx: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    db.delete(ctx.session)
    db.add(AuditEvent(staff_id=ctx.staff.id, action="staff.logout",
                      target_type="staff", target_id=ctx.staff.id))
    db.commit()
    response.delete_cookie("annotatiathon_session", path="/")
