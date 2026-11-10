"""Reception table assignments anchored to teams.

Revision ID: 0007_tables
Revises: 0006_monitor_assignments
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_tables"
down_revision = "0006_monitor_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("competition_teams", sa.Column("table_number", sa.Integer()))
    op.add_column("people", sa.Column("table_number", sa.Integer()))
    op.create_check_constraint(
        "ck_team_table_positive", "competition_teams",
        "table_number IS NULL OR table_number BETWEEN 1 AND 10000",
    )
    op.create_check_constraint(
        "ck_person_table_positive", "people",
        "table_number IS NULL OR table_number BETWEEN 1 AND 10000",
    )


def downgrade() -> None:
    raise RuntimeError("Table assignments are event records and cannot be discarded safely.")
