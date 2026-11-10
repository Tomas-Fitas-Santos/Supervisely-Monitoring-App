"""Real event-scoping, archive/read-only and non-destructive reset contracts."""
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
    Person,
    StaffAccount,
    TicketLedgerEntry,
)
from annotatiathon.security import password_hash


@pytest.fixture
def user(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        db.add(Event(id=DEFAULT_EVENT_ID, name="Initial Event"))
        db.add(StaffAccount(
            email="boss@example.org", name="Organiser", roles=["organiser"],
            password_hash=password_hash("StrongTemporaryPassword!"),
        ))
        db.add(StaffAccount(
            email="monitor@example.org", name="Monitor", roles=["monitor"],
            password_hash=password_hash("StrongMonitorPassword!"),
        ))
    def override():
        with maker() as db:
            yield db
    app.dependency_overrides[get_db] = override
    monkeypatch.setattr("annotatiathon.auth.get_settings",
                        lambda: type("Settings", (), {"app_env": "local"})())
    with TestClient(app) as client:
        result = client.post("/api/v1/auth/login", json={
            "email": "boss@example.org", "password": "StrongTemporaryPassword!",
        })
        assert result.status_code == 200
        yield client, maker, result.json()["csrf_token"]
    app.dependency_overrides.clear()
    engine.dispose()


def mutation(http, csrf, path, payload=None, method="POST"):
    return http.request(
        method, "/api/v1" + path, headers={"X-CSRF-Token": csrf},
        json=payload,
    )


def add_person(http, csrf, email):
    result = mutation(http, csrf, "/people", {
        "name": email.split("@")[0], "email": email, "kind": "participant",
        "team_name": "Team 1",
    })
    assert result.status_code == 201, result.text
    return result.json()["id"]


def ticket_category(http):
    return next(x for x in http.get("/api/v1/tickets/categories").json()["items"]
                if x["slug"] == "pizza")["id"]


def issue(http, csrf, category_id, people):
    result = mutation(http, csrf, "/tickets/preview", {
        "category_id": category_id, "operation": "issue", "quantity": 1,
        "selection": {"scope": "individuals", "person_ids": people},
    })
    assert result.status_code == 200, result.text
    confirmed = mutation(http, csrf, "/tickets/batches/" + result.json()["id"] + "/confirm")
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def make_plan(http, csrf, mode, areas):
    result = mutation(http, csrf, "/events/resets/preview", {
        "mode": mode, "areas": areas,
    })
    assert result.status_code == 200, result.text
    return result.json()


def test_events_separate_people_teams_and_categories(user):
    http, dbmaker, csrf = user
    first_id = add_person(http, csrf, "same@example.org")
    create = mutation(http, csrf, "/events", {"name": "Annotatiathon 2027"})
    assert create.status_code == 201, create.text
    second_event_id = create.json()["id"]
    assert second_event_id != DEFAULT_EVENT_ID
    assert http.get("/api/v1/people").json()["total"] == 0
    assert [x["slug"] for x in http.get("/api/v1/tickets/categories").json()["items"]] == [
        "drinks", "massage", "pizza"
    ]
    second_id = add_person(http, csrf, "same@example.org")
    assert first_id != second_id
    assert http.get("/api/v1/teams").json()["totals"]["competition_teams"] == 1
    assert mutation(http, csrf, "/attendance/" + first_id + "/check-in").status_code == 404
    assert mutation(http, csrf, "/tickets/preview", {
        "category_id": ticket_category(http), "operation": "issue", "quantity": 1,
        "selection": {"scope": "individuals", "person_ids": [first_id]},
    }).status_code == 422
    switch = mutation(http, csrf, "/events/" + DEFAULT_EVENT_ID + "/select")
    assert switch.status_code == 200
    assert http.get("/api/v1/people").json()["items"][0]["id"] == first_id
    with dbmaker() as db:
        people = list(db.scalars(select(Person).where(Person.email == "same@example.org")))
        assert {p.event_id for p in people} == {DEFAULT_EVENT_ID, second_event_id}


def test_archive_enforces_read_only_and_can_reopen(user):
    http, _, csrf = user
    pid = add_person(http, csrf, "archive@example.org")
    archived = mutation(http, csrf, "/events/" + DEFAULT_EVENT_ID + "/archive", {
        "confirmation": "Initial Event"
    })
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert http.get("/api/v1/people").json()["total"] == 1
    assert mutation(http, csrf, "/attendance/" + pid + "/check-in").status_code == 409
    assert mutation(http, csrf, "/people", {
        "name": "new", "email": "new@example.org", "kind": "participant"
    }).status_code == 409
    assert mutation(http, csrf, "/events/resets/preview", {
        "mode": "selected", "areas": ["attendance"]
    }).status_code == 409
    reopen = mutation(http, csrf, "/events/" + DEFAULT_EVENT_ID + "/reopen", {
        "confirmation": "Initial Event"
    })
    assert reopen.status_code == 200
    assert mutation(http, csrf, "/attendance/" + pid + "/check-in").status_code == 200


def test_selective_reset_is_audited_snapshot_export_and_zeroed_ledger(user):
    http, dbmaker, csrf = user
    person_id = add_person(http, csrf, "ticket@example.org")
    assert mutation(http, csrf, "/attendance/" + person_id + "/check-in").status_code == 200
    assert mutation(http, csrf, "/people/invitations-reported", {
        "person_ids": [person_id]
    }).status_code == 200
    # Initial test fixture has no migration seed; create a custom ticket category.
    new_cat = mutation(http, csrf, "/tickets/categories", {
        "slug": "pizza", "name": "Pizza"
    })
    assert new_cat.status_code == 201
    issue(http, csrf, new_cat.json()["id"], [person_id])
    plan = make_plan(http, csrf, "selected", ["attendance", "invitations", "tickets"])
    assert plan["counts"]["people"] == 1
    assert plan["counts"]["checked_in"] == 1
    assert plan["counts"]["ticket_ledger_entries"] == 2
    assert mutation(http, csrf, "/events/resets/" + plan["plan_id"] + "/execute", {
        "confirmation": "not the right phrase"
    }).status_code == 422
    reset = mutation(http, csrf, "/events/resets/" + plan["plan_id"] + "/execute", {
        "confirmation": plan["confirmation_phrase"]
    })
    assert reset.status_code == 200, reset.text
    assert reset.json()["mode"] == "selected"
    assert http.get("/api/v1/attendance/summary").json()["present"] == 0
    assert http.get("/api/v1/people").json()["items"][0]["membership_status"] == "not_invited"
    balance = http.get("/api/v1/tickets/balances").json()["items"][0]
    assert (balance["allocated"], balance["issued"], balance["redeemed"]) == (0, 0, 0)
    snapshot_id = reset.json()["snapshot_id"]
    export = http.get(
        "/api/v1/events/" + DEFAULT_EVENT_ID + "/snapshots/" + snapshot_id
    )
    assert export.status_code == 200
    assert export.json()["records"]["people"][0]["check_in_at"] is not None
    assert export.json()["records"]["people"][0]["email"] == "ticket@example.org"
    with dbmaker() as db:
        ledger = list(db.scalars(select(TicketLedgerEntry).where(
            TicketLedgerEntry.event_id == DEFAULT_EVENT_ID
        )))
        assert len(ledger) == 4
        assert sum(e.quantity for e in ledger if e.kind == "issued") == 0
    assert mutation(http, csrf, "/events/resets/" + plan["plan_id"] + "/execute", {
        "confirmation": plan["confirmation_phrase"]
    }).status_code == 409


def test_fresh_start_archives_old_event_and_preserves_staff(user):
    http, _, csrf = user
    original_person = add_person(http, csrf, "old@example.org")
    plan = make_plan(http, csrf, "fresh", [])
    assert plan["counts"]["people"] == 1
    done = mutation(http, csrf, "/events/resets/" + plan["plan_id"] + "/execute", {
        "confirmation": plan["confirmation_phrase"]
    })
    assert done.status_code == 200, done.text
    assert done.json()["archived_event_id"] == DEFAULT_EVENT_ID
    new_event = done.json()["event"]
    assert new_event["reset_from_id"] == DEFAULT_EVENT_ID
    assert http.get("/api/v1/events").json()["current_event_id"] == new_event["id"]
    assert http.get("/api/v1/people").json()["total"] == 0
    assert len(http.get("/api/v1/tickets/categories").json()["items"]) == 3
    assert len(http.get("/api/v1/staff").json()["items"]) == 2
    assert mutation(http, csrf, "/events/" + DEFAULT_EVENT_ID + "/select").status_code == 200
    assert http.get("/api/v1/people").json()["items"][0]["id"] == original_person
    assert mutation(http, csrf, "/attendance/" + original_person + "/check-in").status_code == 409


def test_roster_reset_dependency_and_preview_staleness(user):
    http, _, csrf = user
    pid = add_person(http, csrf, "reset@example.org")
    cat = mutation(http, csrf, "/tickets/categories", {
        "slug": "drink", "name": "Drink"
    }).json()["id"]
    issue(http, csrf, cat, [pid])
    assert mutation(http, csrf, "/events/resets/preview", {
        "mode": "selected", "areas": ["roster"]
    }).status_code == 409
    plan = make_plan(http, csrf, "selected", ["roster", "tickets"])
    assert mutation(http, csrf, "/people", {
        "name": "Changed", "email": "change@example.org", "kind": "participant"
    }).status_code == 201
    stale = mutation(http, csrf, "/events/resets/" + plan["plan_id"] + "/execute", {
        "confirmation": plan["confirmation_phrase"]
    })
    assert stale.status_code == 409
    new_plan = make_plan(http, csrf, "selected", ["roster", "tickets"])
    done = mutation(http, csrf, "/events/resets/" + new_plan["plan_id"] + "/execute", {
        "confirmation": new_plan["confirmation_phrase"]
    })
    assert done.status_code == 200, done.text
    assert http.get("/api/v1/people").json()["total"] == 0
    # Existing identity is reactivated on reimport; historical ledger remains.
    again = add_person(http, csrf, "reset@example.org")
    assert again == pid
    assert http.get("/api/v1/people").json()["total"] == 1


def test_monitor_cannot_manage_events(user):
    http, _, _csrf = user
    login = http.post("/api/v1/auth/login", json={
        "email": "monitor@example.org", "password": "StrongMonitorPassword!"
    })
    assert login.status_code == 200
    token = login.json()["csrf_token"]
    assert http.get("/api/v1/events").status_code == 200
    assert mutation(http, token, "/events", {"name": "Forbidden Event"}).status_code == 403
    assert mutation(http, token, "/events/resets/preview", {
        "mode": "fresh", "areas": []
    }).status_code == 403
    assert http.get("/api/v1/events/" + DEFAULT_EVENT_ID + "/snapshots").status_code == 403
