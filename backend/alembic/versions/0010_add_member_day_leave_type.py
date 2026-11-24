"""add member day leave type

Revision ID: 0010_add_member_day_leave_type
Revises: 0009_add_member_view_support
Create Date: 2026-04-29 13:00:00

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0010_add_member_day_leave_type"
down_revision: Union[str, None] = "0009_add_member_view_support"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "LeaveType" not in _column_names("MemberDayConfigs"):
        op.add_column(
            "MemberDayConfigs",
            sa.Column("LeaveType", sa.String(length=64), nullable=False, server_default=""),
        )


def downgrade() -> None:
    if "LeaveType" in _column_names("MemberDayConfigs"):
        op.drop_column("MemberDayConfigs", "LeaveType")
