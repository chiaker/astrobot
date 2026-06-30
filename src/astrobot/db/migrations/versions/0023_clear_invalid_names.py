"""clear display_name for users whose stored name fails the new validation

Names must be a single word of letters (e.g. commands like "/menu" or phrases
like "меня зовут олег" used to slip through). Such legacy values are nulled out
here so they don't leak into HTML messages / the LLM prompt. Done in Python so
the match is identical to the onboarding validator (Postgres regex handles the
Unicode \\w class differently).

Revision ID: 0023_clear_invalid_names
Revises: 0022_broadcasts
Create Date: 2026-06-30

"""
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_clear_invalid_names"
down_revision: str | None = "0022_broadcasts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must mirror astrobot.bot.handlers.onboarding._NAME_RE.
_NAME_RE = re.compile(r"^[^\W\d_]+(?:[-'][^\W\d_]+)*$", re.UNICODE)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, display_name FROM users WHERE display_name IS NOT NULL")
    ).fetchall()

    bad_ids = [r[0] for r in rows if not _NAME_RE.match((r[1] or "").strip())]
    if not bad_ids:
        return

    # Chunk the UPDATE to keep the IN-list bounded.
    for i in range(0, len(bad_ids), 1000):
        chunk = bad_ids[i : i + 1000]
        bind.execute(
            sa.text("UPDATE users SET display_name = NULL WHERE id = ANY(:ids)"),
            {"ids": chunk},
        )


def downgrade() -> None:
    # Irreversible: the original (invalid) names are not recoverable.
    pass
