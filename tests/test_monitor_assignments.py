"""Monitor assignment responsibility is balanced, event-local and auditable."""
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
    MonitorAssignment,
    StaffAccount,
)
from annotatiathon.security import password_hash


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        db.add(Event(id=DEFAULT_EVENT_ID, name="Initial Event"))
        db.add(StaffAccount(
            email="owner@example.org", name="Owner", roles=["organiser"],
            password_hash=password_hash("StrongOrganiserPassword!"),
        ))
        db.add(StaffAccount(
            email="monitor@example.org", name="Monitor", roles=["monitor"],
            password_hash=password_hash("StrongMonitorPassword!"),
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
            "email": "owner@example.org", "password": "StrongOrganiserPassword!",
        })
        assert login.status_code == 200
        yield http, maker, login.json()["csrf_token"]
    app.dependency_overrides.clear()
    engine.dispose()


def mutate(http, csrf, path, payload=None, method="POST"):
    return http.request(
        method, "/api/v1" + path, json=payload,
        headers={"X-CSRF-Token": csrf},
    )


def roster(http, csrf, monitor_count=2, team_count=5):
    monitors = []
    for i in range(monitor_count):
        result = mutate(http, csrf, "/people", {
            "name": f"Monitor {i}", "email": f"monitor{i}@example.org", "kind": "monitor",
        })
        assert result.status_code == 201, result.text
        monitors.append(result.json()["id"])
    for i in range(team_count):
        for j in range(2):
            result = mutate(http, csrf, "/people", {
                "name": f"Member {i}-{j}", "email": f"p{i}-{j}@example.org",
                "kind": "participant", "team_name": f"Team {i:02d}",
            })
            assert result.status_code == 201, result.text
    teams = [
        t["id"] for t in http.get("/api/v1/teams").json()["teams"]
        if t["type"] == "competition"
    ]
    assert len(teams) == team_count
    return monitors, teams


def test_balanced_preview_manual_and_idempotent_apply(ctx):
    http, maker, csrf = ctx
    monitors, teams = roster(http, csrf)
    first = mutate(http, csrf, "/monitoring/assignments/preview", {
        "mode": "fill_unassigned",
    })
    assert first.status_code == 200, first.text
    assert first.json()["changes"] == 5
    proposals = first.json()["proposals"]
    assert {item["team_id"] for item in proposals} == set(teams)
    assert {item["new_monitor_id"] for item in proposals} == set(monitors)
    assigned = mutate(
        http, csrf,
        "/monitoring/assignments/plans/"+first.json()["plan_id"]+"/apply",
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["applied"] == 5
    assert mutate(
        http, csrf,
        "/monitoring/assignments/plans/"+first.json()["plan_id"]+"/apply",
    ).json()["already_applied"] is True
    overview = http.get("/api/v1/monitoring/assignments").json()
    assert overview["unassigned_teams"] == 0
    assert sorted(m["assigned_count"] for m in overview["monitors"]) == [2, 3]
    team = http.get("/api/v1/teams").json()["teams"][1]
    assert team["monitor_name"].startswith("Monitor")
    second = mutate(http, csrf, "/monitoring/assignments/preview", {
        "mode": "fill_unassigned",
    })
    assert second.status_code == 200
    assert second.json()["changes"] == 0
    # Manual reassignment is independent of the balancing tool.
    old_id = overview["teams"][0]["monitor_person_id"]
    other = next(mid for mid in monitors if mid != old_id)
    changed = mutate(
        http, csrf, "/monitoring/teams/"+overview["teams"][0]["team_id"],
        {"monitor_person_id": other}, "PUT",
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["changed"] is True
    same = mutate(
        http, csrf, "/monitoring/teams/"+overview["teams"][0]["team_id"],
        {"monitor_person_id": other}, "PUT",
    )
    assert same.json()["changed"] is False
    with maker() as db:
        assert len(db.scalars(select(MonitorAssignment)).all()) == 5


def test_manual_unassign_and_cross_event_protection(ctx):
    http, _, csrf = ctx
    mids, teams = roster(http, csrf, monitor_count=1, team_count=1)
    result = mutate(http, csrf, "/monitoring/teams/"+teams[0], {
        "monitor_person_id": mids[0],
    }, "PUT")
    assert result.status_code == 200
    none = mutate(http, csrf, "/monitoring/teams/"+teams[0], {
        "monitor_person_id": None,
    }, "PUT")
    assert none.status_code == 200
    assert http.get("/api/v1/monitoring/assignments").json()["unassigned_teams"] == 1
    assert mutate(http, csrf, "/events", {"name": "Second Event"}).status_code == 201
    assert http.get("/api/v1/monitoring/assignments").json()["total_teams"] == 0
    assert mutate(http, csrf, "/monitoring/teams/"+teams[0], {
        "monitor_person_id": mids[0],
    }, "PUT").status_code == 404
    assert mutate(http, csrf, "/events/"+DEFAULT_EVENT_ID+"/select").status_code == 200
    assert mutate(http, csrf, "/monitoring/teams/"+teams[0], {
        "monitor_person_id": mids[0],
    }, "PUT").status_code == 200


def test_balancing_plan_detects_stale_roster_and_reset_clears_assignments(ctx):
    http, maker, csrf = ctx
    mids, teams = roster(http, csrf)
    initial = mutate(http, csrf, "/monitoring/assignments/preview", {
        "mode": "rebalance_all",
    })
    assert initial.status_code == 200
    assert mutate(http, csrf, "/monitoring/teams/"+teams[0], {
        "monitor_person_id": mids[0],
    }, "PUT").status_code == 200
    stale = mutate(
        http, csrf,
        "/monitoring/assignments/plans/"+initial.json()["plan_id"]+"/apply",
    )
    assert stale.status_code == 409
    new_plan = mutate(http, csrf, "/monitoring/assignments/preview", {
        "mode": "rebalance_all",
    })
    assert new_plan.status_code == 200
    assert mutate(
        http, csrf,
        "/monitoring/assignments/plans/"+new_plan.json()["plan_id"]+"/apply",
    ).status_code == 200
    reset = mutate(http, csrf, "/events/resets/preview", {
        "mode": "selected", "areas": ["assignments"],
    })
    assert reset.status_code == 200, reset.text
    assert reset.json()["counts"]["monitor_assignments"] == 5
    applied = mutate(http, csrf, "/events/resets/"+reset.json()["plan_id"]+"/execute", {
        "confirmation": reset.json()["confirmation_phrase"],
    })
    assert applied.status_code == 200, applied.text
    assert http.get("/api/v1/monitoring/assignments").json()["assigned_teams"] == 0
    with maker() as db:
        historical = list(db.scalars(select(MonitorAssignment)))
        assert len(historical) == 5
        assert not any(a.active for a in historical)


def test_organiser_only_and_archived_event_is_immutable(ctx):
    http, _, csrf = ctx
    mids, teams = roster(http, csrf, monitor_count=1, team_count=1)
    assert mutate(http, csrf, "/events/"+DEFAULT_EVENT_ID+"/archive", {
        "confirmation": "Initial Event"
    }).status_code == 200
    assert mutate(http, csrf, "/monitoring/teams/"+teams[0], {
        "monitor_person_id": mids[0],
    }, "PUT").status_code == 409
    assert mutate(http, csrf, "/events/"+DEFAULT_EVENT_ID+"/reopen", {
        "confirmation": "Initial Event"
    }).status_code == 200
    login = http.post("/api/v1/auth/login", json={
        "email": "monitor@example.org", "password": "StrongMonitorPassword!",
    })
    assert login.status_code == 200
    token = login.json()["csrf_token"]
    assert http.get("/api/v1/monitoring/assignments").status_code == 200
    assert mutate(http, token, "/monitoring/teams/"+teams[0], {
        "monitor_person_id": mids[0],
    }, "PUT").status_code == 403
    assert mutate(http, token, "/monitoring/assignments/preview", {
        "mode": "fill_unassigned",
    }).status_code == 403
