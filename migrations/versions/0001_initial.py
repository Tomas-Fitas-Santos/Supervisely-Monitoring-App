"""Initial staff, roster, team, settings and audit records.

Revision ID: 0001_initial
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "staff_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("login_failures", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_staff_accounts_email", "staff_accounts", ["email"], unique=True)
    op.create_table(
        "staff_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("staff_id", sa.String(36), sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("csrf_token", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_staff_sessions_staff_id", "staff_sessions", ["staff_id"])
    op.create_index("ix_staff_sessions_expires_at", "staff_sessions", ["expires_at"])
    op.create_table(
        "competition_teams",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_competition_teams_name", "competition_teams", ["name"], unique=True)
    op.create_table(
        "people",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("team_id", sa.String(36), sa.ForeignKey("competition_teams.id")),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("membership_status", sa.String(30), nullable=False),
        sa.Column("supervisely_user_id", sa.Integer()),
        sa.Column("supervisely_login", sa.String(320)),
        sa.Column("check_in_at", sa.Integer()),
        sa.Column("source_fields", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_people_name", "people", ["name"])
    op.create_index("ix_people_email", "people", ["email"], unique=True)
    op.create_index("ix_people_kind", "people", ["kind"])
    op.create_index("ix_people_team_id", "people", ["team_id"])
    op.create_table(
        "supervisely_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_url", sa.String(512), nullable=False),
        sa.Column("team_id", sa.Integer()),
        sa.Column("workspace_id", sa.Integer()),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("staff_id", sa.String(36), sa.ForeignKey("staff_accounts.id")),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(80), nullable=False),
        sa.Column("target_id", sa.String(100)),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_audit_events_staff_id", "audit_events", ["staff_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_table(
        "roster_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("staff_id", sa.String(36), sa.ForeignKey("staff_accounts.id"), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("changed_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_roster_imports_source_hash", "roster_imports", ["source_hash"])


def downgrade() -> None:
    op.drop_table("roster_imports")
    op.drop_table("audit_events")
    op.drop_table("supervisely_config")
    op.drop_table("people")
    op.drop_table("competition_teams")
    op.drop_table("staff_sessions")
    op.drop_table("staff_accounts")
