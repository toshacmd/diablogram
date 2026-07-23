from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    database_url: str = "postgresql+asyncpg://diablogram:diablogram@localhost:5432/diablogram"

    telegram_api_id: int = 0
    telegram_api_hash: str = ""

    notifier_bot_token: str = ""
    notifier_owner_chat_id: str = ""

    ai_provider: str = "anthropic"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    # Override for OpenAI-compatible third-party providers/resellers (e.g. NordRouter).
    # Leave empty to use the official OpenAI API.
    openai_base_url: str = ""

    session_encryption_key: str = ""

    web_host: str = "0.0.0.0"
    web_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
