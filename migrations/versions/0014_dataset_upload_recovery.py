"""Recoverable local dataset uploads; mark historical inactive rows as stale.

Revision ID: 0014_dataset_upload_recovery
Revises: 0013_dataset_coverage
"""
import sqlalchemy as sa
from alembic import op

revision = "0014_dataset_upload_recovery"
down_revision = "0013_dataset_coverage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media_datasets",
        sa.Column("last_activity_at", sa.Integer(), nullable=True),
    )
    op.execute(
        "UPDATE media_datasets SET last_activity_at = COALESCE(created_at, 0)"
    )
    op.alter_column("media_datasets", "last_activity_at", nullable=False)


def downgrade() -> None:
    raise RuntimeError("Dataset recovery timestamps must be preserved.")
