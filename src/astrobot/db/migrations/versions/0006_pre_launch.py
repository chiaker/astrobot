"""pre-launch hardening: referral, push, legal, favorites, lunar events

Revision ID: 0006_pre_launch
Revises: 0005_horoscope_cache
Create Date: 2026-06-09

"""
from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0006_pre_launch"
down_revision: str | None = "0005_horoscope_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _gen_code() -> str:
    return uuid4().hex[:8].upper()


def upgrade() -> None:
    # --- users: new columns ---
    op.add_column("users", sa.Column("referral_code", sa.String(length=16), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "referred_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column("bonus_questions", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "users",
        sa.Column(
            "push_horoscope_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "push_lunar_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column("last_horoscope_push_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("legal_agreed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Backfill referral_code for existing users
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id FROM users")).fetchall()
    for (user_id,) in rows:
        bind.execute(
            sa.text("UPDATE users SET referral_code = :c WHERE id = :id"),
            {"c": _gen_code(), "id": user_id},
        )

    op.alter_column("users", "referral_code", nullable=False)
    op.create_unique_constraint("uq_users_referral_code", "users", ["referral_code"])
    op.create_index("ix_users_referred_by", "users", ["referred_by_user_id"])

    # --- favorites ---
    op.create_table(
        "favorites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("full", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_favorites_user_created", "favorites", ["user_id", "created_at"])

    # --- lunar_events ---
    op.create_table(
        "lunar_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_date", sa.Date(), nullable=False, unique=True),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column(
            "notified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index("ix_lunar_events_date", "lunar_events", ["event_date"])


def downgrade() -> None:
    op.drop_index("ix_lunar_events_date", "lunar_events")
    op.drop_table("lunar_events")

    op.drop_index("ix_favorites_user_created", "favorites")
    op.drop_table("favorites")

    op.drop_index("ix_users_referred_by", "users")
    op.drop_constraint("uq_users_referral_code", "users", type_="unique")
    op.drop_column("users", "legal_agreed_at")
    op.drop_column("users", "last_horoscope_push_at")
    op.drop_column("users", "push_lunar_enabled")
    op.drop_column("users", "push_horoscope_enabled")
    op.drop_column("users", "bonus_questions")
    op.drop_column("users", "referred_by_user_id")
    op.drop_column("users", "referral_code")
