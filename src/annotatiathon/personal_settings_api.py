"""Individual staff Supervisely account verification and shared team settings.

The organiser owns one shared team/workspace configuration. Every monitor
may verify their own personal native identity using an API token; tokens
are checked on the server, then discarded and never persisted.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .models import AuditEvent, StaffAccount, SuperviselyConfig, epoch
from .supervisely_client import NativeAPIError, read_team_snapshot

router = APIRouter(prefix="/api/v1/settings/supervisely", tags=["staff Supervisely identity"])
STAFF_VIEWERS = ("organiser", "monitor", "reception", "activity", "distribution", "observer")


def shared_team(db: Session) -> SuperviselyConfig | None:
    return db.get(SuperviselyConfig, 1)


@router.get("/shared")
def get_shared_team_settings(
    _ctx: AuthContext = Depends(requires(*STAFF_VIEWERS)),
    db: Session = Depends(get_db),
):
    config = shared_team(db)
    return {
        "server_url": config.server_url if config else "https://app.supervisely.com",
        "team_id": config.team_id if config else None,
        "workspace_id": config.workspace_id if config else None,
        "verified": bool(config and config.verified),
        "last_verified_at": config.last_verified_at if config else None,
        "editable": "organiser" in _ctx.staff.roles,
    }


def link_view(staff: StaffAccount, config: SuperviselyConfig | None) -> dict:
    return {
        "linked": staff.supervisely_user_id is not None,
        "user_id": staff.supervisely_user_id,
        "login": staff.supervisely_login,
        "role": staff.supervisely_role,
        "verified_team_id": staff.supervisely_verified_team_id,
        "verified_at": staff.supervisely_verified_at,
        "matches_current_team": bool(
            config and config.team_id and
            config.team_id == staff.supervisely_verified_team_id
        ),
    }


@router.get("/personal")
def get_personal_link(
    ctx: AuthContext = Depends(requires(*STAFF_VIEWERS)),
    db: Session = Depends(get_db),
):
    return link_view(ctx.staff, shared_team(db))


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_token: str = Field(min_length=8, max_length=4096)


@router.post("/personal")
def link_personal_account(
    payload: LinkRequest,
    ctx: AuthContext = Depends(requires("organiser", "monitor", mutation=True)),
    db: Session = Depends(get_db),
):
    config = shared_team(db)
    if not config or not config.team_id:
        raise HTTPException(
            status_code=422,
            detail="An organiser must configure the shared Supervisely Team ID first.",
        )
    try:
        # Verifies token ownership via users.me and current membership via members.list.
        source = read_team_snapshot(config, personal_token=payload.api_token)
    except NativeAPIError as exc:
        raise HTTPException(
            status_code=422 if exc.code in {"untrusted_host", "missing_token"} else 502,
            detail=str(exc),
        ) from exc
    own_member = next(
        (member for member in source.members if member.id == source.account_id),
        None,
    )
    if own_member is None:
        raise HTTPException(status_code=403, detail="This account is not in the shared team.")
    if own_member.role not in {"admin", "manager", "developer"}:
        raise HTTPException(
            status_code=403,
            detail="This account must have a Supervisely monitoring/manager role.",
        )
    other = db.scalar(select(StaffAccount).where(
        StaffAccount.id != ctx.staff.id,
        StaffAccount.supervisely_user_id == source.account_id,
        StaffAccount.supervisely_verified_team_id == config.team_id,
    ))
    if other:
        raise HTTPException(
            status_code=409,
            detail="This Supervisely account is already linked to a different staff login.",
        )
    # Do not retain this API token; local login and event-wide integration are independent.
    ctx.staff.supervisely_user_id = source.account_id
    ctx.staff.supervisely_login = source.account_login
    ctx.staff.supervisely_role = own_member.role
    ctx.staff.supervisely_verified_team_id = config.team_id
    ctx.staff.supervisely_verified_at = epoch()
    db.add(AuditEvent(
        staff_id=ctx.staff.id, action="staff.supervisely.link",
        target_type="staff", target_id=ctx.staff.id,
        details={"team_id": config.team_id, "native_id": source.account_id,
                 "native_role": own_member.role, "token_stored": False},
    ))
    db.commit()
    return link_view(ctx.staff, config)


@router.delete("/personal")
def unlink_personal_account(
    ctx: AuthContext = Depends(requires("organiser", "monitor", mutation=True)),
    db: Session = Depends(get_db),
):
    ctx.staff.supervisely_user_id = None
    ctx.staff.supervisely_login = None
    ctx.staff.supervisely_role = None
    ctx.staff.supervisely_verified_team_id = None
    ctx.staff.supervisely_verified_at = None
    db.add(AuditEvent(
        staff_id=ctx.staff.id, action="staff.supervisely.unlink",
        target_type="staff", target_id=ctx.staff.id, details={"token_stored": False},
    ))
    db.commit()
    return {"linked": False}
