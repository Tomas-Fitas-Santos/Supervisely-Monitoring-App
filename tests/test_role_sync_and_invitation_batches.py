"""Joined-member role synchronization and deterministic invitation batches.

No real Supervisely instance or user email is contacted in these tests.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon.app import app
from annotatiathon.db import get_db
from annotatiathon.models import (
    DEFAULT_EVENT_ID,
    Base,
    Event,
    Person,
    StaffAccount,
    StaffSession,
    SuperviselyConfig,
)
from annotatiathon.security import password_hash
from annotatiathon.supervisely_client import NativeAPIError, NativeMember


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with maker.begin() as db:
        db.add(Event(id=DEFAULT_EVENT_ID, name="Real Test Event"))
        db.add(SuperviselyConfig(id=1, server_url="https://app.supervisely.com", team_id=125980))
        db.add(StaffAccount(
            email="owner@example.org", name="Team Owner",
            password_hash=password_hash("SecretOwnerPassword123!"), roles=["organiser"],
        ))
        db.add(StaffAccount(
            email="monitor@example.org", name="Other Monitor",
            password_hash=password_hash("SecretMonitorPassword123!"), roles=["monitor"],
        ))

    def override_db():
        with maker() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        "annotatiathon.auth.get_settings",
        lambda: type("Settings", (), {"app_env": "local"})(),
    )
    with TestClient(app) as http:
        yield http, maker, monkeypatch
    app.dependency_overrides.clear()
    engine.dispose()


def login(http, email="owner@example.org", password="SecretOwnerPassword123!"):
    result = http.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert result.status_code == 200, result.text
    return {"X-CSRF-Token": result.json()["csrf_token"]}


def test_invite_all_uninvited_up_to_50_by_role_without_page_filter(setup):
    http, maker, _ = setup
    header = login(http)
    with maker.begin() as db:
        for n in range(120):
            status = "not_invited"
            if n == 2:
                status = "reported_sent"
            if n == 4:
                status = "joined"
            db.add(Person(
                event_id=DEFAULT_EVENT_ID, name=f"Monitor {n:03}",
                email=f"monitor{n:03}@example.org", kind="monitor",
                membership_status=status,
            ))
        for n in range(63):
            db.add(Person(
                event_id=DEFAULT_EVENT_ID, name=f"Participant {n:03}",
                email=f"person{n:03}@example.org", kind="participant",
            ))
    first=http.get("/api/v1/people/invitations/next?kind=monitor&limit=50")
    assert first.status_code==200, first.text
    assert first.json()["available"]==118
    assert first.json()["selected"]==50
    assert first.json()["more_after_batch"]==68
    ids=[x["id"] for x in first.json()["items"]]
    emails=[x["email"] for x in first.json()["items"]]
    assert "monitor002@example.org" not in emails
    assert "monitor004@example.org" not in emails
    assert len(set(ids))==50
    participants=http.get("/api/v1/people/invitations/next?kind=participant")
    assert participants.status_code==200
    assert participants.json()["available"]==63
    assert participants.json()["selected"]==50
    assert participants.json()["more_after_batch"]==13

    send=http.post("/api/v1/people/invitations-reported",
      headers=header,json={"person_ids":ids})
    assert send.status_code==200, send.text
    assert send.json()["recorded"]==50
    # A second click cannot silently mark the same recipients as invited.
    assert http.post("/api/v1/people/invitations-reported",
      headers=header,json={"person_ids":ids}).status_code==409
    following=http.get("/api/v1/people/invitations/next?kind=monitor")
    assert following.json()["available"]==68
    assert following.json()["selected"]==50
    assert {x["id"] for x in following.json()["items"]}.isdisjoint(ids)


def test_update_joined_role_changes_supervisely_then_local(setup):
    http, maker, monkeypatch = setup
    header=login(http)
    native_calls=[]

    def native(config, *, user_id, target_role, expected_role):
        native_calls.append((config.team_id,user_id,target_role,expected_role))
        return NativeMember(id=user_id,login="Joined",email="joined@example.org",
                            role=target_role)

    monkeypatch.setattr("annotatiathon.people_api.change_member_role",native)
    with maker.begin() as db:
        db.add(Person(
            event_id=DEFAULT_EVENT_ID, name="Joined",
            email="joined@example.org", kind="participant",
            membership_status="joined", supervisely_user_id=8801,
            supervisely_role="annotator", supervisely_login="Joined",
        ))
    pid=http.get("/api/v1/people").json()["items"][0]["id"]
    result=http.patch("/api/v1/people/"+pid,headers=header,json={"kind":"monitor"})
    assert result.status_code==200,result.text
    assert native_calls==[(125980,8801,"manager","annotator")]
    assert result.json()["kind"]=="monitor"
    assert result.json()["supervisely_role"]=="manager"
    with maker() as db:
        record=db.get(Person,pid)
        assert record.kind=="monitor"
        assert record.supervisely_role=="manager"


def test_native_role_failure_retains_local_role(setup):
    http, maker, monkeypatch=setup
    header=login(http)

    def reject(*args,**kwargs):
        raise NativeAPIError("unauthorised","Supervisely rejected the credential or team access.")

    monkeypatch.setattr("annotatiathon.people_api.change_member_role",reject)
    with maker.begin() as db:
        db.add(Person(event_id=DEFAULT_EVENT_ID,name="Joined",
            email="joined@example.org",kind="participant",membership_status="joined",
            supervisely_user_id=8801,supervisely_role="annotator"))
    pid=http.get("/api/v1/people").json()["items"][0]["id"]
    result=http.patch("/api/v1/people/"+pid,headers=header,json={"kind":"monitor"})
    assert result.status_code==502
    with maker() as db:
        assert db.get(Person,pid).kind=="participant"


def test_event_roster_demotion_does_not_revoke_app_account(setup):
    http, maker, monkeypatch=setup
    login(http)
    with maker.begin() as db:
        db.add(Person(event_id=DEFAULT_EVENT_ID,name="Other Monitor",
            email="monitor@example.org",kind="monitor",membership_status="joined",
            supervisely_user_id=8802,supervisely_role="manager",
            source_fields={"monitor_registration_approved":True}))

    def native(config, *, user_id, target_role, expected_role):
        assert target_role=="annotator"
        return NativeMember(id=user_id,login="OtherMonitor",
                            email="monitor@example.org",role="annotator")

    monkeypatch.setattr("annotatiathon.people_api.change_member_role",native)
    pid=next(x["id"] for x in http.get("/api/v1/people").json()["items"]
             if x["email"]=="monitor@example.org")
    monitor_login=login(http,"monitor@example.org","SecretMonitorPassword123!")
    assert http.get("/api/v1/auth/me").status_code==200
    owner=login(http)
    changed=http.patch("/api/v1/people/"+pid,headers=owner,json={"kind":"participant"})
    assert changed.status_code==200, changed.text
    assert changed.json()["supervisely_role"]=="annotator"
    with maker() as db:
        member=db.scalar(select(StaffAccount).where(
            StaffAccount.email=="monitor@example.org"))
        assert member.active is True
        assert db.scalars(select(StaffSession).where(
            StaffSession.staff_id==member.id)).all()
    response=http.post("/api/v1/auth/login",json={
        "email":"monitor@example.org","password":"SecretMonitorPassword123!"})
    assert response.status_code==200
    assert monitor_login


def test_organiser_role_cannot_be_demoted(setup):
    http, maker, monkeypatch=setup
    header=login(http)
    with maker.begin() as db:
        db.add(Person(event_id=DEFAULT_EVENT_ID,name="Team Owner",
            email="owner@example.org",kind="monitor",membership_status="joined",
            supervisely_user_id=123550,supervisely_role="admin"))
    pid=http.get("/api/v1/people").json()["items"][0]["id"]
    called=[]
    monkeypatch.setattr(
        "annotatiathon.people_api.change_member_role",
        lambda *a,**kw: called.append(1),
    )
    assert http.patch("/api/v1/people/"+pid,headers=header,
                      json={"kind":"participant"}).status_code==409
    assert not called
    with maker() as db:
        assert db.get(Person,pid).kind=="monitor"


def test_pending_invitation_role_edit_does_not_silently_diverge(setup):
    http, maker, _ = setup
    header = login(http)
    with maker.begin() as db:
        db.add(Person(
            event_id=DEFAULT_EVENT_ID,
            name="Previously invited",
            email="pending@example.org",
            kind="monitor",
            membership_status="reported_sent",
            supervisely_user_id=None,
        ))
    person_id = next(
        p["id"] for p in http.get("/api/v1/people").json()["items"]
        if p["email"] == "pending@example.org"
    )
    response = http.patch(
        "/api/v1/people/" + person_id, headers=header, json={"kind": "participant"},
    )
    assert response.status_code == 409
    assert "pending" in response.json()["detail"].lower()
    with maker() as db:
        assert db.get(Person, person_id).kind == "monitor"



def test_saving_existing_participant_repairs_stale_native_manager_role(setup):
    """Regression: screenshot shows Participant in app, Manager on Supervisely."""
    http, maker, monkeypatch = setup
    csrf = login(http)
    calls = []

    def update(config, *, user_id, target_role, expected_role):
        calls.append((user_id, target_role, expected_role))
        return NativeMember(id=user_id, login="other", email="other@example.org",
                            role="annotator")

    monkeypatch.setattr("annotatiathon.people_api.change_member_role", update)
    with maker.begin() as db:
        db.add(Person(
            event_id=DEFAULT_EVENT_ID, name="Other", email="other@example.org",
            kind="participant", membership_status="joined",
            supervisely_user_id=8803, supervisely_role="manager",
            supervisely_login="other",
        ))
    person_id = next(p["id"] for p in http.get("/api/v1/people").json()["items"]
                     if p["email"] == "other@example.org")
    result = http.patch("/api/v1/people/" + person_id, headers=csrf,
                        json={"kind": "participant", "name": "Other"})
    assert result.status_code == 200, result.text
    assert calls == [(8803, "annotator", "manager")]
    assert result.json()["kind"] == "participant"
    assert result.json()["supervisely_role"] == "annotator"
    with maker() as db:
        person = db.get(Person, person_id)
        assert person.kind == "participant"
        assert person.supervisely_role == "annotator"


def test_mismatched_native_role_save_failure_does_not_claim_success(setup):
    http, maker, monkeypatch = setup
    csrf = login(http)

    def broken(*args, **kwargs):
        raise NativeAPIError(
            "timeout", "Supervisely timed out; retry later.",
        )

    monkeypatch.setattr("annotatiathon.people_api.change_member_role", broken)
    with maker.begin() as db:
        db.add(Person(
            event_id=DEFAULT_EVENT_ID, name="Mismatch",
            email="mismatch@example.org", kind="participant",
            membership_status="joined", supervisely_user_id=8810,
            supervisely_role="manager",
        ))
    person_id = next(p["id"] for p in http.get("/api/v1/people").json()["items"]
                     if p["email"] == "mismatch@example.org")
    result = http.patch("/api/v1/people/" + person_id, headers=csrf,
                        json={"kind": "participant", "name": "Not applied"})
    assert result.status_code == 502, result.text
    assert "Supervisely timed out" in result.text
    with maker() as db:
        person = db.get(Person, person_id)
        assert person.name == "Mismatch"
        assert person.supervisely_role == "manager"


def test_normal_roster_edit_does_not_call_native_when_roles_match(setup):
    http, maker, monkeypatch = setup
    csrf = login(http)
    calls = []
    monkeypatch.setattr(
        "annotatiathon.people_api.change_member_role",
        lambda *args, **kwargs: calls.append(1),
    )
    with maker.begin() as db:
        db.add(Person(event_id=DEFAULT_EVENT_ID, name="Good",
            email="good@example.org", kind="monitor",
            membership_status="joined", supervisely_user_id=9001,
            supervisely_role="manager",
        ))
    person_id = next(p["id"] for p in http.get("/api/v1/people").json()["items"]
                     if p["email"] == "good@example.org")
    result = http.patch("/api/v1/people/" + person_id, headers=csrf,
                        json={"kind": "monitor", "name": "Updated"})
    assert result.status_code == 200, result.text
    assert result.json()["name"] == "Updated"
    assert calls == []
