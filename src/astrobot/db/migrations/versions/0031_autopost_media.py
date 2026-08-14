"""autopost_media: pool of neutral animations rotated through autoposts

Empty pool keeps the current behaviour (text-only posts), so nothing to backfill.

Revision ID: 0031_autopost_media
Revises: 0030_autopost_weekdays
Create Date: 2026-08-14

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_autopost_media"
down_revision: str | None = "0030_autopost_weekdays"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autopost_media",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=120), server_default="", nullable=False),
        sa.Column("animation", sa.String(length=512), server_default="", nullable=False),
        sa.Column("animation_data", sa.LargeBinary(), nullable=True),
        sa.Column("animation_name", sa.String(length=255), nullable=True),
        sa.Column("use_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("autopost_media")
