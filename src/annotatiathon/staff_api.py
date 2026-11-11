"""Authenticated staff-role editing, independent of the attendee roster."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import ROLES, AuthContext, requires, staff_view
from .db import get_db
from .models import AuditEvent, StaffAccount

router = APIRouter(prefix="/api/v1/staff", tags=["staff"])


class RolesChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: list[str] = Field(min_length=1, max_length=len(ROLES))


@router.get("")
def list_staff(
    _ctx: AuthContext = Depends(requires("organiser", "monitor")),
    db: Session = Depends(get_db),
):
    staff = db.scalars(select(StaffAccount).order_by(StaffAccount.name)).all()
    return {"items": [
        {**staff_view(person).model_dump(), "active": person.active} for person in staff
    ]}


@router.put("/{staff_id}/roles")
def update_staff_roles(
    staff_id: str,
    payload: RolesChange,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    if len(set(payload.roles)) != len(payload.roles) or any(
        role not in ROLES for role in payload.roles
    ):
        raise HTTPException(status_code=422, detail="Use distinct, supported staff roles.")
    # Serialize organiser role changes across requests to preserve an active organiser.
    db.execute(select(StaffAccount.id).order_by(StaffAccount.id).with_for_update()).all()
    person = db.get(StaffAccount, staff_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Staff account not found.")
    if not person.active:
        raise HTTPException(status_code=409, detail="Cannot change roles of a disabled account.")
    if "organiser" in person.roles and "organiser" not in payload.roles:
        active_staff = list(db.scalars(select(StaffAccount).where(
            StaffAccount.active.is_(True)
        )))
        if not any(
            other.id != person.id and "organiser" in other.roles for other in active_staff
        ):
            raise HTTPException(status_code=409, detail="At least one organiser must remain.")
    before = list(person.roles)
    person.roles = list(payload.roles)
    db.add(AuditEvent(
        staff_id=ctx.staff.id, action="staff.roles.update", target_type="staff",
        target_id=person.id, details={"previous": before, "new": person.roles},
    ))
    db.commit()
    return {**staff_view(person).model_dump(), "active": person.active}
