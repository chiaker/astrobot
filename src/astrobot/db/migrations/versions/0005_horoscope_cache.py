"""horoscope_cache table

Revision ID: 0005_horoscope_cache
Revises: 0004_premium_until
Create Date: 2026-06-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_horoscope_cache"
down_revision: str | None = "0004_premium_until"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "horoscope_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("computed_for", sa.Date(), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("full", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "period", name="uq_horoscope_cache_user_period"),
    )
    op.create_index(
        "ix_horoscope_cache_user_period",
        "horoscope_cache",
        ["user_id", "period"],
    )


def downgrade() -> None:
    op.drop_index("ix_horoscope_cache_user_period", "horoscope_cache")
    op.drop_table("horoscope_cache")
