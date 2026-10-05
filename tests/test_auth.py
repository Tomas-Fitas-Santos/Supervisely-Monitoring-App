import importlib
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from monitoring.service import WorkflowError


@pytest.fixture
def ui(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCAL_DEVELOPMENT', 'true')
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path}/ui.db')
    monkeypatch.setenv('MONITORING_TEAM_ID', '10')
    module = importlib.import_module('monitoring.ui')
    monkeypatch.setattr(module, 'settings', replace(module.settings, local=False))
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
