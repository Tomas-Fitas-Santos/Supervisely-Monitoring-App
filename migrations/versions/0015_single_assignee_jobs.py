"""Single-assignee video jobs and manual dispatch within an active event.

Revision ID: 0015_single_assignee_jobs
Revises: 0014_dataset_upload_recovery
"""
import sqlalchemy as sa
from alembic import op

revision = "0015_single_assignee_jobs"
down_revision = "0014_dataset_upload_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("labeling_jobs", sa.Column("assignee_person_id", sa.String(length=36), nullable=True))
    op.add_column("labeling_jobs", sa.Column("release_mode", sa.String(length=24), nullable=False, server_default="automatic"))
    op.create_foreign_key(
        "fk_labeling_jobs_assignee_person_id", "labeling_jobs", "people",
        ["assignee_person_id"], ["id"],
    )
    op.create_index("ix_labeling_jobs_assignee_person_id", "labeling_jobs", ["assignee_person_id"])


def downgrade() -> None:
    raise RuntimeError("Individual job assignment history must be preserved.")
