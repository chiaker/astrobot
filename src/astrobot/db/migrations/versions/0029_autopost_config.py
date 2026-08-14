"""autopost_config: settings for the LLM-authored astro autopost

Single-row table (id=1) with the on/off switch, cadence and send hour, plus the
bookkeeping the scheduler needs (last run, last used event).

Revision ID: 0029_autopost_config
Revises: 0028_channel_bonus
Create Date: 2026-08-14

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_autopost_config"
down_revision: str | None = "0028_channel_bonus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autopost_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("interval_days", sa.Integer(), server_default="3", nullable=False),
        sa.Column("hour_msk", sa.Integer(), server_default="11", nullable=False),
        sa.Column("last_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_key", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("autopost_config")
