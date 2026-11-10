"""Non-secret organiser settings. Tokens are never accepted or returned by this API."""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .config import get_settings
from .db import get_db
from .integration_settings import SuperviselyConnectionStatus, SuperviselySettings
from .models import AuditEvent, SuperviselyConfig

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


class SettingsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supervisely: SuperviselySettings


def as_status(config: SuperviselyConfig | None) -> SuperviselyConnectionStatus:
    settings: dict[str, Any] = {}
    if config:
        settings = {"server_url": config.server_url, "team_id": config.team_id,
                    "workspace_id": config.workspace_id}
    return SuperviselyConnectionStatus(
        settings=SuperviselySettings(**settings),
        credential_configured=bool(get_settings().supervisely_api_token),
        verified=bool(config and config.verified),
        last_checked_at=(datetime.fromtimestamp(
            config.last_verified_at, tz=timezone.utc
        ).isoformat() if config and config.last_verified_at else None),
        last_error_code=config.last_error_code if config else None,
    )


@router.get("/supervisely", response_model=SuperviselyConnectionStatus)
def get_supervisely_settings(
    _ctx: AuthContext = Depends(requires("organiser")),
    db: Session = Depends(get_db),
):
    return as_status(db.get(SuperviselyConfig, 1))


@router.put("/supervisely", response_model=SuperviselyConnectionStatus)
def put_supervisely_settings(
    payload: SettingsChange,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    config = db.get(SuperviselyConfig, 1)
    if config is None:
        config = SuperviselyConfig(id=1)
        db.add(config)
    config.server_url = payload.supervisely.server_url
    config.team_id = payload.supervisely.team_id
    config.workspace_id = payload.supervisely.workspace_id
    # Never mark as verified just because a user entered IDs.
    config.verified = False
    config.last_verified_at = None
    config.last_error_code = None
    db.add(AuditEvent(staff_id=ctx.staff.id, action="settings.supervisely.update",
                      target_type="supervisely_config", target_id="1",
                      details={"team_id": config.team_id,
                               "workspace_id": config.workspace_id}))
    db.commit()
    return as_status(config)
