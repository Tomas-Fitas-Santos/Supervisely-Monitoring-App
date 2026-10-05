import importlib
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from monitoring.service import WorkflowError
from monitoring.db import EventConfig, EventMember, Team, database, initialize
from monitoring.service import Service
from monitoring.setup import Setup
from monitoring.connection import LocalConnection


@pytest.fixture
def ui(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCAL_DEVELOPMENT', 'true')
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path}/ui.db')
    monkeypatch.setenv('MONITORING_TEAM_ID', '10')
    monkeypatch.setenv('LOCAL_APP_HOME', str(tmp_path))
    module = importlib.import_module('monitoring.ui')
    engine, sessions = database(f'sqlite:///{tmp_path}/ui.db')
    initialize(engine)
    monkeypatch.setattr(module, 'sessions', sessions)
    monkeypatch.setattr(module, 'service', Service(sessions))
    monkeypatch.setattr(module, 'setup', Setup(sessions, 10))
    monkeypatch.setattr(module, 'connection', LocalConnection(sessions, tmp_path))
    monkeypatch.setattr(module, 'settings', replace(module.settings, local=False, home=tmp_path))
    return module


def test_missing_request_token_cannot_reuse_cached_user_api(ui):
    with pytest.raises(WorkflowError, match='Open this app'):
        ui.identity(NS(state=NS(api=None)))


def test_context_user_must_match_credential_owner(ui, monkeypatch):
    api = Mock(server_address=ui.settings.server_address)
    api.user.get_my_info.return_value = NS(id=90)
    monkeypatch.setattr(ui.sly.env, 'user_from_multiuser_app', lambda: 91)
    with pytest.raises(WorkflowError, match='do not match'):
        ui.identity(NS(state=NS(api=api)))


def test_monitoring_membership_is_required(ui, monkeypatch):
    api = Mock(server_address=ui.settings.server_address)
    api.user.get_my_info.return_value = NS(id=90)
    api.user.get_member_info_by_id.return_value = None
    monkeypatch.setattr(ui.sly.env, 'user_from_multiuser_app', lambda: 90)
    with pytest.raises(WorkflowError, match='member of the monitoring team'):
        ui.identity(NS(state=NS(api=api)))


def test_foreign_server_credential_is_rejected(ui, monkeypatch):
    api = Mock(server_address='https://another.example')
    monkeypatch.setattr(ui.sly.env, 'user_from_multiuser_app', lambda: 90)
    with pytest.raises(WorkflowError, match='different Supervisely server'):
        ui.identity(NS(state=NS(api=api)))


def test_live_local_identity_uses_token_owner_instead_of_demo_id(ui, monkeypatch):
    monkeypatch.setattr(ui, 'settings', replace(ui.settings, local=True))
    monkeypatch.setenv('API_TOKEN', 'test-token')
    monkeypatch.setenv('LOCAL_USER_ID', '900')
    api = Mock(server_address=ui.settings.server_address)
    api.user.get_my_info.return_value = NS(id=90)
    api.user.get_member_info_by_id.return_value = NS(id=90, role='admin')
    monkeypatch.setattr(ui.sly.Api, 'from_env', lambda: api)
    uid, gateway = ui.identity(NS(client=NS(host='127.0.0.1')))
    assert uid == 90
    assert gateway.api is api
    api.user.get_member_info_by_id.assert_called_once_with(10, 90)


def test_setup_endpoint_denies_non_admin_before_loading_roster(ui, monkeypatch):
    api = Mock()
    api.user.get_member_info_by_id.return_value = NS(id=90, role='manager')
    monkeypatch.setattr(ui, 'identity', lambda request: (90, NS(api=api)))
    monkeypatch.setattr(ui.dashboard, 'display', Mock())
    catalog = Mock()
    monkeypatch.setattr(ui.SetupGateway, 'catalog', catalog)
    snapshot = Mock()
    monkeypatch.setattr(ui.setup, 'snapshot', snapshot)
    assert ui.setup_action(NS()) == {'ok': False}
    catalog.assert_not_called()
    snapshot.assert_not_called()
    api.team.create.assert_not_called()


def test_action_payload_is_read_from_current_request_not_shared_tab_state(ui):
    first = NS(state=NS(state={'dashboard': {'action': 'review', 'team_id': 1}}))
    second = NS(state=NS(state={'dashboard': {'action': 'release', 'team_id': 2}}))
    assert ui.request_form(first)['team_id'] == 1
    assert ui.request_form(second)['team_id'] == 2
    assert ui.request_form(first)['action'] == 'review'
    with pytest.raises(WorkflowError, match='Missing dashboard'):
        ui.request_form(NS(state=NS(state=None)))


@pytest.mark.parametrize('native_role', ['manager', 'annotator', 'admin'])
def test_native_membership_does_not_bypass_event_monitor_group(ui, monkeypatch, native_role):
    with ui.sessions.begin() as s:
        c = s.get(EventConfig, 1)
        c.owner_id, c.monitoring_team_id, c.annotator_team_id = 99, 10, 20
        s.add(EventMember(user_id=90, group='annotators', login='alice', name='Alice'))
    api = Mock(server_address=ui.settings.server_address)
    api.user.get_my_info.return_value = NS(id=90)
    api.user.get_member_info_by_id.return_value = NS(role=native_role)
    monkeypatch.setattr(ui.sly.env, 'user_from_multiuser_app', lambda: 90)
    with pytest.raises(WorkflowError, match='Monitors group'):
        ui.identity(NS(state=NS(api=api)))
    with ui.sessions.begin() as s:
        s.get(EventMember, 90).group = 'monitors'
    assert ui.identity(NS(state=NS(api=api)))[0] == 90


def test_fresh_hosted_event_claim_requires_launch_team_admin(ui, monkeypatch):
    monkeypatch.setattr(ui, 'setup', Setup(ui.sessions, 0))
    monkeypatch.setattr(ui, 'settings', replace(ui.settings, launch_team_id=10))
    api = Mock(server_address=ui.settings.server_address)
    api.user.get_my_info.return_value = NS(id=90, login='organiser')
    api.user.get_member_info_by_id.return_value = NS(role='manager')
    monkeypatch.setattr(ui.sly.env, 'user_from_multiuser_app', lambda: 90)
    with pytest.raises(WorkflowError, match='Admin'):
        ui.identity(NS(state=NS(api=api)))
    api.user.get_member_info_by_id.return_value = NS(role='admin')
    assert ui.identity(NS(state=NS(api=api)))[0] == 90
    c = ui.event_config()
    assert c.owner_id == 90 and c.local_token is None
    api.user.get_my_info.return_value = NS(id=91, login='other')
    monkeypatch.setattr(ui.sly.env, 'user_from_multiuser_app', lambda: 91)
    with pytest.raises(WorkflowError, match='Only the organiser'):
        ui.identity(NS(state=NS(api=api)))


def test_connection_endpoint_is_local_only_and_rejects_foreign_origin(ui, monkeypatch):
    import asyncio
    request = NS(client=NS(host='127.0.0.1'), headers={}, base_url='http://127.0.0.1:8000/')
    assert asyncio.run(ui.connect(request)).status_code == 403

    monkeypatch.setattr(ui, 'settings', replace(ui.settings, local=True))
    request.headers['origin'] = 'https://foreign.example'
    assert asyncio.run(ui.connect(request)).status_code == 403


def test_monitors_can_read_directory_but_not_setup_operations(ui, monkeypatch):
    import supervisely.app
    data = {'dashboard': {'review_preview': None}}
    monkeypatch.setattr(supervisely.app, 'DataJson', lambda: data)
    monkeypatch.setattr(ui.dashboard, 'display', Mock())
    with ui.sessions.begin() as s:
        c = s.get(EventConfig, 1)
        c.owner_id, c.monitoring_team_id, c.annotator_team_id = 99, 10, 20
        s.add(EventMember(user_id=90, group='monitors', login='monitor', name='Monitor'))
        s.add(Team(id=101, name='Other team', monitor_id=91, annotator_ids=[11, 12]))
    api = Mock()
    api.user.get_member_info_by_id.return_value = NS(role='manager')
    api.workspace.get_list.return_value = []
    monkeypatch.setattr(ui, 'identity', lambda request: (90, NS(api=api)))
    request = NS(state=NS(state={'dashboard': {'setup_action': 'overview'}}))
    assert ui.setup_action(request) == {'ok': True}
    assert data['dashboard']['directory']['teams'][0]['name'] == 'Other team'
    assert data['dashboard']['can_setup'] is False
    assert data['dashboard']['setup'] == {} and data['dashboard']['catalog'] == {}
    assert ui.service.snapshot(90)['teams'] == []
    request.state.state['dashboard']['setup_action'] = 'unassign'
    assert ui.setup_action(request) == {'ok': False}
    with ui.sessions() as s:
        assert s.get(Team, 101).monitor_id == 91


def test_preview_requires_own_assignment_before_remote_download(ui, monkeypatch):
    import supervisely.app
    from monitoring.db import Batch
    data = {'dashboard': {'review_preview': None}}
    monkeypatch.setattr(supervisely.app, 'DataJson', lambda: data)
    monkeypatch.setattr(ui.dashboard, 'display', Mock())
    with ui.sessions.begin() as s:
        s.add(Team(id=101, name='Other team', monitor_id=91, annotator_ids=[11, 12]))
        s.flush()
        s.add(Batch(id='other', team_id=101, position=1, kind='images', dataset_id=50,
                    assets=[{'entity_id': 501, 'source_id': 'image'}]))
    gateway = Mock()
    monkeypatch.setattr(ui, 'identity', lambda request: (90, gateway))
    ui.action(NS(state=NS(state={'dashboard': {'action': 'preview_media', 'team_id': 101, 'batch_id': 'other',
                                              'entity_id': 501, 'frame_index': -1}})))
    gateway.review_preview.assert_not_called()


def test_removed_assignment_clears_cached_media(ui, monkeypatch):
    import supervisely.app
    data = {'dashboard': {'review_preview': {'team_id': 101, 'annotated': 'private-media'}}}
    monkeypatch.setattr(supervisely.app, 'DataJson', lambda: data)
    with ui.sessions.begin() as s:
        s.add(Team(id=101, name='Now assigned elsewhere', monitor_id=91, annotator_ids=[11, 12]))
    api = Mock()
    api.user.get_member_info_by_id.return_value = NS(role='manager')
    ui.display_access(90, NS(api=api))
    assert data['dashboard']['review_preview'] is None
