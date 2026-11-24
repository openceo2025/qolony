"""add iquavis task baselines

Revision ID: 0013_add_iquavis_task_baselines
Revises: 0012_add_wbs_sort_order
Create Date: 2026-08-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013_add_iquavis_task_baselines"
down_revision = "0012_add_wbs_sort_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "IQuavisTaskBaselines",
        sa.Column("TaskId", sa.String(length=36), nullable=False),
        sa.Column("IQuavisTaskId", sa.String(length=64), nullable=False),
        sa.Column("Name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("StartDate", sa.Date(), nullable=False),
        sa.Column("EndDate", sa.Date(), nullable=False),
        sa.Column("ActualManHour", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("Status", sa.String(length=32), nullable=False, server_default="NotStart"),
        sa.Column("ProgressRate", sa.Integer(), nullable=True),
        sa.Column("Memo", sa.Text(), nullable=False, server_default=""),
        sa.Column("CapturedAt", sa.DateTime(timezone=False), nullable=False),
        sa.ForeignKeyConstraint(["TaskId"], ["Tasks.Id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("TaskId"),
    )
    op.create_index(
        "ix_iquavis_task_baselines_iquavis_task_id",
        "IQuavisTaskBaselines",
        ["IQuavisTaskId"],
    )


def downgrade() -> None:
    op.drop_index("ix_iquavis_task_baselines_iquavis_task_id", table_name="IQuavisTaskBaselines")
    op.drop_table("IQuavisTaskBaselines")
