"""Production refresh of accounts invited directly through Supervisely Team members."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select

from monitoring.db import (EventConfig, EventInvitee, EventMember,
                           EventRosterRecord, Registration, SharedInvitation,
                           database, initialize)
from monitoring.event_people import EventPeople
from monitoring.registration import Registrations
from monitoring.service import WorkflowError
from monitoring.shared_invitations import SharedTeamInvitations


@pytest.fixture
def sessions(tmp_path):
    engine, maker = database('sqlite:///' + (tmp_path / 'live-members.db').as_posix())
    initialize(engine)
    with maker.begin() as session:
        config = session.get(EventConfig, 1)
        config.owner_id = 900
        config.monitoring_team_id = 15
        config.annotator_team_id = 15
        config.source_workspace_id = 81
    yield maker
    engine.dispose()


class NativeTeamApi:
    def __init__(self, members):
        self.members = list(members)
        self.calls = []
        self.role = NS(get_list=lambda: [
            NS(id=1, role='admin'), NS(id=3, role='manager'),
            NS(id=4, role='reviewer'), NS(id=5, role='annotator'),
        ])

    def post(self, name, data, **kwargs):
        self.calls.append((name, data))
        if name != 'members.list':
            raise AssertionError('Refresh must never invite a native user.')
        return NS(json=lambda: {'entities': self.members, 'pagesCount': 1})


def member(uid, login, email, role_id, active=True):
    return {'userId': uid, 'login': login, 'email': email,
            'name': login, 'roleId': role_id, 'activated': active}


def test_discover_directly_invited_members_without_workbook(sessions):
    api = NativeTeamApi([
        member(900, 'organiser', 'organiser@example.org', 1),
        member(301, 'monitor-one', 'monitor@example.org', 3),
        member(302, 'bird-one', 'one@example.org', 5),
        member(303, 'bird-two', 'two@example.org', 5),
    ])
    service = SharedTeamInvitations(sessions)
    assert service.refresh(NS(api=api), 900) == {}
    with sessions() as session:
        roster = list(session.scalars(select(EventRosterRecord)))
        assert len(roster) == 4
        assert {r.email:r.event_role for r in roster} == {
            'organiser@example.org': 'monitor',
            'monitor@example.org': 'monitor',
            'one@example.org': 'participant',
            'two@example.org': 'participant',
        }
        assert all(session.get(SharedInvitation, (15, r.email)).status == 'accepted'
                   for r in roster)
        assert session.get(EventMember, 301).group == 'monitors'
        assert session.get(EventMember, 302).group == 'annotators'
    assert set(name for name, _ in api.calls) == {'members.list'}
    service.refresh(NS(api=api), 900)
    with sessions() as session:
        assert len(list(session.scalars(select(EventRosterRecord)))) == 4
        assert len(list(session.scalars(select(EventInvitee)))) == 4


def test_direct_members_can_form_only_explicit_accepted_pair(sessions):
    api = NativeTeamApi([
        member(301, 'bird-one', 'one@example.org', 5),
        member(302, 'bird-two', 'two@example.org', 5),
        member(303, 'bird-three', 'three@example.org', 5, active=False),
    ])
    shared = SharedTeamInvitations(sessions)
    shared.refresh(NS(api=api), 900)
    people = EventPeople(sessions)
    assert {p['email'] for p in people.overview() if p['can_pair']} == {
        'one@example.org', 'two@example.org'}
    with pytest.raises(WorkflowError, match='Confirm'):
        people.create_pair(900, 'PAIR A', ['one@example.org', 'two@example.org'])
    with pytest.raises(WorkflowError, match='accepted'):
        people.create_pair(900, 'PAIR B', ['one@example.org', 'three@example.org'], confirmed=True)
    assert people.create_pair(
        900, 'PAIR A', ['one@example.org', 'two@example.org'], confirmed=True) == 1
    shared.refresh(NS(api=api), 900)
    with sessions() as session:
        rows = list(session.scalars(select(Registration)))
        assert len(rows) == 1
        assert rows[0].team_name == 'PAIR A'
        assert [m['status'] for m in rows[0].members] == ['accepted', 'accepted']
    assert not [name for name, _ in api.calls if name == 'members.add']
    assert not any(p['can_pair'] for p in people.overview()
                   if p['email'] in ('one@example.org', 'two@example.org'))


def test_hidden_email_can_be_linked_to_native_login(sessions):
    api = NativeTeamApi([member(301, 'private-bird', '', 5)])
    shared = SharedTeamInvitations(sessions)
    shared.refresh(NS(api=api), 900)
    rows = EventPeople(sessions).overview()
    assert len(rows) == 1
    assert rows[0]['email'] == ''
    assert rows[0]['needs_review'] is True
    assert not rows[0]['can_pair']
    assert EventPeople(sessions).link_native_email(
        NS(api=api), 900, rows[0]['id'], 'private@example.org') is True
    shared.refresh(NS(api=api), 900)
    with sessions() as session:
        assert session.get(SharedInvitation, (15, 'private@example.org')).status == 'accepted'
        assert len(list(session.scalars(select(EventRosterRecord)))) == 1
    assert EventPeople(sessions).overview()[0]['can_pair'] is True


def test_native_refresh_cannot_override_imported_roles(sessions):
    text = ('team_name,member_1_email,member_2_email,member_1_role,member_2_role\n'
            'PAIR A,one@example.org,two@example.org,participant,participant\n')
    headers, _ = Registrations.columns(text)
    Registrations(sessions).import_csv(900, text, {h:h for h in headers})
    api = NativeTeamApi([member(301, 'bird-one', 'one@example.org', 3)])
    SharedTeamInvitations(sessions).refresh(NS(api=api), 900)
    with sessions() as session:
        entry = session.get(EventInvitee, 'one@example.org')
        assert entry.event_role == 'participant'
        assert entry.native_role == 'annotator'
        assert session.get(SharedInvitation, (15, 'one@example.org')).status == 'role_mismatch'
        assert session.get(EventMember, 301) is None


def test_empty_native_team_can_refresh_before_upload(sessions):
    api = NativeTeamApi([])
    assert SharedTeamInvitations(sessions).refresh(NS(api=api), 900) == {}
    with sessions() as session:
        assert list(session.scalars(select(EventRosterRecord))) == []
        assert list(session.scalars(select(SharedInvitation))) == []
