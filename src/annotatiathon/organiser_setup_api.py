"""Self-registration for database-approved Nightjar Admin and Organiser emails.

No codes, email transport, identity checks against Supervisely or event rosters.
The first registration claims the approved address; subsequent attempts are denied.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import normalized_email, same_origin
from .db import get_db
from .models import AuditEvent, StaffAccount
from .organiser_access import access_roles, is_restricted_mode
from .security import password_hash

router = APIRouter(prefix="/api/v1/auth/setup", tags=["staff account setup"])


class ApprovedAccountRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name is required.")
        return value

    @field_validator("email")
    @classmethod
    def normalize(cls, value: str) -> str:
        return normalized_email(value)


@router.post("/register", status_code=201)
def create_approved_staff_account(
    payload: ApprovedAccountRegistration,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create the login once, without a code. Approval is stored in PostgreSQL."""
    same_origin(request)
    if not is_restricted_mode():
        raise HTTPException(status_code=403, detail="Use the locally configured staff setup.")
    roles = access_roles(db, payload.email)
    if not roles:
        raise HTTPException(status_code=403, detail="This email is not approved for Nightjar.")
    if db.scalar(select(StaffAccount.id).where(StaffAccount.email == payload.email)):
        raise HTTPException(status_code=409, detail="Account already exists. Sign in instead.")
    staff = StaffAccount(
        email=payload.email, name=payload.name, password_hash=password_hash(payload.password),
        roles=roles, active=True,
    )
    db.add(staff)
    try:
        db.flush()
        db.add(AuditEvent(
            staff_id=staff.id,
            action="staff.account.self_registered",
            target_type="staff", target_id=staff.id,
            details={"roles": roles, "approved_in_database": True,
                     "email_ownership_verified": False},
        ))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Account already exists. Sign in.") from exc
    return {"registered": True, "message": "Nightjar account created. You can sign in."}
