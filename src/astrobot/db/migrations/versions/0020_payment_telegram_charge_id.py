"""payment.telegram_charge_id — Telegram Stars charge id for refunds

Revision ID: 0020_payment_telegram_charge_id
Revises: 0019_user_excluded_from_stats
Create Date: 2026-06-28

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_payment_telegram_charge_id"
down_revision: str | None = "0019_user_excluded_from_stats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("telegram_charge_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_payments_telegram_charge_id",
        "payments",
        ["telegram_charge_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_payments_telegram_charge_id", table_name="payments")
    op.drop_column("payments", "telegram_charge_id")
