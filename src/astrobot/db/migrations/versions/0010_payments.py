"""payments table for YooKassa + users.email for receipts

Revision ID: 0010_payments
Revises: 0009_push_settings
Create Date: 2026-06-12

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_payments"
down_revision: str | None = "0009_push_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(255), nullable=True))

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False, server_default="yookassa"),
        sa.Column("yookassa_payment_id", sa.String(64), nullable=True),
        sa.Column("item_code", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="RUB"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_status", "payments", ["status"])
    op.create_index("ix_payments_created_at", "payments", ["created_at"])
    op.create_index(
        "ix_payments_yookassa_payment_id",
        "payments",
        ["yookassa_payment_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_payments_yookassa_payment_id", table_name="payments")
    op.drop_index("ix_payments_created_at", table_name="payments")
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_user_id", table_name="payments")
    op.drop_table("payments")
    op.drop_column("users", "email")
