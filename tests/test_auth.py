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
