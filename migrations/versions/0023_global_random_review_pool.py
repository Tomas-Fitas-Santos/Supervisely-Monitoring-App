"""Index the event-wide randomized pending-frame review pool.

Existing review decisions, source annotations, claims, and scores remain intact.
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_global_random_review_pool"
down_revision = "0022_full_frame_review"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_review_sample_event_random_pending",
        "review_samples",
        ["event_id", "random_key"],
        postgresql_where=sa.text("completed_at IS NULL"),
        sqlite_where=sa.text("completed_at IS NULL"),
    )


def downgrade():
    op.drop_index(
        "ix_review_sample_event_random_pending",
        table_name="review_samples",
    )
