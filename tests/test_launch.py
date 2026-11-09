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


def test_local_live_demo_marker_only_enables_rehearsal_pair(environment):
    # DEMO mode must not enable simulated identities, fake acceptance, or
    # synthetic-native operations. Its only extra permission is the real-pair
    # action in the authenticated local event roster.
    os.environ['MONITORING_REHEARSAL_PAIR'] = '1'
    configure_local_environment()
    assert os.environ['MONITORING_REHEARSAL_PAIR'] == '1'
    assert os.environ['MONITORING_SIMULATOR'] == '0'
    assert os.environ['MONITORING_LIVE_DEMO'] == '0'
    assert os.environ['MONITORING_PILOT'] == '0'
    from monitoring.settings import Settings
    assert Settings.load().local is True


def test_local_deployment_scripts_exist_and_cover_browser_smoke_check():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    startup = (root / 'Start-App.ps1').read_text(encoding='utf-8')
    smoke = (root / 'Test-App.ps1').read_text(encoding='utf-8')
    release = (root / 'Prepare-Release.ps1').read_text(encoding='utf-8')
    assert "[ValidateSet('Local', 'Demo')]" in startup
    assert "MONITORING_REHEARSAL_PAIR" in startup
    assert "-m monitoring.run --local" in startup
    assert "'/healthz'" in smoke
    assert "'/static/event_registrations.js'" in smoke
    assert "registration_invite_selected" in smoke
    assert "monitoring.release" in release
    assert "instance_version" in release
    assert "docker push" in release
