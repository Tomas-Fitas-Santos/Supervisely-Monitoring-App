from unittest.mock import Mock

from monitoring.db import Lease, now
from monitoring.worker import poll_once


def test_only_one_worker_polls_within_the_lease(service):
    gateway = Mock()
    assert poll_once(service, gateway)
    assert not poll_once(service, gateway)
    gateway.progress.assert_not_called()  # Locked tasks are not remote jobs.


def test_expired_worker_lease_is_recovered(service):
    with service.sessions.begin() as s:
        lease = s.get(Lease, 'poll')
        lease.owner, lease.expires_at = 'dead-worker', now() - 1
    assert poll_once(service, Mock())
