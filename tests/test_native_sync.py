"""Native connection and membership reconciliation against isolated test fixtures.

No live Supervisely account or event media is contacted by automated tests.
"""
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon.app import app
from annotatiathon.db import get_db
from annotatiathon.models import DEFAULT_EVENT_ID, Base, Event, StaffAccount, SuperviselyConfig
from annotatiathon.security import password_hash
from annotatiathon.supervisely_client import (
    NativeAPIError,
    NativeMember,
    NativeTeamSnapshot,
    _read_members,
    _request_json,
    require_connection,
)


@pytest.fixture
def context(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        db.add(Event(id=DEFAULT_EVENT_ID, name="Initial Event"))
        db.add(StaffAccount(
            email="owner@example.org", name="Owner",
            roles=["organiser"], password_hash=password_hash("StrongOwnerPassword!"),
        ))
        db.add(StaffAccount(
            email="monitor@example.org", name="Monitor",
            roles=["monitor"], password_hash=password_hash("StrongMonitorPassword!"),
        ))
        db.add(SuperviselyConfig(
            id=1, server_url="https://app.supervisely.com", team_id=125980,
        ))
    def override():
        with maker() as db:
            yield db
    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(
        "annotatiathon.auth.get_settings",
        lambda: type("Settings", (), {"app_env": "local"})(),
    )
    with TestClient(app) as http:
        login = http.post("/api/v1/auth/login", json={
            "email": "owner@example.org", "password": "StrongOwnerPassword!",
        })
        assert login.status_code == 200
        yield http, maker, login.json()["csrf_token"]
    app.dependency_overrides.clear()
    engine.dispose()


def call(http, csrf, endpoint, payload=None):
    return http.post(
        "/api/v1" + endpoint,
        json=payload, headers={"X-CSRF-Token": csrf},
    )


def native_snapshot():
    return NativeTeamSnapshot(
        team_id=125980, team_name="Annotatiathon Pro",
        workspace_id=None, account_id=123550, account_login="organiser-username",
        members=(
            NativeMember(id=123550, login="organiser-username",
                         email=None, role="admin"),
            NativeMember(id=44501, login="AliceReal",
                         email="alice@example.org", role="annotator"),
            NativeMember(id=44502, login="BobReal",
                         email=None, role="annotator"),
        ),
    )


def add(http, csrf, name, email):
    response = call(http, csrf, "/people", {
        "name": name, "email": email, "kind": "participant",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_verify_native_team_does_not_touch_roster_or_expose_token(context, monkeypatch):
    http, maker, csrf = context
    monkeypatch.setattr(
        "annotatiathon.native_sync_api.read_team_snapshot",
        lambda config: native_snapshot(),
    )
    result = call(http, csrf, "/settings/supervisely/verify")
    assert result.status_code == 200, result.text
    assert result.json()["account_login"] == "organiser-username"
    assert result.json()["member_count"] == 3
    assert result.json()["native_mutations"] is False
    assert "API_TOKEN" not in result.text
    assert http.get("/api/v1/settings/supervisely").json()["verified"] is True
    with maker() as db:
        assert db.get(SuperviselyConfig, 1).verified is True


def test_native_preview_manual_link_and_idempotent_local_apply(context, monkeypatch):
    http, _, csrf = context
    alice = add(http, csrf, "Alice", "alice@example.org")
    bob = add(http, csrf, "Bob", "bob@example.org")
    missing = add(http, csrf, "Carol", "carol@example.org")
    monkeypatch.setattr(
        "annotatiathon.native_sync_api.read_team_snapshot",
        lambda config: native_snapshot(),
    )
    first = call(http, csrf, "/people/supervisely/preview", {"manual_links": []})
    assert first.status_code == 200, first.text
    assert [r["person_id"] for r in first.json()["matched"]] == [alice]
    assert len(first.json()["unmatched_native"]) == 2
    second = call(http, csrf, "/people/supervisely/preview", {
        "manual_links": [{"person_id": bob, "native_user_id": 44502}],
    })
    assert second.status_code == 200, second.text
    plan = second.json()
    assert {m["person_id"] for m in plan["matched"]} == {alice, bob}
    assert plan["matched"][1]["method"] == "manual"
    outcome = call(http, csrf, "/people/supervisely/plans/" + plan["plan_id"] + "/apply")
    assert outcome.status_code == 200, outcome.text
    assert outcome.json()["applied"] == 2
    assert outcome.json()["native_mutations"] is False
    again = call(http, csrf, "/people/supervisely/plans/" + plan["plan_id"] + "/apply")
    assert again.status_code == 200
    assert again.json()["already_applied"] is True
    everyone = {p["id"]: p for p in http.get("/api/v1/people").json()["items"]}
    assert everyone[alice]["membership_status"] == "joined"
    assert everyone[bob]["membership_status"] == "joined"
    assert everyone[bob]["supervisely_login"] == "BobReal"
    assert everyone[bob]["supervisely_role"] == "annotator"
    assert everyone[bob]["native_verified_at"] is not None
    assert everyone[missing]["membership_status"] == "not_invited"


def test_native_preview_stales_and_keeps_other_events_isolated(context, monkeypatch):
    http, _, csrf = context
    alice = add(http, csrf, "Alice", "alice@example.org")
    actual = [native_snapshot()]
    monkeypatch.setattr(
        "annotatiathon.native_sync_api.read_team_snapshot",
        lambda config: actual[0],
    )
    p = call(http, csrf, "/people/supervisely/preview", {})
    assert p.status_code == 200
    actual[0] = NativeTeamSnapshot(
        **{**native_snapshot().__dict__, "members": (
            *native_snapshot().members,
            NativeMember(id=44503, login="NewUser", email=None, role="annotator"),
        )}
    )
    assert call(http, csrf,
                "/people/supervisely/plans/"+p.json()["plan_id"]+"/apply").status_code == 409
    actual[0] = native_snapshot()
    new_event = call(http, csrf, "/events", {"name": "Another Event"})
    assert new_event.status_code == 201
    other = call(http, csrf, "/people/supervisely/plans/"+p.json()["plan_id"]+"/apply")
    assert other.status_code == 404
    assert http.get("/api/v1/people").json()["total"] == 0
    assert call(http, csrf, "/events/"+DEFAULT_EVENT_ID+"/select").status_code == 200
    assert http.get("/api/v1/people").json()["items"][0]["id"] == alice


def test_native_membership_read_requires_organiser(context, monkeypatch):
    http, _, csrf = context
    monkeypatch.setattr(
        "annotatiathon.native_sync_api.read_team_snapshot",
        lambda config: native_snapshot(),
    )
    assert call(http, csrf, "/people/supervisely/preview", {
        "manual_links": [{"person_id": "absent", "native_user_id": 44502}],
    }).status_code == 422
    login = http.post("/api/v1/auth/login", json={
        "email": "monitor@example.org", "password": "StrongMonitorPassword!",
    })
    assert login.status_code == 200
    monitor_csrf = login.json()["csrf_token"]
    assert call(http, monitor_csrf, "/settings/supervisely/verify").status_code == 403
    assert call(http, monitor_csrf, "/people/supervisely/preview", {}).status_code == 403
    assert http.get("/api/v1/settings/supervisely").status_code == 403


def test_pagination_and_token_never_in_error(monkeypatch):
    seen = []
    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("members.list"):
            page = request.read().decode()
            if '"page":1' in page:
                return httpx.Response(200, json={
                    "total": 3, "perPage": 2, "pagesCount": 2,
                    "entities": [
                        {"id": 21, "login": "user21", "role": "annotator"},
                        {"id": 22, "login": "user22", "role": "annotator"},
                    ],
                })
            return httpx.Response(200, json={
                "total": 3, "perPage": 2, "pagesCount": 2,
                "entities": [{"id": 23, "login": "user23", "role": "manager"}],
            })
        return httpx.Response(200, json={"id": 1})
    transport = httpx.MockTransport(respond)
    with httpx.Client(transport=transport) as client:
        members = _read_members(client, "https://app.supervisely.com", "PRIVATE_TEST_TOKEN", 125980)
        assert len(members) == 3
        assert all(r.headers["x-api-key"] == "PRIVATE_TEST_TOKEN" for r in seen)
        assert all(r.method == "POST" for r in seen)
        assert all(r.url.host == "app.supervisely.com" for r in seen)
        denied = _request_json(
            client, "https://app.supervisely.com", "PRIVATE_TEST_TOKEN",
            "users.me", {},
        )
        assert denied["id"] == 1
    monkeypatch.setattr(
        "annotatiathon.supervisely_client.get_settings",
        lambda: type("Settings", (), {
            "supervisely_api_token": SecretStr("PRIVATE_TEST_TOKEN"),
            "supervisely_allowed_hosts": "app.supervisely.com",
        })(),
    )
    with pytest.raises(NativeAPIError, match="not allowlisted") as err:
        require_connection(SuperviselyConfig(
            team_id=125980, server_url="https://127.0.0.1",
        ))
    assert "PRIVATE_TEST_TOKEN" not in str(err.value)
