"""user: natal_regens_bonus for purchased extra natal chart generations

Revision ID: 0008_natal_regen_bonus
Revises: 0007_user_profile
Create Date: 2026-06-10

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_natal_regen_bonus"
down_revision: str | None = "0007_user_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "natal_regens_bonus",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "natal_regens_bonus")
