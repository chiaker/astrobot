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

    daily_question_limit: int = Field(default=20, alias="DAILY_QUESTION_LIMIT")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    admin_user: str = Field(default="admin", alias="ADMIN_USER")
    admin_password: str = Field(default="", alias="ADMIN_PASSWORD")
    admin_secret: str = Field(default="", alias="ADMIN_SECRET")

    @property
    def webhook_path(self) -> str:
        return f"/telegram/webhook/{self.webhook_secret}"

    @property
    def webhook_url(self) -> str:
        return f"{self.webhook_base_url.rstrip('/')}{self.webhook_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
