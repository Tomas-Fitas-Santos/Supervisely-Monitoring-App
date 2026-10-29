from types import SimpleNamespace as NS
from unittest.mock import Mock
from sqlalchemy import select
import pytest
from monitoring.registration import Registrations
from monitoring.db import Audit, Registration
from monitoring.service import WorkflowError

CSV='Team,First email,Second email\n"Garden, birds",A@example.test,b@example.test\n'
MAPPING={'team_name':'Team','member_1_email':'First email','member_2_email':'Second email'}


def test_csv_mapping_quoted_values_bom_and_idempotence(service):
    r=Registrations(service.sessions);assert r.import_csv(90,'\ufeff'+CSV,MAPPING)==1
    row=r.rows()[0];assert row['team_name']=='Garden, birds' and row['members'][0]['email']=='a@example.test'
    r.import_csv(90,CSV,MAPPING);assert len(r.rows())==1


@pytest.mark.parametrize('text',[
    'Team,First email,Second email\nA,a@example.test,a@example.test\n',
    'Team,First email,Second email\nA,not-email,b@example.test\n',
    'Team,First email,Second email\nA,a@example.test,b@example.test\nB,a@example.test,c@example.test\n',
    'Team,First email,Second email\nA,a@example.test\n'])
def test_invalid_registrations_fail_atomically(service,text):
    r=Registrations(service.sessions)
    with pytest.raises(WorkflowError):r.import_csv(90,text,MAPPING)
    assert r.rows()==[]


def test_account_resolution_does_not_assume_email_is_login(service):
    r=Registrations(service.sessions);r.import_csv(90,CSV,MAPPING)
    gw=Mock();gw.visible_accounts.return_value=[NS(id=40,login='different_login',email='a@example.test',name='A',disabled=False),NS(id=41,login='b_login',email='b@example.test',name='B',disabled=False)]
    r.resolve(gw,90)
    row=r.rows()[0];assert row['ready'] and row['members'][0]['login']=='different_login'


def test_hidden_email_requires_explicit_audited_identity_verification(service):
    r=Registrations(service.sessions);r.import_csv(90,CSV,MAPPING);row=r.rows()[0]
    gw=Mock();gw.visible_accounts.return_value=[];gw.lookup_login.return_value=NS(id=40,login='verified_login',email=None,name='A',disabled=False)
    r.resolve(gw,90,row['id'],{'0':'verified_login'});assert not r.rows()[0]['members'][0]['user_id']
    r.resolve(gw,90,row['id'],{'0':'verified_login','verified_0':True});assert r.rows()[0]['members'][0]['status']=='verified'
    gw.lookup_login.return_value.email='someone-else@example.test'
    r.resolve(gw,90,row['id'],{'0':'verified_login','verified_0':True});assert r.rows()[0]['members'][0]['status']=='unresolved'
    with service.sessions() as s:assert any(a.action=='registration_matched' for a in s.scalars(select(Audit)))


def test_participant_cannot_move_to_another_imported_team(service):
    r=Registrations(service.sessions);r.import_csv(90,CSV,MAPPING)
    with pytest.raises(WorkflowError,match='another'):
        r.import_csv(90,'Team,First email,Second email\nNew,a@example.test,new@example.test\n',MAPPING)


def test_unknown_accounts_block_provisioning_before_remote_mutation(service):
    r=Registrations(service.sessions);r.import_csv(90,CSV,MAPPING);setup=Mock()
    with pytest.raises(WorkflowError,match='Resolve'):r.provision(Mock(),setup,90,r.rows()[0]['id'])
    setup.register_team.assert_not_called()


def test_google_form_one_participant_per_row_groups_exact_pairs(service):
    text='Team,Email,Name\nGarden,a@example.test,A\nGarden,b@example.test,B\n'
    mapping={'team_name':'Team','participant_email':'Email','participant_name':'Name'}
    r=Registrations(service.sessions);assert r.import_csv(90,text,mapping)==1
    assert [m['email'] for m in r.rows()[0]['members']]==['a@example.test','b@example.test']
    with pytest.raises(WorkflowError,match='exactly two'):r.import_csv(90,'Team,Email,Name\nSingle,c@example.test,C\n',mapping)
