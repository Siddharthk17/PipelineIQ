"""Application configuration loaded from environment variables.

Uses Pydantic BaseSettings for type-safe configuration with validation
at import time. The application crashes at startup if any required
setting is missing or invalid, preventing runtime configuration errors.
"""

import logging
import sys
from pathlib import Path
from typing import List, Optional

import structlog
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
WEAK_SECRET_VALUES = frozenset(
    {
        "",
        "change-me",
        "change-me-in-production",
        "change-me-in-development",
        "ci_test_secret_key_not_for_production",
        "pipelineiq-dev-secret-key-change-in-production-2024-minimum-32chars",
        "pipelineiq-dev-jwt-secret-change-in-production-2024",
    }
)
MAX_SCHEDULE_CONFIG_BYTES = 51200


class Settings(BaseSettings):
    """PipelineIQ application settings.

    All settings are loaded from environment variables or a .env file.
    Every setting has a sensible default or is derived from another setting.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        # docker-compose-only vars (REPLICATION_USER, MINIO_ROOT_USER, etc.)
        # coexist in .env
    )

    APP_NAME: str = "PipelineIQ"
    APP_VERSION: str = "12.7.3"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    AUTO_CREATE_TABLES: bool = False

    DATABASE_URL: str = (
        "postgresql://postgres:pipelineiq_dev_password@localhost:5432/pipelineiq"
    )
    DATABASE_WRITE_URL: str = ""
    DATABASE_READ_URL: str = ""
    READ_REPLICA_HOST: str = "localhost"
    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    REDIS_BROKER_URL: str = "redis://localhost:6379/0"
    REDIS_BACKEND_URL: str = "redis://localhost:6379/1"
    REDIS_PUBSUB_URL: str = "redis://localhost:6380/0"
    REDIS_CACHE_URL: str = "redis://localhost:6381/0"
    REDIS_YJS_URL: str = "redis://localhost:6382/0"

    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""
    CELERY_WORKERS_CRITICAL: int = 2
    CELERY_WORKERS_DEFAULT: int = 3
    CELERY_WORKERS_BULK: int = 2

    UPLOAD_DIR: Path = Path("./uploads")
    MAX_UPLOAD_SIZE: int = 500 * 1024 * 1024  # 500 MB
    ALLOWED_EXTENSIONS: frozenset = frozenset({".csv", ".json", ".parquet", ".xlsx"})

    MAX_PIPELINE_STEPS: int = 50
    MAX_ROWS_PER_FILE: int = 1_000_000
    STEP_TIMEOUT_SECONDS: int = 300
    WORKER_MEMORY_LIMIT_GB: int = 2
    WORKER_MAX_ROWS_TO_SCAN: int = 10_000_000

    API_PREFIX: str = "/api/v1"
    CORS_ORIGINS: List[str] = [
        "http://localhost",
        "http://localhost:80",
        "http://localhost:3000",
        "https://pipeline-iq0.vercel.app",
        "https://pipelineiq-api.onrender.com",
    ]

    # Caching
    CACHE_TTL_STATS: int = 30

    # Rate Limiting
    RATE_LIMIT_PIPELINE_RUN: str = "10/minute"
    RATE_LIMIT_FILE_UPLOAD: str = "30/minute"
    RATE_LIMIT_VALIDATION: str = "60/minute"
    RATE_LIMIT_READ: str = "120/minute"

    # Schema Drift
    DRIFT_DETECTION_ENABLED: bool = True

    # Data Profiling
    PROFILE_MAX_ROWS: int = 1_000_000
    PROFILE_SAMPLE_ROWS: int = 100_000

    # Autonomous healing
    AUTONOMOUS_HEALING_ENABLED: bool = True
    AUTONOMOUS_HEALING_MAX_ATTEMPTS: int = 3

    # Versioning
    MAX_VERSIONS_PER_PIPELINE: int = 50

    # Flower (Celery monitoring)
    FLOWER_USER: str = "admin"
    FLOWER_PASSWORD: str = ""

    # Grafana
    GRAFANA_USER: str = "admin"
    GRAFANA_PASSWORD: str = ""

    # S3 / MinIO Storage
    STORAGE_TYPE: str = "local"  # "local" or "s3"
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_BUCKET: str = "pipelineiq-outputs"  # Dedicated bucket for pipeline outputs
    S3_ENDPOINT_URL: Optional[str] = None  # Required for MinIO
    WASM_BUCKET: str = "pipelineiq-wasm"

    # Sentry
    SENTRY_DSN: str = ""
    ENVIRONMENT: str = "development"
    MINIO_IMAGE_TAG: str = "latest"

    # OpenTelemetry
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://jaeger:4317"
    OTEL_SAMPLE_RATE: float = 0.1
    OTEL_ENABLED: bool = True
    OTEL_SERVICE_NAME: str = "pipelineiq-api"
    JAEGER_UI_URL: str = "http://localhost:16686"

    # Gemini AI
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_FALLBACK_MODELS: str = "gemini-2.0-flash,gemini-1.5-flash"

    # Email (SMTP)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    SMTP_TIMEOUT: int = 10

    # PostgreSQL (used by docker-compose, not directly by app)
    POSTGRES_PASSWORD: str = ""
    POSTGRES_USER: str = "pipelineiq"
    POSTGRES_DB: str = "pipelineiq"

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
    def set_database_defaults(self) -> "Settings":
        """Default read/write database URLs to DATABASE_URL when not set."""
        if not self.DATABASE_WRITE_URL:
            self.DATABASE_WRITE_URL = self.DATABASE_URL
        if not self.DATABASE_READ_URL:
            self.DATABASE_READ_URL = self.DATABASE_WRITE_URL
        return self

    @model_validator(mode="after")
    def set_celery_defaults(self) -> "Settings":
        """Default Celery broker/backend URLs to their dedicated Redis roles."""
        if not self.CELERY_BROKER_URL:
            self.CELERY_BROKER_URL = self.REDIS_BROKER_URL
        if (
            not self.CELERY_RESULT_BACKEND
            or self.CELERY_RESULT_BACKEND == self.REDIS_BROKER_URL
        ):
            self.CELERY_RESULT_BACKEND = self.REDIS_BACKEND_URL
        return self

    @model_validator(mode="after")
    def validate_secret_key_strength(self) -> "Settings":
        """Prevent startup with default or weak signing keys.

        Production environments MUST have a strong, non-default SECRET_KEY.
        Non-production environments (development, CI, test) auto-generate
        a strong ephemeral key when one is not provided, so ephemeral
        local/CI runs work without manual secret setup while production
        still gets a hard fail.
        """
        if self.SECRET_KEY in WEAK_SECRET_VALUES or len(self.SECRET_KEY) < 32:
            if self.ENVIRONMENT == "production":
                raise ValueError(
                    "SECRET_KEY must be a non-default random value with at least 32 characters."
                )
            import secrets as _secrets
            self.SECRET_KEY = _secrets.token_urlsafe(48)
        return self

    @model_validator(mode="after")
    def validate_production_operational_secrets(self) -> "Settings":
        """Prevent public monitoring services from using blank or weak passwords."""
        if self.ENVIRONMENT != "production":
            return self
        weak_values = {
            "FLOWER_PASSWORD": self.FLOWER_PASSWORD,
            "GRAFANA_PASSWORD": self.GRAFANA_PASSWORD,
        }
        for name, value in weak_values.items():
            if value in WEAK_SECRET_VALUES or len(value) < 16:
                raise ValueError(f"{name} must be set to a strong production password.")
        return self


# Module-level singleton — validated at import time.
# If configuration is invalid, the application crashes here with a clear error.
settings = Settings()

DATABASE_WRITE_URL = settings.DATABASE_WRITE_URL
DATABASE_READ_URL = settings.DATABASE_READ_URL


def _configure_logging() -> None:
    """Configure structured JSON logging for production, readable for development."""
    log_level = getattr(logging, settings.LOG_LEVEL)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if settings.ENVIRONMENT == "production":
        # JSON output for production — parseable by Grafana Loki, Datadog, etc.
        processors = shared_processors + [
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Human-readable console output for development
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )

    # Bridge standard logging into structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Celery overrides the root logger level to WARNING on worker startup.
    # Force it back to the configured level so structlog INFO/DEBUG messages
    # are not silently dropped.
    logging.getLogger().setLevel(log_level)


_configure_logging()
