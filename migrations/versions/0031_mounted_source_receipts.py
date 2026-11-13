"""Persistent source receipts for incremental mounted-folder synchronization."""
import sqlalchemy as sa
from alembic import op

revision = "0031_mounted_source_receipts"
down_revision = "0030_balanced_queue"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("mounted_source_files",
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("media_datasets.id"), primary_key=True),
        sa.Column("relative_path", sa.Text(), primary_key=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False))


def downgrade():
    op.drop_table("mounted_source_files")
