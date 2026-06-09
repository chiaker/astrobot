"""cache natal result on profile + track displayed message_ids

Revision ID: 0003_natal_cache_message_ids
Revises: 0002_brief_full
Create Date: 2026-05-26

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_natal_cache_message_ids"
down_revision: str | None = "0002_brief_full"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "birth_profiles",
        sa.Column("cached_natal_brief", sa.Text(), nullable=True),
    )
    op.add_column(
        "birth_profiles",
        sa.Column("cached_natal_full", sa.Text(), nullable=True),
    )
    op.add_column(
        "responses",
        sa.Column(
            "message_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("responses", "message_ids")
    op.drop_column("birth_profiles", "cached_natal_full")
    op.drop_column("birth_profiles", "cached_natal_brief")
