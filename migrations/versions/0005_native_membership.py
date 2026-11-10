"""Read-only native membership reconciliation, verified connection metadata.

Revision ID: 0005_native
Revises: 0004_activities
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_native"
down_revision = "0004_activities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("people", sa.Column("supervisely_role", sa.String(40)))
    op.add_column("people", sa.Column("native_verified_at", sa.Integer()))
    op.add_column("supervisely_config", sa.Column("last_verified_at", sa.Integer()))
    op.add_column("supervisely_config", sa.Column("last_error_code", sa.String(40)))
    op.create_table(
        "native_sync_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("staff_id", sa.String(36), sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("roster_digest", sa.String(64), nullable=False),
        sa.Column("associations", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("applied_at", sa.Integer()),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_native_sync_plans_event_id", "native_sync_plans", ["event_id"])


def downgrade() -> None:
    raise RuntimeError(
        "Native identity verification history downgrade is intentionally blocked."
    )
