"""brief/full responses + per-user default response mode

Revision ID: 0002_brief_full
Revises: 0001_initial
Create Date: 2026-05-26

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_brief_full"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("default_response", sa.String(length=8), nullable=False, server_default="brief"),
    )

    op.create_table(
        "responses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("full", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_responses_user_id", "responses", ["user_id"])
    op.create_index("ix_responses_created_at", "responses", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_responses_created_at", "responses")
    op.drop_index("ix_responses_user_id", "responses")
    op.drop_table("responses")
    op.drop_column("users", "default_response")
