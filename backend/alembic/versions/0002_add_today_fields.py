"""add today toggle fields

Revision ID: 0002_add_today_fields
Revises: 0001_initial
Create Date: 2026-02-07 00:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_add_today_fields"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("Tasks", sa.Column("TodayOverride", sa.Boolean(), nullable=True))
    op.add_column("Tasks", sa.Column("TodaySortOrder", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("Tasks", "TodaySortOrder")
    op.drop_column("Tasks", "TodayOverride")
