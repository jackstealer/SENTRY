"""Typed settings from the environment and `.env`. The only place config is read (hard rule 1)."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    user_id: str = "me"
    user_name: str = ""
    user_tz: str = "Asia/Kolkata"
    database_url: str = "postgresql+asyncpg://sentry:sentry@127.0.0.1:5432/sentry"
    api_host: str = "127.0.0.1"  # 0.0.0.0 inside Docker (Phase 14)
    api_port: int = 8000

    openai_api_key: SecretStr | None = None
    openai_model_reasoning: str = ""
    openai_model_fast: str = ""
    openai_embedding_model: str = ""
    openai_embedding_dims: int = 1536
    openai_model_fallbacks: Annotated[list[str], NoDecode] = []

    telegram_bot_token: SecretStr | None = None
    telegram_allowed_chat_id: int | None = None

    google_oauth_client_file: Path | None = None
    google_token_file: Path | None = None
    google_sentry_calendar_id: str = ""

    persona_honorific: str = "sir"  # "" disables it, so blank must stay blank here
    dashboard_token: SecretStr | None = None

    activity_poll_seconds: int = 5
    activity_idle_seconds: int = 120
    activity_store_titles: bool = False

    agent_shared_secret: SecretStr | None = None
    tts_voice: str = ""
    search_provider: str = ""
    search_api_key: SecretStr | None = None
    home_assistant_url: str = ""
    home_assistant_token: SecretStr | None = None

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator(
        "openai_api_key",
        "telegram_bot_token",
        "telegram_allowed_chat_id",
        "google_oauth_client_file",
        "google_token_file",
        "dashboard_token",
        "agent_shared_secret",
        "search_api_key",
        "home_assistant_token",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        # `.env.example` ships `KEY=` lines; an empty value means "not configured".
        return None if value == "" else value

    @field_validator("openai_model_fallbacks", mode="before")
    @classmethod
    def _split_comma_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("user_tz")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {value!r}") from exc
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
