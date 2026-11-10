"""Manual and balanced event-local monitor-to-team responsibility assignments.

All operations are local Nightjar logistics, not Supervisely role/job mutations.
"""
import hashlib
import json
from collections import Counter
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import (
    AuditEvent,
    CompetitionTeam,
    MonitorAssignment,
    MonitorAssignmentPlan,
    Person,
    epoch,
)

router = APIRouter(prefix="/api/v1/monitoring", tags=["monitor assignments"])
VIEW_ROLES = ("organiser", "monitor", "reception", "activity", "distribution", "observer")


def state(db: Session, event_id: str) -> tuple[list[CompetitionTeam], list[Person], dict[str, MonitorAssignment]]:
    teams = list(db.scalars(select(CompetitionTeam).where(
        CompetitionTeam.event_id == event_id, CompetitionTeam.active.is_(True),
    ).order_by(CompetitionTeam.name, CompetitionTeam.id)))
    monitors = list(db.scalars(select(Person).where(
        Person.event_id == event_id, Person.active.is_(True),
        Person.kind == "monitor",
    ).order_by(Person.name, Person.id)))
    assignments = {
        a.team_id: a for a in db.scalars(select(MonitorAssignment).where(
            MonitorAssignment.event_id == event_id, MonitorAssignment.active.is_(True),
        ))
    }
    return teams, monitors, assignments


def snapshot_digest(
    teams: list[CompetitionTeam], monitors: list[Person],
    assignments: dict[str, MonitorAssignment],
) -> str:
    record = {
        "teams": [(t.id, t.name) for t in teams],
        "monitors": [(p.id, p.name) for p in monitors],
        "assignments": sorted(
            (tid, a.monitor_person_id) for tid, a in assignments.items()
        ),
    }
    return hashlib.sha256(json.dumps(
        record, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def overview(
    teams: list[CompetitionTeam], monitors: list[Person],
    assignments: dict[str, MonitorAssignment],
) -> dict:
    monitor_names = {p.id: p.name for p in monitors}
    rows = []
    count: Counter[str] = Counter()
    for team in teams:
        assignment = assignments.get(team.id)
        assigned_to = (
            assignment.monitor_person_id if assignment and
            assignment.monitor_person_id in monitor_names else None
        )
        if assigned_to:
            count[assigned_to] += 1
        rows.append({
            "team_id": team.id, "team_name": team.name,
            "monitor_person_id": assigned_to,
            "monitor_name": monitor_names.get(assigned_to) if assigned_to else None,
        })
    return {
        "teams": rows,
        "monitors": [{
            "id": p.id, "name": p.name, "email": p.email,
            "assigned_count": count[p.id],
        } for p in monitors],
        "total_teams": len(rows),
        "assigned_teams": sum(1 for row in rows if row["monitor_person_id"]),
        "unassigned_teams": sum(1 for row in rows if not row["monitor_person_id"]),
    }


@router.get("/assignments")
def list_assignments(
    ctx: AuthContext = Depends(requires(*VIEW_ROLES)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    return overview(*state(db, event.id))


class AssignmentChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monitor_person_id: str | None


@router.put("/teams/{team_id}")
def assign_team(
    team_id: str, payload: AssignmentChange,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    team = db.scalar(select(CompetitionTeam).where(
        CompetitionTeam.id == team_id, CompetitionTeam.event_id == event.id,
        CompetitionTeam.active.is_(True),
    ))
    if not team:
        raise HTTPException(status_code=404, detail="Active competition team not found.")
    if payload.monitor_person_id:
        monitor = db.scalar(select(Person).where(
            Person.id == payload.monitor_person_id,
            Person.event_id == event.id, Person.active.is_(True),
            Person.kind == "monitor",
        ))
        if monitor is None:
            raise HTTPException(status_code=422, detail="Choose an active event monitor.")
    existing = db.scalar(select(MonitorAssignment).where(
        MonitorAssignment.team_id == team.id,
        MonitorAssignment.event_id == event.id,
    ).with_for_update())
    before = existing.monitor_person_id if existing and existing.active else None
    if before == payload.monitor_person_id:
        return {
            "team_id": team.id, "monitor_person_id": before, "changed": False
        }
    if payload.monitor_person_id is None:
        if existing:
            existing.active = False
    elif existing:
        existing.monitor_person_id = payload.monitor_person_id
        existing.active = True
        existing.assigned_by = ctx.staff.id
    else:
        db.add(MonitorAssignment(
            event_id=event.id, team_id=team.id,
            monitor_person_id=payload.monitor_person_id,
            assigned_by=ctx.staff.id,
        ))
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="monitoring.assignment.manual",
        target_type="competition_team", target_id=team.id,
        details={"previous_monitor_id": before,
                 "new_monitor_id": payload.monitor_person_id},
    ))
    db.commit()
    return {
        "team_id": team.id, "monitor_person_id": payload.monitor_person_id,
        "changed": True,
    }


class BalanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["fill_unassigned", "rebalance_all"] = "fill_unassigned"


@router.post("/assignments/preview")
def preview_balanced(
    payload: BalanceRequest,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    teams, monitors, assignments = state(db, event.id)
    if teams and not monitors:
        raise HTTPException(
            status_code=422,
            detail="Register an active event monitor before allocating competition teams.",
        )
    ids = {p.id: p.name for p in monitors}
    valid_previous = {
        t.id: assignments[t.id].monitor_person_id
        if t.id in assignments and assignments[t.id].monitor_person_id in ids else None
        for t in teams
    }
    counts: Counter[str] = Counter()
    if payload.mode == "fill_unassigned":
        for previous in valid_previous.values():
            if previous:
                counts[previous] += 1
    proposals = []
    for team in teams:
        previous = valid_previous[team.id]
        if previous and payload.mode == "fill_unassigned":
            continue
        chosen = min(monitors, key=lambda m: (counts[m.id], m.name.casefold(), m.id))
        counts[chosen.id] += 1
        if previous != chosen.id:
            proposals.append({
                "team_id": team.id, "team_name": team.name,
                "previous_monitor_id": previous,
                "new_monitor_id": chosen.id, "new_monitor_name": chosen.name,
            })
    plan = MonitorAssignmentPlan(
        event_id=event.id, staff_id=ctx.staff.id, mode=payload.mode,
        roster_digest=snapshot_digest(teams, monitors, assignments),
        proposals=proposals, expires_at=epoch() + 600,
    )
    db.add(plan)
    db.commit()
    return {
        "plan_id": plan.id, "mode": plan.mode,
        "expires_at": plan.expires_at, "total_teams": len(teams),
        "monitors": len(monitors), "changes": len(proposals),
        "proposals": proposals,
        "native_mutations": False,
    }


@router.post("/assignments/plans/{plan_id}/apply")
def apply_balanced(
    plan_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    plan = db.scalar(select(MonitorAssignmentPlan).where(
        MonitorAssignmentPlan.id == plan_id,
        MonitorAssignmentPlan.event_id == event.id,
        MonitorAssignmentPlan.staff_id == ctx.staff.id,
    ).with_for_update())
    if not plan:
        raise HTTPException(status_code=404, detail="Assignment plan not found.")
    if plan.applied_at:
        return {"applied": len(plan.proposals), "already_applied": True}
    if plan.expires_at < epoch():
        raise HTTPException(status_code=409, detail="Plan expired; preview again.")
    teams, monitors, assignments = state(db, event.id)
    if plan.roster_digest != snapshot_digest(teams, monitors, assignments):
        raise HTTPException(status_code=409, detail="Monitor/roster assignments changed; preview again.")
    active_monitors = {p.id for p in monitors}
    active_teams = {t.id for t in teams}
    for item in plan.proposals:
        if item["team_id"] not in active_teams or item["new_monitor_id"] not in active_monitors:
            raise HTTPException(status_code=409, detail="An assignment target was deactivated.")
        row = db.scalar(select(MonitorAssignment).where(
            MonitorAssignment.team_id == item["team_id"],
            MonitorAssignment.event_id == event.id,
        ).with_for_update())
        if row is None:
            db.add(MonitorAssignment(
                event_id=event.id, team_id=item["team_id"],
                monitor_person_id=item["new_monitor_id"], assigned_by=ctx.staff.id,
            ))
        else:
            row.monitor_person_id = item["new_monitor_id"]
            row.active = True
            row.assigned_by = ctx.staff.id
    plan.applied_at = epoch()
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="monitoring.assignment.balanced",
        target_type="monitor_assignment_plan", target_id=plan.id,
        details={"mode": plan.mode, "changes": len(plan.proposals),
                 "team_ids": [x["team_id"] for x in plan.proposals]},
    ))
    db.commit()
    return {"applied": len(plan.proposals), "already_applied": False,
            "native_mutations": False}
