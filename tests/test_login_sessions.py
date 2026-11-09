from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, select

from monitoring.connection import LocalConnection
from monitoring.db import Batch, EventConfig, EventMember, LoginSession, Team, now
from monitoring.service import WorkflowError


@pytest.fixture
def accounts(service, tmp_path, monkeypatch):
    import supervisely as sly
    with service.sessions.begin() as s:
        s.execute(delete(Batch))
        s.execute(delete(Team))
    apis = {}
    for uid, login, token in [(99, 'admin', 'admin-secret'), (90, 'monitor', 'monitor-secret'), (91, 'other', 'other-secret')]:
        api = Mock(server_address='https://app.supervisely.com')
        api.user.get_my_info.return_value = NS(id=uid, login=login)
        api.user.get_member_info_by_id.return_value = NS(role='admin' if uid == 99 else 'manager', disabled=False)
        apis[token] = api
    monkeypatch.setattr(sly, 'Api', lambda server, token: apis[token])
    local = LocalConnection(service.sessions, tmp_path)
    admin, _ = local.login('https://app.supervisely.com', 'admin-secret', 99)
    with local.sessions.begin() as s:
        s.get(EventConfig, 1).monitoring_team_id = s.get(EventConfig, 1).annotator_team_id = 10
        s.add(EventMember(user_id=90, login='monitor', name='Monitor', group='monitors'))
    return local, apis, admin


def test_monitors_have_independent_logins_and_never_replace_admin_polling_token(accounts):
    local, apis, admin = accounts
    monitor, _ = local.login('https://app.supervisely.com', 'monitor-secret', 90)
    assert local.session_api(admin) == (apis['admin-secret'], 99)
    assert local.session_api(monitor) == (apis['monitor-secret'], 90)
    assert local.api() is apis['admin-secret']
    with local.sessions() as s:
        assert s.get(EventConfig, 1).owner_id == 99
        records = list(s.scalars(select(LoginSession)))
        assert all(r.id not in (admin, monitor) and '-secret' not in r.token for r in records)
    local.logout(monitor)
    with pytest.raises(WorkflowError, match='Sign in'):
        local.session_api(monitor)
    assert local.session_api(admin)[1] == 99


def test_expired_forged_or_wrong_identity_sessions_are_rejected(accounts):
    local, _, admin = accounts
    with pytest.raises(WorkflowError, match='another Supervisely user'):
        local.login('https://app.supervisely.com', 'monitor-secret', 99)
    assert local.session('forged') is None
    with local.sessions.begin() as s:
        s.get(LoginSession, local.session_id(admin)).expires_at = now() - 1
    with pytest.raises(WorkflowError, match='Sign in'):
        local.session_api(admin)


def test_unregistered_native_admin_cannot_sign_in_as_event_staff(accounts):
    local, apis, _ = accounts
    apis['other-secret'].user.get_member_info_by_id.return_value = NS(role='admin', disabled=False)
    with pytest.raises(WorkflowError, match='approved as a monitor'):
        local.login('https://app.supervisely.com', 'other-secret', 91)
