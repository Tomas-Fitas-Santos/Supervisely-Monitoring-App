"""Persist independent repeat assignments and batch ownership."""

import sqlalchemy as sa
from alembic import op

revision = "0020_repeat_dispatch"
down_revision = "0019_sampled_review"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("work_blocks", sa.Column("repeat_team_id", sa.String(36),
                                          sa.ForeignKey("competition_teams.id")))
    op.add_column("work_blocks", sa.Column("repeat_job_id", sa.String(36),
                                          sa.ForeignKey("labeling_jobs.id")))
    op.create_index("ix_work_block_repeat_queue", "work_blocks",
                    ["plan_id", "repeat_team_id", "id"],
                    postgresql_where=sa.text("repeat_job_id IS NULL"))


def downgrade():
    op.drop_index("ix_work_block_repeat_queue", table_name="work_blocks")
    op.drop_column("work_blocks", "repeat_job_id")
    op.drop_column("work_blocks", "repeat_team_id")
