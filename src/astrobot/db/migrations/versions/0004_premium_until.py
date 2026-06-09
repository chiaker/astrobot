"""premium_until on users

Revision ID: 0004_premium_until
Revises: 0003_natal_cache_message_ids
Create Date: 2026-06-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_premium_until"
down_revision: str | None = "0003_natal_cache_message_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("premium_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_premium_until", "users", ["premium_until"])


def downgrade() -> None:
    op.drop_index("ix_users_premium_until", "users")
    op.drop_column("users", "premium_until")
