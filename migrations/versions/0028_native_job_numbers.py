"""Persist participant-facing Supervisely job numbers."""
import sqlalchemy as sa
from alembic import op

revision = "0028_native_job_numbers"
down_revision = "0027_mounted_source_folders"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("last_native_job_number", sa.Integer(),
                                      nullable=False, server_default="0"))
    op.add_column("labeling_jobs", sa.Column("native_job_number", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_event_native_job_number", "labeling_jobs",
                                ["event_id", "native_job_number"])


def downgrade():
    op.drop_constraint("uq_event_native_job_number", "labeling_jobs", type_="unique")
    op.drop_column("labeling_jobs", "native_job_number")
    op.drop_column("events", "last_native_job_number")
