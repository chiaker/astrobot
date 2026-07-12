"""followup_config: editable day-2 follow-up content (text + animation)

Single-row table (id=1) letting admins edit the 48h follow-up nudge and upload
a GIF/MP4 from the admin, instead of the hardcoded text + FOLLOWUP_ANIMATION env.

Revision ID: 0026_followup_config
Revises: 0025_user_username
Create Date: 2026-07-12

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_followup_config"
down_revision: str | None = "0025_user_username"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "followup_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("animation", sa.String(length=512), server_default="", nullable=False),
        sa.Column("animation_data", sa.LargeBinary(), nullable=True),
        sa.Column("animation_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("followup_config")
