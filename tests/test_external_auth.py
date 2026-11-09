"""External monitor email-code login and revocation contracts (no real SMTP calls)."""
import pytest

from monitoring.db import EventRosterRecord
from monitoring.external_auth import ExternalAuth
from monitoring.service import WorkflowError


def prepare(service):
    with service.sessions.begin() as session:
        session.add(EventRosterRecord(
            record_id='staff-1', source_sheet='Monitors',
            email='monitor@example.test', event_role='monitor',
            team_name='', team_number='', display_name='Monitor',
            source_fields={'monitor_email':'monitor@example.test'},
            needs_review=False))


def test_code_login_requires_allowlist_and_prevents_replay(service,monkeypatch):
    prepare(service)
    monkeypatch.setenv('EXTERNAL_ORGANISER_EMAIL','owner@example.test')
    sent=[]
    monkeypatch.setattr(ExternalAuth,'_send',staticmethod(
        lambda email,code:sent.append((email,code))))
    auth=ExternalAuth(service.sessions,secret='q'*64)
    assert auth.request_code('unknown@example.test') is False
    assert sent==[]
    assert auth.request_code('monitor@example.test') is True
    assert len(sent)==1 and len(sent[0][1])==6
    assert auth.request_code('monitor@example.test') is False
    with pytest.raises(WorkflowError,match='Invalid or expired'):
        auth.verify_code('monitor@example.test','000000' if sent[0][1]!='000000' else '111111')
    token=auth.verify_code('monitor@example.test',sent[0][1])
    assert auth.session_email(token)=='monitor@example.test'
    with pytest.raises(WorkflowError,match='Invalid or expired'):
        auth.verify_code('monitor@example.test',sent[0][1])
    auth.logout(token)
    assert auth.session_email(token) is None


def test_role_revocation_disables_existing_session(service,monkeypatch):
    prepare(service)
    monkeypatch.setenv('EXTERNAL_ORGANISER_EMAIL','owner@example.test')
    sent=[]
    monkeypatch.setattr(ExternalAuth,'_send',staticmethod(
        lambda email,code:sent.append(code)))
    auth=ExternalAuth(service.sessions,secret='r'*64)
    auth.request_code('monitor@example.test')
    token=auth.verify_code('monitor@example.test',sent.pop())
    with service.sessions.begin() as session:
        session.get(EventRosterRecord,'staff-1').event_role='participant'
    assert auth.session_email(token) is None


def test_owner_can_log_in_before_monitor_import(service,monkeypatch):
    monkeypatch.setenv('EXTERNAL_ORGANISER_EMAIL','owner@example.test')
    sent=[]
    monkeypatch.setattr(ExternalAuth,'_send',staticmethod(
        lambda email,code:sent.append(code)))
    auth=ExternalAuth(service.sessions,secret='p'*64)
    assert auth.request_code('owner@example.test')
    assert auth.session_email(auth.verify_code('owner@example.test',sent[0]))=='owner@example.test'
