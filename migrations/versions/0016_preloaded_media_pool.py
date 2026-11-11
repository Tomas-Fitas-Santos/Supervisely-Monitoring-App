"""Pre-event shared Supervisely media pool, manual duplicate pool and worker priority.

Revision ID: 0016_preloaded_media_pool
Revises: 0015_single_assignee_jobs
"""
import sqlalchemy as sa
from alembic import op

revision = "0016_preloaded_media_pool"
down_revision = "0015_single_assignee_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_datasets", sa.Column(
        "native_manual_dataset_id", sa.Integer(), nullable=True,
    ))
    op.add_column("native_operations", sa.Column(
        "priority", sa.Integer(), nullable=False, server_default="0",
    ))
    op.add_column("native_operations", sa.Column(
        "payload", sa.JSON(), nullable=True,
    ))
    op.create_table(
        "native_source_media",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("media_datasets.id"), nullable=False),
        sa.Column("media_id", sa.String(36), sa.ForeignKey("media_assets.id"), nullable=False),
        sa.Column("native_dataset_id", sa.Integer(), nullable=False),
        sa.Column("native_image_id", sa.Integer(), nullable=False),
        sa.UniqueConstraint("media_id", name="uq_native_source_media"),
        sa.UniqueConstraint("native_image_id", name="uq_native_source_image"),
    )
    op.create_index("ix_native_source_media_event_id", "native_source_media", ["event_id"])
    op.create_index("ix_native_source_media_dataset_id", "native_source_media", ["dataset_id"])
    op.create_index("ix_native_source_media_media_id", "native_source_media", ["media_id"])


def downgrade() -> None:
    raise RuntimeError("Preloaded media and labeling-job identity history must be preserved.")
