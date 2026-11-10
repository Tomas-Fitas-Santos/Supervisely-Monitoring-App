"""Activity scheduling, ranked results and ticket award invariants."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon.app import app
from annotatiathon.db import get_db
from annotatiathon.models import (
    DEFAULT_EVENT_ID,
    ActivityAward,
    Base,
    Event,
    StaffAccount,
    TicketLedgerEntry,
)
from annotatiathon.security import password_hash


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine("sqlite+pysqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        db.add(Event(id=DEFAULT_EVENT_ID, name="Initial Event"))
        db.add(StaffAccount(
            email="organiser@example.org", name="Organiser",
            password_hash=password_hash("SecureOrganiserPassword!"),
            roles=["organiser"],
        ))
        db.add(StaffAccount(
            email="activity@example.org", name="Activity Staff",
            password_hash=password_hash("SecureActivityPassword!"),
            roles=["activity"],
        ))
    def override():
        with maker() as db:
            yield db
    app.dependency_overrides[get_db] = override
    monkeypatch.setattr("annotatiathon.auth.get_settings",
                        lambda: type("Settings", (), {"app_env": "local"})())
    with TestClient(app) as http:
        response = http.post("/api/v1/auth/login", json={
            "email": "organiser@example.org",
            "password": "SecureOrganiserPassword!",
        })
        assert response.status_code == 200
        yield http, maker, response.json()["csrf_token"]
    app.dependency_overrides.clear()
    engine.dispose()


def call(http, csrf, path, data=None, method="POST"):
    return http.request(
        method, "/api/v1" + path,
        json=data, headers={"X-CSRF-Token": csrf},
    )


def create_setup(http, csrf):
    pids = []
    for name in ("Alice", "Bob"):
        result = call(http, csrf, "/people", {
            "name": name, "email": name.lower() + "@example.org",
            "kind": "participant", "team_name": "Bluebirds",
        })
        assert result.status_code == 201, result.text
        pids.append(result.json()["id"])
    team_id = http.get("/api/v1/teams").json()["teams"][1]["id"]
    category = call(http, csrf, "/tickets/categories", {
        "name": "Prize Pizza", "slug": "prize-pizza",
    })
    assert category.status_code == 201, category.text
    activity = call(http, csrf, "/activities", {
        "name": "Bird quiz", "description": "Knowledge contest",
        "location": "Auditorium", "start_at": 1795000000,
        "end_at": 1795003600,
    })
    assert activity.status_code == 201, activity.text
    return pids, team_id, category.json()["id"], activity.json()["id"]


def test_activity_lifecycle_and_team_reward_allocations(ctx):
    http, dbmaker, csrf = ctx
    people, team_id, category_id, activity_id = create_setup(http, csrf)
    assert http.get("/api/v1/activities").json()["items"][0]["name"] == "Bird quiz"
    result = call(http, csrf, "/activities/" + activity_id + "/results", {
        "subject_type": "team", "subject_id": team_id, "rank": 1,
        "score_points": 100, "notes": "Top team",
    }, "PUT")
    assert result.status_code == 200, result.text
    assert result.json()["created"] is True
    update = call(http, csrf, "/activities/" + activity_id + "/results", {
        "subject_type": "team", "subject_id": team_id, "rank": 1, "score_points": 110,
    }, "PUT")
    assert update.status_code == 200
    assert update.json()["created"] is False
    prize = call(http, csrf, "/activities/" + activity_id + "/prizes", {
        "category_id": category_id, "min_rank": 1, "max_rank": 1, "quantity": 2,
    })
    assert prize.status_code == 201, prize.text
    complete = call(http, csrf, "/activities/" + activity_id + "/finalise")
    assert complete.status_code == 200, complete.text
    assert complete.json()["awards"] == 2
    assert complete.json()["ticket_batches"] == 1
    retry = call(http, csrf, "/activities/" + activity_id + "/finalise")
    assert retry.status_code == 200
    assert retry.json()["already_finalised"] is True
    detail = http.get("/api/v1/activities/" + activity_id).json()
    assert detail["activity"]["status"] == "completed"
    assert len(detail["results"]) == 1
    assert len(detail["awards"]) == 2
    rows = http.get("/api/v1/tickets/balances").json()["items"]
    assert {r["person_id"] for r in rows} == set(people)
    assert all((r["allocated"], r["issued"], r["redeemed"]) == (2, 0, 0) for r in rows)
    assert call(http, csrf, "/activities/" + activity_id + "/results", {
        "subject_type": "team", "subject_id": team_id, "rank": 2,
    }, "PUT").status_code == 409
    assert call(http, csrf, "/activities/" + activity_id + "/prizes", {
        "category_id": category_id, "min_rank": 1, "max_rank": 1, "quantity": 1,
    }).status_code == 409
    with dbmaker() as db:
        awards = list(db.scalars(select(ActivityAward)))
        assert len(awards) == 2
        ledger = list(db.scalars(select(TicketLedgerEntry)))
        assert len(ledger) == 2
        assert all(x.kind == "allocated" for x in ledger)


def test_manual_ticket_issuance_from_prize_allocation(ctx):
    http, _, csrf = ctx
    people, team_id, cat, activity_id = create_setup(http, csrf)
    assert call(http, csrf, "/activities/" + activity_id + "/results", {
        "subject_type": "team", "subject_id": team_id, "rank": 1,
    }, "PUT").status_code == 200
    assert call(http, csrf, "/activities/" + activity_id + "/prizes", {
        "category_id": cat, "min_rank": 1, "max_rank": 1, "quantity": 1,
    }).status_code == 201
    assert call(http, csrf, "/activities/" + activity_id + "/finalise").status_code == 200
    issue = call(http, csrf, "/tickets/preview", {
        "category_id": cat, "operation": "issue", "quantity": 1,
        "selection": {"scope": "individuals", "person_ids": [people[0]]},
    })
    assert issue.status_code == 200
    assert call(http, csrf, "/tickets/batches/" + issue.json()["id"] + "/confirm").status_code == 200
    records = http.get("/api/v1/tickets/balances?person_id=" + people[0]).json()["items"]
    assert len(records) == 1
    assert records[0]["allocated"] == 1
    assert records[0]["issued"] == 1
    assert records[0]["issued_remaining"] == 1


def test_activity_winner_and_ticket_ids_cannot_cross_events(ctx):
    http, _, csrf = ctx
    people, team_id, cat, old_activity = create_setup(http, csrf)
    new_event = call(http, csrf, "/events", {"name": "Next Competition"})
    assert new_event.status_code == 201
    new_activity = call(http, csrf, "/activities", {"name": "Next quiz"})
    assert new_activity.status_code == 201
    new_id = new_activity.json()["id"]
    assert http.get("/api/v1/activities/" + old_activity).status_code == 404
    assert call(http, csrf, "/activities/" + new_id + "/results", {
        "subject_type": "person", "subject_id": people[0], "rank": 1,
    }, "PUT").status_code == 422
    assert call(http, csrf, "/activities/" + new_id + "/results", {
        "subject_type": "team", "subject_id": team_id, "rank": 1,
    }, "PUT").status_code == 422
    assert call(http, csrf, "/activities/" + new_id + "/prizes", {
        "category_id": cat, "min_rank": 1, "max_rank": 1, "quantity": 1,
    }).status_code == 422


def test_awards_require_activity_and_ticket_reset_together(ctx):
    http, dbmaker, csrf = ctx
    people, team_id, category, activity = create_setup(http, csrf)
    call(http, csrf, "/activities/" + activity + "/results", {
        "subject_type": "team", "subject_id": team_id, "rank": 1,
    }, "PUT")
    call(http, csrf, "/activities/" + activity + "/prizes", {
        "category_id": category, "min_rank": 1, "max_rank": 1, "quantity": 1,
    })
    assert call(http, csrf, "/activities/" + activity + "/finalise").status_code == 200
    assert call(http, csrf, "/events/resets/preview", {
        "mode": "selected", "areas": ["activities"],
    }).status_code == 409
    plan = call(http, csrf, "/events/resets/preview", {
        "mode": "selected", "areas": ["tickets", "activities"],
    })
    assert plan.status_code == 200, plan.text
    assert plan.json()["counts"]["activity_awards"] == 2
    done = call(http, csrf, "/events/resets/" + plan.json()["plan_id"] + "/execute", {
        "confirmation": plan.json()["confirmation_phrase"],
    })
    assert done.status_code == 200, done.text
    assert http.get("/api/v1/activities").json()["items"] == []
    ledger = http.get("/api/v1/tickets/balances").json()["items"]
    assert len(ledger) == 2
    assert all(r["allocated"] == 0 for r in ledger)
    export = http.get(
        "/api/v1/events/" + DEFAULT_EVENT_ID + "/snapshots/" + done.json()["snapshot_id"]
    )
    assert export.status_code == 200
    assert len(export.json()["records"]["activity_awards"]) == 2
    with dbmaker() as db:
        assert len(list(db.scalars(select(ActivityAward)))) == 2


def test_activity_permissions_and_validation(ctx):
    http, _, csrf = ctx
    people, team_id, cat, activity = create_setup(http, csrf)
    assert call(http, csrf, "/activities", {
        "name": "Invalid", "start_at": 100, "end_at": 50,
    }).status_code == 422
    assert call(http, csrf, "/activities/" + activity + "/results", {
        "subject_type": "team", "subject_id": team_id, "rank": 0,
    }, "PUT").status_code == 422
    assert call(http, csrf, "/activities/" + activity + "/prizes", {
        "category_id": cat, "min_rank": 3, "max_rank": 1, "quantity": 1,
    }).status_code == 422
    login = http.post("/api/v1/auth/login", json={
        "email": "activity@example.org", "password": "SecureActivityPassword!",
    })
    assert login.status_code == 200
    activity_csrf = login.json()["csrf_token"]
    assert http.get("/api/v1/activities").status_code == 200
    assert call(http, activity_csrf, "/activities/" + activity + "/results", {
        "subject_type": "person", "subject_id": people[0], "rank": 1,
    }, "PUT").status_code == 200
    assert call(http, activity_csrf, "/activities/" + activity + "/prizes", {
        "category_id": cat, "min_rank": 1, "max_rank": 1, "quantity": 1,
    }).status_code == 403
    assert call(http, activity_csrf, "/activities/" + activity + "/finalise").status_code == 403
