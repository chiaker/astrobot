"""user.followup_sent_at — dedup for the 48h-after-registration follow-up

Revision ID: 0018_user_followup_sent_at
Revises: 0017_payment_cancel_reason
Create Date: 2026-06-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_user_followup_sent_at"
down_revision: str | None = "0017_payment_cancel_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("followup_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "followup_sent_at")
