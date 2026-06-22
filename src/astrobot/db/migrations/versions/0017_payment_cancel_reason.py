"""payment.cancel_reason — why a payment ended up canceled

Revision ID: 0017_payment_cancel_reason
Revises: 0016_premium_questions_used
Create Date: 2026-06-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_payment_cancel_reason"
down_revision: str | None = "0016_premium_questions_used"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("cancel_reason", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payments", "cancel_reason")
