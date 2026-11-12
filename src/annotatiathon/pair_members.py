"""Uniform two-person pair lookup for participants and monitor-based trial teams."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MonitorPairMember, Person


def pair_members(db: Session, event_id: str, team_id: str) -> tuple[list[Person], str]:
    participants = list(db.scalars(select(Person).where(
        Person.event_id == event_id,
        Person.team_id == team_id,
        Person.kind == "participant",
        Person.active.is_(True),
    ).order_by(Person.id)))
    monitor_rows = list(db.scalars(select(Person).join(
        MonitorPairMember, MonitorPairMember.person_id == Person.id
    ).where(
        MonitorPairMember.event_id == event_id,
        MonitorPairMember.team_id == team_id,
        MonitorPairMember.active.is_(True),
        Person.event_id == event_id,
        Person.kind == "monitor",
        Person.active.is_(True),
    ).order_by(Person.id)))
    # Competition teams may have one or two participants.
    if 1 <= len(participants) <= 2 and not monitor_rows:
        return participants, "participants"
    if len(monitor_rows) == 2 and not participants:
        return monitor_rows, "monitor_pair"
    return [], "invalid"
