"""Both sheets are imported without dropping private registration fields."""
import base64
import io
from openpyxl import Workbook
import pytest
from sqlalchemy import select

from monitoring.db import EventInvitee, EventRosterRecord, Registration, SharedInvitation
from monitoring.event_people import EventPeople
from monitoring.service import WorkflowError


def sample(monitors=None, people=None):
    workbook=Workbook()
    staff=workbook.active
    staff.title='Monitors'
    staff.append(['monitor_email','monitor_name','event_role','native_role'])
    for person in monitors or []:
        staff.append([person,'','monitor','manager'])
    participants=workbook.create_sheet('Participants')
    participants.append(['team_name','team_number','participant_name',
                         'participant_email','participant_role',
                         'data_processing_consent','phone','presence_confirmation'])
    for person in people or []:
        participants.append(person)
    buff=io.BytesIO()
    workbook.save(buff)
    return base64.b64encode(buff.getvalue()).decode()


def test_official_import_keeps_staff_and_registers_dual_role_participants(service):
    manager = EventPeople(service.sessions)
    roster = sample(
        monitors=['first@example.test', 'second@example.test',
                  'third@example.test'],
        people=[
            ['Bird Pair', '1', 'First Bird', 'bird-one@example.test',
             'participant_monitor', 'Sim', '', 'Confirmado'],
            ['Bird Pair', '1', 'Second Bird', 'bird-two@example.test',
             'participant_monitor', 'Sim', '', 'Confirmado'],
        ])
    result = manager.import_workbook(90, roster)
    assert result == {'monitors': 3, 'participants': 2,
                      'needs_review': 0, 'email_overlap': 0}
    assert len(manager.overview()) == 5
    assert manager.prepare_pairs(90) == 1
    with service.sessions() as session:
        team = session.scalar(select(Registration))
        assert len(team.members) == 2
        assert {member['role'] for member in team.members} == {'participant_monitor'}
        assert len(list(session.scalars(select(EventInvitee)))) == 5

def test_official_import_preserves_metadata_and_flags_nonconsented_people(service):
    people=[
        ['Bird Pair','17','Bird One','one@example.test','participant',
         'Sim','999000111','Confirmado'],
        ['Bird Pair','17','Bird Two','two@example.test','participant',
         'Sim','999000222','Confirmado'],
        ['Other Pair','19','Bird Three','three@example.test','participant',
         'Não','999000333','Desistiu'],
        ['Other Pair','19','Bird Four','','participant',
         'Sim','999000444',''],
    ]
    manager=EventPeople(service.sessions)
    result=manager.import_workbook(90,sample(monitors=['staff@example.test'],people=people))
    assert result['participants']==4
    assert result['needs_review']==2
    profiles=manager.overview()
    one=next(p for p in profiles if p['email']=='one@example.test')
    staff=next(p for p in profiles if p['email']=='staff@example.test')
    blocked=next(p for p in profiles if p['email']=='three@example.test')
    assert one['source_fields']['phone']=='999000111'
    assert one['invitable']  # Consent-cleared CSV/workbook users can be invited before pairing.
    assert staff['invitable']  # Monitor invitations do not need a pair.
    assert not blocked['invitable']  # No consent, cancellation.
    assert manager.prepare_pairs(90)==1
    prepared=manager.overview()
    assert next(p for p in prepared if p['email']=='one@example.test')['invitable']
    assert next(p for p in prepared if p['email']=='two@example.test')['invitable']
    assert not next(p for p in prepared if p['email']=='three@example.test')['invitable']
    with service.sessions() as session:
        rows=list(session.scalars(select(Registration)))
        assert len(rows)==1
        assert rows[0].team_name=='Bird Pair'


def test_role_cannot_silently_override_live_invitation(service):
    people=EventPeople(service.sessions)
    people.import_workbook(90,sample(monitors=['staff@example.test']))
    record=people.overview()[0]
    with service.sessions.begin() as session:
        session.add(SharedInvitation(team_id=125980,email='staff@example.test',
                                     status='accepted'))
    with pytest.raises(WorkflowError,match='native role'):
        people.set_role(90,record['id'],'participant_monitor')
    with service.sessions() as session:
        assert session.get(EventRosterRecord,record['id']).event_role=='monitor'



def test_two_csv_imports_supply_role_ready_emails_without_job_pairing(service):
    """CSV upload must support both sheets without sending native invitations."""
    people = EventPeople(service.sessions)
    monitors = (
        'monitor_email,monitor_name,event_role\n'
        'monitor.one@example.test,Primary monitor,monitor\n'
        'monitor.two@example.test,Secondary monitor,monitor\n'
    )
    participants = (
        'team_name,participant_name,participant_email,participant_role,data_processing_consent\n'
        'Pair A,Ana,ana@example.test,participant,Sim\n'
        'Pair A,Ben,ben@example.test,participant,Yes\n'
        'Pair B,Claire,claire@example.test,participant_monitor,Sim\n'
        'Pair B,David,david@example.test,participant,Não\n'
    )
    first = people.import_csv(90, monitors, 'Monitors')
    assert first['monitors'] == 2
    second = people.import_csv(90, participants, 'Participants')
    assert second['participants'] == 4
    roster = {p['email']: p for p in people.overview()}
    assert roster['ana@example.test']['invitable']
    assert roster['ben@example.test']['invitable']
    assert roster['claire@example.test']['invitable']
    assert not roster['david@example.test']['invitable']
    assert roster['monitor.one@example.test']['invitable']
    with service.sessions() as session:
        assert len(list(session.scalars(select(Registration)))) == 0
        invitees = {x.email:x for x in session.scalars(select(EventInvitee))}
        assert invitees['ana@example.test'].native_role == 'annotator'
        assert invitees['claire@example.test'].native_role == 'reviewer'
        assert invitees['monitor.one@example.test'].native_role == 'manager'
        assert 'david@example.test' not in invitees


def test_csv_import_is_sheet_scoped_and_keeps_other_people(service):
    people = EventPeople(service.sessions)
    people.import_csv(90, (
        'monitor_email,event_role\n'
        'a@example.test,monitor\n'
    ), 'Monitors')
    people.import_csv(90, (
        'team_name,participant_name,participant_email,participant_role,data_processing_consent\n'
        'Pair X,X,x@example.test,participant,Sim\n'
    ), 'Participants')
    names = {p['email'] for p in people.overview()}
    assert names == {'a@example.test', 'x@example.test'}
    people.import_csv(90, (
        'monitor_email,event_role\n'
        'b@example.test,monitor\n'
    ), 'Monitors')
    new = {p['email']:p for p in people.overview()}
    assert new['a@example.test']['event_role'] == 'inactive'
    assert new['x@example.test']['invitable']
    assert new['b@example.test']['invitable']


@pytest.mark.parametrize('tab,csv_text', [
    ('Unknown', 'monitor_email,event_role\na@example.test,monitor\n'),
    ('Monitors', 'monitor_email,event_role\n'),
    ('Monitors', 'email,role\na@example.test,monitor\n'),
    ('Participants', 'participant_email,participant_role\na@example.test,participant\n'),
    ('Participants', 'team_name,participant_name,participant_email,participant_role\n'
     'Pair A,Ana,ana@example.test,participant\n'),
])
def test_csv_upload_schema_and_consent_guards(service, tab, csv_text):
    people = EventPeople(service.sessions)
    if tab == 'Participants' and 'data_processing_consent' not in csv_text and 'team_name,' in csv_text:
        people.import_csv(90, csv_text, tab)
        assert not people.overview()[0]['invitable']
    else:
        with pytest.raises(WorkflowError):
            people.import_csv(90, csv_text, tab)


def test_csv_role_conflict_across_sheets_blocks_invite(service):
    people = EventPeople(service.sessions)
    people.import_csv(90, (
        'monitor_email,event_role\n'
        'duplicate@example.test,monitor\n'
    ), 'Monitors')
    people.import_csv(90, (
        'team_name,participant_name,participant_email,participant_role,data_processing_consent\n'
        'Pair B,Dup,duplicate@example.test,participant,Sim\n'
    ), 'Participants')
    rows = [p for p in people.overview() if p['email']=='duplicate@example.test']
    assert len(rows) == 2
    assert all(p['needs_review'] for p in rows)
    assert all(not p['invitable'] for p in rows)
