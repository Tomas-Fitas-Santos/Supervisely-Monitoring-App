"""Contract tests for event pair distribution and physical arrival check-in."""
import pytest
from sqlalchemy import select

from monitoring.attendance import EventRoster
from monitoring.db import ArrivalCheckin, Audit, EventConfig, EventMember, Registration
from monitoring.registration import Registrations
from monitoring.service import WorkflowError


def prepare(service, number=7):
    with service.sessions.begin() as session:
        session.get(EventConfig, 1).owner_id = 90
        session.get(EventConfig, 1).owner_login = 'organiser'
        for mid in [90, 91, 92]:
            session.add(EventMember(user_id=mid, group='monitors',
                login=f'monitor-{mid}', name=f'Monitor {mid}'))
    text = 'Team,First,Second\n' + ''.join(
        f'Pair {i},p{i}a@example.test,p{i}b@example.test\n'
        for i in range(1, number+1)
    )
    Registrations(service.sessions).import_csv(90, text,
        {'team_name':'Team','member_1_email':'First','member_2_email':'Second'})
    return EventRoster(service.sessions)


def test_organiser_sees_competition_pairs_and_two_distinct_members(service):
    roster = prepare(service)
    view = roster.overview(90, organiser=True)
    assert view['summary']['teams'] == 7
    assert view['summary']['participants'] == 14
    assert len(view['monitors']) == 3
    assert all(len(t['members']) == 2 for t in view['teams'])
    assert not roster.overview(500)['teams']


def test_balancing_even_with_no_provisioned_teams_and_is_idempotent(service):
    roster = prepare(service)
    key, plan = roster.preview_balance(90, [90, 91, 92])
    assert key and len(plan['rows']) == 7
    assert max(plan['loads'].values()) - min(plan['loads'].values()) == 1
    assert sorted(plan['loads'].values()) == [2, 2, 3]
    with service.sessions() as session:
        assert all(r.monitor_id == 0 for r in session.scalars(select(Registration)))
    emails = {mid:f'monitor{mid}@example.test' for mid in [90, 91, 92]}
    assert roster.apply_balance(90, key, emails) == 7
    with service.sessions() as session:
        rows = list(session.scalars(select(Registration)))
        assert {r.monitor_id for r in rows} == {90, 91, 92}
        assert len([a for a in session.scalars(select(Audit))
                    if a.action == 'roster_monitor_assigned']) == 7
    with pytest.raises(WorkflowError, match='unapplied'):
        roster.apply_balance(90, key, emails)
    key2, preview2 = roster.preview_balance(90, [90, 91, 92])
    assert key2 is None and preview2['rows'] == []


def test_rebalance_preserves_native_provisioned_team(service):
    roster = prepare(service, 3)
    with service.sessions.begin() as session:
        first = session.scalar(select(Registration).where(Registration.team_name=='Pair 1'))
        first.team_id = 1  # Test fixture's native Team 1 is monitored by 90.
        first.monitor_id = 90
    key, plan = roster.preview_balance(90, [90, 91], rebalance=True)
    assert all(row['team_name'] != 'Pair 1' for row in plan['rows'])
    with pytest.raises(WorkflowError, match='already provisioned'):
        roster.assign_one(90, first.id, 91, 'monitor91@example.test')


def test_changed_roster_invalidates_preview_and_leaves_assignments_unchanged(service):
    roster = prepare(service, 3)
    key, _ = roster.preview_balance(90, [90, 91])
    with service.sessions.begin() as session:
        session.scalar(select(Registration).where(Registration.team_name=='Pair 2')).monitor_id = 91
    with pytest.raises(WorkflowError, match='Roster changed'):
        roster.apply_balance(90, key, {90:'a@example.test',91:'b@example.test'})
    with service.sessions() as session:
        assert sum(bool(r.monitor_id) for r in session.scalars(select(Registration))) == 1


def test_checkin_per_person_scoped_to_monitor_with_reversal_audit(service):
    roster = prepare(service, 1)
    team = roster.overview(90, organiser=True)['teams'][0]
    assert roster.assign_one(90,team['id'],91,'monitor91@example.test')
    email = team['members'][0]['email']
    with pytest.raises(WorkflowError,match='assigned monitor'):
        roster.set_checkin(92,team['id'],email,True)
    assert roster.set_checkin(91,team['id'],email,True)
    assert not roster.set_checkin(91,team['id'],email,True)
    view = roster.overview(91)
    assert view['summary']['checked_in'] == 1
    assert view['teams'][0]['arrival_status'] == 'partial'
    with pytest.raises(WorkflowError,match='reason'):
        roster.set_checkin(91,team['id'],email,False)
    assert roster.set_checkin(91,team['id'],email,False,'Entered wrong person')
    assert roster.overview(91)['summary']['checked_in'] == 0
    with service.sessions() as session:
        assert session.get(ArrivalCheckin,(team['id'],email)).present is False
        actions = [a.action for a in session.scalars(select(Audit))
                   if 'checkin' in a.action]
        assert actions == ['participant_checkin','participant_checkin_undo']


def test_manual_assignment_requires_registered_monitor_and_two_participants(service):
    roster = prepare(service, 1)
    team_id = roster.overview(90, organiser=True)['teams'][0]['id']
    with pytest.raises(WorkflowError,match='roster'):
        roster.assign_one(90,team_id,700,'wrong@example.test')
    assert roster.assign_one(90,team_id,92,'monitor92@example.test')
    assert not roster.assign_one(90,team_id,92,'monitor92@example.test')
