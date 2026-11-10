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
        yield client, login.json()["csrf_token"]
    app.dependency_overrides.clear()
    engine.dispose()


def post(client, csrf, path, payload=None, method="POST"):
    return client.request(method, "/api/v1"+path, json=payload,
                          headers={"X-CSRF-Token": csrf})


def test_first_arrival_sets_team_table_partner_inherits_without_reentry(reception):
    client, csrf = reception
    ids = []
    for n in ["Anna", "Ben"]:
        person = post(client, csrf, "/people", {
            "name": n, "email": n.lower()+"@example.org",
            "kind": "participant", "team_name": "Robins",
        })
        assert person.status_code == 201
        ids.append(person.json()["id"])
    first_attempt = post(client, csrf, "/attendance/"+ids[0]+"/check-in")
    assert first_attempt.status_code == 422
    first = post(client, csrf, "/attendance/"+ids[0]+"/check-in",
                 {"table_number": 42})
    assert first.status_code == 200, first.text
    assert first.json()["table_number"] == 42
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


def test_cannot_change_team_table_by_checking_in_partner(reception):
    client, csrf = reception
    ids = []
    for name in ["A", "B"]:
        out = post(client, csrf, "/people", {
            "name": name, "email": name.lower()+"@example.org", "kind": "participant",
            "team_name": "Ducks",
        })
        ids.append(out.json()["id"])
    assert post(client, csrf, "/attendance/"+ids[0]+"/check-in",
                {"table_number": 17}).status_code == 200
    conflicting = post(client, csrf, "/attendance/"+ids[1]+"/check-in",
                       {"table_number": 25})
    assert conflicting.status_code == 409
    assert client.get("/api/v1/attendance/summary").json()["present"] == 1
    assert post(client, csrf, "/attendance/"+ids[1]+"/check-in").status_code == 200
