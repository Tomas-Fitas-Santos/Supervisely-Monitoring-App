"""Operator password recovery preserves the account and invalidates old sessions."""
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon import manage
from annotatiathon.models import AuditEvent, Base, StaffAccount, StaffSession
from annotatiathon.security import password_hash, password_matches


def test_reset_password_keeps_account_and_revokes_sessions(monkeypatch, capsys):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        staff = StaffAccount(
            email="organiser@example.org", name="Organiser", roles=["organiser"],
            password_hash=password_hash("OriginalPassword1234"),
            locked_until=9999999999, login_failures=4,
        )
        db.add(staff)
        db.flush()
        staff_id = staff.id
        db.add(StaffSession(
            id="session-digest", staff_id=staff_id,
            csrf_token="csrf", expires_at=9999999999,
        ))
    monkeypatch.setattr(manage, "session_factory", lambda: maker)
    monkeypatch.setattr(
        sys, "argv", ["nightjar-manage", "reset-password",
                      "--email", "organiser@example.org"],
    )
    answers = iter(["ReplacementPassword1234", "ReplacementPassword1234"])
    monkeypatch.setattr(manage.getpass, "getpass", lambda _prompt: next(answers))
    manage.main()
    assert "password updated" in capsys.readouterr().out.lower()
    with maker() as db:
        staff = db.get(StaffAccount, staff_id)
        assert staff.roles == ["organiser"]
        assert staff.login_failures == 0
        assert staff.locked_until is None
        assert password_matches(staff.password_hash, "ReplacementPassword1234")
        assert not password_matches(staff.password_hash, "OriginalPassword1234")
        assert db.scalar(select(StaffSession.id).where(
            StaffSession.staff_id == staff_id,
        )) is None
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.action == "staff.password.reset_cli",
        )) is not None
    engine.dispose()
