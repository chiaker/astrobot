"""users.username — store the Telegram @username (refreshed on each interaction)

Lets the admin pick a test-send recipient by username instead of a numeric ID.

Revision ID: 0025_user_username
Revises: 0024_broadcast_animation_upload
Create Date: 2026-07-01

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_user_username"
down_revision: str | None = "0024_broadcast_animation_upload"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=32), nullable=True))
    op.create_index("ix_users_username", "users", ["username"])


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
