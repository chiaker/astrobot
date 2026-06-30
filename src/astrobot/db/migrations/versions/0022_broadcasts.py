"""broadcasts — admin-authored broadcast campaigns with per-segment variants

Revision ID: 0022_broadcasts
Revises: 0021_subscriptions
Create Date: 2026-06-30

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_broadcasts"
down_revision: str | None = "0021_subscriptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_user_id", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sent_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_broadcasts_status", "broadcasts", ["status"])
    op.create_index("ix_broadcasts_scheduled_at", "broadcasts", ["scheduled_at"])

    op.create_table(
        "broadcast_variants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("broadcast_id", sa.Integer(), nullable=False),
        sa.Column("segment", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("animation", sa.String(length=512), server_default="", nullable=False),
        sa.Column("buttons", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["broadcast_id"], ["broadcasts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("broadcast_id", "segment", name="uq_broadcast_variant_segment"),
    )
    op.create_index(
        "ix_broadcast_variants_broadcast_id", "broadcast_variants", ["broadcast_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_broadcast_variants_broadcast_id", table_name="broadcast_variants")
    op.drop_table("broadcast_variants")
    op.drop_index("ix_broadcasts_scheduled_at", table_name="broadcasts")
    op.drop_index("ix_broadcasts_status", table_name="broadcasts")
    op.drop_table("broadcasts")
