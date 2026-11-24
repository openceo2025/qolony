"""add iquavis sync support

Revision ID: 0007_add_iquavis_sync_support
Revises: 0006_add_child_dependency_mode
Create Date: 2026-04-04 00:00:01

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0007_add_iquavis_sync_support"
down_revision: Union[str, None] = "0006_add_child_dependency_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return set(inspector.get_table_names())


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    task_columns = _column_names("Tasks")
    with op.batch_alter_table("Tasks") as batch_op:
        if "IQuavisTaskId" not in task_columns:
            batch_op.add_column(sa.Column("IQuavisTaskId", sa.String(length=36), nullable=True))
        if "IQuavisProjectId" not in task_columns:
            batch_op.add_column(sa.Column("IQuavisProjectId", sa.String(length=36), nullable=True))
        if "IQuavisLastPulledAt" not in task_columns:
            batch_op.add_column(sa.Column("IQuavisLastPulledAt", sa.DateTime(), nullable=True))
        if "IQuavisLastPushedAt" not in task_columns:
            batch_op.add_column(sa.Column("IQuavisLastPushedAt", sa.DateTime(), nullable=True))

    if "ux_tasks_iquavis_task_id" not in _index_names("Tasks"):
        op.create_index("ux_tasks_iquavis_task_id", "Tasks", ["IQuavisTaskId"], unique=True)

    table_names = _table_names()
    if "IQuavisSyncJobs" not in table_names:
        op.create_table(
            "IQuavisSyncJobs",
            sa.Column("Id", sa.String(length=36), nullable=False),
            sa.Column("TaskId", sa.String(length=36), nullable=False),
            sa.Column("IQuavisTaskId", sa.String(length=36), nullable=False),
            sa.Column("JobType", sa.String(length=32), nullable=False),
            sa.Column("PayloadJson", sa.Text(), nullable=False),
            sa.Column("Status", sa.String(length=16), nullable=False, server_default="Pending"),
            sa.Column("Attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("NextRunAt", sa.DateTime(), nullable=False),
            sa.Column("LastStartedAt", sa.DateTime(), nullable=True),
            sa.Column("LastFinishedAt", sa.DateTime(), nullable=True),
            sa.Column("LastHttpStatus", sa.Integer(), nullable=True),
            sa.Column("LastError", sa.Text(), nullable=True),
            sa.Column("CreateDate", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("UpdateDate", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["TaskId"], ["Tasks.Id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("Id"),
        )
        op.create_index("ix_iquavis_sync_jobs_due", "IQuavisSyncJobs", ["Status", "NextRunAt"], unique=False)
        op.create_index("ix_iquavis_sync_jobs_task", "IQuavisSyncJobs", ["TaskId", "JobType"], unique=False)


def downgrade() -> None:
    table_names = _table_names()
    if "IQuavisSyncJobs" in table_names:
        index_names = _index_names("IQuavisSyncJobs")
        if "ix_iquavis_sync_jobs_due" in index_names:
            op.drop_index("ix_iquavis_sync_jobs_due", table_name="IQuavisSyncJobs")
        if "ix_iquavis_sync_jobs_task" in index_names:
            op.drop_index("ix_iquavis_sync_jobs_task", table_name="IQuavisSyncJobs")
        op.drop_table("IQuavisSyncJobs")

    task_indexes = _index_names("Tasks")
    if "ux_tasks_iquavis_task_id" in task_indexes:
        op.drop_index("ux_tasks_iquavis_task_id", table_name="Tasks")

    task_columns = _column_names("Tasks")
    with op.batch_alter_table("Tasks") as batch_op:
        if "IQuavisLastPushedAt" in task_columns:
            batch_op.drop_column("IQuavisLastPushedAt")
        if "IQuavisLastPulledAt" in task_columns:
            batch_op.drop_column("IQuavisLastPulledAt")
        if "IQuavisProjectId" in task_columns:
            batch_op.drop_column("IQuavisProjectId")
        if "IQuavisTaskId" in task_columns:
            batch_op.drop_column("IQuavisTaskId")
