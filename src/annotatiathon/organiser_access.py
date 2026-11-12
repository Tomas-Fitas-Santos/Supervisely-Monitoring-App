"""Database-backed Nightjar staff access, independent of event and Supervisely roles."""
from sqlalchemy.orm import Session

from .config import get_settings
from .models import ApprovedStaffEmail


def _emails(raw: str) -> set[str]:
    return {
        value.strip().lower()
        for value in raw.replace(";", ",").split(",")
        if value.strip()
    }


def configured_emails() -> dict[str, list[str]]:
    """One-time bootstrap inputs from private .env.live; never used by login."""
    settings = get_settings()
    admins = _emails(settings.nightjar_admin_emails)
    organisers = _emails(settings.nightjar_organiser_emails)
    return {
        **{email: ["organiser"] for email in organisers - admins},
        **{email: ["admin", "organiser"] for email in admins},
    }


def access_roles(db: Session, email: str) -> list[str]:
    """Only the persisted approval table grants app access."""
    entry = db.get(ApprovedStaffEmail, email.strip().lower())
    if entry is None or not entry.enabled:
        return []
    if entry.role == "admin":
        return ["admin", "organiser"]
    if entry.role == "organiser":
        return ["organiser"]
    return []


def is_restricted_mode() -> bool:
    return get_settings().app_env == "production"
