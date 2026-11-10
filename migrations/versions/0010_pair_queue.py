"""Add monitor competitor pairs and native correction-job provenance.

Revision ID: 0010_pair_queue
Revises: 0009_native_work
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_pair_queue"
down_revision = "0009_native_work"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("labeling_jobs", sa.Column(
        "correction_round", sa.Integer(), nullable=False, server_default="0",
    ))
    op.create_table(
        "monitor_pair_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("team_id", sa.String(36), sa.ForeignKey("competition_teams.id"), nullable=False),
        sa.Column("person_id", sa.String(36), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("team_id", "person_id", name="uq_monitor_pair_team_person"),
    )
    for col in ("event_id", "team_id", "person_id"):
        op.create_index(f"ix_monitor_pair_members_{col}", "monitor_pair_members", [col])
    op.create_table(
        "native_job_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("labeling_jobs.id"), nullable=False),
        sa.Column("person_id", sa.String(36), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("native_job_id", sa.Integer(), nullable=False),
        sa.Column("correction_round", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("native_job_id", name="uq_native_historical_job_id"),
    )
    for col in ("event_id", "job_id", "person_id"):
        op.create_index(f"ix_native_job_revisions_{col}", "native_job_revisions", [col])


def downgrade() -> None:
    raise RuntimeError("Competition and native job history must not be erased.")
