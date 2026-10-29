import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from sqlalchemy import delete

from monitoring.connection import LocalConnection, server_url
from monitoring.db import Batch, EventConfig, SetupState, Team
from monitoring.service import WorkflowError
from monitoring.settings import Settings


@pytest.fixture
def connection(service, tmp_path, monkeypatch):
    import supervisely as sly
    with service.sessions.begin() as s:
        s.execute(delete(Batch))
        s.execute(delete(Team))
    api = Mock()
    api.user.get_my_info.return_value = NS(id=99, login='organiser')
    monkeypatch.setattr(sly, 'Api', Mock(return_value=api))
    return LocalConnection(service.sessions, tmp_path), api


def test_connection_encrypts_credentials_and_survives_restart(connection):
    local, api = connection
    local.save('https://app.supervisely.com/', 'private-token')
    with local.sessions() as s:
        c = s.get(EventConfig, 1)
        assert 'private-token' not in c.local_token
        assert c.owner_id == 99 and c.server_address == 'https://app.supervisely.com'
    assert LocalConnection(local.sessions, local.home).api() is api
    import supervisely as sly
    sly.Api.assert_called_with('https://app.supervisely.com', 'private-token')
    assert b'private-token' not in (local.home / 'credential.key').read_bytes()


def test_existing_connection_pins_organiser_and_server(connection):
    local, api = connection
    local.save('https://app.supervisely.com', 'first-token')
    api.user.get_my_info.return_value = NS(id=100, login='other')
    with pytest.raises(WorkflowError, match='organiser account'):
        local.save('https://app.supervisely.com', 'other-token')
    api.user.get_my_info.return_value = NS(id=99, login='organiser')
    with pytest.raises(WorkflowError, match='organiser account'):
        local.save('https://other.example', 'other-token')
    local.save('https://app.supervisely.com', 'new-token')
    local.api()
    import supervisely as sly
    sly.Api.assert_called_with('https://app.supervisely.com', 'new-token')


def test_authentication_failure_does_not_store_or_echo_token(connection):
    local, api = connection
    api.user.get_my_info.side_effect = RuntimeError('private-token exposed upstream')
    with pytest.raises(WorkflowError, match='authenticate') as error:
        local.save('https://app.supervisely.com', 'private-token')
    assert 'private-token' not in str(error.value)
    with local.sessions() as s:
        assert s.get(EventConfig, 1).owner_id is None
        assert s.get(EventConfig, 1).local_token is None


def test_connection_cannot_interrupt_active_setup(connection):
    local, api = connection
    with local.sessions.begin() as s:
        s.get(SetupState, 1).operation_id = 'running_setup'
    with pytest.raises(WorkflowError, match='current setup'):
        local.save('https://app.supervisely.com', 'private-token')
    with local.sessions() as s:
        assert s.get(SetupState, 1).operation_id == 'running_setup'
        assert s.get(EventConfig, 1).local_token is None


@pytest.mark.parametrize('url', ['https://name:secret@example.com', 'file:///etc/passwd', 'https://example.com?token=x'])
def test_connection_requires_a_base_server_address(url):
    with pytest.raises(WorkflowError, match='base address'):
        server_url(url)


def test_no_env_configuration_is_needed_for_fresh_local_start(tmp_path, monkeypatch):
    import monitoring.settings as module
    monkeypatch.setattr(module, 'load_dotenv', lambda: None)
    for key in ('DATABASE_URL', 'MONITORING_TEAM_ID', 'LOCAL_DEVELOPMENT', 'TASK_ID', 'task_id', 'SLY_APP_TASK_ID',
                'SERVER_ADDRESS', 'POLL_INTERVAL_SECONDS', 'STALE_AFTER_SECONDS', 'CONTEXT_TEAMID', 'TEAM_ID'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('LOCAL_APP_HOME', str(tmp_path))
    settings = Settings.load()
    assert settings.local and settings.monitoring_team_id == 0
    assert settings.database_url == 'sqlite:///' + (tmp_path / 'event.db').as_posix()
    (tmp_path / 'settings.json').write_text(json.dumps({'database_url': 'sqlite:///live.db'}))
    assert Settings.load().database_url == 'sqlite:///live.db'


def test_hosted_start_requires_persistent_storage_but_no_event_ids(tmp_path, monkeypatch):
    import monitoring.settings as module
    monkeypatch.setattr(module, 'load_dotenv', lambda: None)
    monkeypatch.setenv('TASK_ID', '100')
    monkeypatch.delenv('LOCAL_DEVELOPMENT', raising=False)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with pytest.raises(RuntimeError, match='persistent PostgreSQL'):
        Settings.load()
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://configured')
    monkeypatch.setenv('MONITORING_TEAM_ID', 'YOUR_OLD_PLACEHOLDER')
    assert Settings.load().monitoring_team_id == 0
    assert not Settings.load().local
