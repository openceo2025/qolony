"""add colony roles, teams and policies

Revision ID: 0015_add_colony_org
Revises: 0014_add_task_updated_by
Create Date: 2026-11-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015_add_colony_org"
down_revision = "0014_add_task_updated_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 既存データは消さない。役職は全員「一般」で始め、Top Manager だけ「社長」にする。
    op.add_column(
        "Users",
        sa.Column("ColonyRole", sa.String(length=16), nullable=False, server_default="member"),
    )
    op.execute("UPDATE Users SET ColonyRole = 'president' WHERE IsTopManager = 1")
    op.add_column("Tasks", sa.Column("ColonySize", sa.Integer(), nullable=True))

    op.create_table(
        "ColonyTeams",
        sa.Column("Id", sa.String(length=36), primary_key=True),
        sa.Column("Name", sa.String(length=128), nullable=False),
        sa.Column(
            "ParentTeamId",
            sa.String(length=36),
            sa.ForeignKey("ColonyTeams.Id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "LeaderUserId",
            sa.String(length=36),
            sa.ForeignKey("Users.Id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("Emblem", sa.String(length=32), nullable=False, server_default="leaf"),
        sa.Column("DisplayOrder", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("CreateDate", sa.DateTime(timezone=False), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "ColonyTeamMembers",
        sa.Column(
            "TeamId",
            sa.String(length=36),
            sa.ForeignKey("ColonyTeams.Id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "UserId",
            sa.String(length=36),
            sa.ForeignKey("Users.Id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("CreateDate", sa.DateTime(timezone=False), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "ColonyPolicies",
        sa.Column("Id", sa.String(length=36), primary_key=True),
        sa.Column("Kind", sa.String(length=16), nullable=False, server_default="text"),
        sa.Column("Text", sa.Text(), nullable=False, server_default=""),
        sa.Column("X", sa.Float(), nullable=False, server_default="0"),
        sa.Column("Y", sa.Float(), nullable=False, server_default="0"),
        sa.Column("Size", sa.Float(), nullable=False, server_default="1"),
        sa.Column("PointsJson", sa.Text(), nullable=False, server_default="[]"),
        sa.Column(
            "AuthorUserId",
            sa.String(length=36),
            sa.ForeignKey("Users.Id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("CreateDate", sa.DateTime(timezone=False), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("ColonyPolicies")
    op.drop_table("ColonyTeamMembers")
    op.drop_table("ColonyTeams")
    op.drop_column("Tasks", "ColonySize")
    op.drop_column("Users", "ColonyRole")
