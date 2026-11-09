"""Automatic imports mirror the supplied Monitors / Participants workbook layout."""
import base64
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from monitoring.db import EventConfig, EventInvitee, Registration, RosterContact, SharedInvitation
from monitoring.excel_import import roster_tables
from monitoring.roster_import import import_roster
from monitoring.service import WorkflowError
from monitoring.shared_invitations import SharedTeamInvitations


@pytest.fixture
def sessions(service):
    with service.sessions.begin() as s:
        config = s.get(EventConfig, 1)
        config.monitoring_team_id = config.annotator_team_id = 10
        config.owner_id = 99
    return service.sessions


def workbook(sheets):
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets:
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    stream = BytesIO()
    book.save(stream)
    book.close()
    return base64.b64encode(stream.getvalue()).decode()


def people_csv(rows):
    return 'team_number,team_name,participant_name,participant_email\n' + rows


def test_workbook_reads_both_sheets_and_keeps_numbered_teams_distinct(sessions):
    encoded = workbook([
        ('Monitors', [
            ['monitor_id', 'monitor_email', 'monitor_name', 'event_role', 'native_role', 'invitation_status'],
            [1, 'm@example.org', 'Monitor', 'monitor', 'admin', 'accepted'],
        ]),
        ('Participants', [
            ['team_number', 'team_name', 'participant_name', 'participant_email', 'phone', 'date_of_birth'],
            [1, 'Same name', 'A', 'a@example.org', 'PRIVATE', 'PRIVATE'],
            [1, 'Same name', 'B', 'b@example.org'],
            [2, 'Same name', 'C', 'c@example.org'],
            [2, 'Same name', 'D', 'd@example.org'],
        ]),
    ])
    result = import_roster(sessions, 99, roster_tables(encoded))
    assert result == dict(teams=2, monitors=1, participants=4, missing_emails=0)
    people = SharedTeamInvitations(sessions).people()
    assert len(people) == 5 and {p['status'] for p in people} == {'not_invited'}
    assert next(p for p in people if p['email'] == 'm@example.org')['name'] == 'Monitor'
    with sessions() as s:
        assert {r.team_name for r in s.scalars(select(Registration))} == {'1 · Same name', '2 · Same name'}
        assert s.get(EventInvitee, 'm@example.org').native_role == 'manager'
        assert not list(s.scalars(select(SharedInvitation)))
        assert 'PRIVATE' not in repr([(r.name, r.email, r.team_name) for r in s.scalars(select(RosterContact))])


@pytest.mark.parametrize('empty_first', [True, False])
def test_empty_participants_sheet_does_not_hide_monitors(sessions, empty_first):
    sheets = [
        ('Participants', [['team_number', 'participant_email']]),
        ('Monitors', [['monitor_email', 'monitor_name'], ['m@example.org', 'Monitor']]),
    ]
    if not empty_first:
        sheets.reverse()
    result = import_roster(sessions, 99, roster_tables(workbook(sheets)))
    assert result == dict(teams=0, monitors=1, participants=0, missing_emails=0)


def test_incomplete_teams_and_missing_emails_stay_visible_and_can_be_completed(sessions):
    original = people_csv('1,Pair,A,a@example.org\n1,Pair,B,\n2,Solo,C,c@example.org\n')
    import_roster(sessions, 99, [('Participants', original)])
    people = SharedTeamInvitations(sessions).people()
    assert len(people) == 3
    assert next(p for p in people if p['name'] == 'B')['status'] == 'missing_email'
    # A verified identity and its pending invitation must survive corrections.
    with sessions.begin() as s:
        reg = s.scalar(select(Registration).where(Registration.team_name == '1 · Pair'))
        members = [dict(m) for m in reg.members]
        members[0].update(user_id=42, status='accepted')
        reg.members = members
        s.add(SharedInvitation(team_id=10, email='a@example.org', status='pending'))
    corrected = people_csv('1,Pair,A,a@example.org\n1,Pair,B,b@example.org\n2,Solo,C,c@example.org\n2,Solo,D,d@example.org\n')
    import_roster(sessions, 99, [('Participants', corrected)])
    assert len(SharedTeamInvitations(sessions).people()) == 4
    assert not any(p['status'] == 'missing_email' for p in SharedTeamInvitations(sessions).people())
    with sessions() as s:
        reg = s.scalar(select(Registration).where(Registration.team_name == '1 · Pair'))
        assert reg.members[0]['user_id'] == 42 and reg.members[0]['status'] == 'accepted'
        assert s.get(SharedInvitation, (10, 'a@example.org')).status == 'pending'


def test_monitor_in_participant_sheet_gets_one_dual_role_identity(sessions):
    tables = [('Monitors', 'monitor_email,monitor_name\na@example.org,Also monitor\n'),
              ('Participants', people_csv('1,Pair,A,a@example.org\n1,Pair,B,b@example.org\n'))]
    import_roster(sessions, 99, tables)
    import_roster(sessions, 99, tables)  # Idempotent; no duplicate people or invitations.
    people = SharedTeamInvitations(sessions).people()
    assert len(people) == 2
    assert next(p for p in people if p['email'] == 'a@example.org')['role'] == 'participant_monitor'
    with sessions() as s:
        assert s.get(EventInvitee, 'a@example.org').native_role == 'reviewer'


def test_separate_monitor_then_participant_csv_recognizes_both_roles(sessions):
    import_roster(sessions, 99, [('Monitors', 'monitor_email,monitor_name\na@example.org,Monitor\n')])
    import_roster(sessions, 99, [('Participants', people_csv('1,Pair,A,a@example.org\n1,Pair,B,b@example.org\n'))])
    with sessions() as s:
        assert s.get(EventInvitee, 'a@example.org').event_role == 'participant_monitor'


def test_conflicting_monitor_columns_in_a_pair_are_rejected(sessions):
    text = ('team_name,participant_email,monitor_email\n'
            'Pair,a@example.org,m1@example.org\nPair,b@example.org,m2@example.org\n')
    with pytest.raises(WorkflowError, match='conflicting monitor'):
        import_roster(sessions, 99, [('Participants', text)])


def test_invalid_monitor_does_not_partially_import_participants(sessions):
    with pytest.raises(WorkflowError, match='invalid email'):
        import_roster(sessions, 99, [
            ('Participants', people_csv('1,Pair,A,a@example.org\n1,Pair,B,b@example.org\n')),
            ('Monitors', 'monitor_email,monitor_name\ninvalid,Monitor\n'),
        ])
    with sessions() as s:
        assert not list(s.scalars(select(Registration)))
        assert not list(s.scalars(select(EventInvitee)))


def test_complete_existing_pair_cannot_be_replaced_on_reimport(sessions):
    original = people_csv('1,Pair,A,a@example.org\n1,Pair,B,b@example.org\n')
    import_roster(sessions, 99, [('Participants', original)])
    with pytest.raises(WorkflowError, match='different members'):
        import_roster(sessions, 99, [('Participants', original.replace('b@example.org', 'other@example.org'))])


def test_more_than_two_people_need_distinct_team_numbers(sessions):
    with pytest.raises(WorkflowError, match='more than two'):
        import_roster(sessions, 99, [('Participants', people_csv(
            '1,Pair,A,a@example.org\n1,Pair,B,b@example.org\n1,Pair,C,c@example.org\n'))])
