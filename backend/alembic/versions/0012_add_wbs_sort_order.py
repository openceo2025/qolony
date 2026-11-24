"""add wbs sort order

Revision ID: 0012_add_wbs_sort_order
Revises: 0011_add_member_identity_fields
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_add_wbs_sort_order"
down_revision = "0011_add_member_identity_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("Tasks", sa.Column("WbsSortOrder", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("Tasks", "WbsSortOrder")
