import logging
import uuid

from sqlalchemy import select, update

from .controller import sync
from .db import Batch, Lease, Team, now

log = logging.getLogger(__name__)


def poll_once(service, gateway, interval=60):
    owner = str(uuid.uuid4())
    with service.sessions.begin() as s:
        result = s.execute(update(Lease).where(Lease.name == 'poll', Lease.expires_at <= now())
                           .values(owner=owner, expires_at=now() + 300))
        if result.rowcount != 1:
            return False
    try:
        with service.sessions() as s:
            work = [(s.get(Team, b.team_id), b) for b in s.scalars(select(Batch).where(
                Batch.state.in_(['active', 'approved']))) if b.job_ids]
        for team, batch in work:
            with service.sessions.begin() as s:
                renewed = s.execute(update(Lease).where(Lease.name == 'poll', Lease.owner == owner)
                                    .values(expires_at=now() + 300))
                if renewed.rowcount != 1:
                    return False
            sync(service, gateway, team, batch, activity=batch.state == 'active')
        return True
    finally:
        with service.sessions.begin() as s:
            s.execute(update(Lease).where(Lease.name == 'poll', Lease.owner == owner)
                      .values(expires_at=now() + interval))


def poll_forever(service, gateway, stop, interval=60):
    while not stop.is_set():
        try:
            poll_once(service, gateway, interval)
        except Exception:
            log.warning('Polling cycle failed; retrying on the next interval.')
        stop.wait(interval)
