"""Optional video-to-image plan dependency; image blocks have no source video."""
import sqlalchemy as sa
from alembic import op

revision = "0032_sequential_image_plans"
down_revision = "0031_mounted_source_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("labeling_plans", sa.Column("after_plan_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_plan_predecessor", "labeling_plans", "labeling_plans",
                          ["after_plan_id"], ["id"])
    op.create_index("ix_labeling_plans_after_plan_id", "labeling_plans", ["after_plan_id"])
    op.alter_column("work_blocks", "video_id", existing_type=sa.String(36), nullable=True)


def downgrade():
    # A downgrade cannot represent image blocks; fail rather than delete work.
    op.alter_column("work_blocks", "video_id", existing_type=sa.String(36), nullable=False)
    op.drop_index("ix_labeling_plans_after_plan_id", table_name="labeling_plans")
    op.drop_constraint("fk_plan_predecessor", "labeling_plans", type_="foreignkey")
    op.drop_column("labeling_plans", "after_plan_id")
