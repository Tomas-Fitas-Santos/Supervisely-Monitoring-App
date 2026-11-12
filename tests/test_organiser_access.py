"""Production account access is independent of event membership or native roles."""
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon import manage, organiser_access, organiser_setup_api
from annotatiathon.app import app
from annotatiathon.db import get_db
from annotatiathon.models import DEFAULT_EVENT_ID, AuditEvent, Base, Event, Person, StaffAccount
from annotatiathon.people_api import align_staff_access
from annotatiathon.security import password_hash, password_matches


@pytest.fixture
def approved(monkeypatch):
    settings = SimpleNamespace(
        app_env="production",
        nightjar_admin_emails="owner@example.org; coadmin@example.org",
        nightjar_organiser_emails="organiser@example.org; owner@example.org",
        nightjar_public_url="https://nightjar.example",
        nightjar_smtp_host="smtp.example.org",
        nightjar_smtp_port=587,
        nightjar_smtp_from="nightjar@example.org",
        nightjar_smtp_user="",
        nightjar_smtp_password=None,
    )
    monkeypatch.setattr(organiser_access, "get_settings", lambda: settings)
    monkeypatch.setattr(organiser_setup_api, "get_settings", lambda: settings)
    monkeypatch.setattr("annotatiathon.auth.get_settings", lambda: settings)
    return settings


@pytest.fixture
def clients(approved, monkeypatch):
    engine = create_engine("sqlite+pysqlite://",
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        db.add(Event(id=DEFAULT_EVENT_ID, name="Event"))
        db.add(StaffAccount(
            email="owner@example.org", name="Owner", active=False,
            roles=["monitor"], password_hash=password_hash("ExistingSecretPhrase123"),
        ))
        db.add(StaffAccount(
            email="outsider@example.org", name="Outsider",
            roles=["monitor"], password_hash=password_hash("OutsiderPassword123"),
        ))
        db.add(Person(
            event_id=DEFAULT_EVENT_ID, email="owner@example.org",
            kind="participant", name="Owner", active=True,
        ))
    def override():
        with maker() as db:
            yield db
    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(manage, "session_factory", lambda: maker)
    with TestClient(app, base_url="https://testserver") as http:
        yield http, maker
    app.dependency_overrides.clear()
    engine.dispose()


def test_allowlist_roles_and_no_roster_deactivation(clients, monkeypatch):
    http, maker = clients
    with maker.begin() as db:
        p = db.scalar(select(Person).where(Person.email == "owner@example.org"))
        align_staff_access(db, p)
        assert db.scalar(select(StaffAccount).where(
            StaffAccount.email == p.email)).active is False  # unchanged until sync

    monkeypatch.setattr(sys, "argv", ["nightjar-manage", "sync-organiser-access"])
    manage.main()
    with maker() as db:
        owner = db.scalar(select(StaffAccount).where(
            StaffAccount.email == "owner@example.org"))
        outsider = db.scalar(select(StaffAccount).where(
            StaffAccount.email == "outsider@example.org"))
        assert owner.active and owner.roles == ["admin", "organiser"]
        assert not outsider.active
        assert password_matches(owner.password_hash, "ExistingSecretPhrase123")
    assert http.post("/api/v1/auth/login", json={
        "email": "owner@example.org", "password": "ExistingSecretPhrase123"
    }).status_code == 200
    assert http.post("/api/v1/auth/login", json={
        "email": "outsider@example.org", "password": "OutsiderPassword123"
    }).status_code == 401

    with maker.begin() as db:
        p = db.scalar(select(Person).where(Person.email == "owner@example.org"))
        p.kind = "participant"
        align_staff_access(db, p)
    with maker() as db:
        assert db.scalar(select(StaffAccount).where(
            StaffAccount.email == "owner@example.org")).active


def test_code_free_verified_registration_and_recovery(clients, monkeypatch):
    http, maker = clients
    sent = []
    monkeypatch.setattr(organiser_setup_api, "_send_link",
                        lambda email, token: sent.append((email, token)))
    unknown = http.post("/api/v1/auth/setup/request", json={
        "email": "outsider@example.org",
    })
    assert unknown.status_code == 200 and not sent
    request = http.post("/api/v1/auth/setup/request", json={
        "email": "organiser@example.org",
    })
    assert request.status_code == 200 and len(sent) == 1
    # No password or token is returned in the API response.
    assert "token" not in request.text and "link" in request.text
    token = sent[0][1]
    failed = http.post("/api/v1/auth/setup/complete", json={
        "email": "organiser@example.org", "password": "NewLongPassword123",
        "token": "invalid" * 6,
    })
    assert failed.status_code == 403
    created = http.post("/api/v1/auth/setup/complete", json={
        "email": "organiser@example.org", "password": "NewLongPassword123",
        "token": token,
    })
    assert created.status_code == 200, created.text
    assert http.post("/api/v1/auth/setup/complete", json={
        "email": "organiser@example.org", "password": "AnotherPassword123",
        "token": token,
    }).status_code == 403
    login = http.post("/api/v1/auth/login", json={
        "email": "organiser@example.org", "password": "NewLongPassword123",
    })
    assert login.status_code == 200
    assert login.json()["staff"]["roles"] == ["organiser"]
    with maker() as db:
        staff = db.scalar(select(StaffAccount).where(
            StaffAccount.email == "organiser@example.org"))
        assert staff.active and staff.roles == ["organiser"]
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.action == "staff.account.email_verified_setup")) is not None


def test_legacy_roster_registration_is_disabled_in_production(clients):
    http, _ = clients
    response = http.post("/api/v1/auth/register", json={
        "email": "owner@example.org", "password": "CorrectLongPassword123",
    })
    assert response.status_code == 410
