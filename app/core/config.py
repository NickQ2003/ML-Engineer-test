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
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    max_pdf_pages: int = Field(default=100, gt=0)
    ocr_dpi: int = Field(default=200, ge=72, le=600)
    ocr_languages: str = Field(default="eng", min_length=1)
    ocr_timeout_seconds: float = Field(default=60.0, gt=0)
    ocr_max_concurrent_jobs: int = Field(default=2, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
