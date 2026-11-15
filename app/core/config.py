"""Centralized, environment-overridable application settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings used by the application and its infrastructure adapters."""

    model_config = SettingsConfigDict(extra="ignore")

    app_name: str = "Document Intelligence Service"
    app_env: str = "development"
    log_level: str = "INFO"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = Field(default="documents", min_length=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
