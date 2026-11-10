"""Event activity planning, results, prize rules and auditable awards.

Revision ID: 0004_activities
Revises: 0003_events
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_activities"
down_revision = "0003_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "activities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36),
                  sa.ForeignKey("events.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location", sa.String(200), nullable=False),
        sa.Column("start_at", sa.Integer()),
        sa.Column("end_at", sa.Integer()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("awards_finalised_at", sa.Integer()),
        sa.Column("created_by", sa.String(36),
                  sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "end_at IS NULL OR start_at IS NULL OR end_at > start_at",
            name="ck_activity_time_order",
        ),
        sa.CheckConstraint(
            "status IN ('scheduled','live','completed','cancelled')",
            name="ck_activity_status",
        ),
    )
    op.create_index("ix_activities_event_id", "activities", ["event_id"])
    op.create_table(
        "activity_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36),
                  sa.ForeignKey("events.id"), nullable=False),
        sa.Column("activity_id", sa.String(36),
                  sa.ForeignKey("activities.id"), nullable=False),
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.String(36), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score_points", sa.Integer()),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("recorded_by", sa.String(36),
                  sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "activity_id", "subject_type", "subject_id",
            name="uq_activity_subject_result",
        ),
        sa.CheckConstraint("rank >= 1", name="ck_activity_rank_positive"),
        sa.CheckConstraint(
            "subject_type IN ('person','team')", name="ck_activity_subject_type"
        ),
    )
    op.create_index("ix_activity_results_event_id", "activity_results", ["event_id"])
    op.create_index("ix_activity_results_activity_id", "activity_results", ["activity_id"])
    op.create_table(
        "activity_prizes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36),
                  sa.ForeignKey("events.id"), nullable=False),
        sa.Column("activity_id", sa.String(36),
                  sa.ForeignKey("activities.id"), nullable=False),
        sa.Column("category_id", sa.String(36),
                  sa.ForeignKey("ticket_categories.id"), nullable=False),
        sa.Column("min_rank", sa.Integer(), nullable=False),
        sa.Column("max_rank", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(36),
                  sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "min_rank >= 1 AND max_rank >= min_rank", name="ck_activity_prize_rank_range"
        ),
        sa.CheckConstraint("quantity >= 1", name="ck_activity_prize_quantity"),
    )
    op.create_index("ix_activity_prizes_event_id", "activity_prizes", ["event_id"])
    op.create_index("ix_activity_prizes_activity_id", "activity_prizes", ["activity_id"])
    op.create_table(
        "activity_awards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36),
                  sa.ForeignKey("events.id"), nullable=False),
        sa.Column("activity_id", sa.String(36),
                  sa.ForeignKey("activities.id"), nullable=False),
        sa.Column("prize_id", sa.String(36),
                  sa.ForeignKey("activity_prizes.id"), nullable=False),
        sa.Column("person_id", sa.String(36),
                  sa.ForeignKey("people.id"), nullable=False),
        sa.Column("distribution_id", sa.String(36),
                  sa.ForeignKey("ticket_distributions.id"), nullable=False),
        sa.Column("awarded_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "activity_id", "prize_id", "person_id",
            name="uq_activity_prize_recipient",
        ),
    )
    op.create_index("ix_activity_awards_event_id", "activity_awards", ["event_id"])
    op.create_index("ix_activity_awards_activity_id", "activity_awards", ["activity_id"])


def downgrade() -> None:
    raise RuntimeError(
        "Activity and prize history downgrade is intentionally blocked to "
        "prevent destructive loss of awards and event evidence."
    )
