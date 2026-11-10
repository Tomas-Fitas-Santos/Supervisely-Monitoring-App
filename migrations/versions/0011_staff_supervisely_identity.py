"""Verified personal Supervisely identity links for monitoring staff.

Revision ID: 0011_staff_supervisely_identity
Revises: 0010_pair_queue
"""
import sqlalchemy as sa
from alembic import op

revision = "0011_staff_supervisely_identity"
down_revision = "0010_pair_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("staff_accounts", sa.Column(
        "supervisely_user_id", sa.Integer(), nullable=True,
    ))
    op.add_column("staff_accounts", sa.Column(
        "supervisely_login", sa.String(320), nullable=True,
    ))
    op.add_column("staff_accounts", sa.Column(
        "supervisely_role", sa.String(40), nullable=True,
    ))
    op.add_column("staff_accounts", sa.Column(
        "supervisely_verified_team_id", sa.Integer(), nullable=True,
    ))
    op.add_column("staff_accounts", sa.Column(
        "supervisely_verified_at", sa.Integer(), nullable=True,
    ))


def downgrade() -> None:
    raise RuntimeError("Do not erase verified staff account history.")
