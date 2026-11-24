"""add task updated by user

Revision ID: 0014_add_task_updated_by
Revises: 0013_add_iquavis_task_baselines
Create Date: 2026-08-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_add_task_updated_by"
down_revision = "0013_add_iquavis_task_baselines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLiteでは既存テーブルへの名前付きFK追加ができないため、列だけ足す。
    # 参照整合はアプリ側（更新時に実在ユーザーIDのみ書き込む）で担保する。
    op.add_column("Tasks", sa.Column("UpdatedByUserId", sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column("Tasks", "UpdatedByUserId")
