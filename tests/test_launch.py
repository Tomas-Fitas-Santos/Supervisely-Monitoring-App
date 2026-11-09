import os

import pytest

from monitoring.launch import configure_local_environment


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.setattr(os, 'environ', {})
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_local_app_resumes_existing_live_store_without_version_flags(environment):
    home = environment / 'data/account-pilot'
    home.mkdir(parents=True)
    (home / 'pilot.db').write_bytes(b'existing event')
    os.environ.update(MONITORING_DEMO='1', MONITORING_SIMULATOR='1', MONITORING_PILOT='1')
    assert configure_local_environment() == home
    assert os.environ['DATABASE_URL'].endswith('/data/account-pilot/pilot.db')
    assert (home / 'pilot.db').read_bytes() == b'existing event'
    assert all(os.environ[k] == '0' for k in ('MONITORING_DEMO','MONITORING_SIMULATOR','MONITORING_PILOT','MONITORING_LIVE_DEMO'))
    assert configure_local_environment() == home


def test_configured_storage_is_respected(environment):
    custom = environment / 'my-event'
    os.environ.update(LOCAL_APP_HOME=str(custom), DATABASE_URL='sqlite:///chosen.db', UPLOAD_STAGING_DIR=str(custom/'staged'))
    assert configure_local_environment() == custom
    assert os.environ['DATABASE_URL'] == 'sqlite:///chosen.db'
    assert os.environ['UPLOAD_STAGING_DIR'] == str(custom/'staged')


def test_local_hosting_cannot_replace_hosted_identity(environment):
    os.environ['TASK_ID'] = '123'
    with pytest.raises(RuntimeError, match='hosted Supervisely'):
        configure_local_environment()
    assert not (environment / 'data').exists()
