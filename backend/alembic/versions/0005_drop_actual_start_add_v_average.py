"""drop actual start date and add v_average

Revision ID: 0005_drop_actual_start_add_v_average
Revises: 0004_add_daily_actual_history
Create Date: 2026-02-15 00:00:01

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0005_drop_actual_start_add_v_average"
down_revision: Union[str, None] = "0004_add_daily_actual_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    task_columns = _column_names("Tasks")
    if "ActualStartDate" in task_columns:
        with op.batch_alter_table("Tasks") as batch_op:
            batch_op.drop_column("ActualStartDate")

    snapshot_columns = _column_names("DailyProgressSnapshots")
    if "VAverage" not in snapshot_columns:
        op.add_column(
            "DailyProgressSnapshots",
            sa.Column("VAverage", sa.Float(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    snapshot_columns = _column_names("DailyProgressSnapshots")
    if "VAverage" in snapshot_columns:
        with op.batch_alter_table("DailyProgressSnapshots") as batch_op:
            batch_op.drop_column("VAverage")

    task_columns = _column_names("Tasks")
    if "ActualStartDate" not in task_columns:
        with op.batch_alter_table("Tasks") as batch_op:
            batch_op.add_column(sa.Column("ActualStartDate", sa.Date(), nullable=True))
