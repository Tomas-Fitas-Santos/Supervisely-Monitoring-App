"""Index independent frame review claims without dropping historical decisions."""

import sqlalchemy as sa
from alembic import op

revision = "0022_full_frame_review"
down_revision = "0021_event_schedule"
branch_labels = None
depends_on = None


def upgrade():
    # Existing sampled rows remain untouched and keep their review decisions.
    # Allow additional submitted source frames from the same native job.
    op.drop_constraint(
        "review_samples_job_id_correction_round_key",
        "review_samples", type_="unique",
    )
    op.create_unique_constraint(
        "uq_review_sample_job_round_media", "review_samples",
        ["job_id", "correction_round", "media_id"],
    )
    op.create_index(
        "ix_review_samples_job_round", "review_samples",
        ["job_id", "correction_round"],
    )
    op.create_index(
        "ix_review_samples_fairness", "review_samples",
        ["event_id", "team_id", "completed_at", "expires_at"],
    )


def downgrade():
    # Downgrading after full-frame ingestion would discard the distinction
    # between different submitted frames. Refuse rather than destroy reviews.
    connection = op.get_bind()
    count = connection.execute(sa.text(
        "SELECT count(*) FROM (SELECT job_id, correction_round "
        "FROM review_samples GROUP BY job_id, correction_round "
        "HAVING count(*) > 1) AS duplicates"
    )).scalar_one()
    if count:
        raise RuntimeError("Full-frame reviews exist; downgrade would lose data.")
    op.drop_index("ix_review_samples_fairness", table_name="review_samples")
    op.drop_index("ix_review_samples_job_round", table_name="review_samples")
    op.drop_constraint(
        "uq_review_sample_job_round_media", "review_samples", type_="unique",
    )
    op.create_unique_constraint(
        "review_samples_job_id_correction_round_key", "review_samples",
        ["job_id", "correction_round"],
    )
