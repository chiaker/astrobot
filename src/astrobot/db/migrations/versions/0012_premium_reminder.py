"""user: premium_reminded_until for premium-expiry reminder dedupe

Revision ID: 0012_premium_reminder
Revises: 0011_payment_refund
Create Date: 2026-06-12

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_premium_reminder"
down_revision: str | None = "0011_payment_refund"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("premium_reminded_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "premium_reminded_until")
