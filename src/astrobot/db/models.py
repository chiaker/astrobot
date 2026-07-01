from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    lang: Mapped[str] = mapped_column(String(8), default="ru")
    default_response: Mapped[str] = mapped_column(String(8), default="brief")
    premium_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # The premium_until value we've already sent an expiry reminder for, so a
    # renewal (new premium_until) re-arms the reminder.
    premium_reminded_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    referral_code: Mapped[str] = mapped_column(String(16), unique=True)
    referred_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    bonus_questions: Mapped[int] = mapped_column(Integer, default=0)
    free_questions_balance: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    premium_questions_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Questions asked before this moment don't count against the monthly quota —
    # set on premium purchase so a buyer gets a full fresh allowance.
    questions_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    push_horoscope_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    push_lunar_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_horoscope_push_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When the day-2 (48h-after-registration) follow-up message was sent.
    # NULL = not sent yet.
    followup_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    legal_agreed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(4), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    astro_terms_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    natal_regens_bonus: Mapped[int] = mapped_column(Integer, default=0)
    push_tz: Mapped[str | None] = mapped_column(String(64), nullable=True)
    push_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    push_city_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Test / staff accounts flagged here are dropped from the admin Сводка
    # (statistics & reports) so they don't skew metrics.
    excluded_from_stats: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    profile: Mapped[BirthProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    questions: Mapped[list[QuestionLog]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    usage: Mapped[list[LLMUsageLog]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    responses: Mapped[list[Response]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    favorites: Mapped[list[Favorite]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    payments: Mapped[list[Payment]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    subscription: Mapped[Subscription | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    support_tickets: Mapped[list[SupportTicket]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class BirthProfile(Base):
    __tablename__ = "birth_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    birth_date: Mapped[date] = mapped_column(Date)
    birth_time: Mapped[time] = mapped_column(Time)
    time_unknown: Mapped[bool] = mapped_column(Boolean, default=False)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    tz: Mapped[str] = mapped_column(String(64))
    city_name: Mapped[str] = mapped_column(String(255))
    cached_natal_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    cached_natal_full: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="profile")


class GeocodeCache(Base):
    __tablename__ = "geocode_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    display_name: Mapped[str] = mapped_column(String(512))
    tz: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class QuestionLog(Base):
    __tablename__ = "question_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    user: Mapped[User] = relationship(back_populates="questions")


class Response(Base):
    __tablename__ = "responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    brief: Mapped[str] = mapped_column(Text)
    full: Mapped[str] = mapped_column(Text)
    message_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    user: Mapped[User] = relationship(back_populates="responses")


class HoroscopeCache(Base):
    __tablename__ = "horoscope_cache"
    __table_args__ = (
        UniqueConstraint("user_id", "period", name="uq_horoscope_cache_user_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    period: Mapped[str] = mapped_column(String(16))
    computed_for: Mapped[date] = mapped_column(Date)
    brief: Mapped[str] = mapped_column(Text)
    full: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Favorite(Base):
    __tablename__ = "favorites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(255))
    brief: Mapped[str] = mapped_column(Text)
    full: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="favorites")


class LunarEvent(Base):
    __tablename__ = "lunar_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(8))
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), default="yookassa")
    # Set after the provider responds; unique → idempotency key for the webhook
    yookassa_payment_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    # Telegram Stars charge id (telegram_payment_charge_id) — used for refunds
    # via refund_star_payment. NULL for non-Stars payments.
    telegram_charge_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    item_code: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    # pending | succeeded | canceled | refunded
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # Why a canceled payment was canceled: user | create_error | yookassa |
    # timeout | orphan. NULL for non-canceled payments.
    cancel_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refunded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="payments")


class Subscription(Base):
    """A recurring premium subscription. At most one row per user (reused on
    re-subscribe). Premium gating still lives on User.premium_until — this row
    just drives the auto-renewal that extends it.

    Telegram Stars renewals are push-based (Telegram charges and notifies us);
    YooKassa renewals are pulled by charge_due_card_subscriptions_job using the
    saved card token.
    """

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(16))  # telegram_stars | yookassa
    plan_code: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # YooKassa saved-card token, used to charge renewals without user action.
    yookassa_payment_method_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # Stars charge id of the active subscription — needed to cancel it via
    # edit_user_star_subscription.
    telegram_charge_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Mirror of premium_until at subscription granularity.
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # YooKassa: when the scheduler should attempt the next charge. NULL for Stars
    # (Telegram drives renewals) and for canceled subscriptions.
    next_charge_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="subscription")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), default="support")  # support | refund
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | answered
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="support_tickets")


class Broadcast(Base):
    """An admin-authored broadcast campaign. One campaign fans out to several
    user segments, each with its own text / animation / buttons (BroadcastVariant).
    The dispatch job scans users by ascending id (resumable via cursor_user_id),
    classifies each into a segment, and sends the matching enabled variant."""

    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    # draft | scheduled | sending | sent | canceled
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    # When the dispatch job should start sending (UTC). Admin enters MSK.
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Resume cursor: highest User.id already processed for this broadcast.
    cursor_user_id: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sent_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    variants: Mapped[list[BroadcastVariant]] = relationship(
        back_populates="broadcast",
        cascade="all, delete-orphan",
        order_by="BroadcastVariant.id",
    )


class BroadcastVariant(Base):
    """Per-segment content for a Broadcast. buttons is a JSON list of
    {type, label, value} dicts (see astrobot.bot.keyboards.build_broadcast_kb)."""

    __tablename__ = "broadcast_variants"
    __table_args__ = (
        UniqueConstraint("broadcast_id", "segment", name="uq_broadcast_variant_segment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(
        ForeignKey("broadcasts.id", ondelete="CASCADE"), index=True
    )
    segment: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    # A Telegram file_id (auto-cached from the first send of an uploaded file) or
    # a legacy pasted file_id/URL. Empty until known.
    animation: Mapped[str] = mapped_column(String(512), default="", server_default="")
    # The uploaded animation bytes (source of truth; survives redeploys). On the
    # first send its file_id is cached into `animation` so later sends skip the
    # re-upload. animation_name preserves the original filename for Telegram.
    animation_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    animation_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    buttons: Mapped[list] = mapped_column(JSON, default=list)

    broadcast: Mapped[Broadcast] = relationship(back_populates="variants")


class LLMUsageLog(Base):
    __tablename__ = "llm_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    user: Mapped[User] = relationship(back_populates="usage")
