import os

import pytest

from monitoring.launch import configure_local_mode


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.setattr(os, 'environ', {})
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize('mode', ['demo', 'pilot'])
def test_demo_and_pilot_use_live_identity_and_leave_simulated_records_untouched(environment, mode):
    old = environment / 'data/organiser-demo/demo.db'
    old.parent.mkdir(parents=True)
    old.write_bytes(b'existing offline records')
    os.environ.update(MONITORING_DEMO='1', MONITORING_SIMULATOR='1', API_TOKEN='old token',
                      SYNC_API_TOKEN='old sync token', LOCAL_USER_ID='900', MONITORING_TEAM_ID='123')
    home = configure_local_mode(mode)
    assert home == environment / 'data/account-pilot'
    assert os.environ['DATABASE_URL'].endswith('/data/account-pilot/pilot.db')
    assert os.environ['MONITORING_SIMULATOR'] == os.environ['MONITORING_DEMO'] == '0'
    assert os.environ['MONITORING_PILOT'] == '1'
    assert os.environ['MONITORING_LIVE_DEMO'] == ('1' if mode == 'demo' else '0')
    assert not any(os.environ[k] for k in ('API_TOKEN', 'SYNC_API_TOKEN', 'LOCAL_USER_ID'))
    assert os.environ['MONITORING_TEAM_ID'] == '0'
    assert old.read_bytes() == b'existing offline records'


def test_demo_and_pilot_resume_same_database_key_and_uploads(environment):
    home = configure_local_mode('pilot')
    saved = {}
    for name in ('pilot.db', '.credential.key', 'uploads/photo.jpg'):
        file = home / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(name.encode())
        saved[file] = file.read_bytes()
    assert configure_local_mode('demo') == home
    assert configure_local_mode('pilot') == home
    assert all(file.read_bytes() == data for file, data in saved.items())


def test_simulator_requires_explicit_separate_launch_mode(environment):
    configure_local_mode('demo')
    home = configure_local_mode('simulator')
    assert home == environment / 'data/organiser-demo'
    assert os.environ['MONITORING_SIMULATOR'] == '1'
    assert os.environ['MONITORING_PILOT'] == os.environ['MONITORING_LIVE_DEMO'] == '0'
    assert os.environ['DATABASE_URL'].endswith('/data/organiser-demo/demo.db')


@pytest.mark.parametrize('mode', ['demo', 'pilot', 'simulator'])
def test_local_modes_cannot_replace_hosted_identity(environment, mode):
    os.environ['TASK_ID'] = '123'
    with pytest.raises(RuntimeError, match='hosted Supervisely'):
        configure_local_mode(mode)
    assert not (environment / 'data').exists()
