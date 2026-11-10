"""Non-secret organiser settings. Tokens are never accepted or returned by this API."""
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .credentials import encrypt_token
from .db import get_db
from .integration_settings import SuperviselyConnectionStatus, SuperviselySettings
from .models import AuditEvent, SuperviselyConfig, epoch
from .supervisely_client import NativeAPIError, read_team_snapshot

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
        credential_configured=bool(config and config.token_ciphertext),
        verified=bool(config and config.verified),
        last_checked_at=(datetime.fromtimestamp(
            config.last_verified_at, tz=UTC
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


class CredentialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_token: str = Field(min_length=8, max_length=4096)


@router.put("/supervisely/credential", response_model=SuperviselyConnectionStatus)
def save_supervisely_credential(
    payload: CredentialInput,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    config = db.get(SuperviselyConfig, 1)
    if config is None or not config.team_id:
        raise HTTPException(status_code=422, detail="Save the shared Supervisely Team ID first.")
    try:
        snapshot = read_team_snapshot(config, personal_token=payload.api_token)
        native = next(
            (member for member in snapshot.members if member.id == snapshot.account_id),
            None,
        )
        if native is None or native.role not in {"admin", "manager"}:
            raise HTTPException(
                status_code=403,
                detail="The organiser integration key requires an admin or manager of this team.",
            )
        ciphertext = encrypt_token(payload.api_token.strip())
    except NativeAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    config.token_ciphertext = ciphertext
    config.verified = True
    config.last_verified_at = epoch()
    config.last_error_code = None
    db.add(AuditEvent(
        staff_id=ctx.staff.id, action="settings.supervisely.credential.replace",
        target_type="supervisely_config", target_id="1",
        details={"team_id": config.team_id, "native_account_id": snapshot.account_id,
                 "secret_logged": False},
    ))
    db.commit()
    return as_status(config)


@router.delete("/supervisely/credential", response_model=SuperviselyConnectionStatus)
def revoke_supervisely_credential(
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    config = db.get(SuperviselyConfig, 1)
    if config is None:
        raise HTTPException(status_code=404, detail="No Supervisely integration configured.")
    config.token_ciphertext = None
    config.verified = False
    config.last_verified_at = None
    db.add(AuditEvent(
        staff_id=ctx.staff.id, action="settings.supervisely.credential.revoke",
        target_type="supervisely_config", target_id="1", details={},
    ))
    db.commit()
    return as_status(config)
