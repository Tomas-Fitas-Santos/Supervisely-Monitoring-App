"""Event seating and audited food/drink ticket contract tests."""
import pytest
from sqlalchemy import select

from monitoring.db import Audit, EventConfig, EventMember, FoodDrinkTransaction, Registration, TeamSeat
from monitoring.event_logistics import EventLogistics
from monitoring.registration import Registrations
from monitoring.service import WorkflowError


def seed(service):
    with service.sessions.begin() as session:
        session.get(EventConfig, 1).owner_id = 90
        for uid in (90,91,92):
            session.add(EventMember(user_id=uid, group='monitors',
                login=f'monitor{uid}', name=f'Monitor {uid}'))
    Registrations(service.sessions).import_csv(90,
        'team_name,member_1_email,member_2_email,member_1_role,member_2_role\n'
        'Bird Pair,first@example.test,second@example.test,participant,participant\n',
        {k:k for k in ('team_name','member_1_email','member_2_email',
                      'member_1_role','member_2_role')})
    with service.sessions.begin() as session:
        team = session.scalar(select(Registration))
        team.monitor_id = 91
        return team.id


def test_table_change_visible_during_checkin_and_scoped(service):
    reg = seed(service)
    desk = EventLogistics(service.sessions)
    with pytest.raises(WorkflowError, match='assigned monitor'):
        desk.set_table(92, reg, '17')
    assert desk.set_table(91, reg, '17')
    assert not desk.set_table(91, reg, '17')
    assert desk.overview(90,organiser=True)['teams'][0]['table_number']=='17'
    assert desk.set_table(90,reg,'24',organiser=True)
    assert desk.overview(91)['teams'][0]['table_number']=='24'
    with service.sessions() as session:
        assert session.get(TeamSeat,reg).table_number=='24'


def test_ticket_issue_redeem_recovery_idempotency(service):
    reg=seed(service)
    desk=EventLogistics(service.sessions)
    email='first@example.test'
    assert desk.ticket_action(91,'issue0001',reg,email,'pizza','issue',2)
    assert not desk.ticket_action(91,'issue0001',reg,email,'pizza','issue',2)
    assert desk.ticket_action(92,'redeem001',reg,email,'pizza','redeem',1)
    with pytest.raises(WorkflowError,match='No unredeemed'):
        desk.ticket_action(92,'redeem002',reg,email,'pizza','redeem',2)
    with pytest.raises(WorkflowError,match='assigned monitor'):
        desk.ticket_action(92,'issue0002',reg,email,'pizza','issue',1)
    assert desk.ticket_action(91,'undo00001',reg,email,'pizza',
                              'undo_redemption',1,'Reception correction')
    view=desk.overview(90,organiser=True)
    person=view['teams'][0]['members'][0]
    assert person['pizza']=={'issued':2,'redeemed':0}
    assert desk.ticket_action(91,'void00001',reg,email,'pizza',
                              'void_issue',1,'Extra ticket')
    with service.sessions() as session:
        assert len(list(session.scalars(select(FoodDrinkTransaction))))==4
        assert len([entry for entry in session.scalars(select(Audit))
            if entry.action.startswith('food_drink_')])==4


def test_no_redemption_without_issued_ticket(service):
    reg=seed(service)
    desk=EventLogistics(service.sessions)
    with pytest.raises(WorkflowError,match='No unredeemed'):
        desk.ticket_action(91,'drink0001',reg,'second@example.test',
                            'drink','redeem',1)
