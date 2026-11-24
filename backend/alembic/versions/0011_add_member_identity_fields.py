"""add member identity fields

Revision ID: 0011_add_member_identity_fields
Revises: 0010_add_member_day_leave_type
Create Date: 2026-05-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_add_member_identity_fields"
down_revision = "0010_add_member_day_leave_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("Users", sa.Column("Nickname", sa.String(length=128), nullable=False, server_default=""))
    op.add_column("Users", sa.Column("FullName", sa.String(length=256), nullable=False, server_default=""))
    op.add_column("Users", sa.Column("IsActive", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.execute('UPDATE "Users" SET "Nickname" = "Name" WHERE "Nickname" = \'\'')


def downgrade() -> None:
    op.drop_column("Users", "IsActive")
    op.drop_column("Users", "FullName")
    op.drop_column("Users", "Nickname")
