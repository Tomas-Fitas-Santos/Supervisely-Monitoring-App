"""Preserve each complete source-frame review through incomplete-job retries."""
import sqlalchemy as sa
from alembic import op

revision = "0024_per_frame_acceptance"
down_revision = "0023_global_random_review_pool"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "review_samples",
        sa.Column("validated_complete", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
    )
    op.create_index(
        "ix_review_sample_validated_job_media",
        "review_samples", ["job_id", "media_id", "validated_complete"],
    )


def downgrade():
    op.drop_index("ix_review_sample_validated_job_media", table_name="review_samples")
    op.drop_column("review_samples", "validated_complete")
