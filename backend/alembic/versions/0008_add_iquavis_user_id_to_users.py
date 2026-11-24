"""add iquavis user id to users

Revision ID: 0008_add_iquavis_user_id_to_users
Revises: 0007_add_iquavis_sync_support
Create Date: 2026-04-04 12:00:00

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0008_add_iquavis_user_id_to_users"
down_revision: Union[str, None] = "0007_add_iquavis_sync_support"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    user_columns = _column_names("Users")
    with op.batch_alter_table("Users") as batch_op:
        if "IQuavisUserId" not in user_columns:
            batch_op.add_column(sa.Column("IQuavisUserId", sa.String(length=36), nullable=True))

    if "ux_users_iquavis_user_id" not in _index_names("Users"):
        op.create_index("ux_users_iquavis_user_id", "Users", ["IQuavisUserId"], unique=True)


def downgrade() -> None:
    user_indexes = _index_names("Users")
    if "ux_users_iquavis_user_id" in user_indexes:
        op.drop_index("ux_users_iquavis_user_id", table_name="Users")

    user_columns = _column_names("Users")
    with op.batch_alter_table("Users") as batch_op:
        if "IQuavisUserId" in user_columns:
            batch_op.drop_column("IQuavisUserId")
