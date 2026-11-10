"""Local dataset assets, annotation job plans, review and final exports.

Revision ID: 0008_workflows
Revises: 0007_tables
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_workflows"
down_revision = "0007_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_datasets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("upload_kind", sa.String(12), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column("source_bytes", sa.BigInteger(), nullable=False),
        sa.Column("image_count", sa.Integer(), nullable=False),
        sa.Column("frame_count", sa.Integer(), nullable=False),
        sa.Column("video_count", sa.Integer(), nullable=False),
        sa.Column("native_project_id", sa.Integer()),
        sa.Column("native_dataset_id", sa.Integer()),
        sa.Column("created_by", sa.String(36),
                  sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.Integer()),
        sa.Column("error_code", sa.String(50)),
    )
    op.create_index("ix_media_datasets_event_id", "media_datasets", ["event_id"])
    op.create_table(
        "dataset_staged_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_id", sa.String(36),
                  sa.ForeignKey("media_datasets.id"), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("expected_bytes", sa.BigInteger(), nullable=False),
        sa.Column("received_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("dataset_id", "relative_path", name="uq_stage_path"),
    )
    op.create_index("ix_dataset_staged_files_dataset_id", "dataset_staged_files", ["dataset_id"])
    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("dataset_id", sa.String(36),
                  sa.ForeignKey("media_datasets.id"), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("morphology", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("video_group", sa.String(500)),
        sa.Column("frame_index", sa.Integer()),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("native_image_id", sa.Integer()),
        sa.Column("native_dataset_id", sa.Integer()),
        sa.UniqueConstraint("dataset_id", "relative_path", name="uq_dataset_asset_path"),
    )
    for key in ("event_id", "dataset_id", "morphology", "kind", "native_image_id"):
        op.create_index("ix_media_assets_" + key, "media_assets", [key])
    op.create_table(
        "labeling_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("media_datasets.id"), nullable=False),
        sa.Column("jobs_per_team", sa.Integer(), nullable=False),
        sa.Column("redundancy", sa.Integer(), nullable=False),
        sa.Column("video_limit", sa.Integer()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_by", sa.String(36),
                  sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("approved_at", sa.Integer()),
    )
    op.create_index("ix_labeling_plans_event_id", "labeling_plans", ["event_id"])
    op.create_index("ix_labeling_plans_dataset_id", "labeling_plans", ["dataset_id"])
    op.create_table(
        "labeling_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("media_datasets.id"), nullable=False),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("labeling_plans.id"), nullable=False),
        sa.Column("team_id", sa.String(36), sa.ForeignKey("competition_teams.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("media_ids", sa.JSON(), nullable=False),
        sa.Column("frame_count", sa.Integer(), nullable=False),
        sa.Column("video_count", sa.Integer(), nullable=False),
        sa.Column("morphology_counts", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("review_status", sa.String(24), nullable=False),
        sa.Column("assigned_monitor_id", sa.String(36), sa.ForeignKey("people.id")),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.Integer()),
        sa.Column("submitted_at", sa.Integer()),
        sa.Column("completed_at", sa.Integer()),
        sa.UniqueConstraint("plan_id", "team_id", "sequence", name="uq_job_plan_team_seq"),
    )
    for key in ("event_id", "plan_id", "team_id"):
        op.create_index("ix_labeling_jobs_" + key, "labeling_jobs", [key])
    op.create_table(
        "native_job_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("labeling_jobs.id"), nullable=False),
        sa.Column("person_id", sa.String(36), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("native_job_id", sa.Integer(), nullable=False),
        sa.Column("native_status", sa.String(30), nullable=False),
        sa.Column("last_synced_at", sa.Integer()),
        sa.UniqueConstraint("job_id", "person_id", name="uq_native_binding_person_job"),
        sa.UniqueConstraint("native_job_id", name="uq_native_job_id"),
    )
    for key in ("event_id", "job_id", "person_id"):
        op.create_index("ix_native_job_bindings_" + key, "native_job_bindings", [key])
    op.create_table(
        "job_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("labeling_jobs.id"), nullable=False),
        sa.Column("media_id", sa.String(36), sa.ForeignKey("media_assets.id"), nullable=False),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("correct_keypoints", sa.Integer(), nullable=False),
        sa.Column("missing_keypoints", sa.Integer(), nullable=False),
        sa.Column("incorrect_keypoints", sa.Integer(), nullable=False),
        sa.Column("visible_keypoints", sa.Integer(), nullable=False),
        sa.Column("occluded_keypoints", sa.Integer(), nullable=False),
        sa.Column("temporal_consistency", sa.Boolean()),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("reviewer_id", sa.String(36),
                  sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("reviewed_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("job_id", "media_id", name="uq_job_media_review"),
    )
    for key in ("event_id", "job_id"):
        op.create_index("ix_job_reviews_" + key, "job_reviews", [key])
    op.create_table(
        "event_closeouts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False,
                  unique=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("closed_by", sa.String(36),
                  sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("closed_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_event_closeouts_event_id", "event_closeouts", ["event_id"], unique=True)


def downgrade() -> None:
    raise RuntimeError("Media and labeling results cannot be discarded by migration downgrade.")
