"""Ticket distribution ledger and configurable categories.

Revision ID: 0002_tickets
Revises: 0001_initial
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_tickets"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_categories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("warning_enabled", sa.Boolean(), nullable=False),
        sa.Column("warning_threshold", sa.Integer()),
        sa.Column("warning_window_minutes", sa.Integer(), nullable=False),
        sa.CheckConstraint("warning_threshold IS NULL OR warning_threshold >= 1",
                           name="ck_ticket_warning_threshold_positive"),
        sa.CheckConstraint("warning_window_minutes >= 1",
                           name="ck_ticket_window_positive"),
    )
    op.create_index("ix_ticket_categories_slug", "ticket_categories", ["slug"], unique=True)
    op.create_table(
        "ticket_distributions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("category_id", sa.String(36),
                  sa.ForeignKey("ticket_categories.id"), nullable=False),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("recipient_ids", sa.JSON(), nullable=False),
        sa.Column("scope", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("requested_by", sa.String(36),
                  sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("confirmed_by", sa.String(36),
                  sa.ForeignKey("staff_accounts.id")),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.Integer()),
        sa.Column("reversed_at", sa.Integer()),
        sa.Column("reversal_staff_id", sa.String(36),
                  sa.ForeignKey("staff_accounts.id")),
        sa.CheckConstraint("quantity >= 1", name="ck_ticket_distribution_quantity"),
    )
    op.create_table(
        "ticket_ledger",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("category_id", sa.String(36),
                  sa.ForeignKey("ticket_categories.id"), nullable=False),
        sa.Column("person_id", sa.String(36),
                  sa.ForeignKey("people.id"), nullable=False),
        sa.Column("distribution_id", sa.String(36),
                  sa.ForeignKey("ticket_distributions.id"), nullable=False),
        sa.Column("kind", sa.String(15), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("staff_id", sa.String(36),
                  sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("reversal_of", sa.String(36),
                  sa.ForeignKey("ticket_ledger.id"), unique=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity != 0", name="ck_ticket_ledger_nonzero"),
    )
    for col in ("category_id", "person_id", "distribution_id", "kind", "created_at"):
        op.create_index(f"ix_ticket_ledger_{col}", "ticket_ledger", [col])
    categories = sa.table(
        "ticket_categories",
        sa.column("id", sa.String),
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
        sa.column("active", sa.Boolean),
        sa.column("warning_enabled", sa.Boolean),
        sa.column("warning_threshold", sa.Integer),
        sa.column("warning_window_minutes", sa.Integer),
    )
    op.bulk_insert(categories, [
        {"id": "00000000-0000-4000-8000-000000000001", "slug": "pizza", "name": "Pizza",
         "active": True, "warning_enabled": True, "warning_threshold": 2,
         "warning_window_minutes": 60},
        {"id": "00000000-0000-4000-8000-000000000002", "slug": "drinks", "name": "Drinks",
         "active": True, "warning_enabled": True, "warning_threshold": 3,
         "warning_window_minutes": 60},
        {"id": "00000000-0000-4000-8000-000000000003", "slug": "massage", "name": "Massage",
         "active": True, "warning_enabled": False, "warning_threshold": None,
         "warning_window_minutes": 60},
    ])


def downgrade() -> None:
    # Explicit downgrade is for controlled migration development only; it destroys ledger data.
    op.drop_table("ticket_ledger")
    op.drop_table("ticket_distributions")
    op.drop_table("ticket_categories")
