"""channel-subscription promo: +2 questions, one per account

Revision ID: 0028_channel_bonus
Revises: 0027_free_questions_5
Create Date: 2026-08-13

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_channel_bonus"
down_revision: str | None = "0027_free_questions_5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("channel_bonus_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "channel_bonus_at")
