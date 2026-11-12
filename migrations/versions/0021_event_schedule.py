"""Add event-wide UTC scheduling bounds (non-destructive, backwards compatible)."""

import sqlalchemy as sa
from alembic import op

revision = "0021_event_schedule"
down_revision = "0020_repeat_dispatch"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("start_at", sa.Integer(), nullable=True))
    op.add_column("events", sa.Column("end_at", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_event_schedule_valid", "events",
        "(start_at IS NULL AND end_at IS NULL) OR "
        "(start_at IS NOT NULL AND end_at IS NOT NULL AND end_at > start_at)",
    )


def downgrade():
    op.drop_constraint("ck_event_schedule_valid", "events", type_="check")
    op.drop_column("events", "end_at")
    op.drop_column("events", "start_at")
