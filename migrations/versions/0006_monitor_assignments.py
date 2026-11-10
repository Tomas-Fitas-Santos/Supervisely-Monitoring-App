"""Monitor assignments with balanced preview and audited decisions.

Revision ID: 0006_monitor_assignments
Revises: 0005_native
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_monitor_assignments"
down_revision = "0005_native"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitor_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("team_id", sa.String(36),
                  sa.ForeignKey("competition_teams.id"), nullable=False),
        sa.Column("monitor_person_id", sa.String(36),
                  sa.ForeignKey("people.id"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("assigned_by", sa.String(36),
                  sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("team_id", name="uq_monitor_assignment_team"),
    )
    op.create_index("ix_monitor_assignments_event_id", "monitor_assignments", ["event_id"])
    op.create_index("ix_monitor_assignments_team_id", "monitor_assignments", ["team_id"])
    op.create_index(
        "ix_monitor_assignments_monitor_person_id", "monitor_assignments",
        ["monitor_person_id"],
    )
    op.create_table(
        "monitor_assignment_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("staff_id", sa.String(36),
                  sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("roster_digest", sa.String(64), nullable=False),
        sa.Column("proposals", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("applied_at", sa.Integer()),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_monitor_assignment_plans_event_id", "monitor_assignment_plans",
        ["event_id"],
    )


def downgrade() -> None:
    raise RuntimeError("Monitor assignment history downgrade is intentionally blocked.")
