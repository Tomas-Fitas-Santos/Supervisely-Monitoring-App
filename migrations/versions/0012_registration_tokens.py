"""Email-verified monitor registration and encrypted organiser token.

Revision ID: 0012_registration_tokens
Revises: 0011_staff_supervisely_identity
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_registration_tokens"
down_revision = "0011_staff_supervisely_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("supervisely_config", sa.Column("token_ciphertext", sa.Text(), nullable=True))
    op.create_table(
        "registration_challenges",
        sa.Column("email", sa.String(320), primary_key=True),
        sa.Column("code_digest", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("Do not erase verified registrations or integration credentials.")
