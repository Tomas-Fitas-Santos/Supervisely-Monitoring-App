"""Additive video/frame provenance and indexed event job reads.

Revision ID: 0017_frame_identity
Revises: 0016_preloaded_media_pool
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_frame_identity"
down_revision = "0016_preloaded_media_pool"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media_datasets", sa.Column("extraction_policy", sa.String(24), nullable=False,
                                               server_default="sample_1fps"))
    op.create_table("media_videos",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("media_datasets.id"), nullable=False),
        sa.Column("video_group", sa.String(500), nullable=False),
        sa.Column("source_relative_path", sa.Text()), sa.Column("source_sha256", sa.String(64)),
        sa.Column("extraction_policy", sa.String(24), nullable=False),
        sa.Column("density", sa.String(24), nullable=False),
        sa.Column("frame_count", sa.Integer(), nullable=False),
        sa.Column("source_frame_count", sa.Integer()),
        sa.Column("extracted_bytes", sa.BigInteger(), nullable=False),
        sa.Column("time_base", sa.String(64)), sa.Column("frame_rate", sa.String(64)),
        sa.Column("identity_status", sa.String(24), nullable=False),
        sa.Column("missing_frame_count", sa.Integer()), sa.Column("identity_notes", sa.JSON(), nullable=False),
        sa.UniqueConstraint("dataset_id", "video_group", name="uq_dataset_video_group"))
    op.create_index("ix_media_videos_event_id", "media_videos", ["event_id"])
    op.create_index("ix_media_videos_dataset_id", "media_videos", ["dataset_id"])
    op.add_column("media_assets", sa.Column("video_id", sa.String(36)))
    op.create_foreign_key("fk_media_assets_video_id", "media_assets", "media_videos", ["video_id"], ["id"])
    op.add_column("media_assets", sa.Column("presentation_timestamp", sa.BigInteger()))
    op.add_column("media_assets", sa.Column("frame_width", sa.Integer()))
    op.add_column("media_assets", sa.Column("frame_height", sa.Integer()))
    op.create_index("ix_media_assets_video_id", "media_assets", ["video_id"])
    op.create_unique_constraint("uq_video_frame_ordinal", "media_assets", ["video_id", "frame_index"])
    op.create_index("ix_jobs_event_order", "labeling_jobs", ["event_id", "team_id", "sequence", "id"])
    op.create_index("ix_jobs_media_membership", "labeling_jobs", [sa.text("(media_ids::jsonb)")], postgresql_using="gin")
    op.add_column("labeling_jobs", sa.Column("annotation_snapshot_round", sa.Integer()))
    op.add_column("labeling_jobs", sa.Column("annotation_snapshot_frames", sa.Integer()))
    op.add_column("labeling_jobs", sa.Column("annotation_snapshot_annotated_frames", sa.Integer()))


def downgrade():
    raise RuntimeError("Source frame provenance must be preserved.")
