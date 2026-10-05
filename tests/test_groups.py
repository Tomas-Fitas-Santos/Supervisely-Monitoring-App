from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from sqlalchemy import delete

from monitoring.db import Batch, EventConfig, EventMember, SetupState, Team
from monitoring.service import WorkflowError
from monitoring.setup import Setup


@pytest.fixture
def event(service, tmp_path):
    with service.sessions.begin() as s:
        s.execute(delete(Batch))
        s.execute(delete(Team))
        c = s.get(EventConfig, 1)
        c.owner_id, c.owner_login, c.server_address = 99, 'organiser', 'https://app.supervisely.com'
    return Setup(service.sessions, 0, tmp_path / 'uploads')


def person(uid, login, role='annotator'):
    return NS(id=uid, login=login, name=login.title(), role=role, disabled=False)


def remote():
    gw = Mock()
    gw.api.team.create.side_effect = [NS(id=10, name='Monitors'), NS(id=20, name='Annotators')]
    gw.api.workspace.create.return_value = NS(id=30)
    gw.role_id.side_effect = lambda role: {'manager': 5, 'annotator': 7}[role]
    people = {p.login: p for p in [person(90, 'monitor', 'manager'), person(91, 'other', 'manager'),
                                      person(11, 'alice'), person(12, 'bob'), person(21, 'carol'), person(22, 'dave')]}
    gw.add_login.side_effect = lambda tid, login, role: people[login]
    gw.monitor.side_effect = lambda tid, uid: next(p for p in people.values() if p.id == uid)
    return gw


def groups(event, gw):
    event.configure_groups(gw, 99, 'create_groups', 'Monitors', 'Annotators')
    event.add_members(gw, 99, 'add_monitors', 'monitors', ['monitor', 'other'])
    event.add_members(gw, 99, 'add_annotators', 'annotators', ['alice', 'bob', 'carol', 'dave'])


def test_groups_and_registered_users_are_persisted_without_env_ids(event):
    gw = remote()
    event.authorize(gw, 99)
    groups(event, gw)
    assert event.monitoring_team_id == 10
    saved = Setup(event.sessions, 999).groups()
    assert saved['ready'] and saved['workspace_id'] == 30
    assert [m['login'] for m in saved['monitors']] == ['monitor', 'other']
    assert len(saved['annotators']) == 4
    assert gw.api.user.create.call_count == 0
    with event.sessions() as s:
        assert s.get(SetupState, 1).operation_id is None


def test_one_person_cannot_be_in_both_event_groups(event):
    gw = remote()
    groups(event, gw)
    with pytest.raises(WorkflowError, match='only one'):
        event.add_members(gw, 99, 'overlap_group', 'monitors', ['alice'])
    assert gw.add_login.call_count == 6


def test_organiser_cannot_join_annotator_pool_and_lose_monitoring_access(event):
    gw = remote()
    groups(event, gw)
    with pytest.raises(WorkflowError, match='organiser cannot'):
        event.add_members(gw, 99, 'owner_annotator', 'annotators', ['organiser'])
    assert gw.add_login.call_count == 6


def test_group_creation_rejects_same_native_team(event):
    gw = remote()
    with pytest.raises(WorkflowError, match='separate'):
        event.configure_groups(gw, 99, 'same_groups', 'Monitors', 'Annotators', 10, 10)
    gw.api.team.create.assert_not_called()


def test_groups_can_adopt_existing_native_teams(event):
    gw = remote()
    gw.api.team.get_info_by_id.side_effect = lambda tid: NS(id=tid, name=str(tid))
    event.configure_groups(gw, 99, 'adopt_groups', 'Monitors', 'Annotators', 10, 20)
    gw.api.team.create.assert_not_called()
    assert event.groups()['annotator_team_id'] == 20
    assert gw.organiser.call_count == 2


def test_pair_creation_can_precede_monitor_assignment_and_checks_pool(event):
    gw = remote()
    groups(event, gw)
    gw.api.team.create.side_effect = None
    gw.api.team.create.return_value = NS(id=101, name='Pair A')
    event.create_team(gw, 99, 'unassigned_pair', 'Pair A', ['alice', 'bob'])
    assert event.roster()[0]['monitor_id'] == 0
    gw.add_monitor.assert_not_called()
    with pytest.raises(WorkflowError, match='unpaired'):
        event.create_team(gw, 99, 'duplicate_pair', 'Pair B', ['alice', 'carol'])
    with pytest.raises(WorkflowError, match='Annotators group'):
        event.create_team(gw, 99, 'outside_pair', 'Pair C', ['unknown', 'carol'])
    with pytest.raises(WorkflowError, match='to a monitor'):
        event.preview(gw, 99, [50], [101], 1, 2, 1, True, False)
    assert gw.api.team.create.call_count == 3  # Two groups and the valid pair only.


def test_balanced_monitor_preview_accounts_for_existing_load_and_access(event, service):
    gw = remote()
    groups(event, gw)
    with event.sessions.begin() as s:
        s.add_all([Team(id=101, name='A', monitor_id=90, annotator_ids=[11, 12]),
                   Team(id=102, name='B', monitor_id=0, annotator_ids=[21, 22]),
                   Team(id=103, name='C', monitor_id=0, annotator_ids=[31, 32]),
                   Team(id=104, name='D', monitor_id=0, annotator_ids=[41, 42])])
    key, rows = event.preview_monitor_assignments(gw, 99, [90, 91])
    assert [r['monitor_id'] for r in rows] == [91, 90, 91]
    gw.add_monitor.assert_not_called()
    event.apply_monitor_assignments(gw, 99, key)
    assert [t['id'] for t in service.snapshot(90)['teams']] == [101, 103]
    assert [t['id'] for t in service.snapshot(91)['teams']] == [102, 104]
    assert gw.add_monitor.call_count == 3
    with pytest.raises(WorkflowError, match='unapplied'):
        event.apply_monitor_assignments(gw, 99, key)


def test_monitor_preview_rejects_people_outside_group_and_stale_roster(event):
    gw = remote()
    groups(event, gw)
    with event.sessions.begin() as s:
        s.add(Team(id=101, name='A', monitor_id=0, annotator_ids=[11, 12]))
    with pytest.raises(WorkflowError, match='Monitors group'):
        event.preview_monitor_assignments(gw, 99, [11])
    key, _ = event.preview_monitor_assignments(gw, 99, [90])
    with event.sessions.begin() as s:
        s.get(Team, 101).revision += 1
    with pytest.raises(WorkflowError, match='changed'):
        event.apply_monitor_assignments(gw, 99, key)
    gw.add_monitor.assert_not_called()


def test_failed_bulk_assignment_publishes_no_partial_monitor_ownership(event):
    gw = remote()
    groups(event, gw)
    with event.sessions.begin() as s:
        s.add_all([Team(id=101, name='A', monitor_id=0, annotator_ids=[11, 12]),
                   Team(id=102, name='B', monitor_id=0, annotator_ids=[21, 22])])
    key, _ = event.preview_monitor_assignments(gw, 99, [90, 91])
    gw.add_monitor.side_effect = [None, TimeoutError('private-token')]
    with pytest.raises(WorkflowError, match='needs inspection') as error:
        event.apply_monitor_assignments(gw, 99, key)
    assert 'private-token' not in str(error.value)
    assert all(t['monitor_id'] == 0 for t in event.roster())
    assert event.snapshot()['blocked_by'] == key


def test_removal_is_blocked_for_assigned_people(event):
    gw = remote()
    groups(event, gw)
    with event.sessions.begin() as s:
        s.add(Team(id=101, name='A', monitor_id=90, annotator_ids=[11, 12]))
    for uid in (90, 11):
        with pytest.raises(WorkflowError, match='assigned'):
            event.remove_member(gw, 99, 'remove_assigned', uid)
    event.remove_member(gw, 99, 'remove_unused', 91)
    gw.api.user.remove_from_team.assert_called_once_with(91, 10)
    assert [u['id'] for u in event.groups()['monitors']] == [90]


def test_existing_event_members_are_adopted_during_group_upgrade(event):
    gw = remote()
    event._legacy_monitoring_team_id = 10
    with event.sessions.begin() as s:
        s.add(Team(id=101, name='A', monitor_id=90, annotator_ids=[11, 12]))
    gw.api.team.get_info_by_id.return_value = NS(id=10, name='Old monitoring')
    gw.api.team.create.side_effect = [NS(id=20, name='Annotators')]
    users = {u.id: u for u in [person(11, 'alice'), person(12, 'bob'), person(90, 'monitor', 'manager')]}
    gw.api.user.get_member_info_by_id.side_effect = lambda tid, uid: users[uid]
    event.configure_groups(gw, 99, 'upgrade_groups', 'Monitors', 'Annotators', 10)
    assert [u['id'] for u in event.groups()['monitors']] == [90]
    assert [u['id'] for u in event.groups()['annotators']] == [11, 12]
