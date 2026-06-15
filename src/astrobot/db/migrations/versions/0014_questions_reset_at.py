"""user.questions_reset_at — fresh question quota on premium purchase

Revision ID: 0014_questions_reset_at
Revises: 0013_support_tickets
Create Date: 2026-06-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_questions_reset_at"
down_revision: str | None = "0013_support_tickets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("questions_reset_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "questions_reset_at")
