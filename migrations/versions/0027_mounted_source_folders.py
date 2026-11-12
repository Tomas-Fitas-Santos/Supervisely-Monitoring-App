"""Persist the read-only host source folder for directly mirrored datasets."""
import sqlalchemy as sa
from alembic import op

revision = "0027_mounted_source_folders"
down_revision = "0026_approved_staff_emails"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media_datasets", sa.Column("source_folder", sa.String(length=200), nullable=True))
    op.create_index("ix_media_datasets_source_folder", "media_datasets", ["source_folder"])


def downgrade():
    op.drop_index("ix_media_datasets_source_folder", table_name="media_datasets")
    op.drop_column("media_datasets", "source_folder")
