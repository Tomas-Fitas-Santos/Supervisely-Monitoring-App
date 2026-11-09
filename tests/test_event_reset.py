from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from sqlalchemy import text

from monitoring import db
from monitoring.reset import EventReset
from monitoring.service import WorkflowError
from monitoring.setup import Setup


@pytest.fixture
def reset(service):
    # Use foreign keys here to verify reset deletion order on SQLite too.
    with service.sessions.begin() as s:
        if s.bind.dialect.name == 'sqlite':
            s.execute(text('PRAGMA foreign_keys=ON'))
        config = s.get(db.EventConfig, 1)
        config.owner_id, config.owner_login = 99, 'admin'
        config.monitoring_team_id = config.annotator_team_id = 10
        config.source_workspace_id, config.local_token = 20, 'encrypted-secret'
        rules = s.get(db.CompetitionConfig, 1)
        rules.rules, rules.frozen = {'name': 'My event', 'deadline': 123}, True
        s.add(db.EventMember(user_id=99, login='admin', name='Admin', group='monitors'))
        s.add(db.EventMember(user_id=90, login='monitor', name='Monitor', group='monitors'))
        s.add(db.RosterContact(id='contact', email='', name='Missing email', role='participant', team_name='Pair'))
        s.add(db.Registration(id='pair', team_name='Pair', members=[], monitor_id=90, team_id=1))
        s.flush()
        s.add(db.ArrivalCheckin(registration_id='pair', member_email='a@test.org', present=True))
        s.add(db.TeamOnboarding(registration_id='pair', memberships=[], status='ready'))
        s.add(db.PreparedJob(id='job', registration_id='pair', position=1, plan_id='plan', assets=[]))
        s.add(db.SharedInvitation(team_id=10, email='a@test.org', status='pending'))
        s.add(db.Upload(id='upload', actor_id=99, name='file.jpg', size=10))
        s.flush()
        s.add(db.UploadedPath(upload_id='upload', path='root/images/file.jpg'))
        s.add(db.SourceOrigin(source_id='images:1', relative_path='root/images/file.jpg', checksum='abc'))
        s.add(db.Submission(id='submission', batch_id='a1', version=1, digest='abc', payload={}))
        s.flush()
        s.add(db.Assessment(submission_id='submission', entity_id=101, frame_index=-1,
                            bird_id='unit', criterion='whole', verdict='good', monitor_id=90))
        s.add(db.Review(batch_id='a1', entity_id=101, frame_index=-1, decision='accepted', note='ok', monitor_id=90))
        s.add(db.WorkUnit(batch_id='a1', entity_id=101, frame_index=-1, details={}))
        s.add(db.TaskProgress(job_id=100, batch_id='a1', details={}))
        s.add(db.TaskAttempt(id='attempt', batch_id='a1', parent_job_ids=[], job_ids=[], report=''))
        s.add(db.EventClosure(id=1, operation_id='close', deadline=123))
    return EventReset(Setup(service.sessions, 10))


@pytest.mark.parametrize('scope', ['roster', 'dataset', 'checkin', 'event', 'monitoring', 'settings'])
def test_reset_scopes_preserve_credentials_and_back_up_records(reset, scope):
    gateway = Mock()
    key, summary = reset.preview(99, scope)
    assert summary['scope'] == scope
    with reset.sessions() as s:
        assert s.get(db.Submission, 'submission')  # Preview has no destructive effects.
    reset.apply(gateway, 99, key)
    with reset.sessions() as s:
        backup = s.get(db.ResetBackup, key)
        assert backup.data['assessments'][0]['verdict'] == 'good'
        assert 'encrypted-secret' not in str(backup.data)
        assert s.get(db.EventConfig, 1).local_token == 'encrypted-secret'
        assert s.get(db.SharedInvitation, (10, 'a@test.org')).status == 'pending'
        assert bool(s.get(db.Registration, 'pair')) == (scope not in ('roster', 'settings'))
        assert bool(s.get(db.RosterContact, 'contact')) == (scope not in ('roster', 'settings'))
        assert backup.data['roster_contacts'][0]['name'] == 'Missing email'
        assert bool(s.get(db.Batch, 'a1')) == (scope == 'checkin')
        assert bool(s.get(db.ArrivalCheckin, ('pair', 'a@test.org'))) == (scope not in ('roster', 'settings', 'checkin'))
        assert bool(s.get(db.Upload, 'upload')) == (scope not in ('dataset', 'settings'))
        assert s.get(db.EventConfig, 1).monitoring_team_id == (None if scope == 'settings' else 10)
        assert s.get(db.SetupState, 1).operation_id is None
        if scope != 'checkin':
            assert not s.get(db.CompetitionConfig, 1).frozen
            assert s.get(db.CompetitionConfig, 1).rules.get('deadline') is None
    gateway.api.labeling_job.stop.assert_not_called()


def test_reset_rejects_nonowner_stale_preview_and_repeated_apply(reset):
    with pytest.raises(WorkflowError, match='organiser'):
        reset.preview(90, 'checkin')
    key, _ = reset.preview(99, 'checkin')
    with pytest.raises(WorkflowError, match='organiser'):
        reset.apply(Mock(), 90, key)
    with reset.sessions.begin() as s:
        s.get(db.ArrivalCheckin, ('pair', 'a@test.org')).present = False
    with pytest.raises(WorkflowError, match='event changed'):
        reset.apply(Mock(), 99, key)
    fresh, _ = reset.preview(99, 'checkin')
    reset.apply(Mock(), 99, fresh)
    with pytest.raises(WorkflowError, match='Preview'):
        reset.apply(Mock(), 99, fresh)


def test_reset_stops_owned_jobs_before_clearing_results(reset):
    with reset.sessions.begin() as s:
        s.get(db.Batch, 'a1').job_ids = [100]
    gateway = Mock()
    gateway.api.labeling_job.get_info_by_id.side_effect = [
        NS(team_id=1, dataset_id=31, status=status) for status in ('pending', 'pending', 'completed')]
    key, _ = reset.preview(99, 'monitoring')
    reset.apply(gateway, 99, key)
    gateway.api.labeling_job.stop.assert_called_once_with(100)
    with reset.sessions() as s:
        assert s.get(db.Batch, 'a1') is None
        assert s.get(db.ResetBackup, key).data['batches'][0]['job_ids'] == [100]


@pytest.mark.parametrize('failure', ['ownership', 'timeout', 'unconfirmed'])
def test_uncertain_native_reset_preserves_local_records_and_backup(reset, failure):
    with reset.sessions.begin() as s:
        s.get(db.Batch, 'a1').job_ids = [100]
    gateway = Mock()
    gateway.api.labeling_job.get_info_by_id.return_value = NS(team_id=2 if failure == 'ownership' else 1, dataset_id=31, status='pending')
    if failure == 'timeout':
        gateway.api.labeling_job.stop.side_effect = TimeoutError()
    key, _ = reset.preview(99, 'monitoring')
    with pytest.raises(WorkflowError, match='needs inspection'):
        reset.apply(gateway, 99, key)
    with reset.sessions() as s:
        assert s.get(db.Batch, 'a1') and s.get(db.Submission, 'submission')
        assert s.get(db.ResetBackup, key)
        assert s.get(db.SetupState, 1).operation_id == key


def test_reset_cannot_erase_uncertain_release_or_existing_operation(reset):
    with reset.sessions.begin() as s:
        s.get(db.Batch, 'a1').state = 'releasing'
    with pytest.raises(WorkflowError, match='Reconcile'):
        reset.preview(99, 'monitoring')
    with reset.sessions.begin() as s:
        s.get(db.SetupState, 1).operation_id = 'uncertain'
    with pytest.raises(WorkflowError, match='inspect'):
        reset.preview(99, 'settings')


def test_roster_can_be_reimported_after_reset(reset):
    from monitoring.registration import Registrations
    key, _ = reset.preview(99, 'roster')
    reset.apply(Mock(), 99, key)
    csv = ('team_name,member_1_email,member_2_email,member_1_role,member_2_role\n'
           'New pair,a@test.org,b@test.org,participant,participant\n')
    registrations = Registrations(reset.sessions)
    headers, _ = registrations.columns(csv)
    registrations.import_csv(99, csv, {name:name for name in headers})
    assert registrations.rows()[0]['team_name'] == 'New pair'


def test_binding_can_be_configured_again_after_full_reset(reset):
    from monitoring.event_settings import EventSettings
    key, _ = reset.preview(99, 'settings')
    reset.apply(Mock(), 99, key)
    event = EventSettings(reset.sessions)
    gateway = Mock()
    gateway.api.team.get_info_by_id.return_value = NS(id=40)
    gateway.api.user.get_member_info_by_id.return_value = NS(role='admin', disabled=False)
    gateway.api.workspace.get_list.return_value = [NS(id=50)]
    result = event.bind(gateway, 99, 40, 50, event.snapshot(99)['revision'])
    assert result['team_id'] == 40 and result['workspace_id'] == 50
