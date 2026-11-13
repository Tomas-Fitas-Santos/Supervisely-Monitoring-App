"""Record the durable competition intake cutoff."""

import sqlalchemy as sa
from alembic import op

revision = "0029_competition_closeout"
down_revision = "0028_native_job_numbers"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_event_status", "events", type_="check")
    op.create_check_constraint(
        "ck_event_status", "events", "status IN ('active','archived','finalizing','resetting')",
    )
    op.add_column("events", sa.Column("closing_at", sa.Integer(), nullable=True))
    op.add_column("events", sa.Column("closing_by", sa.String(36), nullable=True))
    op.create_foreign_key("fk_events_closing_by", "events", "staff_accounts", ["closing_by"], ["id"])


def downgrade():
    op.drop_constraint("ck_event_status", "events", type_="check")
    op.create_check_constraint("ck_event_status", "events", "status IN ('active','archived')")
    op.drop_constraint("fk_events_closing_by", "events", type_="foreignkey")
    op.drop_column("events", "closing_by")
    op.drop_column("events", "closing_at")
