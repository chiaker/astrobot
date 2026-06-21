"""user.free_questions_balance — track free lifetime question quota directly

Revision ID: 0015_free_questions_balance
Revises: 0014_questions_reset_at
Create Date: 2026-06-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_free_questions_balance"
down_revision: str | None = "0014_questions_reset_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("free_questions_balance", sa.Integer(), nullable=False, server_default="2"),
    )


def downgrade() -> None:
    op.drop_column("users", "free_questions_balance")
