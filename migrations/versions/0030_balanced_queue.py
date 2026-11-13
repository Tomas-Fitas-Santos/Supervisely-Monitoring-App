"""Revisioned append batches and additive morphology-balanced queues."""
from alembic import op
import sqlalchemy as sa

revision = "0030_balanced_queue"
down_revision = "0029_competition_closeout"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("inventory_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("configuration_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("mirrored_revision", sa.Integer(), nullable=False, server_default="0"),
    ):
        op.add_column("media_datasets", column)
    op.execute("""
        UPDATE media_datasets d SET mirrored_revision = 1
        WHERE d.status = 'ready' AND d.native_dataset_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM native_operations op
                      WHERE op.kind = 'mirror_dataset' AND op.target_id = d.id AND op.status = 'done')
          AND (SELECT count(*) FROM native_source_media n WHERE n.dataset_id = d.id)
              = (d.image_count + d.frame_count)
    """)
    op.add_column("labeling_plans", sa.Column("allocation_mode", sa.String(24),
                                             nullable=False, server_default="legacy"))
    op.add_column("labeling_plans", sa.Column("prepared_revision", sa.Integer(), nullable=True))
    op.create_table("dataset_append_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("media_datasets.id"), nullable=False),
        sa.Column("upload_kind", sa.String(12), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("source_bytes", sa.BigInteger(), nullable=False),
        sa.Column("revision_at_start", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("last_activity_at", sa.Integer(), nullable=False),
        sa.Column("committed_at", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(70), nullable=True))
    for col in ("event_id", "dataset_id"):
        op.create_index("ix_dataset_append_batches_" + col, "dataset_append_batches", [col])
    op.create_table("dataset_append_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("dataset_append_batches.id"), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("expected_bytes", sa.BigInteger(), nullable=False),
        sa.Column("received_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("batch_id", "relative_path", name="uq_append_batch_path"))
    op.create_index("ix_dataset_append_files_batch_id", "dataset_append_files", ["batch_id"])
    op.create_table("dataset_prepared_queues",
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("media_datasets.id"), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("inventory_revision", sa.Integer(), nullable=False),
        sa.Column("configuration_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("runs", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False))
    op.create_index("ix_dataset_prepared_queues_event_id", "dataset_prepared_queues", ["event_id"])
    op.create_table("balanced_video_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("media_datasets.id"), nullable=False),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("labeling_plans.id"), nullable=False),
        sa.Column("video_id", sa.String(36), sa.ForeignKey("media_videos.id"), nullable=False),
        sa.Column("video_group", sa.String(500), nullable=False),
        sa.Column("source_folder", sa.String(200), nullable=False),
        sa.Column("morphology_group", sa.String(200), nullable=False),
        sa.Column("first_ordinal", sa.Integer(), nullable=False),
        sa.Column("frame_count", sa.Integer(), nullable=False),
        sa.Column("next_offset", sa.Integer(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("selection_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("plan_id", "video_id", name="uq_balanced_plan_video"))
    for col in ("event_id", "dataset_id", "plan_id", "morphology_group"):
        op.create_index("ix_balanced_video_runs_" + col, "balanced_video_runs", [col])
    op.create_index("ix_balanced_run_open", "balanced_video_runs", ["plan_id", "next_offset", "selection_order"])


def downgrade():
    op.drop_index("ix_balanced_run_open", table_name="balanced_video_runs")
    for col in ("event_id", "dataset_id", "plan_id", "morphology_group"):
        op.drop_index("ix_balanced_video_runs_" + col, table_name="balanced_video_runs")
    op.drop_table("balanced_video_runs")
    op.drop_index("ix_dataset_prepared_queues_event_id", table_name="dataset_prepared_queues")
    op.drop_table("dataset_prepared_queues")
    op.drop_index("ix_dataset_append_files_batch_id", table_name="dataset_append_files")
    op.drop_table("dataset_append_files")
    for col in ("event_id", "dataset_id"):
        op.drop_index("ix_dataset_append_batches_" + col, table_name="dataset_append_batches")
    op.drop_table("dataset_append_batches")
    op.drop_column("labeling_plans", "prepared_revision")
    op.drop_column("labeling_plans", "allocation_mode")
    for col in ("mirrored_revision", "configuration_revision", "inventory_revision"):
        op.drop_column("media_datasets", col)
