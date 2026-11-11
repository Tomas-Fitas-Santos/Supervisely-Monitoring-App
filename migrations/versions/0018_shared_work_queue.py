"""Persist primary work blocks and guard open claims."""

import sqlalchemy as sa
from alembic import op

revision = "0018_shared_work_queue"
down_revision = "0017_frame_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("labeling_plans", sa.Column("queue_seed", sa.String(64)))
    op.create_table(
        "work_blocks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("labeling_plans.id"), nullable=False),
        sa.Column("video_id", sa.String(36), sa.ForeignKey("media_videos.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("frame_count", sa.Integer(), nullable=False),
        sa.Column("media_ids", sa.JSON(), nullable=False),
        sa.Column("repeat_media_ids", sa.JSON(), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("labeling_jobs.id"), unique=True),
        sa.UniqueConstraint("plan_id", "video_id", "ordinal", name="uq_work_block_ordinal"),
        sa.CheckConstraint("frame_count BETWEEN 1 AND 30", name="ck_work_block_size"),
    )
    op.create_index("ix_work_blocks_event_id", "work_blocks", ["event_id"])
    op.create_index("ix_work_block_available", "work_blocks", ["plan_id", "video_id", "ordinal"],
                    postgresql_where=sa.text("job_id IS NULL"))
    op.create_table(
        "work_packets",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("labeling_jobs.id"), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("person_id", sa.String(36), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("submitted_at", sa.Integer()),
    )
    op.create_index("ix_work_packets_event_id", "work_packets", ["event_id"])
    op.create_index("uq_open_work_packet", "work_packets", ["event_id", "person_id"],
                    unique=True, postgresql_where=sa.text("submitted_at IS NULL"))


def downgrade() -> None:
    raise RuntimeError("Shared queue claim history must be preserved.")
