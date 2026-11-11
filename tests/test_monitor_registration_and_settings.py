"""Roster-only Nightjar monitor signup and separate Supervisely linking.

No SMTP, verification code, or email delivery is used by registration.
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
    )
    monkeypatch.setattr("annotatiathon.credentials.get_settings", lambda: settings)
    monkeypatch.setattr(
        "annotatiathon.auth.get_settings",
        lambda: SimpleNamespace(app_env="local"),
    )
    def override_db():
        with maker() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as http:
        yield http, maker, None
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


def test_monitor_signup_uses_only_approved_sql_roster_and_app_password(setup):
    http, maker, _ = setup
    csrf = login(http)
    for email, kind in (
        ("monitor@example.org", "monitor"),
        ("ordinary@example.org", "participant"),
    ):
        result = http.post("/api/v1/people", json={
            "name": email.split("@")[0], "email": email, "kind": kind,
        }, headers={"X-CSRF-Token": csrf})
        assert result.status_code == 201, result.text
    http.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})

    for email in ("outsider@example.org", "ordinary@example.org"):
        result = http.post("/api/v1/auth/register", json={
            "email": email, "password": "SafeMonitorPassword_123!&",
        })
        assert result.status_code == 403, result.text

    created = http.post("/api/v1/auth/register", json={
        "email": "  MONITOR@example.org  ",
        "password": "SafeMonitorPassword_123!&",
    })
    assert created.status_code == 201, created.text
    assert created.json()["registered"] is True
    assert login(http, "monitor@example.org", "SafeMonitorPassword_123!&")
    with maker() as db:
        staff = db.scalar(select(StaffAccount).where(
            StaffAccount.email == "monitor@example.org",
        ))
        assert staff.roles == ["monitor"]
        assert staff.name == "monitor"
        assert staff.supervisely_user_id is None
        assert db.scalar(select(StaffAccount).where(
            StaffAccount.email == "outsider@example.org",
        )) is None


def test_existing_monitor_credentials_survive_roster_and_event_changes(setup):
    """A successful signup grants a persistent Nightjar account.

    Later roster/event edits cannot invalidate its password or sign-in.
    Explicit staff deactivation still revokes account access.
    """
    http, maker, _ = setup
    email = "persistent@example.org"
    password = "PersistentNightjarPassword2026!"
    with maker.begin() as db:
        db.add(Person(
            event_id=DEFAULT_EVENT_ID, email=email,
            name="Persistent Monitor", kind="monitor",
        ))
    created = http.post("/api/v1/auth/register", json={
        "email": email, "password": password,
    })
    assert created.status_code == 201, created.text
    with maker.begin() as db:
        roster_person = db.scalar(select(Person).where(Person.email == email))
        roster_person.source_fields = {"monitor_registration_approved": False}
        roster_person.active = False
        db.add(Event(name="Another active event"))
    # Registration approval is checked when creating the account only.
    # Staff account credentials and privileges remain database-backed.
    response = http.post("/api/v1/auth/login", json={
        "email": email, "password": password,
    })
    assert response.status_code == 200, response.text
    assert response.json()["staff"]["roles"] == ["monitor"]
    assert http.get("/api/v1/auth/me").status_code == 200
    with maker.begin() as db:
        account = db.scalar(select(StaffAccount).where(StaffAccount.email == email))
        account.active = False
    assert http.get("/api/v1/auth/me").status_code == 401
    assert http.post("/api/v1/auth/login", json={
        "email": email, "password": password,
    }).status_code == 401


def test_account_claim_is_single_use_and_signup_requires_strong_password(setup):
    http, maker, _ = setup
    with maker.begin() as db:
        db.add(Person(
            event_id=DEFAULT_EVENT_ID, email="claimed@example.org",
            name="Approved Monitor", kind="monitor",
        ))
    short = http.post("/api/v1/auth/register", json={
        "email": "claimed@example.org", "password": "weak",
    })
    assert short.status_code == 422
    first = http.post("/api/v1/auth/register", json={
        "email": "claimed@example.org", "password": "S#trong&P@ssword?2026",
    })
    assert first.status_code == 201
    again = http.post("/api/v1/auth/register", json={
        "email": "claimed@example.org", "password": "AnotherVeryStrongPassword123",
    })
    assert again.status_code == 409
    assert login(http, "claimed@example.org", "S#trong&P@ssword?2026")


def test_signup_does_not_require_supervisely_membership_before_link(setup):
    http, maker, _ = setup
    with maker.begin() as db:
        db.add(Person(
            event_id=DEFAULT_EVENT_ID, email="unlinked@example.org",
            name="Unlinked Monitor", kind="monitor",
            membership_status="not_invited", supervisely_role=None,
        ))
    created = http.post("/api/v1/auth/register", json={
        "email": "unlinked@example.org", "password": "OnlyForNightjar123#",
    })
    assert created.status_code == 201, created.text
    assert login(http, "unlinked@example.org", "OnlyForNightjar123#")
    assert http.get("/api/v1/settings/supervisely/shared").status_code == 200
    assert http.get("/api/v1/settings/supervisely/personal").json()["linked"] is False


def test_native_auto_import_does_not_authorise_signup(setup):
    http, maker, _ = setup
    with maker.begin() as db:
        db.add(Person(
            event_id=DEFAULT_EVENT_ID, email="native@example.org",
            name="Native Account", kind="monitor",
            source_fields={"source": "supervisely_member_sync"},
        ))
    denied = http.post("/api/v1/auth/register", json={
        "email": "native@example.org", "password": "SafeMonitorPassword_123",
    })
    assert denied.status_code == 403


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
    http, maker, _ = setup
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
    again = http.post("/api/v1/people/supervisely/refresh",
                      headers={"X-CSRF-Token": csrf})
    assert again.status_code == 200
    assert again.json()["created"] == 0
    people = {p["email"]: p for p in http.get("/api/v1/people").json()["items"]}
    assert people["bird@example.org"]["kind"] == "participant"
    native = people["monitorbird@example.org"]
    assert native["kind"] == "monitor"
    assert native["native_auto_imported"] is True
    assert native["monitor_registration_approved"] is True
    # Explicit organiser denial overrides verified team membership.
    blocked = http.put("/api/v1/people/"+native["id"]+"/monitor-registration",
        headers={"X-CSRF-Token": csrf}, json={"approved": False})
    assert blocked.status_code == 200
    assert blocked.json()["monitor_registration_approved"] is False
    denied = http.post("/api/v1/auth/register", json={
        "email": "monitorbird@example.org",
        "password": "SafeMonitorPassword_123",
    })
    assert denied.status_code == 403
    approved = http.put("/api/v1/people/"+native["id"]+"/monitor-registration",
        headers={"X-CSRF-Token": csrf}, json={"approved": True})
    assert approved.status_code == 200
    assert approved.json()["monitor_registration_approved"] is True
    created = http.post("/api/v1/auth/register", json={
        "email": "monitorbird@example.org",
        "password": "SafeMonitorPassword_123",
    })
    assert created.status_code == 201, created.text
    participant_denied = http.post("/api/v1/auth/register", json={
        "email": "bird@example.org", "password": "SafeMonitorPassword_123",
    })
    assert participant_denied.status_code == 403
    with maker() as db:
        assert len(list(db.scalars(select(Person)))) == 2


def test_native_verified_manager_can_register_without_extra_approval(setup, monkeypatch):
    http, _maker, _ = setup
    csrf = login(http)
    source = NativeTeamSnapshot(
        **{**native_snapshot().__dict__, "members": (
            NativeMember(id=556, login="verified_manager",
                         email="verifiedmanager@example.org", role="manager"),
        )}
    )
    monkeypatch.setattr(
        "annotatiathon.native_sync_api.read_team_snapshot",
        lambda config: source,
    )
    synced = http.post("/api/v1/people/supervisely/refresh",
                       headers={"X-CSRF-Token": csrf})
    assert synced.status_code == 200, synced.text
    people = http.get("/api/v1/people").json()["items"]
    entry = next(p for p in people if p["email"] == "verifiedmanager@example.org")
    assert entry["monitor_registration_approved"] is True
    created = http.post("/api/v1/auth/register", json={
        "email": entry["email"], "password": "PersistentNativePassword_2026!",
    })
    assert created.status_code == 201, created.text
    assert login(http, entry["email"], "PersistentNativePassword_2026!")


def test_organiser_can_revoke_signup_for_approved_monitor(setup):
    http, _maker, _ = setup
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
    denied = http.post("/api/v1/auth/register", json={
        "email": "roster@example.org", "password": "SafeMonitorPassword_123",
    })
    assert denied.status_code == 403


def test_signup_and_login_remain_separate_from_supervisely(setup):
    http, maker, _ = setup
    with maker.begin() as db:
        db.add(Person(
            event_id=DEFAULT_EVENT_ID, email="event-monitor@example.org",
            name="Event Monitor", kind="monitor",
            membership_status="joined", supervisely_role="annotator",
        ))
    # Native role alignment does not prevent creation of a Nightjar account.
    result = http.post("/api/v1/auth/register", json={
        "email": "event-monitor@example.org",
        "password": "SecureNightjarPassword2026!",
    })
    assert result.status_code == 201, result.text
    assert login(http, "event-monitor@example.org", "SecureNightjarPassword2026!")
    # Registering never grants organiser rights or links any native token.
    with maker() as db:
        staff = db.scalar(select(StaffAccount).where(
            StaffAccount.email == "event-monitor@example.org",
        ))
        assert staff.roles == ["monitor"]
        assert staff.supervisely_user_id is None


def test_no_email_verification_endpoints_or_mail_settings_are_required(setup):
    http, _maker, _ = setup
    assert http.get("/api/v1/auth/register/status").status_code == 404
    assert http.post("/api/v1/auth/register/request", json={
        "email": "test@example.org",
    }).status_code == 404
    assert http.post("/api/v1/auth/register/complete", json={
        "email": "test@example.org",
    }).status_code == 404
    assert http.get("/api/v1/settings/registration-email").status_code == 404
