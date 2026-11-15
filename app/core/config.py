"""Centralized, environment-overridable application settings."""

from functools import lru_cache

from pydantic import Field, field_validator
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
    max_page_pixels: int = Field(default=20_000_000, gt=0)
    ocr_dpi: int = Field(default=200, ge=72, le=600)
    ocr_languages: str = Field(default="eng", min_length=1)
    ocr_timeout_seconds: float = Field(default=60.0, gt=0)
    ocr_max_concurrent_jobs: int = Field(default=2, gt=0)
    ocr_max_queued_jobs: int = Field(default=2, ge=0)
    ocr_queue_timeout_seconds: float = Field(default=10.0, gt=0)
    ocr_upload_timeout_seconds: float = Field(default=30.0, gt=0)
    ocr_document_timeout_seconds: float = Field(default=180.0, gt=0)
    ocr_shutdown_timeout_seconds: float = Field(default=10.0, gt=0)
    ocr_orientation_min_confidence: float = Field(default=80.0, ge=0, le=100)

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        level = str(value).upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        return level


@lru_cache
def get_settings() -> Settings:
    return Settings()
