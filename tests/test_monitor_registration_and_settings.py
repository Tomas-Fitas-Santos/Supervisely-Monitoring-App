"""Production API contracts for roster-only registration and token settings.

External email and Supervisely requests are isolated using injected test adapters.
"""
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon.app import app
from annotatiathon.db import get_db
from annotatiathon.models import (
    DEFAULT_EVENT_ID, Base, Event, Person, StaffAccount, SuperviselyConfig,
)
from annotatiathon.security import password_hash
from annotatiathon.supervisely_client import NativeMember, NativeTeamSnapshot


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with maker.begin() as db:
        db.add(Event(id=DEFAULT_EVENT_ID, name="Annotatiathon"))
        db.add(StaffAccount(
            email="organiser@example.org", name="Organiser", roles=["organiser"],
            password_hash=password_hash("TEST_LONG_SECURE_PASSWORD"),
        ))
        db.add(SuperviselyConfig(
            id=1, team_id=125980, workspace_id=2001,
            server_url="https://app.supervisely.com",
        ))
    key = Fernet.generate_key().decode()
    settings = SimpleNamespace(nightjar_encryption_key=SecretStr(key))
    monkeypatch.setattr("annotatiathon.credentials.get_settings", lambda: settings)
    monkeypatch.setattr("annotatiathon.registration_api.get_settings", lambda: settings)
    monkeypatch.setattr(
        "annotatiathon.auth.get_settings",
        lambda: SimpleNamespace(app_env="local"),
    )
    delivery = []
    monkeypatch.setattr(
        "annotatiathon.registration_api.send_verification",
        lambda email, code: delivery.append((email, code)),
    )
    def override_db():
        with maker() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as http:
        yield http, maker, delivery
    app.dependency_overrides.clear()
    engine.dispose()


def login(http, email="organiser@example.org", password="TEST_LONG_SECURE_PASSWORD"):
    r = http.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def native_snapshot():
    return NativeTeamSnapshot(
        team_id=125980, team_name="Event Team", workspace_id=2001,
        account_id=123550, account_login="organiser_supervisely",
        members=(
            NativeMember(id=123550, login="organiser_supervisely",
                         email=None, role="admin"),
            NativeMember(id=44444, login="monitor_supervisely",
                         email="monitor@example.org", role="manager"),
        ),
    )


def test_monitor_signup_requires_approved_roster_and_email_code(setup):
    http, maker, sent = setup
    csrf = login(http)
    for email, kind in (
        ("monitor@example.org", "monitor"),
        ("ordinary@example.org", "participant"),
    ):
        r = http.post("/api/v1/people", json={
            "name": email.split("@")[0], "email": email, "kind": kind,
        }, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 201, r.text
    http.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})

    for email in ("outsider@example.org", "ordinary@example.org"):
        r = http.post("/api/v1/auth/register/request", json={"email": email})
        assert r.status_code == 200
    assert sent == []

    request = http.post(
        "/api/v1/auth/register/request", json={"email": "monitor@example.org"},
    )
    assert request.status_code == 200, request.text
    assert len(sent) == 1
    code = sent[0][1]
    wrong = http.post("/api/v1/auth/register/complete", json={
        "email": "monitor@example.org", "code": "00000000",
        "password": "SafeMonitorPassword_123",
    })
    assert wrong.status_code == 400
    accepted = http.post("/api/v1/auth/register/complete", json={
        "email": "monitor@example.org", "code": code,
        "password": "SafeMonitorPassword_123",
    })
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["registered"] is True
    assert login(http, "monitor@example.org", "SafeMonitorPassword_123")
    with maker() as db:
        staff = db.scalar(select(StaffAccount).where(
            StaffAccount.email == "monitor@example.org"
        ))
        assert staff.roles == ["monitor"]


def test_native_auto_import_does_not_authorise_signup(setup):
    http, maker, sent = setup
    with maker.begin() as db:
        db.add(Person(
            event_id=DEFAULT_EVENT_ID, email="native@example.org",
            name="Native Account", kind="monitor",
            source_fields={"source": "supervisely_member_sync"},
        ))
    assert http.post("/api/v1/auth/register/request",
                     json={"email": "native@example.org"}).status_code == 200
    assert sent == []


def test_shared_token_is_encrypted_and_monitor_ids_are_readonly(setup, monkeypatch):
    http, maker, _ = setup
    csrf = login(http)
    monkeypatch.setattr(
        "annotatiathon.settings_api.read_team_snapshot",
        lambda config, personal_token: native_snapshot(),
    )
    token = "REAL_TOKEN_MUST_NEVER_APPEAR_IN_RESPONSES"
    saved = http.put("/api/v1/settings/supervisely/credential",
        headers={"X-CSRF-Token": csrf}, json={"api_token": token})
    assert saved.status_code == 200, saved.text
    assert token not in saved.text
    assert saved.json()["credential_configured"] is True
    with maker() as db:
        config = db.get(SuperviselyConfig, 1)
        from annotatiathon.credentials import decrypt_token
        assert config.token_ciphertext != token
        assert decrypt_token(config.token_ciphertext) == token

    http.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    with maker.begin() as db:
        db.add(StaffAccount(
            email="monitor@example.org", name="Monitor", roles=["monitor"],
            password_hash=password_hash("AnotherMonitorPassword_123"),
        ))
    monitor_csrf = login(http, "monitor@example.org", "AnotherMonitorPassword_123")
    shared = http.get("/api/v1/settings/supervisely/shared")
    assert shared.status_code == 200
    assert shared.json()["team_id"] == 125980
    assert shared.json()["workspace_id"] == 2001
    assert shared.json()["editable"] is False
    forbidden = http.put("/api/v1/settings/supervisely",
        headers={"X-CSRF-Token": monitor_csrf},
        json={"supervisely": {"team_id": 42}})
    assert forbidden.status_code == 403


def test_staff_personal_supervisely_token_is_not_stored(setup, monkeypatch):
    http, maker, _ = setup
    with maker.begin() as db:
        db.add(StaffAccount(
            email="monitor@example.org", name="Monitor", roles=["monitor"],
            password_hash=password_hash("AnotherMonitorPassword_123"),
        ))
    csrf = login(http, "monitor@example.org", "AnotherMonitorPassword_123")
    monkeypatch.setattr(
        "annotatiathon.personal_settings_api.read_team_snapshot",
        lambda config, personal_token: NativeTeamSnapshot(
            **{**native_snapshot().__dict__,
               "account_id": 44444,
               "account_login": "monitor_supervisely"}
        ),
    )
    token = "VERY_PRIVATE_PERSONAL_TOKEN"
    result = http.post("/api/v1/settings/supervisely/personal",
        headers={"X-CSRF-Token": csrf}, json={"api_token": token})
    assert result.status_code == 200, result.text
    assert result.json()["user_id"] == 44444
    assert token not in result.text
    with maker() as db:
        member = db.scalar(select(StaffAccount).where(
            StaffAccount.email == "monitor@example.org"
        ))
        assert member.supervisely_user_id == 44444
        assert member.supervisely_verified_team_id == 125980
        assert not hasattr(member, "supervisely_api_token")
