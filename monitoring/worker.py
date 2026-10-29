import logging
import uuid

from sqlalchemy import select, update

from .controller import sync
from .db import Batch, Lease, Team, now

log = logging.getLogger(__name__)


def poll_once(service, gateway, interval=60, user_id=None):
    owner = str(uuid.uuid4())
    lease_name = 'poll' if user_id is None else f'poll:{user_id}'
    if user_id is not None:
        with service.sessions.begin() as s:
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert
            insert = sqlite_insert if s.bind.dialect.name == 'sqlite' else pg_insert
            s.execute(insert(Lease).values(name=lease_name, expires_at=0, owner='').on_conflict_do_nothing(index_elements=['name']))
    with service.sessions.begin() as s:
        result = s.execute(update(Lease).where(Lease.name == lease_name, Lease.expires_at <= now())
                           .values(owner=owner, expires_at=now() + 300))
        if result.rowcount != 1:
            return False
    try:
        with service.sessions() as s:
            from .db import SetupState
            if s.get(SetupState, 1).operation_id:
                return False
            work = [(s.get(Team, b.team_id), b) for b in s.scalars(select(Batch).where(
                Batch.state.in_(['active', 'approved']))) if b.job_ids
                and (user_id is None or s.get(Team, b.team_id).monitor_id == user_id)]
        for team, batch in work:
            with service.sessions.begin() as s:
                if s.get(SetupState, 1).operation_id:
                    return False
                renewed = s.execute(update(Lease).where(Lease.name == lease_name, Lease.owner == owner)
                                    .values(expires_at=now() + 300))
                if renewed.rowcount != 1:
                    return False
            sync(service, gateway, team, batch, activity=batch.state == 'active')
            from .competition import Competition
            with service.sessions() as s:
                current = s.get(Batch, batch.id)
                capturing_team = s.get(Team, team.id)
                submitted = all(v in ('on_review', 'completed', 'review_completed') for v in current.remote_status.split(','))
                uncaptured = Competition(service.sessions).latest(s, batch.id) is None
            if submitted and (uncaptured or current.remote_status != batch.remote_status) and hasattr(gateway, 'capture_annotations'):
                Competition(service.sessions).refresh(gateway, capturing_team, current)
        from .competition import Competition
        from .db import EventConfig, EventClosure
        configuration = Competition(service.sessions).config()
        deadline = configuration['rules']['deadline']
        with service.sessions() as s:
            config = s.get(EventConfig, 1)
            closed = s.get(EventClosure, 1) is not None
        if user_id is None and configuration['frozen'] and deadline and now() >= deadline and not closed:
            from .event_actions import close_event
            from .setup import Setup
            close_event(service, Setup(service.sessions, config.monitoring_team_id), gateway, config.owner_id or 0)
        return True
    finally:
        with service.sessions.begin() as s:
            s.execute(update(Lease).where(Lease.name == lease_name, Lease.owner == owner)
                      .values(expires_at=now() + interval))


def poll_forever(service, gateway, stop, interval=60):
    while not stop.is_set():
        try:
            poll_once(service, gateway, interval)
        except Exception:
            log.warning('Polling cycle failed; retrying on the next interval.')
        stop.wait(interval)
