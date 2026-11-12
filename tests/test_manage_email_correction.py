"""Account-email corrections preserve an existing staff identity and approval."""

import sys

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon import manage
from annotatiathon.models import (
    ApprovedStaffEmail,
    AuditEvent,
    Base,
    StaffAccount,
    StaffSession,
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


def run_change(monkeypatch, source="staff.previous@example.org",
               target="staff.corrected@example.org"):
    monkeypatch.setattr(sys, "argv", [
        "nightjar-manage", "change-staff-email",
        "--from-email", source, "--to-email", target,
    ])
    manage.main()


def add_source(maker, *, target_approval=None, destination_account=False):
    with maker.begin() as db:
        old = ApprovedStaffEmail(email="staff.previous@example.org",
                                 role="organiser", enabled=True)
        db.add(old)
        if target_approval is not None:
            db.add(ApprovedStaffEmail(email="staff.corrected@example.org",
                                      role=target_approval, enabled=True))
        staff = StaffAccount(
            email="staff.previous@example.org", name="StaffMember", roles=["organiser"],
            password_hash=password_hash("ExistingStaffMemberPassword2026!"),
            active=True,
        )
        db.add(staff)
        db.flush()
        staff_id = staff.id
        db.add(StaffSession(id="session-digest", staff_id=staff_id,
                            csrf_token="csrf", expires_at=9999999999))
        if destination_account:
            db.add(StaffAccount(
                email="staff.corrected@example.org", name="Other",
                roles=["organiser"], password_hash=password_hash("OtherPassword2026!"),
            ))
    return staff_id


def test_correct_account_and_approval_without_password_reset(database, monkeypatch):
    staff_id = add_source(database)
    run_change(monkeypatch)
    with database() as db:
        staff = db.get(StaffAccount, staff_id)
        assert staff.email == "staff.corrected@example.org"
        assert staff.name == "StaffMember" and staff.roles == ["organiser"]
        assert password_matches(staff.password_hash, "ExistingStaffMemberPassword2026!")
        assert db.get(ApprovedStaffEmail, "staff.previous@example.org") is None
        corrected = db.get(ApprovedStaffEmail, "staff.corrected@example.org")
        assert corrected.enabled and corrected.role == "organiser"
        assert db.scalars(select(StaffSession)).all() == []
        audit = db.scalar(select(AuditEvent).where(
            AuditEvent.action == "staff.email.corrected_cli"))
        assert audit.staff_id == staff_id
        assert audit.details["old_email"] == "staff.previous@example.org"
        assert audit.details["new_email"] == "staff.corrected@example.org"


def test_use_existing_matching_target_approval(database, monkeypatch):
    staff_id = add_source(database, target_approval="organiser")
    run_change(monkeypatch)
    with database() as db:
        assert db.get(StaffAccount, staff_id).email == "staff.corrected@example.org"
        assert db.get(ApprovedStaffEmail, "staff.previous@example.org") is None
        assert db.get(ApprovedStaffEmail, "staff.corrected@example.org").enabled


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
        assert db.get(StaffAccount, staff_id).email == "staff.previous@example.org"
        assert db.get(ApprovedStaffEmail, "staff.previous@example.org").enabled
        assert db.get(StaffSession, "session-digest") is not None
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.action == "staff.email.corrected_cli")) is None
