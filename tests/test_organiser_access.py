"""Nightjar Admin/Organiser self-signup is driven only by PostgreSQL approval records."""
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon import manage, organiser_access
from annotatiathon.app import app
from annotatiathon.db import get_db
from annotatiathon.models import (
    DEFAULT_EVENT_ID, ApprovedStaffEmail, AuditEvent, Base, Event, Person, StaffAccount,
)
from annotatiathon.people_api import align_staff_access
from annotatiathon.security import password_hash, password_matches


@pytest.fixture
def settings(monkeypatch):
    values = SimpleNamespace(
        app_env="production",
        nightjar_admin_emails="owner@example.org; coadmin@example.org",
        nightjar_organiser_emails="organiser@example.org; owner@example.org",
    )
    monkeypatch.setattr(organiser_access, "get_settings", lambda: values)
    monkeypatch.setattr("annotatiathon.auth.get_settings", lambda: values)
    return values


@pytest.fixture
def setup(settings, monkeypatch):
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


def bootstrap(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["nightjar-manage", "sync-organiser-access"])
    manage.main()


def test_one_time_database_bootstrap_and_existing_passwords(setup, monkeypatch, settings):
    http, maker = setup
    with maker.begin() as db:
        roster = db.scalar(select(Person).where(Person.email == "owner@example.org"))
        align_staff_access(db, roster)
    bootstrap(monkeypatch)
    with maker() as db:
        assert len(db.scalars(select(ApprovedStaffEmail)).all()) == 3
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

    # Later .env changes must NEVER replace the existing DB approvals.
    settings.nightjar_admin_emails = "intruder@example.org"
    settings.nightjar_organiser_emails = ""
    bootstrap(monkeypatch)
    with maker() as db:
        assert db.get(ApprovedStaffEmail, "owner@example.org") is not None
        assert db.get(ApprovedStaffEmail, "intruder@example.org") is None
        roster = db.scalar(select(Person).where(Person.email == "owner@example.org"))
        roster.kind = "participant"
        align_staff_access(db, roster)
        assert db.scalar(select(StaffAccount).where(
            StaffAccount.email == "owner@example.org")).active


def test_database_approved_account_self_signup_without_codes(setup, monkeypatch):
    http, maker = setup
    bootstrap(monkeypatch)
    forbidden = http.post("/api/v1/auth/setup/register", json={
        "email": "outsider@example.org", "name": "Outsider",
        "password": "LongPrivatePassword123",
    })
    assert forbidden.status_code == 403
    created = http.post("/api/v1/auth/setup/register", json={
        "email": " ORGANISER@example.org ", "name": "Organiser's Own Name",
        "password": "LongPrivatePassword123",
    })
    assert created.status_code == 201, created.text
    duplicate = http.post("/api/v1/auth/setup/register", json={
        "email": "organiser@example.org", "name": "Someone Else",
        "password": "DifferentPrivatePassword123",
    })
    assert duplicate.status_code == 409
    assert http.post("/api/v1/auth/login", json={
        "email": "organiser@example.org", "password": "DifferentPrivatePassword123"
    }).status_code == 401
    result = http.post("/api/v1/auth/login", json={
        "email": "organiser@example.org", "password": "LongPrivatePassword123",
    })
    assert result.status_code == 200 and result.json()["staff"]["roles"] == ["organiser"]
    assert http.get("/api/v1/staff/approved").status_code == 403

    with maker() as db:
        a = db.scalar(select(StaffAccount).where(
            StaffAccount.email == "organiser@example.org"))
        assert a.active and a.name == "Organiser's Own Name"
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.action == "staff.account.self_registered")) is not None


def test_admin_only_approved_email_list_and_no_legacy_registration(setup, monkeypatch):
    http, maker = setup
    bootstrap(monkeypatch)
    assert http.post("/api/v1/auth/register", json={
        "email": "owner@example.org", "password": "CorrectLongPassword123",
    }).status_code == 410
    assert http.post("/api/v1/auth/setup/request", json={
        "email": "owner@example.org",
    }).status_code == 404
    assert http.post("/api/v1/auth/setup/complete", json={
        "email": "owner@example.org", "password": "CorrectLongPassword123",
        "token": "a" * 30,
    }).status_code == 404
    admin = http.post("/api/v1/auth/login", json={
        "email": "owner@example.org", "password": "ExistingSecretPhrase123",
    })
    assert admin.status_code == 200
    resp = http.get("/api/v1/staff/approved")
    assert resp.status_code == 200
    by_email = {row["email"]: row for row in resp.json()["items"]}
    assert by_email["owner@example.org"]["role"] == "admin"
    assert by_email["coadmin@example.org"]["registered"] is False
    assert by_email["organiser@example.org"]["role"] == "organiser"
