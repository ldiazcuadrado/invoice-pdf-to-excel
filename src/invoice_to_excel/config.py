"""Runtime configuration, read from INVOICE_* environment variables and an optional .env file."""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ExtractorMode = Literal["rules", "llm", "auto"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INVOICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: LogLevel = "INFO"
    log_format: Literal["text", "json"] = "text"

    extractor: ExtractorMode = "rules"
    confidence_threshold: float = Field(default=0.7, ge=0, le=1)
    csv_locale: Literal["en", "es"] = "en"

    ocr_enabled: bool = True
    ocr_language: str = "spa"
    ocr_dpi: int = Field(default=300, ge=72, le=600)
    tesseract_cmd: str | None = None

    # Sending invoice text to a third-party API requires this explicit opt-in.
    llm_enabled: bool = False
    llm_model: str = "claude-sonnet-5-5"
    llm_max_tokens: int = Field(default=2048, ge=256)
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "INVOICE_ANTHROPIC_API_KEY"),
    )

    web_host: str = "127.0.0.1"
    web_port: int = Field(default=8000, ge=1, le=65535)
    web_max_upload_mb: int = Field(default=10, ge=1)
    web_max_files: int = Field(default=50, ge=1)

    watch_settle_seconds: float = Field(default=1.0, ge=0)

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_case_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value
