"""user.premium_questions_used — explicit counter for premium monthly question usage

Revision ID: 0016_premium_questions_used
Revises: 0015_free_questions_balance
Create Date: 2026-06-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_premium_questions_used"
down_revision: str | None = "0015_free_questions_balance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("premium_questions_used", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "premium_questions_used")
