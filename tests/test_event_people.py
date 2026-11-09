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
    assert not one['invitable']  # Pair must be prepared first.
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
