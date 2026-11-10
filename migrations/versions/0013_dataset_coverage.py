"""Ecomorphology folder curation and observed movement frame ranges.

Revision ID: 0013_dataset_coverage
Revises: 0012_registration_tokens
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_dataset_coverage"
down_revision = "0012_registration_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_folder_classifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("media_datasets.id"), nullable=False),
        sa.Column("source_folder", sa.String(200), nullable=False),
        sa.Column("ecomorphology", sa.String(200), nullable=False),
        sa.Column("species_name", sa.String(200), nullable=True),
        sa.Column("updated_by", sa.String(36), sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
        sa.UniqueConstraint("dataset_id", "source_folder",
                            name="uq_dataset_source_folder_classification"),
    )
    op.create_index("ix_dataset_folder_classifications_event_id",
                    "dataset_folder_classifications", ["event_id"])
    op.create_index("ix_dataset_folder_classifications_dataset_id",
                    "dataset_folder_classifications", ["dataset_id"])

    op.create_table(
        "dataset_movement_segments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("media_datasets.id"), nullable=False),
        sa.Column("video_group", sa.String(500), nullable=False),
        sa.Column("start_frame", sa.Integer, nullable=False),
        sa.Column("end_frame", sa.Integer, nullable=False),
        sa.Column("movement_type", sa.String(60), nullable=False),
        sa.Column("notes", sa.Text, nullable=False),
        sa.Column("labeled_by", sa.String(36), sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("labeled_at", sa.Integer, nullable=False),
        sa.CheckConstraint("start_frame >= 1 AND end_frame >= start_frame",
                           name="ck_movement_segment_range"),
    )
    op.create_index("ix_dataset_movement_segments_event_id",
                    "dataset_movement_segments", ["event_id"])
    op.create_index("ix_dataset_movement_segments_dataset_id",
                    "dataset_movement_segments", ["dataset_id"])
    op.create_index("ix_movement_dataset_video",
                    "dataset_movement_segments", ["dataset_id", "video_group"])


def downgrade() -> None:
    raise RuntimeError("Do not erase observed dataset movement annotations.")
