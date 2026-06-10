"""user profile: display_name, gender, astro_terms_enabled

Revision ID: 0007_user_profile
Revises: 0006_pre_launch
Create Date: 2026-06-10

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_user_profile"
down_revision: str | None = "0006_pre_launch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(128), nullable=True))
    op.add_column("users", sa.Column("gender", sa.String(4), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "astro_terms_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "astro_terms_enabled")
    op.drop_column("users", "gender")
    op.drop_column("users", "display_name")
