"""autopost_config.weekdays: pick the days autoposts go out on

Empty string keeps the existing behaviour (every interval_days days), so the
column is additive — nothing to backfill.

Revision ID: 0030_autopost_weekdays
Revises: 0029_autopost_config
Create Date: 2026-08-14

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_autopost_weekdays"
down_revision: str | None = "0029_autopost_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "autopost_config",
        sa.Column("weekdays", sa.String(length=16), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("autopost_config", "weekdays")
