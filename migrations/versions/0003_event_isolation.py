"""Isolate event data and retain existing records in the initial event.

Revision ID: 0003_events
Revises: 0002_tickets
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_events"
down_revision = "0002_tickets"
branch_labels = None
depends_on = None

INITIAL_ID = "00000000-0000-4000-8000-000000000010"


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("archived_at", sa.Integer()),
        sa.Column("reset_from_id", sa.String(36), sa.ForeignKey("events.id")),
        sa.CheckConstraint("status IN ('active','archived')",
                           name="ck_event_status"),
    )
    op.execute(sa.text("""
        INSERT INTO events (id, name, status, created_at)
        VALUES (:id, 'Initial Event', 'active', EXTRACT(EPOCH FROM NOW())::integer)
    """).bindparams(id=INITIAL_ID))
    op.add_column("staff_sessions", sa.Column("event_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_session_event", "staff_sessions", "events", ["event_id"], ["id"]
    )
    op.execute(sa.text("UPDATE staff_sessions SET event_id = :id").bindparams(id=INITIAL_ID))
    for table in (
        "competition_teams", "people", "roster_imports", "ticket_categories",
        "ticket_distributions", "ticket_ledger",
    ):
        op.add_column(table, sa.Column("event_id", sa.String(36), nullable=True))
        op.execute(sa.text(f"UPDATE {table} SET event_id = :id").bindparams(id=INITIAL_ID))
        op.alter_column(table, "event_id", nullable=False)
        op.create_foreign_key(f"fk_{table}_event", table, "events", ["event_id"], ["id"])
        op.create_index(f"ix_{table}_event_id", table, ["event_id"])
    op.add_column("audit_events", sa.Column("event_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_audit_events_event", "audit_events", "events", ["event_id"], ["id"]
    )
    op.create_index("ix_audit_events_event_id", "audit_events", ["event_id"])
    op.execute(sa.text("""
        UPDATE audit_events SET event_id = :id
        WHERE action NOT LIKE 'staff.%' AND action <> 'settings.supervisely.update'
    """).bindparams(id=INITIAL_ID))
    op.add_column(
        "competition_teams", sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.true()
        )
    )
    # The earlier migrations used unique indexes, not unique table constraints.
    op.drop_index("ix_competition_teams_name", table_name="competition_teams")
    op.drop_index("ix_people_email", table_name="people")
    op.drop_index("ix_ticket_categories_slug", table_name="ticket_categories")
    op.create_index("ix_competition_teams_name", "competition_teams", ["name"])
    op.create_index("ix_people_email", "people", ["email"])
    op.create_index("ix_ticket_categories_slug", "ticket_categories", ["slug"])
    op.create_unique_constraint(
        "uq_competition_event_name", "competition_teams", ["event_id", "name"]
    )
    op.create_unique_constraint(
        "uq_person_event_email", "people", ["event_id", "email"]
    )
    op.create_unique_constraint(
        "uq_ticket_event_slug", "ticket_categories", ["event_id", "slug"]
    )
    op.create_table(
        "event_reset_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("staff_id", sa.String(36),
                  sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("areas", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("executed_at", sa.Integer()),
    )
    op.create_index("ix_event_reset_plans_event_id", "event_reset_plans", ["event_id"])
    op.create_table(
        "event_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("reset_plan_id", sa.String(36),
                  sa.ForeignKey("event_reset_plans.id"), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_event_snapshots_event_id", "event_snapshots", ["event_id"])


def downgrade() -> None:
    raise RuntimeError(
        "Event-scoped schema downgrade is intentionally blocked: it could merge "
        "person/team identity namespaces and discard archival metadata."
    )
