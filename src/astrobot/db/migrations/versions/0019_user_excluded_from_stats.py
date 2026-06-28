"""user.excluded_from_stats — drop test/staff accounts from admin Сводка

Revision ID: 0019_user_excluded_from_stats
Revises: 0018_user_followup_sent_at
Create Date: 2026-06-28

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_user_excluded_from_stats"
down_revision: str | None = "0018_user_followup_sent_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "excluded_from_stats",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "excluded_from_stats")
