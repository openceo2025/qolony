"""add member view support

Revision ID: 0009_add_member_view_support
Revises: 0008_add_iquavis_user_id_to_users
Create Date: 2026-04-29 12:00:00

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0009_add_member_view_support"
down_revision: Union[str, None] = "0008_add_iquavis_user_id_to_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return set(inspector.get_table_names())


def upgrade() -> None:
    tables = _table_names()
    if "MemberProfiles" not in tables:
        op.create_table(
            "MemberProfiles",
            sa.Column("UserId", sa.String(length=36), nullable=False),
            sa.Column("DefaultCommitRate", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("ConditionIcon", sa.String(length=32), nullable=False, server_default="normal"),
            sa.Column("FormationRole", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("FormationPosition", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("FormationX", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("FormationY", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("DisplayOrder", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("TitleTagsJson", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("SpecialAbilitiesJson", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("UpdateDate", sa.DateTime(timezone=False), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["UserId"], ["Users.Id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("UserId"),
        )

    if "MemberDayConfigs" not in tables:
        op.create_table(
            "MemberDayConfigs",
            sa.Column("Id", sa.String(length=36), nullable=False),
            sa.Column("UserId", sa.String(length=36), nullable=False),
            sa.Column("WorkDate", sa.Date(), nullable=False),
            sa.Column("IsVacation", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("CommitRateOverride", sa.Float(), nullable=True),
            sa.Column("Note", sa.Text(), nullable=False, server_default=""),
            sa.Column("UpdateDate", sa.DateTime(timezone=False), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["UserId"], ["Users.Id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("Id"),
            sa.UniqueConstraint("UserId", "WorkDate", name="uq_member_day_config"),
        )


def downgrade() -> None:
    tables = _table_names()
    if "MemberDayConfigs" in tables:
        op.drop_table("MemberDayConfigs")
    if "MemberProfiles" in tables:
        op.drop_table("MemberProfiles")
