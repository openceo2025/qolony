"""add task memo

Revision ID: 0003_add_task_memo
Revises: 0002_add_today_fields
Create Date: 2026-02-07 00:00:01

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003_add_task_memo"
down_revision: Union[str, None] = "0002_add_today_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "Tasks",
        sa.Column("Memo", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("Tasks", "Memo")
