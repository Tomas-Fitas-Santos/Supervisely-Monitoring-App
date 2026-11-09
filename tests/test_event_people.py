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


def test_demo_import_keeps_all_staff_then_creates_real_dual_role_pair(service):
    manager=EventPeople(service.sessions)
    encoded=sample(monitors=['first@example.test','second@example.test',
                             'third@example.test'])
    result=manager.import_workbook(90,encoded)
    assert result=={'monitors':3,'participants':0,'needs_review':0,'email_overlap':0}
    profiles=manager.overview()
    assert len(profiles)==3
    manager.set_role(90,profiles[0]['id'],'participant_monitor')
    manager.set_role(90,profiles[1]['id'],'participant_monitor')
    count=manager.prepare_demo_pair(90,[
        profiles[0]['email'],profiles[1]['email']])
    assert count==1
    with service.sessions() as session:
        team=session.scalar(select(Registration))
        assert len(team.members)==2
        assert {member['role'] for member in team.members}=={'participant_monitor'}
        assert len(list(session.scalars(select(EventInvitee))))==3


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
    assert one['source_fields']['phone']=='999000111'
    assert manager.prepare_pairs(90)==1
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
