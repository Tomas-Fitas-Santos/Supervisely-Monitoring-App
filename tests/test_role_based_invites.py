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


def test_bulk_invitations_load_directory_and_roles_once_per_request(sessions):
    from unittest.mock import Mock
    from monitoring.request_api import RequestApi
    text = 'team_name,member_1_email,member_2_email\n'
    accounts = {}
    for index in range(20):
        text += f'Pair {index},a{index}@example.org,b{index}@example.org\n'
        for prefix in ('a', 'b'):
            email = f'{prefix}{index}@example.org'
            accounts[email] = dict(id=len(accounts)+100, login=f'{prefix}{index}', email=email, disabled=False)
    import_text(sessions, text)
    bind_event(sessions)
    api = FakeApi(accounts)
    api.user.get_list = Mock(wraps=api.user.get_list)
    api.role.get_list = Mock(wraps=api.role.get_list)
    api.post = Mock(wraps=api.post)
    result = SharedTeamInvitations(sessions).invite(NS(api=RequestApi(api)), 900)
    assert result['invited'] == 40
    api.user.get_list.assert_called_once()
    api.role.get_list.assert_called_once()
    assert sum(call.args[0] == 'members.add' for call in api.post.call_args_list) == 40
    assert sum(call.args[0] == 'members.list' for call in api.post.call_args_list) == 4


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


def test_simple_csv_assigns_participant_roles_from_participant_columns(sessions):
    bind_event(sessions)
    text = 'team_name,member_1_email,member_2_email,monitor_email\nPAIR A,a@example.org,b@example.org,monitor@example.org\n'
    import_text(sessions, text)
    with sessions() as session:
        assert session.get(EventInvitee, 'a@example.org').native_role == 'annotator'
        assert session.get(EventInvitee, 'monitor@example.org').native_role == 'manager'


def test_separate_monitor_is_invited_with_manager_role(sessions):
    text = (
        'team_name,monitor_email,member_1_email,member_2_email,member_1_role,member_2_role\n'
        'PAIR A,monitor@example.org,a@example.org,b@example.org,participant,participant\n'
    )
    import_text(sessions, text)
    bind_event(sessions)
    accounts = {
        email: {'id': index, 'login': 'native-' + str(index), 'email': email,
                'disabled': False}
        for index, email in enumerate(
            ['a@example.org', 'b@example.org', 'monitor@example.org'], 300)
    }
    api = FakeApi(accounts)
    results = SharedTeamInvitations(sessions).invite(NS(api=api), 900)
    assert results['invited'] == 3
    roles = {next(u['email'] for u in accounts.values()
                  if u['login'] == payload['login']): payload['roleId']
             for payload in api.calls}
    assert roles == {
        'a@example.org': 5, 'b@example.org': 5, 'monitor@example.org': 3,
    }


def import_invitation_roster(sessions):
    bind_event(sessions)
    import_text(sessions,
        'team_name,monitor_email,member_1_email,member_2_email,member_1_name,member_1_role,member_2_role\n'
        'A,monitor@example.org,a@example.org,b@example.org,Ana,participant_monitor,participant\n'
        'B,monitor@example.org,c@example.org,d@example.org,Carla,participant,participant\n')
    return {email: {'id': index, 'login': 'native-' + str(index), 'email': email, 'disabled': False}
            for index, email in enumerate(['a@example.org','b@example.org','c@example.org','d@example.org','monitor@example.org'], 300)}


@pytest.mark.parametrize('audience,email,expected', [
    ('monitors', None, {'a@example.org','monitor@example.org'}),
    ('participants', None, {'a@example.org','b@example.org','c@example.org','d@example.org'}),
    ('everyone', None, {'a@example.org','b@example.org','c@example.org','d@example.org','monitor@example.org'}),
    ('person', 'b@example.org', {'b@example.org'}),
])
def test_invitation_audience_sends_only_selected_people(sessions, audience, email, expected):
    accounts = import_invitation_roster(sessions)
    api = FakeApi(accounts)
    result = SharedTeamInvitations(sessions).invite(NS(api=api), 900, audience=audience, email=email)
    by_login = {person['login']: address for address, person in accounts.items()}
    assert {by_login[call['login']] for call in api.calls} == expected
    assert result['invited'] == len(expected)


def test_imported_people_are_visible_before_native_invitations_and_are_deduplicated(sessions):
    import_invitation_roster(sessions)
    people = SharedTeamInvitations(sessions).people()
    assert len(people) == 5
    assert {p['status'] for p in people} == {'not_invited'}
    monitor = next(p for p in people if p['email'] == 'monitor@example.org')
    assert sorted(monitor['teams']) == ['A', 'B']
    assert next(p for p in people if p['email'] == 'a@example.org')['name'] == 'Ana'


def test_overlapping_group_invites_do_not_resend_pending_people(sessions):
    api = FakeApi(import_invitation_roster(sessions))
    invitations = SharedTeamInvitations(sessions)
    invitations.invite(NS(api=api), 900, audience='monitors')
    invitations.invite(NS(api=api), 900, audience='participants')
    result = invitations.invite(NS(api=api), 900)
    assert len(api.calls) == 5
    assert result['invited'] == 0 and result['pending'] == 5


@pytest.mark.parametrize('audience,email', [('person','outside@example.org'),('invalid',None)])
def test_invalid_invitation_selection_cannot_send_anything(sessions, audience, email):
    api = FakeApi(import_invitation_roster(sessions))
    with pytest.raises(WorkflowError):
        SharedTeamInvitations(sessions).invite(NS(api=api), 900, audience=audience, email=email)
    assert not api.calls


def test_person_rows_import_the_team_monitor_without_extra_setup(sessions):
    bind_event(sessions)
    import_text(sessions,
        'team_name,participant_email,monitor_email\n'
        'A,a@example.org,monitor@example.org\n'
        'A,b@example.org,monitor@example.org\n')
    people = SharedTeamInvitations(sessions).people()
    assert len(people) == 3
    assert next(p for p in people if p['role'] == 'monitor')['email'] == 'monitor@example.org'


def test_explicit_selected_invites_only_the_named_accounts_and_preserves_roles(sessions):
    accounts = import_invitation_roster(sessions)
    api = FakeApi(accounts)
    gateway = NS(api=api)
    invitations = SharedTeamInvitations(sessions)
    result = invitations.invite(
        gateway, 900, selected_emails=['B@EXAMPLE.ORG', 'monitor@example.org'])
    assert result == {'invited': 2, 'pending': 0, 'accepted': 0, 'failed': 0}
    native_to_email = {entry['login']: email for email, entry in accounts.items()}
    assert {native_to_email[call['login']] for call in api.calls} == {
        'b@example.org', 'monitor@example.org'}
    roles = {native_to_email[call['login']]: call['roleId'] for call in api.calls}
    assert roles == {'b@example.org': 5, 'monitor@example.org': 3}
    with sessions() as s:
        assert s.get(SharedInvitation, (15, 'a@example.org')).status == 'not_invited'
        assert s.get(SharedInvitation, (15, 'b@example.org')).status == 'pending'
    # Re-selecting already pending people does not resend. Another selection
    # can be invited later without touching previously unselected accounts.
    retry = invitations.invite(
        gateway, 900, selected_emails=['b@example.org', 'monitor@example.org'])
    assert retry['invited'] == 0 and retry['pending'] == 2
    assert len(api.calls) == 2
    later = invitations.invite(gateway, 900, selected_emails=['a@example.org'])
    assert later['invited'] == 1
    assert len(api.calls) == 3


@pytest.mark.parametrize('selection', [
    [], 'a@example.org', {'emails':['a@example.org']}, [5],
    ['a@example.org', 'A@example.org'],
    ['a@example.org', 'not-in-roster@example.org'],
])
def test_selected_invites_reject_invalid_or_empty_batches_without_native_writes(
        sessions, selection):
    api = FakeApi(import_invitation_roster(sessions))
    with pytest.raises(WorkflowError):
        SharedTeamInvitations(sessions).invite(
            NS(api=api), 900, selected_emails=selection)
    assert not api.calls


def test_selected_invites_fail_closed_for_unreviewed_roster_records(sessions):
    from monitoring.db import EventRosterRecord
    accounts = import_invitation_roster(sessions)
    with sessions.begin() as s:
        s.add(EventRosterRecord(
            record_id='blocked-b', source_sheet='Participants',
            team_number='2', team_name='Pending Pair',
            display_name='Blocked registrant', email='b@example.org',
            event_role='participant', needs_review=True,
            source_fields={'data_processing_consent': 'Não'}))
    api = FakeApi(accounts)
    with pytest.raises(WorkflowError, match='need review'):
        SharedTeamInvitations(sessions).invite(
            NS(api=api), 900, selected_emails=['a@example.org', 'b@example.org'])
    # Verify the entire batch was rejected without partially inviting a.
    assert not api.calls


def test_selected_invitation_roster_frontend_requires_explicit_selection():
    source = (Path(__file__).resolve().parents[1] /
              'monitoring/static/event_registrations.js').read_text(encoding='utf-8')
    handler = (Path(__file__).resolve().parents[1] /
               'monitoring/ui.py').read_text(encoding='utf-8')
    assert "v-model=\"inviteEmails\"" in source
    assert "Select matching eligible" in source
    assert "Invite selected" in source
    assert "registration_invite_selected" in source
    assert "registration_provision_all" not in source
    assert "selected_emails=addresses" in handler
    assert "if not isinstance(addresses, list) or not addresses:" in handler



def test_email_only_native_invitation_when_global_user_directory_is_unavailable(sessions):
    """The API login field may receive the exact roster email as fallback."""
    accounts = import_invitation_roster(sessions)
    api = FakeApi(accounts)

    def no_global_directory():
        raise PermissionError('Supervisely instance-wide users.list is unavailable')

    api.user.get_list = no_global_directory
    invitation = SharedTeamInvitations(sessions).invite(
        NS(api=api), 900, selected_emails=['b@example.org'])
    assert invitation['invited'] == 1
    assert len(api.calls) == 1
    assert api.calls[0] == {'teamId':15, 'login':'b@example.org', 'roleId':5}
    with sessions() as session:
        evidence = session.get(SharedInvitation, (15, 'b@example.org'))
        assert evidence.status == 'pending'
        assert evidence.login == 'b@example.org'


def test_email_resolution_prefers_verified_native_username_when_directory_available(sessions):
    """Email lookup is exact; never guess a username from its local part."""
    accounts = import_invitation_roster(sessions)
    api = FakeApi(accounts)
    invitation = SharedTeamInvitations(sessions).invite(
        NS(api=api), 900, selected_emails=['b@example.org'])
    assert invitation['invited'] == 1
    assert api.calls[0]['login'] == accounts['b@example.org']['login']
    assert api.calls[0]['login'] != 'b'
