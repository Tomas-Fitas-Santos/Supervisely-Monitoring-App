"""Durable native job queue and independent annotation-media provenance.

Revision ID: 0009_native_work
Revises: 0008_workflows
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_native_work"
down_revision = "0008_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("labeling_jobs", sa.Column("native_dataset_id", sa.Integer()))
    op.create_table(
        "native_media_instances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("labeling_jobs.id"), nullable=False),
        sa.Column("media_id", sa.String(36), sa.ForeignKey("media_assets.id"), nullable=False),
        sa.Column("native_dataset_id", sa.Integer(), nullable=False),
        sa.Column("native_image_id", sa.Integer(), nullable=False),
        sa.UniqueConstraint("job_id", "media_id", name="uq_native_job_media"),
        sa.UniqueConstraint("native_image_id", name="uq_native_media_image_id"),
    )
    for key in ("event_id", "job_id", "media_id"):
        op.create_index("ix_native_media_instances_" + key, "native_media_instances", [key])
    op.create_table(
        "native_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(70)),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.Integer()),
        sa.Column("completed_at", sa.Integer()),
        sa.UniqueConstraint("kind", "target_id", name="uq_native_task_target"),
    )
    op.create_index("ix_native_operations_event_id", "native_operations", ["event_id"])


def downgrade() -> None:
    raise RuntimeError("Live Supervisely job provenance must not be erased by migration.")
