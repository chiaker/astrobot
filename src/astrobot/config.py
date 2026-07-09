from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(alias="BOT_TOKEN")

    # Messenger platform this deploy serves. Selects the adapter (aiogram vs
    # maxapi). One image, two deploys — each with its own .env.
    platform: Literal["telegram", "max"] = Field(default="telegram", alias="PLATFORM")

    run_mode: Literal["polling", "webhook"] = Field(default="polling", alias="RUN_MODE")
    webhook_base_url: str = Field(default="", alias="WEBHOOK_BASE_URL")
    webhook_secret: str = Field(default="", alias="WEBHOOK_SECRET")
    web_host: str = Field(default="0.0.0.0", alias="WEB_HOST")
    web_port: int = Field(default=8000, alias="WEB_PORT")

    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")

    llm_base_url: str = Field(default="https://api.deepseek.com/v1", alias="LLM_BASE_URL")
    llm_api_key: str = Field(alias="LLM_API_KEY")
    llm_model: str = Field(default="deepseek-v4-flash", alias="LLM_MODEL")
    llm_model_natal: str | None = Field(default=None, alias="LLM_MODEL_NATAL")
    llm_model_horoscope: str | None = Field(default=None, alias="LLM_MODEL_HOROSCOPE")
    llm_model_question: str | None = Field(default=None, alias="LLM_MODEL_QUESTION")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    admin_user: str = Field(default="admin", alias="ADMIN_USER")
    admin_password: str = Field(default="", alias="ADMIN_PASSWORD")
    admin_secret: str = Field(default="", alias="ADMIN_SECRET")

    bot_username: str = Field(default="", alias="BOT_USERNAME")
    ops_chat_id: int | None = Field(default=None, alias="OPS_CHAT_ID")
    # Optional welcome animation on first /start: a direct gif/mp4 URL or a
    # Telegram file_id. Empty → text-only greeting (current behaviour).
    welcome_animation: str = Field(default="", alias="WELCOME_ANIMATION")
    # Optional animation for the 48h-after-registration follow-up. URL or
    # Telegram file_id. Empty → text-only follow-up.
    followup_animation: str = Field(default="", alias="FOLLOWUP_ANIMATION")

    # YooKassa (direct REST API)
    yookassa_shop_id: str = Field(default="", alias="YOOKASSA_SHOP_ID")
    yookassa_secret_key: str = Field(default="", alias="YOOKASSA_SECRET_KEY")
    yookassa_return_url: str = Field(default="", alias="YOOKASSA_RETURN_URL")
    yookassa_webhook_ips: str = Field(default="", alias="YOOKASSA_WEBHOOK_IPS")
    # Required by YooKassa on each receipt item: 1=без НДС (НПД/УСН), 4=НДС 20% (ОСН)
    yookassa_vat_code: int = Field(default=1, alias="YOOKASSA_VAT_CODE")

    # Auto-renewing subscriptions (recurring payments). Keep OFF until YooKassa
    # enables recurring for the shop: with it off, the monthly plan is sold as a
    # one-time payment (no saved card token, no Stars subscription, no
    # auto-charge), which avoids the create-payment error from requesting
    # save_payment_method on a shop that doesn't allow it.
    recurring_enabled: bool = Field(default=False, alias="RECURRING_ENABLED")

    # Refund policy
    refund_window_days: int = Field(default=14, alias="REFUND_WINDOW_DAYS")
    refund_max_consumed_pct: int = Field(default=25, alias="REFUND_MAX_CONSUMED_PCT")

    # Premium-expiry reminder
    premium_reminder_days_before: int = Field(
        default=3, alias="PREMIUM_REMINDER_DAYS_BEFORE"
    )

    push_horoscope_hour: int = Field(default=9, alias="PUSH_HOROSCOPE_HOUR")

    llm_price_input_usd_per_m: float = Field(
        default=0.14, alias="LLM_PRICE_INPUT_USD_PER_M"
    )
    llm_price_output_usd_per_m: float = Field(
        default=0.28, alias="LLM_PRICE_OUTPUT_USD_PER_M"
    )
    llm_price_cache_hit_usd_per_m: float = Field(
        default=0.07, alias="LLM_PRICE_CACHE_HIT_USD_PER_M"
    )

    @property
    def webhook_path(self) -> str:
        return f"/telegram/webhook/{self.webhook_secret}"

    @property
    def webhook_url(self) -> str:
        return f"{self.webhook_base_url.rstrip('/')}{self.webhook_path}"

    @property
    def yookassa_return_url_effective(self) -> str:
        # Where YooKassa sends the user back after payment. Platform-specific
        # deep link to the bot; override with YOOKASSA_RETURN_URL when the exact
        # format differs. TODO(max): confirm the canonical MAX bot link format.
        if self.yookassa_return_url:
            return self.yookassa_return_url
        if self.platform == "max":
            return f"https://max.ru/{self.bot_username}" if self.bot_username else "https://max.ru"
        if self.bot_username:
            return f"https://t.me/{self.bot_username}"
        return "https://t.me"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
