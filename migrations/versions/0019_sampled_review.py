"""Persist immutable sampled annotations, leases, decisions and rubric versions."""

import sqlalchemy as sa
from alembic import op

revision = "0019_sampled_review"
down_revision = "0018_shared_work_queue"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_rubrics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.UniqueConstraint("event_id", "version"),
    )
    op.create_index("ix_review_rubrics_event_id", "review_rubrics", ["event_id"])
    op.create_table(
        "review_samples",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("labeling_jobs.id"), nullable=False),
        sa.Column("team_id", sa.String(36), sa.ForeignKey("competition_teams.id"), nullable=False),
        sa.Column("media_id", sa.String(36), sa.ForeignKey("media_assets.id"), nullable=False),
        sa.Column("correction_round", sa.Integer(), nullable=False),
        sa.Column("random_key", sa.String(36), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("reviewer_id", sa.String(36), sa.ForeignKey("staff_accounts.id")),
        sa.Column("claim_token", sa.String(36)),
        sa.Column("expires_at", sa.Integer()),
        sa.Column("completed_at", sa.Integer()),
        sa.Column("rubric_id", sa.String(36), sa.ForeignKey("review_rubrics.id")),
        sa.Column("decision", sa.JSON()),
        sa.UniqueConstraint("job_id", "correction_round"),
    )
    op.create_index("ix_review_samples_event_id", "review_samples", ["event_id"])
    op.create_index("ix_review_sample_claim", "review_samples", ["event_id", "team_id", "random_key"])


def downgrade():
    op.drop_table("review_samples")
    op.drop_table("review_rubrics")
