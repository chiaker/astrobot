"""user: push_tz, push_hour, push_city_name for per-user push notification settings

Revision ID: 0009_push_settings
Revises: 0008_natal_regen_bonus
Create Date: 2026-06-11

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_push_settings"
down_revision: str | None = "0008_natal_regen_bonus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("push_tz", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("push_hour", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("push_city_name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "push_city_name")
    op.drop_column("users", "push_hour")
    op.drop_column("users", "push_tz")
