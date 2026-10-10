"""Service configuration, read from the environment."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAYLOAD_CACHE_")

    database_url: str = "sqlite+aiosqlite:///./payload_cache.db"
    # Only shapes the stand-in transformer; a real service brings its own latency.
    transformer_delay_seconds: float = Field(default=0.1, ge=0)
