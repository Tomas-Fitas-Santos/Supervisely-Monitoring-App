"""Durable read-only validation task leases and retry scheduling.

Remote publishing remains manually reconciled after uncertain outcomes.
"""
import sqlalchemy as sa
from alembic import op

revision = "0025_validation_queue_leases"
down_revision = "0024_per_frame_acceptance"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("native_operations", sa.Column("lease_owner", sa.String(36), nullable=True))
    op.add_column("native_operations", sa.Column("lease_until", sa.Integer(), nullable=True))
    op.add_column("native_operations", sa.Column("next_attempt_at", sa.Integer(), nullable=True))
    op.create_index(
        "ix_native_validation_pending",
        "native_operations", ["kind", "status", "next_attempt_at", "priority"],
    )


def downgrade():
    op.drop_index("ix_native_validation_pending", table_name="native_operations")
    op.drop_column("native_operations", "next_attempt_at")
    op.drop_column("native_operations", "lease_until")
    op.drop_column("native_operations", "lease_owner")
