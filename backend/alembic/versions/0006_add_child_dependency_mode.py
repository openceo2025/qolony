"""add child dependency mode to tasks

Revision ID: 0006_add_child_dependency_mode
Revises: 0005_drop_actual_start_add_v_average
Create Date: 2026-02-21 00:00:01

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0006_add_child_dependency_mode"
down_revision: Union[str, None] = "0005_drop_actual_start_add_v_average"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    task_columns = _column_names("Tasks")
    if "ChildDependencyMode" not in task_columns:
        op.add_column(
            "Tasks",
            sa.Column("ChildDependencyMode", sa.String(length=16), nullable=False, server_default="Serial"),
        )

    op.execute(
        sa.text(
            "UPDATE Tasks "
            "SET ChildDependencyMode = 'Serial' "
            "WHERE ChildDependencyMode IS NULL OR TRIM(ChildDependencyMode) = ''"
        )
    )


def downgrade() -> None:
    task_columns = _column_names("Tasks")
    if "ChildDependencyMode" in task_columns:
        with op.batch_alter_table("Tasks") as batch_op:
            batch_op.drop_column("ChildDependencyMode")
