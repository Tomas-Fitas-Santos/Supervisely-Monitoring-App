import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from monitoring.deployment import production_database, production_environment
from monitoring.service import WorkflowError


@pytest.fixture
def environment(monkeypatch):
    for key in ('TASK_ID', 'task_id', 'SLY_APP_TASK_ID', 'LOCAL_DEVELOPMENT', 'MONITORING_SIMULATOR', 'MONITORING_PILOT', 'MONITORING_DEMO', 'MONITORING_LIVE_DEMO'):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_production_cannot_start_as_an_unauthenticated_local_server(environment):
    with pytest.raises(WorkflowError, match='native Supervisely'):
        production_environment()


@pytest.mark.parametrize('key,value', [('LOCAL_DEVELOPMENT', 'true'), ('MONITORING_SIMULATOR', '1'), ('MONITORING_PILOT', '1'), ('MONITORING_DEMO', '1'), ('MONITORING_LIVE_DEMO', '1')])
def test_production_rejects_local_identity_modes(environment, key, value):
    environment.setenv('TASK_ID', '123')
    environment.setenv(key, value)
    with pytest.raises(WorkflowError):
        production_environment()


def test_production_requires_postgres_and_no_saved_local_identity(service, environment):
    environment.setenv('TASK_ID', '123')
    production_environment()
    import os
    assert os.environ['LOCAL_DEVELOPMENT'] == 'false'
    sessions = MagicMock()
    session = sessions.return_value.__enter__.return_value
    session.bind.dialect.name = 'sqlite'
    with pytest.raises(WorkflowError, match='PostgreSQL'):
        production_database(sessions)
    session.bind.dialect.name = 'postgresql'
    session.get.side_effect = [NS(id=1)]
    with pytest.raises(WorkflowError, match='simulator database'):
        production_database(sessions)
    session.get.side_effect = [None, NS(local_token='encrypted-test-token')]
    with pytest.raises(WorkflowError, match='saved local organiser token'):
        production_database(sessions)
    session.get.side_effect = [None, NS(local_token=None)]
    production_database(sessions)


def test_release_sources_are_complete_and_exclude_credentials_and_runtime_data(tmp_path):
    from monitoring.release import prepare
    root = prepare('registry.example/monitor:pilot-validated', '6.99.1', tmp_path / 'release')
    config = json.loads((root / 'config.json').read_text())
    assert config['is_multi_user_app'] is True
    assert config['entrypoint'].endswith('--production')
    assert config['instance_version'] == config['min_instance_version'] == '6.99.1'
    assert config['docker_image'] == 'registry.example/monitor:pilot-validated'
    assert (root / 'monitoring' / 'static' / 'vendor' / 'vue.min.js').is_file()
    assert len(list((root / 'examples').rglob('*.jpg'))) == 2
    assert not list(root.rglob('requirements.txt'))
    assert not list(root.rglob('*.db')) and not list(root.rglob('.env'))
    assert not list(root.rglob('*.pyc'))
    manifest = json.loads((root / 'release-checksums.json').read_text())
    for name, digest in manifest.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    with pytest.raises(ValueError, match='already exists'):
        prepare('registry.example/monitor:tag', '6.99.1', root)


@pytest.mark.parametrize('image,version', [('monitor', '6.99.1'), ('monitor:tag', 'unknown'), ('$(bad):tag', '6.99.1')])
def test_release_needs_explicit_image_and_validated_instance_version(tmp_path, image, version):
    from monitoring.release import prepare
    with pytest.raises(ValueError):
        prepare(image, version, tmp_path / 'release')
