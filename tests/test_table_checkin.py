"""Manual receptionist admission is event-scoped and team table numbers are shared."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon.app import app
from annotatiathon.db import get_db
from annotatiathon.models import DEFAULT_EVENT_ID, Base, Event, StaffAccount
from annotatiathon.security import password_hash


@pytest.fixture
def reception(monkeypatch):
    engine = create_engine("sqlite+pysqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        db.add(Event(id=DEFAULT_EVENT_ID, name="Initial Event"))
        db.add(StaffAccount(
            email="desk@example.org", name="Reception",
            roles=["organiser", "reception"],
            password_hash=password_hash("SecureReceptionPassword!"),
        ))
        db.add(StaffAccount(
            email="monitor@example.org", name="Monitor",
            roles=["monitor"],
            password_hash=password_hash("SecureMonitorPassword!"),
        ))
    def override():
        with maker() as db:
            yield db
    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(
        "annotatiathon.auth.get_settings",
        lambda: type("Settings", (), {"app_env": "local"})(),
    )
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={
            "email": "desk@example.org", "password": "SecureReceptionPassword!",
        })
        assert login.status_code == 200
        yield client, login.json()["csrf_token"], maker
    app.dependency_overrides.clear()
    engine.dispose()


def post(client, csrf, path, payload=None, method="POST"):
    return client.request(method, "/api/v1"+path, json=payload,
                          headers={"X-CSRF-Token": csrf})


def test_check_in_without_table_then_assign_shared_table(reception, verify_participants):
    client, csrf, maker = reception
    ids = []
    for n in ["Anna", "Ben"]:
        person = post(client, csrf, "/people", {
            "name": n, "email": n.lower()+"@example.org",
            "kind": "participant", "team_name": "Robins",
        })
        assert person.status_code == 201
        ids.append(person.json()["id"])
    verify_participants(maker, *ids)
    first = post(client, csrf, "/attendance/"+ids[0]+"/check-in")
    assert first.status_code == 200, first.text
    assert first.json()["table_number"] is None
    team_id = first.json()["team_id"]
    assignment = post(client, csrf, f"/attendance/teams/{team_id}/table", {"table_number": 42}, "PUT")
    assert assignment.status_code == 200, assignment.text
    partner = post(client, csrf, "/attendance/"+ids[1]+"/check-in")
    assert partner.status_code == 200
    assert partner.json()["table_number"] == 42
    people = {p["id"]: p for p in client.get("/api/v1/people").json()["items"]}
    assert people[ids[0]]["table_number"] == 42
    assert people[ids[1]]["table_number"] == 42
    assert client.get("/api/v1/teams").json()["teams"][1]["table_number"] == 42
    assert post(client, csrf, "/attendance/"+ids[1]+"/check-in").json()["changed"] is False
    correction = post(client, csrf, "/attendance/teams/"+
                      people[ids[0]]["team_id"]+"/table",
                      {"table_number": 53}, "PUT")
    assert correction.status_code == 200
    updated = client.get("/api/v1/people").json()["items"]
    assert all(p["table_number"] == 53 for p in updated)
    reset = post(client, csrf, "/events/resets/preview",
                 {"mode": "selected", "areas": ["attendance"]})
    assert reset.status_code == 200
    apply = post(client, csrf, "/events/resets/"+reset.json()["plan_id"]+"/execute",
                 {"confirmation": reset.json()["confirmation_phrase"]})
    assert apply.status_code == 200
    new = client.get("/api/v1/people").json()["items"]
    assert all(p["check_in_at"] is None and p["table_number"] is None for p in new)


def test_cannot_change_team_table_by_checking_in_partner(reception, verify_participants):
    client, csrf, maker = reception
    ids = []
    for name in ["A", "B"]:
        out = post(client, csrf, "/people", {
            "name": name, "email": name.lower()+"@example.org", "kind": "participant",
            "team_name": "Ducks",
        })
        ids.append(out.json()["id"])
    verify_participants(maker, *ids)
    first = post(client, csrf, "/attendance/"+ids[0]+"/check-in")
    assert first.status_code == 200
    team_id = first.json()["team_id"]
    assert post(client, csrf, f"/attendance/teams/{team_id}/table", {"table_number": 17}, "PUT").status_code == 200
    partner = post(client, csrf, "/attendance/"+ids[1]+"/check-in", {"table_number": 25})
    assert partner.status_code == 200
    assert partner.json()["table_number"] == 17
    assert client.get("/api/v1/attendance/summary").json()["present"] == 2


def test_checkout_preserves_table_after_last_participant_leaves(reception, verify_participants):
    client, csrf, maker = reception
    ids = []
    for name in ("Ava", "Bea"):
        created = post(client, csrf, "/people", {
            "name": name, "email": name.lower()+"@example.org",
            "kind": "participant", "team_name": "Finches",
        })
        assert created.status_code == 201
        ids.append(created.json()["id"])
    verify_participants(maker, *ids)
    arrival = post(client, csrf, "/attendance/"+ids[0]+"/check-in")
    assert arrival.status_code == 200
    team_id = arrival.json()["team_id"]
    assert post(client, csrf, f"/attendance/teams/{team_id}/table", {"table_number": 12}, "PUT").status_code == 200
    assert post(client, csrf, "/attendance/"+ids[1]+"/check-in").status_code == 200

    first = post(client, csrf, "/attendance/"+ids[0]+"/reverse")
    assert first.status_code == 200
    assert first.json()["table_number"] == 12
    teammate = {p["id"]: p for p in client.get("/api/v1/people").json()["items"]}
    assert teammate[ids[1]]["table_number"] == 12

    last = post(client, csrf, "/attendance/"+ids[1]+"/reverse")
    assert last.status_code == 200
    assert last.json()["table_number"] == 12
    people = {p["id"]: p for p in client.get("/api/v1/people").json()["items"]}
    assert all(people[pid]["table_number"] == 12 for pid in ids)
    assert all(people[pid]["check_in_at"] is None for pid in ids)
    later = post(client, csrf, "/attendance/"+ids[0]+"/check-in")
    assert later.status_code == 200
    assert later.json()["table_number"] == 12


def test_monitor_staff_can_check_in_participants_but_not_monitors(reception, verify_participants):
    client, csrf, maker = reception
    participant = post(client, csrf, "/people", {
        "name": "Alice", "email": "alice@example.org",
        "kind": "participant", "team_name": "Owls",
    }).json()["id"]
    native_monitor = post(client, csrf, "/people", {
        "name": "Event monitor", "email": "person-monitor@example.org",
        "kind": "monitor",
    }).json()["id"]

    summary = client.get("/api/v1/attendance/summary").json()
    assert summary == {"registered": 1, "present": 0, "not_arrived": 1}
    assert post(client, csrf, "/attendance/"+native_monitor+"/check-in",
                {"table_number": 1}).status_code == 409

    logged_in = client.post("/api/v1/auth/login", json={
        "email": "monitor@example.org", "password": "SecureMonitorPassword!",
    })
    assert logged_in.status_code == 200
    monitor_csrf = logged_in.json()["csrf_token"]
    path = "/attendance/"+participant+"/check-in"
    unverified = post(client, monitor_csrf, path)
    assert unverified.status_code == 409
    assert "Supervisely" in unverified.json()["detail"]
    verify_participants(maker, participant)
    checked = post(client, monitor_csrf, path)
    assert checked.status_code == 200, checked.text
    assert client.get("/api/v1/attendance/summary").json() == {
        "registered": 1, "present": 1, "not_arrived": 0,
    }
    assert post(client, monitor_csrf, "/attendance/"+participant+"/reverse").status_code == 200
    assert post(client, monitor_csrf, "/attendance/"+native_monitor+"/reverse").status_code == 409
