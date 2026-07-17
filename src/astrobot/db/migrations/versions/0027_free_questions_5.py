"""free plan 2→5 questions + one-off +3 gift for existing free users

New users now start with 5 free questions (server_default 2→5). Every existing
user gets a flat +3 top-up (spent 0 → 5, spent 1 → 4, spent 2 → 3) and is flagged
`free_gift_pending` so the scheduler can send them a one-time "Подарок +3" message —
premium users included (the +3 lands in their free bucket, consumed before the
monthly quota).

Revision ID: 0027_free_questions_5
Revises: 0026_followup_config
Create Date: 2026-07-16

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_free_questions_5"
down_revision: str | None = "0026_followup_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "free_gift_pending",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    # New users start with 5 from now on.
    op.alter_column("users", "free_questions_balance", server_default="5")

    # Flat +3 to every existing user, and flag them for the gift message. Runs
    # once (alembic). New users created after this aren't affected — they start
    # with 5 and free_gift_pending defaults to false.
    op.execute(
        sa.text(
            "UPDATE users "
            "SET free_questions_balance = free_questions_balance + 3, "
            "    free_gift_pending = true"
        )
    )


def downgrade() -> None:
    op.alter_column("users", "free_questions_balance", server_default="2")
    op.drop_column("users", "free_gift_pending")
    # The +3 already granted is not clawed back (data migration, irreversible).
