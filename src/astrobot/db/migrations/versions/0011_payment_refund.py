"""payments.refunded_at for tracking refunds

Revision ID: 0011_payment_refund
Revises: 0010_payments
Create Date: 2026-06-12

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_payment_refund"
down_revision: str | None = "0010_payments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payments", "refunded_at")
