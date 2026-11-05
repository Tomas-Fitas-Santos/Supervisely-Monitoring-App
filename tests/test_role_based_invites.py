"""Role-aware shared-team invitation contracts (no external Supervisely writes)."""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select

from monitoring.db import (
    EventConfig, EventInvitee, EventMember, Registration,
    SharedInvitation, database, initialize,
)
from monitoring.registration import Registrations
from monitoring.service import WorkflowError
from monitoring.setup_gateway import SetupGateway
from monitoring.shared_invitations import SharedTeamInvitations


SAMPLE = (Path(__file__).resolve().parents[1] /
          'examples' / 'organiser-demo' / 'registrations.csv')


@pytest.fixture
def sessions(tmp_path):
    engine, maker = database('sqlite:///' + (tmp_path / 'roster.db').as_posix())
    initialize(engine)
    yield maker
    engine.dispose()


def import_text(sessions, text, actor=900):
    headers, _ = Registrations.columns(text)
    Registrations(sessions).import_csv(actor, text, {k: k for k in headers})


def bind_event(sessions):
    with sessions.begin() as s:
        event = s.get(EventConfig, 1)
        event.owner_id = 900
        event.monitoring_team_id = 15
        event.annotator_team_id = 15
        event.source_workspace_id = 81


def test_demo_csv_explicitly_sets_both_people_as_monitors(sessions):
    import_text(sessions, SAMPLE.read_text(encoding='utf-8'))
    with sessions() as s:
        rows = list(s.scalars(select(Registration)))
        assert len(rows) == 1
        assert len(rows[0].members) == 2
        assert [m['role'] for m in rows[0].members] == [
            'participant_monitor', 'participant_monitor'
        ]
        roles = list(s.scalars(select(EventInvitee).order_by(EventInvitee.email)))
        assert len(roles) == 2
        assert [r.event_role for r in roles] == [
            'participant_monitor', 'participant_monitor'
        ]
        assert all(r.native_role == 'reviewer' for r in roles)


def test_explicit_staff_can_monitor_more_than_one_pair(sessions):
    text = (
        'team_name,monitor_email,member_1_email,member_2_email,member_1_role,member_2_role\n'
        'PAIR A,monitor@example.org,a@example.org,b@example.org,participant,participant\n'
        'PAIR B,monitor@example.org,c@example.org,d@example.org,participant,participant\n'
    )
    import_text(sessions, text)
    with sessions() as s:
        staff = s.get(EventInvitee, 'monitor@example.org')
        assert staff.event_role == 'monitor'
        assert staff.native_role == 'manager'
        assert len(staff.registration_ids) == 2
        assert len(list(s.scalars(select(EventInvitee)))) == 5


def test_monitor_participant_overlap_requires_dual_role(sessions):
    text = (
        'team_name,monitor_email,member_1_email,member_2_email,member_1_role,member_2_role\n'
        'PAIR A,a@example.org,a@example.org,b@example.org,participant,participant\n'
    )
    with pytest.raises(WorkflowError, match='participant_monitor'):
        import_text(sessions, text)


def test_reimport_cannot_silently_elevate_pending_invite(sessions):
    original = (
        'team_name,member_1_email,member_2_email,member_1_role,member_2_role\n'
        'PAIR A,a@example.org,b@example.org,participant,participant\n'
    )
    import_text(sessions, original)
    with sessions.begin() as s:
        s.add(SharedInvitation(team_id=15, email='a@example.org', status='pending'))
    new = original.replace('participant,participant', 'participant_monitor,participant')
    with pytest.raises(WorkflowError, match='already exists|cannot silently'):
        import_text(sessions, new)
    with sessions() as s:
        assert s.get(EventInvitee, 'a@example.org').native_role == 'annotator'


class FakeApi:
    def __init__(self, accounts, native=None):
        self.accounts = accounts
        self.native = native or []
        self.calls = []
        self.user = NS(
            get_list=lambda: [NS(**x) for x in accounts.values()],
            get_info_by_login=lambda login: NS(**next(
                x for x in accounts.values() if x['login'] == login)),
        )
        self.role = NS(get_list=lambda: [
            NS(id=3, role='manager'), NS(id=4, role='reviewer'), NS(id=5, role='annotator')
        ])

    def post(self, operation, data, **kwargs):
        if operation == 'members.list':
            return NS(json=lambda: {'entities': self.native, 'pagesCount': 1})
        assert operation == 'members.add'
        self.calls.append(data)
        return NS(json=lambda: {'userId': 201})


def demo_accounts():
    return {
        'tomas.gaming.21@gmail.com': {
            'id': 201, 'login': 'nightjar-gaming',
            'email': 'tomas.gaming.21@gmail.com', 'disabled': False,
        },
        'tomas.santos.work2002@gmail.com': {
            'id': 202, 'login': 'nightjar-work',
            'email': 'tomas.santos.work2002@gmail.com', 'disabled': False,
        },
    }


def test_native_invites_receive_declared_reviewer_roles(sessions):
    import_text(sessions, SAMPLE.read_text(encoding='utf-8'))
    bind_event(sessions)
    api = FakeApi(demo_accounts())
    result = SharedTeamInvitations(sessions).invite(NS(api=api), 900)
    assert result['invited'] == 2
    assert [x['roleId'] for x in api.calls] == [4, 4]
    assert sorted(x['login'] for x in api.calls) == [
        'nightjar-gaming', 'nightjar-work'
    ]
    with sessions() as s:
        assert all(s.get(SharedInvitation, (15, email)).status == 'pending'
                   for email in demo_accounts())


def test_accepted_native_roles_register_both_as_event_monitors(sessions, monkeypatch):
    import_text(sessions, SAMPLE.read_text(encoding='utf-8'))
    bind_event(sessions)
    monkeypatch.setenv('MONITORING_PILOT', '1')
    native = [
        {'userId': account['id'], 'login': account['login'],
         'email': account['email'], 'roleId': 4, 'activated': True}
        for account in demo_accounts().values()
    ]
    api = FakeApi(demo_accounts(), native=native)
    result = SharedTeamInvitations(sessions).refresh(NS(api=api), 900)
    assert next(iter(result.values()))['membership_status'] == 'ready'
    with sessions() as s:
        assert s.get(EventMember, 201).group == 'monitors'
        assert s.get(EventMember, 202).group == 'monitors'
        reg = s.scalar(select(Registration))
        assert reg.monitor_id == 201
        assert {p['status'] for p in reg.members} == {'accepted'}


def test_native_role_mismatch_blocks_readiness(sessions):
    import_text(sessions, SAMPLE.read_text(encoding='utf-8'))
    bind_event(sessions)
    native = [
        {'userId': account['id'], 'login': account['login'],
         'email': account['email'], 'role': 'annotator', 'activated': True}
        for account in demo_accounts().values()
    ]
    result = SharedTeamInvitations(sessions).refresh(NS(api=FakeApi(demo_accounts(), native)), 900)
    assert next(iter(result.values()))['membership_status'] == 'needs_attention'
    with sessions() as s:
        assert not list(s.scalars(select(EventMember).where(EventMember.group == 'monitors')))
        assert all(s.get(SharedInvitation, (15, email)).status == 'role_mismatch'
                   for email in demo_accounts())
