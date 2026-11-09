"""Real API contracts exercised against an isolated SQL database, never a demo mode."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon.app import app
from annotatiathon.db import get_db
from annotatiathon.models import Base, Person, StaffAccount, StaffSession
from annotatiathon.security import password_hash


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        db.add(StaffAccount(email="organiser@example.org", name="Event Organiser",
                            password_hash=password_hash("CorrectSecretPassword1!"),
                            roles=["organiser"]))
        db.add(StaffAccount(email="monitor@example.org", name="Event Monitor",
                            password_hash=password_hash("CorrectSecretPassword2!"),
                            roles=["monitor"]))
    def override():
        with maker() as db:
            yield db
    app.dependency_overrides[get_db] = override
    monkeypatch.setattr("annotatiathon.auth.get_settings",
                        lambda: type("Settings", (), {"app_env": "local"})())
    with TestClient(app) as test_client:
        yield test_client, maker
    app.dependency_overrides.clear()
    engine.dispose()


def sign_in(client: TestClient, email: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def test_login_sessions_and_csrf(client):
    http, maker = client
    assert http.get("/api/v1/auth/me").status_code == 401
    assert http.post("/api/v1/auth/login", json={
        "email": "organiser@example.org", "password": "wrong"
    }).status_code == 401
    csrf = sign_in(http, "organiser@example.org", "CorrectSecretPassword1!")
    assert http.get("/api/v1/auth/me").json()["staff"]["roles"] == ["organiser"]
    assert http.put("/api/v1/settings/supervisely", json={
        "supervisely": {"team_id": 125980}
    }).status_code == 403
    assert http.put("/api/v1/settings/supervisely", headers={"X-CSRF-Token": csrf},
                    json={"supervisely": {"team_id": 125980}}).status_code == 200
    with maker() as db:
        assert len(db.scalars(select(StaffSession)).all()) == 1
    assert http.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert http.get("/api/v1/auth/me").status_code == 401


def test_staff_role_blocks_settings_mutations(client):
    http, _ = client
    csrf = sign_in(http, "monitor@example.org", "CorrectSecretPassword2!")
    assert http.get("/api/v1/settings/supervisely").status_code == 403
    response = http.put("/api/v1/settings/supervisely",
                        headers={"X-CSRF-Token": csrf},
                        json={"supervisely": {"team_id": 125980}})
    assert response.status_code == 403
    assert http.get("/api/v1/teams").status_code == 200


def test_roster_preview_import_reimport_and_soft_delete(client):
    http, maker = client
    csrf = sign_in(http, "organiser@example.org", "CorrectSecretPassword1!")
    headers = {"X-CSRF-Token": csrf}
    payload = {
        "csv_text": "\\ufeffName,Email,Role,Team\nOne,one@example.org,participant,Team 001\n"
                    "Two,two@example.org,participant,Team 001\n"
                    "Monitor,monitorperson@example.org,monitor,\n",
        "mapping": {"name": "Name", "email": "Email", "kind": "Role", "team": "Team"},
        "default_kind": "participant",
    }
    preview = http.post("/api/v1/roster/preview", headers=headers, json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()["created"] == 3
    with maker() as db:
        assert db.scalar(select(Person)) is None
    first = http.post("/api/v1/roster/import", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["created"] == 3
    second = http.post("/api/v1/roster/import", headers=headers, json=payload)
    assert second.status_code == 200
    assert second.json()["unchanged"] == 3
    teams = http.get("/api/v1/teams").json()["teams"]
    assert teams[0]["name"] == "Monitoring Team"
    assert len(teams[0]["members"]) == 1
    assert len(teams[1]["members"]) == 2
    rows = http.get("/api/v1/people").json()
    assert rows["total"] == 3
    selected = [p["id"] for p in rows["items"] if p["kind"] == "participant"]
    result = http.post("/api/v1/people/invitations-reported",
                       headers=headers, json={"person_ids": selected})
    assert result.status_code == 200
    assert result.json()["native_verified"] is False
    edited = http.patch("/api/v1/people/"+selected[0], headers=headers, json={"active": False})
    assert edited.status_code == 200
    assert http.get("/api/v1/people").json()["total"] == 2
    with maker() as db:
        assert len(db.scalars(select(Person)).all()) == 3


def test_invitation_group_roles_are_never_mixed(client):
    http, _ = client
    csrf = sign_in(http, "organiser@example.org", "CorrectSecretPassword1!")
    headers = {"X-CSRF-Token": csrf}
    for email,kind in (("a@example.org","participant"),("b@example.org","monitor")):
        assert http.post("/api/v1/people",headers=headers,
                         json={"name":"Valid","email":email,"kind":kind}).status_code == 201
    ids = [p["id"] for p in http.get("/api/v1/people").json()["items"]]
    assert http.post("/api/v1/people/invitations-reported",
                     headers=headers,json={"person_ids":ids}).status_code == 422


def test_password_failure_lockout(client):
    http, maker = client
    for _ in range(5):
        response=http.post("/api/v1/auth/login",json={
            "email":"organiser@example.org","password":"incorrect"})
        assert response.status_code == 401
    assert http.post("/api/v1/auth/login",json={
        "email":"organiser@example.org","password":"CorrectSecretPassword1!"
    }).status_code == 401
    with maker() as db:
        staff=db.scalar(select(StaffAccount).where(StaffAccount.email=="organiser@example.org"))
        assert staff.locked_until is not None


def test_no_secret_allowed_in_settings_payload(client):
    http, _ = client
    csrf=sign_in(http,"organiser@example.org","CorrectSecretPassword1!")
    response=http.put("/api/v1/settings/supervisely",
        headers={"X-CSRF-Token":csrf},
        json={"supervisely":{"team_id":125980,"api_token":"should-not-be-accepted"}})
    assert response.status_code==422
    assert "api_token" not in http.get("/api/v1/settings/supervisely").json()["settings"]
