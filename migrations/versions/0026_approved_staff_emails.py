"""Create persistent approved Nightjar Admin and Organiser email list."""
import sqlalchemy as sa
from alembic import op

revision = "0026_approved_staff_emails"
down_revision = "0025_validation_queue_leases"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "approved_staff_emails",
        sa.Column("email", sa.String(length=320), primary_key=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'organiser')", name="ck_approved_staff_role"),
    )


def downgrade():
    op.drop_table("approved_staff_emails")
