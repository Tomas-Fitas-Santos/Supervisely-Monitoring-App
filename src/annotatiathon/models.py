"""Persistent event records. All account and attendee identifiers are application-owned."""
import time
import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def epoch() -> int:
    return int(time.time())


def identifier() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


DEFAULT_EVENT_ID = "00000000-0000-4000-8000-000000000010"


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)
    archived_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reset_from_id: Mapped[str | None] = mapped_column(
        ForeignKey("events.id"), nullable=True
    )


class ResetPlan(Base):
    __tablename__ = "event_reset_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    mode: Mapped[str] = mapped_column(String(20))
    areas: Mapped[list[str]] = mapped_column(JSON, default=list)
    digest: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[int] = mapped_column(Integer)
    executed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)


class EventSnapshot(Base):
    __tablename__ = "event_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    reset_plan_id: Mapped[str] = mapped_column(ForeignKey("event_reset_plans.id"))
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class StaffAccount(Base):
    __tablename__ = "staff_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(Text)
    roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    login_failures: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class StaffSession(Base):
    __tablename__ = "staff_sessions"
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class CompetitionTeam(Base):
    __tablename__ = "competition_teams"
    __table_args__ = (UniqueConstraint("event_id", "name", name="uq_competition_event_name"),)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    name: Mapped[str] = mapped_column(String(200), index=True)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class Person(Base):
    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("event_id", "email", name="uq_person_event_email"),)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    name: Mapped[str] = mapped_column(String(200), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    team_id: Mapped[str | None] = mapped_column(
        ForeignKey("competition_teams.id"), index=True, nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # This value is never set to "joined" by a roster import or a browser claim.
    membership_status: Mapped[str] = mapped_column(String(30), default="not_invited")
    supervisely_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supervisely_login: Mapped[str | None] = mapped_column(String(320), nullable=True)
    check_in_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)
    updated_at: Mapped[int] = mapped_column(Integer, default=epoch, onupdate=epoch)


class SuperviselyConfig(Base):
    __tablename__ = "supervisely_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    server_url: Mapped[str] = mapped_column(
        String(512), default="https://app.supervisely.com"
    )
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    workspace_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[int] = mapped_column(Integer, default=epoch, onupdate=epoch)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"), index=True, nullable=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    staff_id: Mapped[str | None] = mapped_column(
        ForeignKey("staff_accounts.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch, index=True)


class ImportBatch(Base):
    __tablename__ = "roster_imports"
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    row_count: Mapped[int] = mapped_column(Integer)
    created_count: Mapped[int] = mapped_column(Integer)
    changed_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class TicketCategory(Base):
    __tablename__ = "ticket_categories"
    __table_args__ = (UniqueConstraint("event_id", "slug", name="uq_ticket_event_slug"),)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    slug: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    warning_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    warning_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warning_window_minutes: Mapped[int] = mapped_column(Integer, default=60)


class TicketDistribution(Base):
    """Snapshot of recipients before an organiser/distributor confirms a batch."""

    __tablename__ = "ticket_distributions"
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    category_id: Mapped[str] = mapped_column(ForeignKey("ticket_categories.id"))
    operation: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(Integer)
    recipient_ids: Mapped[list[str]] = mapped_column(JSON)
    scope: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="preview")
    requested_by: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("staff_accounts.id"), nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)
    confirmed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reversed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reversal_staff_id: Mapped[str | None] = mapped_column(
        ForeignKey("staff_accounts.id"), nullable=True
    )


class TicketLedgerEntry(Base):
    """Signed append-only ledger. One row per balance change, including reversals."""

    __tablename__ = "ticket_ledger"
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    category_id: Mapped[str] = mapped_column(ForeignKey("ticket_categories.id"), index=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), index=True)
    distribution_id: Mapped[str] = mapped_column(
        ForeignKey("ticket_distributions.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(15), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    reversal_of: Mapped[str | None] = mapped_column(
        ForeignKey("ticket_ledger.id"), nullable=True, unique=True
    )
    created_at: Mapped[int] = mapped_column(Integer, default=epoch, index=True)
