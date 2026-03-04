"""Application configuration loaded from environment variables.

Uses Pydantic BaseSettings for type-safe configuration with validation
at import time. The application crashes at startup if any required
setting is missing or invalid, preventing runtime configuration errors.
"""

import logging
from pathlib import Path
from typing import List

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class Settings(BaseSettings):
    """PipelineIQ application settings.

    All settings are loaded from environment variables or a .env file.
    Every setting has a sensible default or is derived from another setting.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    APP_NAME: str = "PipelineIQ"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str = "postgresql://postgres:pipelineiq_dev_password@localhost:5432/pipelineiq"
    SECRET_KEY: str = "change-me-in-production"

    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""

    UPLOAD_DIR: Path = Path("./uploads")
    MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024  # 50 MB
    ALLOWED_EXTENSIONS: frozenset = frozenset({".csv", ".json"})

    MAX_PIPELINE_STEPS: int = 50
    MAX_ROWS_PER_FILE: int = 1_000_000
    STEP_TIMEOUT_SECONDS: int = 300

    API_PREFIX: str = "/api/v1"
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]

    # Caching
    CACHE_TTL_STATS: int = 30

    # Rate Limiting
    RATE_LIMIT_PIPELINE_RUN: str = "10/minute"
    RATE_LIMIT_FILE_UPLOAD: str = "30/minute"
    RATE_LIMIT_VALIDATION: str = "60/minute"
    RATE_LIMIT_READ: str = "120/minute"

    # Schema Drift
    DRIFT_DETECTION_ENABLED: bool = True

    # Versioning
    MAX_VERSIONS_PER_PIPELINE: int = 50

    # PostgreSQL (used by docker-compose, not directly by app)
    POSTGRES_PASSWORD: str = ""

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Ensure LOG_LEVEL is a valid Python logging level."""
        normalized = value.upper()
        if normalized not in VALID_LOG_LEVELS:
            raise ValueError(
                f"Invalid LOG_LEVEL '{value}'. "
                f"Must be one of: {sorted(VALID_LOG_LEVELS)}"
            )
        return normalized

    @field_validator("UPLOAD_DIR")
    @classmethod
    def ensure_upload_dir_exists(cls, value: Path) -> Path:
        """Create the upload directory if it does not exist."""
        value.mkdir(parents=True, exist_ok=True)
        return value

    @model_validator(mode="after")
    def set_celery_defaults(self) -> "Settings":
        """Default Celery broker and backend URLs to REDIS_URL if not set."""
        if not self.CELERY_BROKER_URL:
            self.CELERY_BROKER_URL = self.REDIS_URL
        if not self.CELERY_RESULT_BACKEND:
            self.CELERY_RESULT_BACKEND = self.REDIS_URL
        return self


# Module-level singleton — validated at import time.
# If configuration is invalid, the application crashes here with a clear error.
settings = Settings()

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
