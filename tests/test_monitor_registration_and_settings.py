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
    DEFAULT_EVENT_ID,
    Base,
    Event,
    Person,
    StaffAccount,
    SuperviselyConfig,
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
    settings = SimpleNamespace(
        nightjar_encryption_key=SecretStr(key),
        smtp_host="smtp.example.org",
        smtp_sender="nightjar@example.org",
        smtp_port=587,
        smtp_username="nightjar",
        smtp_password=SecretStr("fake_test_password_not_live"),
        smtp_use_ssl=False,
    )
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


def test_auto_sync_imports_participant_and_monitor_then_organiser_can_approve(
    setup, monkeypatch,
):
    http, maker, sent = setup
    csrf = login(http)
    source = NativeTeamSnapshot(
        **{**native_snapshot().__dict__, "members": (
            NativeMember(id=555, login="annotator-bird",
                         email="bird@example.org", role="annotator"),
            NativeMember(id=556, login="monitor-bird",
                         email="monitorbird@example.org", role="manager"),
            NativeMember(id=557, login="email-unknown", email=None, role="manager"),
        )}
    )
    monkeypatch.setattr(
        "annotatiathon.native_sync_api.read_team_snapshot",
        lambda config: source,
    )
    first = http.post("/api/v1/people/supervisely/refresh",
                      headers={"X-CSRF-Token": csrf})
    assert first.status_code == 200, first.text
    assert first.json()["created"] == 2
    assert first.json()["unmatched_native"] == 1
    again = http.post("/api/v1/people/supervisely/refresh",
                      headers={"X-CSRF-Token": csrf})
    assert again.status_code == 200, again.text
    assert again.json()["created"] == 0
    people = {p["email"]: p for p in http.get("/api/v1/people").json()["items"]}
    assert people["bird@example.org"]["kind"] == "participant"
    assert people["monitorbird@example.org"]["kind"] == "monitor"
    native = people["monitorbird@example.org"]
    assert native["native_auto_imported"] is True
    assert native["monitor_registration_approved"] is False

    http.post("/api/v1/auth/register/request",
              json={"email": "monitorbird@example.org"})
    assert sent == []

    approved = http.put("/api/v1/people/"+native["id"]+"/monitor-registration",
        headers={"X-CSRF-Token": csrf}, json={"approved": True})
    assert approved.status_code == 200
    assert approved.json()["monitor_registration_approved"] is True
    requested = http.post("/api/v1/auth/register/request",
                          json={"email": "monitorbird@example.org"})
    assert requested.status_code == 200
    assert len(sent) == 1

    forbidden = http.put(
        "/api/v1/people/"+people["bird@example.org"]["id"]+"/monitor-registration",
        headers={"X-CSRF-Token": csrf}, json={"approved": True},
    )
    assert forbidden.status_code == 409

    with maker() as db:
        all_entries = list(db.scalars(select(Person)))
        assert len(all_entries) == 2


def test_organiser_can_revoke_signup_for_approved_monitor(setup):
    http, _maker, sent = setup
    csrf = login(http)
    added = http.post("/api/v1/people",headers={"X-CSRF-Token": csrf},
        json={"name": "Roster Monitor", "email": "roster@example.org",
              "kind": "monitor"})
    assert added.status_code == 201
    assert added.json()["monitor_registration_approved"] is True
    disabled = http.put(
        "/api/v1/people/"+added.json()["id"]+"/monitor-registration",
        headers={"X-CSRF-Token": csrf},json={"approved": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["monitor_registration_approved"] is False
    http.post("/api/v1/auth/register/request",
              json={"email": "roster@example.org"})
    assert sent == []


def test_registration_email_readiness_and_organiser_test_are_protected(
    setup, monkeypatch,
):
    http, _maker, _sent = setup
    status = http.get("/api/v1/auth/register/status")
    assert status.status_code == 200, status.text
    assert status.json()["email_verification_available"] is True
    assert status.json()["verification_code_minutes"] == 10
    assert http.get("/api/v1/settings/registration-email").status_code == 401

    csrf = login(http)
    response = http.get("/api/v1/settings/registration-email")
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is True
    assert response.json()["sender"] == "nightjar@example.org"
    assert "fake_test_password_not_live" not in response.text

    deliveries = []
    monkeypatch.setattr(
        "annotatiathon.registration_api.send_mail",
        lambda email, subject, body: deliveries.append((email, subject, body)),
    )
    test = http.post("/api/v1/settings/registration-email/test",
        headers={"X-CSRF-Token": csrf})
    assert test.status_code == 200, test.text
    assert test.json()["sent"] is True
    assert deliveries[0][0] == "organiser@example.org"
    assert "email delivery is working" in deliveries[0][2].lower()
    assert "Your one-time Nightjar monitor registration code" not in deliveries[0][2]
    assert http.post("/api/v1/settings/registration-email/test").status_code == 403


def test_monitor_code_resend_rate_limit_and_revoked_approval(
    setup,
):
    http, maker, delivered = setup
    csrf = login(http)
    response = http.post("/api/v1/people", headers={"X-CSRF-Token": csrf},
        json={"name": "Registered Monitor", "email": "check@example.org",
              "kind": "monitor"})
    assert response.status_code == 201, response.text
    http.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})

    first = http.post("/api/v1/auth/register/request",
        json={"email": "check@example.org"})
    assert first.status_code == 200
    assert first.json()["resend_after_seconds"] == 60
    assert len(delivered) == 1
    second = http.post("/api/v1/auth/register/request",
        json={"email": "check@example.org"})
    assert second.status_code == 200
    assert len(delivered) == 1
    code = delivered[0][1]

    with maker.begin() as db:
        person = db.scalar(select(Person).where(Person.email == "check@example.org"))
        person.kind = "participant"
    forbidden = http.post("/api/v1/auth/register/complete", json={
        "email": "check@example.org", "code": code,
        "password": "ValidMonitorP@ssword&2026",
    })
    assert forbidden.status_code == 403
    assert http.post("/api/v1/auth/login", json={
        "email": "check@example.org", "password": "ValidMonitorP@ssword&2026",
    }).status_code == 401


def test_registration_code_expires_and_cannot_be_reused(setup):
    from annotatiathon.models import RegistrationChallenge, epoch

    http, maker, delivered = setup
    csrf = login(http)
    http.post("/api/v1/people", headers={"X-CSRF-Token": csrf},
        json={"name": "Monitor", "email": "valid@example.org", "kind": "monitor"})
    http.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    http.post("/api/v1/auth/register/request", json={"email": "valid@example.org"})
    assert delivered
    code = delivered[0][1]
    with maker.begin() as db:
        challenge = db.get(RegistrationChallenge, "valid@example.org")
        challenge.expires_at = epoch() - 1
    expired = http.post("/api/v1/auth/register/complete", json={
        "email": "valid@example.org", "code": code,
        "password": "Safe?Strong&Password*2026",
    })
    assert expired.status_code == 400
    with maker.begin() as db:
        challenge = db.get(RegistrationChallenge, "valid@example.org")
        challenge.expires_at = epoch() + 600
    accepted = http.post("/api/v1/auth/register/complete", json={
        "email": "valid@example.org", "code": code,
        "password": "Safe?Strong&Password*2026",
    })
    assert accepted.status_code == 200, accepted.text
    assert http.post("/api/v1/auth/register/complete", json={
        "email": "valid@example.org", "code": code,
        "password": "Safe?Strong&Password*2026",
    }).status_code == 400
    assert login(http,"valid@example.org","Safe?Strong&Password*2026")


def test_smtp_failure_does_not_reveal_monitor_roster_membership(setup, monkeypatch):
    from fastapi import HTTPException

    http, maker, _sent = setup
    with maker.begin() as db:
        db.add(Person(
            event_id=DEFAULT_EVENT_ID, email="eligible@example.org",
            name="Eligible Monitor", kind="monitor",
        ))

    def fail_send(_email, _code):
        raise HTTPException(status_code=503, detail="Internal SMTP failure.")

    monkeypatch.setattr("annotatiathon.registration_api.send_verification", fail_send)
    eligible = http.post("/api/v1/auth/register/request",
        json={"email": "eligible@example.org"})
    unknown = http.post("/api/v1/auth/register/request",
        json={"email": "stranger@example.org"})
    assert eligible.status_code == 200
    assert eligible.json() == unknown.json()
    assert "eligible@example.org" not in eligible.text

    monkeypatch.setattr("annotatiathon.registration_api.smtp_ready", lambda: False)
    disabled = http.post("/api/v1/auth/register/request",
        json={"email": "eligible@example.org"})
    assert disabled.status_code == 503
    disabled_unknown = http.post("/api/v1/auth/register/request",
        json={"email": "stranger@example.org"})
    assert disabled_unknown.status_code == 503
    assert disabled.json() == disabled_unknown.json()
