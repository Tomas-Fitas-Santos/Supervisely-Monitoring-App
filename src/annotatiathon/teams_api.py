"""Create and disband local two-monitor competition pairs, without role changes.

Teams may be disbanded only before labeling jobs are planned. Native annotations,
historical assignments and event audit records are never deleted.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import (
    AuditEvent,
    CompetitionTeam,
    LabelingJob,
    MonitorAssignment,
    MonitorPairMember,
    Person,
)
from .pair_members import pair_members

router = APIRouter(prefix="/api/v1/teams", tags=["monitor-based competition pairs"])


class MonitorPairInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    monitor_person_ids: tuple[str, str]

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A competition team name is required.")
        return cleaned


@router.get("/monitor-pair-availability")
def monitor_pair_availability(
    ctx: AuthContext = Depends(requires("organiser")),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    monitors = list(db.scalars(select(Person).where(
        Person.event_id == event.id,
        Person.active.is_(True), Person.kind == "monitor",
    ).order_by(Person.name, Person.email)))
    links = list(db.scalars(select(MonitorPairMember).join(
        CompetitionTeam, CompetitionTeam.id == MonitorPairMember.team_id,
    ).where(
        MonitorPairMember.event_id == event.id,
        MonitorPairMember.active.is_(True),
        CompetitionTeam.active.is_(True),
        CompetitionTeam.event_id == event.id,
    )))
    assigned = {row.person_id: row.team_id for row in links}
    return {"items": [{
        "id": p.id, "name": p.name, "email": p.email,
        "available": p.id not in assigned,
        "current_pair_team_id": assigned.get(p.id),
        "supervisely_role": p.supervisely_role,
        "membership_status": p.membership_status,
    } for p in monitors]}


@router.post("/monitor-pairs", status_code=201)
def create_monitor_pair(
    payload: MonitorPairInput,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    if payload.monitor_person_ids[0] == payload.monitor_person_ids[1]:
        raise HTTPException(status_code=422, detail="Choose two different monitors.")
    previous_name = db.scalar(select(CompetitionTeam.id).where(
        CompetitionTeam.event_id == event.id,
        CompetitionTeam.name == payload.name,
    ))
    if previous_name:
        raise HTTPException(status_code=409, detail="Competition team name already exists.")
    people = list(db.scalars(select(Person).where(
        Person.event_id == event.id,
        Person.id.in_(payload.monitor_person_ids),
        Person.kind == "monitor", Person.active.is_(True),
    )))
    if len(people) != 2:
        raise HTTPException(
            status_code=422, detail="Choose two active monitors from the event roster."
        )
    used = db.scalar(select(MonitorPairMember.id).join(
        CompetitionTeam, CompetitionTeam.id == MonitorPairMember.team_id
    ).where(
        MonitorPairMember.event_id == event.id,
        MonitorPairMember.person_id.in_(payload.monitor_person_ids),
        MonitorPairMember.active.is_(True),
        CompetitionTeam.active.is_(True),
        CompetitionTeam.event_id == event.id,
    ))
    if used:
        raise HTTPException(
            status_code=409, detail="One monitor is already in an active trial pair."
        )
    team = CompetitionTeam(event_id=event.id, name=payload.name, active=True)
    db.add(team)
    db.flush()
    for person in people:
        db.add(MonitorPairMember(
            event_id=event.id, team_id=team.id, person_id=person.id,
            active=True,
        ))
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="team.monitor_pair.create", target_type="competition_team",
        target_id=team.id,
        details={"monitor_ids": sorted(payload.monitor_person_ids),
                 "native_role_mutations": False, "members": 2},
    ))
    db.commit()
    return {
        "id": team.id, "name": team.name, "active": True,
        "member_ids": sorted(payload.monitor_person_ids),
        "native_mutations": False,
    }


@router.post("/monitor-pairs/{team_id}/disband")
def disband_monitor_pair(
    team_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    team = db.scalar(select(CompetitionTeam).where(
        CompetitionTeam.event_id == event.id,
        CompetitionTeam.id == team_id,
    ).with_for_update())
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found.")
    if not team.active:
        return {"id": team.id, "status": "disbanded", "already_disbanded": True}
    people, mode = pair_members(db, event.id, team.id)
    if mode != "monitor_pair":
        raise HTTPException(status_code=409, detail="This is not a two-monitor trial team.")
    has_jobs = db.scalar(select(LabelingJob.id).where(
        LabelingJob.event_id == event.id,
        LabelingJob.team_id == team.id,
    ))
    if has_jobs:
        raise HTTPException(
            status_code=409,
            detail="This pair already has competition jobs. Finish or archive the event "
                   "to preserve annotation provenance; disbanding would break job links.",
        )
    links = list(db.scalars(select(MonitorPairMember).where(
        MonitorPairMember.event_id == event.id,
        MonitorPairMember.team_id == team.id,
        MonitorPairMember.active.is_(True),
    )))
    for link in links:
        link.active = False
    for item in db.scalars(select(MonitorAssignment).where(
        MonitorAssignment.event_id == event.id,
        MonitorAssignment.team_id == team.id,
        MonitorAssignment.active.is_(True),
    )):
        item.active = False
    team.active = False
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="team.monitor_pair.disband", target_type="competition_team",
        target_id=team.id,
        details={"monitor_ids": [p.id for p in people],
                 "native_mutations": False, "historical_records_preserved": True},
    ))
    db.commit()
    return {"id": team.id, "status": "disbanded", "already_disbanded": False,
            "native_mutations": False}
