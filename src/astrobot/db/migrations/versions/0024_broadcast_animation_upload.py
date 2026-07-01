"""broadcast_variants: store uploaded animation bytes (+ original filename)

Lets admins upload a GIF/MP4 directly in the constructor instead of pasting a
file_id/URL. The bytes are the source of truth; the Telegram file_id is cached
back into `animation` on the first send.

Revision ID: 0024_broadcast_animation_upload
Revises: 0023_clear_invalid_names
Create Date: 2026-07-01

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_broadcast_animation_upload"
down_revision: str | None = "0023_clear_invalid_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "broadcast_variants",
        sa.Column("animation_data", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "broadcast_variants",
        sa.Column("animation_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("broadcast_variants", "animation_name")
    op.drop_column("broadcast_variants", "animation_data")
