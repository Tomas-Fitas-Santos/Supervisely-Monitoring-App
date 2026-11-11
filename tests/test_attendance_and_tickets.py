"""Integration tests for staff access, attendance and auditable ticket transactions.

These isolated synthetic fixtures do not create any in-app demo event or native Supervisely data.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon.app import app
from annotatiathon.db import get_db
from annotatiathon.models import (
    DEFAULT_EVENT_ID,
    AuditEvent,
    Base,
    Event,
    StaffAccount,
    TicketDistribution,
    TicketLedgerEntry,
)
from annotatiathon.security import password_hash


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine("sqlite+pysqlite://",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        db.add(Event(id=DEFAULT_EVENT_ID, name="Initial Event"))
        for email, role, password in [
            ("boss@example.org", "organiser", "organiser-password-12345"),
            ("monitor@example.org", "monitor", "monitor-password-12345"),
        ]:
            db.add(StaffAccount(email=email, name=role.title(),
                                roles=[role], password_hash=password_hash(password)))
    def override():
        with maker() as db:
            yield db
    app.dependency_overrides[get_db] = override
    monkeypatch.setattr("annotatiathon.auth.get_settings",
                        lambda: type("Settings", (), {"app_env": "local"})())
    with TestClient(app) as client:
        organiser = client.post("/api/v1/auth/login", json={
            "email": "boss@example.org", "password": "organiser-password-12345",
        })
        assert organiser.status_code == 200, organiser.text
        csrf = organiser.json()["csrf_token"]
        yield client, maker, csrf, organiser.json()["staff"]["id"]
    app.dependency_overrides.clear()
    engine.dispose()


def new_person(client, csrf, email: str, team: str | None = None) -> str:
    body = {"name": email.split("@")[0], "email": email, "kind": "participant"}
    if team:
        body["team_name"] = team
    response = client.post("/api/v1/people", headers={"X-CSRF-Token": csrf},
                           json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def new_category(client, csrf):
    result = client.post("/api/v1/tickets/categories",
                         headers={"X-CSRF-Token": csrf},
                         json={"slug": "pizza", "name": "Pizza"})
    assert result.status_code == 201, result.text
    return result.json()["id"]


def preview(client, csrf, category, *, people=None, operation="issue", quantity=1,
            scope="individuals", teams=None):
    response = client.post("/api/v1/tickets/preview", headers={"X-CSRF-Token": csrf},
        json={"category_id": category, "operation": operation, "quantity": quantity,
              "selection": {"scope": scope, "person_ids": people or [], "team_ids": teams or []}})
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, csrf, batch_id):
    return client.post("/api/v1/tickets/batches/"+batch_id+"/confirm",
                       headers={"X-CSRF-Token": csrf})


def reverse(client, csrf, batch_id):
    return client.post("/api/v1/tickets/batches/"+batch_id+"/reverse",
                       headers={"X-CSRF-Token": csrf})


def test_staff_can_be_organiser_and_monitor_without_second_account(setup):
    client, dbmaker, csrf, staff_id = setup
    response = client.put("/api/v1/staff/"+staff_id+"/roles",
                          headers={"X-CSRF-Token": csrf},
                          json={"roles": ["organiser", "monitor"]})
    assert response.status_code == 200, response.text
    assert set(response.json()["roles"]) == {"organiser", "monitor"}
    assert set(client.get("/api/v1/auth/me").json()["staff"]["roles"]) == {
        "organiser", "monitor"
    }
    blocked = client.put("/api/v1/staff/"+staff_id+"/roles",
                         headers={"X-CSRF-Token": csrf},
                         json={"roles": ["monitor"]})
    assert blocked.status_code == 409
    assert client.put("/api/v1/staff/"+staff_id+"/roles",
                      json={"roles": ["organiser"]}).status_code == 403
    with dbmaker() as db:
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.action == "staff.roles.update"
        )) is not None


def test_reception_check_in_is_idempotent_and_reversible(setup):
    client, maker, csrf, _staff_id = setup
    pid = new_person(client, csrf, "arrival@example.org")
    summary = client.get("/api/v1/attendance/summary").json()
    assert summary["registered"] == 1
    assert summary["present"] == 0
    path = "/api/v1/attendance/"+pid+"/check-in"
    assert client.post(path).status_code == 403
    first = client.post(path, headers={"X-CSRF-Token": csrf})
    assert first.status_code == 200
    assert first.json()["changed"] is True
    again = client.post(path, headers={"X-CSRF-Token": csrf})
    assert again.status_code == 200
    assert again.json()["changed"] is False
    assert client.get("/api/v1/attendance/summary").json()["present"] == 1
    assert client.get("/api/v1/people?attendance=present").json()["total"] == 1
    assert client.get("/api/v1/people?attendance=missing").json()["total"] == 0
    undone = client.post("/api/v1/attendance/"+pid+"/reverse",
                         headers={"X-CSRF-Token": csrf})
    assert undone.status_code == 200
    assert undone.json()["checked_in"] is False
    assert client.get("/api/v1/people?attendance=missing").json()["total"] == 1
    with maker() as db:
        events = list(db.scalars(select(AuditEvent).where(
            AuditEvent.action.like("attendance.%")
        )))
        assert len(events) == 2


def test_ticket_preview_is_snapshot_and_confirmation_is_idempotent(setup):
    client, maker, csrf, _staff_id = setup
    a = new_person(client, csrf, "a@example.org", "Team One")
    b = new_person(client, csrf, "b@example.org", "Team One")
    category = new_category(client, csrf)
    client.post("/api/v1/attendance/"+a+"/check-in", headers={"X-CSRF-Token": csrf}, json={"table_number": 18})
    first = preview(client, csrf, category, scope="all_checked_in")
    assert first["count"] == 1
    assert first["recipients"][0]["id"] == a
    client.post("/api/v1/attendance/"+b+"/check-in", headers={"X-CSRF-Token": csrf})
    assert confirm(client, csrf, first["id"]).status_code == 200
    retry = confirm(client, csrf, first["id"])
    assert retry.status_code == 200
    assert retry.json()["already_confirmed"] is True
    account = client.get("/api/v1/tickets/balances").json()["items"]
    assert len(account) == 1
    assert account[0]["person_id"] == a
    assert account[0]["allocated"] == 1
    assert account[0]["issued"] == 1
    assert account[0]["redeemed"] == 0
    with maker() as db:
        ledger = db.scalars(select(TicketLedgerEntry)).all()
        assert len(ledger) == 2
        batch = db.get(TicketDistribution, first["id"])
        assert batch.recipient_ids == [a]
    team_id = client.get("/api/v1/teams").json()["teams"][1]["id"]
    next_batch = preview(client, csrf, category, scope="teams", teams=[team_id])
    assert next_batch["count"] == 2


def test_soft_warnings_and_consistent_balances_with_reversal(setup):
    client, maker, csrf, _ = setup
    person = new_person(client, csrf, "pizza@example.org")
    category = new_category(client, csrf)
    change = client.patch("/api/v1/tickets/categories/"+category,
                          headers={"X-CSRF-Token": csrf},
                          json={"warning_enabled": True, "warning_threshold": 2,
                                "warning_window_minutes": 60})
    assert change.status_code == 200
    first = preview(client, csrf, category, people=[person], quantity=2)
    assert first["soft_warning_count"] == 0
    assert confirm(client, csrf, first["id"]).status_code == 200
    next_issue = preview(client, csrf, category, people=[person])
    assert next_issue["soft_warning_count"] == 1
    assert confirm(client, csrf, next_issue["id"]).status_code == 200
    assert reverse(client, csrf, next_issue["id"]).status_code == 200
    reversed_again = reverse(client, csrf, next_issue["id"])
    assert reversed_again.json()["already_reversed"] is True
    redemption = preview(client, csrf, category, people=[person], operation="redeem")
    assert confirm(client, csrf, redemption["id"]).status_code == 200
    # Cannot remove all originally issued tickets until the redemption is reversed.
    assert reverse(client, csrf, first["id"]).status_code == 409
    assert reverse(client, csrf, redemption["id"]).status_code == 200
    assert reverse(client, csrf, first["id"]).status_code == 200
    final = client.get("/api/v1/tickets/balances?person_id="+person).json()["items"][0]
    assert (final["allocated"],final["issued"],final["redeemed"]) == (0, 0, 0)
    with maker() as db:
        entries = list(db.scalars(select(TicketLedgerEntry)))
        assert len(entries) == 10  # includes signed reversal history
        assert any(entry.quantity < 0 for entry in entries)


def test_redemption_cannot_exceed_physical_issuance_and_role_isolation(setup):
    client, maker, csrf, _ = setup
    person = new_person(client, csrf, "drink@example.org")
    category = new_category(client, csrf)
    redeem = preview(client, csrf, category, people=[person], operation="redeem")
    assert confirm(client, csrf, redeem["id"]).status_code == 409
    with maker() as db:
        assert db.get(TicketDistribution, redeem["id"]).status == "preview"
        assert not db.scalars(select(TicketLedgerEntry)).all()
    monitor_login = client.post("/api/v1/auth/login", json={
        "email": "monitor@example.org", "password": "monitor-password-12345"
    })
    assert monitor_login.status_code == 200
    monitor_csrf = monitor_login.json()["csrf_token"]
    assert client.get("/api/v1/tickets/categories").status_code == 200
    assert client.post("/api/v1/tickets/preview", headers={"X-CSRF-Token": monitor_csrf},
      json={"category_id": category, "operation": "issue", "quantity": 1,
            "selection": {"scope": "individuals", "person_ids": [person]}}).status_code == 200
    assert client.post("/api/v1/tickets/categories",
        headers={"X-CSRF-Token": monitor_csrf},
        json={"slug": "forbidden", "name": "Forbidden"}).status_code == 403
