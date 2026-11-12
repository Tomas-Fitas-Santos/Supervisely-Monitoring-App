"""Nightjar login allowlist, independent of all event and Supervisely roles.

Set the nine approved addresses in the private .env.live file, not the public repo.
"""
from .config import get_settings


def _emails(raw: str) -> set[str]:
    return {value.strip().lower() for value in raw.replace(";", ",").split(",") if value.strip()}


def configured_emails() -> dict[str, list[str]]:
    settings = get_settings()
    admins = _emails(settings.nightjar_admin_emails)
    organisers = _emails(settings.nightjar_organiser_emails)
    return {
        **{email: ["organiser"] for email in organisers - admins},
        **{email: ["admin", "organiser"] for email in admins},
    }


def access_roles(email: str) -> list[str]:
    return configured_emails().get(email.strip().lower(), [])


def is_restricted_mode() -> bool:
    return get_settings().app_env == "production"
