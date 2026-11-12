"""Account-email corrections preserve an existing staff identity and approval."""

import sys

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon import manage
from annotatiathon.models import (
    ApprovedStaffEmail, AuditEvent, Base, StaffAccount, StaffSession,
)
from annotatiathon.security import password_hash, password_matches


@pytest.fixture
def database(monkeypatch):
    engine = create_engine("sqlite+pysqlite://",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(manage, "session_factory", lambda: maker)
    monkeypatch.setattr(manage, "is_restricted_mode", lambda: True)
    yield maker
    engine.dispose()


def run_change(monkeypatch, source="romain.david@gmail.com",
               target="romain.david1@gmail.com"):
    monkeypatch.setattr(sys, "argv", [
        "nightjar-manage", "change-staff-email",
        "--from-email", source, "--to-email", target,
    ])
    manage.main()


def add_source(maker, *, target_approval=None, destination_account=False):
    with maker.begin() as db:
        old = ApprovedStaffEmail(email="romain.david@gmail.com",
                                 role="organiser", enabled=True)
        db.add(old)
        if target_approval is not None:
            db.add(ApprovedStaffEmail(email="romain.david1@gmail.com",
                                      role=target_approval, enabled=True))
        staff = StaffAccount(
            email="romain.david@gmail.com", name="Romain", roles=["organiser"],
            password_hash=password_hash("ExistingRomainPassword2026!"),
            active=True,
        )
        db.add(staff)
        db.flush()
        staff_id = staff.id
        db.add(StaffSession(id="session-digest", staff_id=staff_id,
                            csrf_token="csrf", expires_at=9999999999))
        if destination_account:
            db.add(StaffAccount(
                email="romain.david1@gmail.com", name="Other",
                roles=["organiser"], password_hash=password_hash("OtherPassword2026!"),
            ))
    return staff_id


def test_correct_account_and_approval_without_password_reset(database, monkeypatch):
    staff_id = add_source(database)
    run_change(monkeypatch)
    with database() as db:
        staff = db.get(StaffAccount, staff_id)
        assert staff.email == "romain.david1@gmail.com"
        assert staff.name == "Romain" and staff.roles == ["organiser"]
        assert password_matches(staff.password_hash, "ExistingRomainPassword2026!")
        assert db.get(ApprovedStaffEmail, "romain.david@gmail.com") is None
        corrected = db.get(ApprovedStaffEmail, "romain.david1@gmail.com")
        assert corrected.enabled and corrected.role == "organiser"
        assert db.scalars(select(StaffSession)).all() == []
        audit = db.scalar(select(AuditEvent).where(
            AuditEvent.action == "staff.email.corrected_cli"))
        assert audit.staff_id == staff_id
        assert audit.details["old_email"] == "romain.david@gmail.com"
        assert audit.details["new_email"] == "romain.david1@gmail.com"


def test_use_existing_matching_target_approval(database, monkeypatch):
    staff_id = add_source(database, target_approval="organiser")
    run_change(monkeypatch)
    with database() as db:
        assert db.get(StaffAccount, staff_id).email == "romain.david1@gmail.com"
        assert db.get(ApprovedStaffEmail, "romain.david@gmail.com") is None
        assert db.get(ApprovedStaffEmail, "romain.david1@gmail.com").enabled


@pytest.mark.parametrize("other_account,target_role", [
    (True, None), (False, "admin"),
])
def test_refuse_account_collision_or_conflicting_target_role(
    database, monkeypatch, other_account, target_role,
):
    staff_id = add_source(database, target_approval=target_role,
                          destination_account=other_account)
    with pytest.raises(SystemExit):
        run_change(monkeypatch)
    with database() as db:
        assert db.get(StaffAccount, staff_id).email == "romain.david@gmail.com"
        assert db.get(ApprovedStaffEmail, "romain.david@gmail.com").enabled
        assert db.get(StaffSession, "session-digest") is not None
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.action == "staff.email.corrected_cli")) is None
