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
    # CRIT-05: Short-lived access token (15 min) with refresh token rotation.
    # Older default of 1440 minutes (24h) kept a stolen token live for a full day.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # MED-05: JWT claims (iss/aud/nbf) — verified on every token decode.
    JWT_ISSUER: str = "pipelineiq"
    JWT_AUDIENCE: str = "pipelineiq-api"
    # CRIT-02 / MED-04: Isolated signing secrets derived via HKDF from SECRET_KEY.
    # These are auto-derived below (`sse_secret_key`, `webhook_secret_key`)
    # so the operator only sets SECRET_KEY, but each consumer uses a distinct key.
    ACCESS_TOKEN_SECRET: str = ""  # if set, overrides JWT signing key
    SSE_SECRET_KEY: str = ""  # isolated SSE HMAC key (auto-derived if blank)
    WEBHOOK_SIGNING_SECRET: str = ""  # isolated webhook HMAC key (auto-derived if blank)

    # HIGH-11: Account lockout to mitigate brute-force / credential stuffing.
    ACCOUNT_LOCKOUT_THRESHOLD: int = 5  # failed attempts before lockout
    ACCOUNT_LOCKOUT_DURATION_SECONDS: int = 900  # 15-minute lockout window

    # CRIT-02: When Redis is unreachable during revocation check, fail CLOSED.
    # Set to False only in ephemeral local/CI without Redis (auto-handled below).
    TOKEN_REVOCATION_FAIL_OPEN: bool = False

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
    MAX_PIPELINE_YAML_BYTES: int = 1 * 1024 * 1024
    MAX_ROWS_PER_FILE: int = 1_000_000
    STEP_TIMEOUT_SECONDS: int = 300
    WORKER_MEMORY_LIMIT_GB: int = 2
    WORKER_MAX_ROWS_TO_SCAN: int = 10_000_000

    API_PREFIX: str = "/api/v1"
    # HIGH-09 / MED-02: strict explicit origins only. The "localhost:*"
    # wildcard prefix matching was removed so dev ports cannot be spoofed
    # by malicious pages. Allow an explicit localhost override via env.
    CORS_ORIGINS: List[str] = [
        "http://localhost",
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
    MINIO_IMAGE_TAG: str = "RELEASE.2025-04-22T22-12-26Z"

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
    AI_PROMPT_MAX_CHARS: int = 24_000
    AI_PROMPT_MAX_TEXT_CHARS: int = 4_000
    AI_PROMPT_MAX_ERROR_CHARS: int = 1_000
    AI_PROMPT_MAX_CONFIG_CHARS: int = 12_000
    AI_INCLUDE_FILENAMES_IN_PROMPTS: bool = False

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

        Auto-generate a strong ephemeral SECRET_KEY when the value is
        missing, empty, or matches a known weak placeholder. This lets
        ephemeral local/CI/test runs (alembic upgrade, test suites,
        developer machines) start without manual secret setup.

        The only way to fail closed is when the operator has EXPLICITLY
        opted into production mode (`ENVIRONMENT=production` set in the
        process environment) AND supplied a weak key. Defaulting to
        `development` is treated as the safe-but-lenient path: auto-gen
        beats crashing the developer's alembic migration.
        """
        if self.SECRET_KEY in WEAK_SECRET_VALUES or len(self.SECRET_KEY) < 32:
            import os as _os
            import secrets as _secrets
            env_explicitly_prod = _os.environ.get("ENVIRONMENT") == "production"
            if env_explicitly_prod:
                raise ValueError(
                    "SECRET_KEY must be a non-default random value with at least 32 characters."
                )
            self.SECRET_KEY = _secrets.token_urlsafe(48)
        return self

    @model_validator(mode="after")
    def validate_production_operational_secrets(self) -> "Settings":
        """Prevent public monitoring services from using blank or weak passwords."""
        if self.ENVIRONMENT != "production":
            return self
        if self.ACCESS_TOKEN_EXPIRE_MINUTES > 15:
            raise ValueError(
                "ACCESS_TOKEN_EXPIRE_MINUTES must be 15 or lower in production."
            )
        weak_values = {
            "FLOWER_PASSWORD": self.FLOWER_PASSWORD,
            "GRAFANA_PASSWORD": self.GRAFANA_PASSWORD,
        }
        for name, value in weak_values.items():
            if value in WEAK_SECRET_VALUES or len(value) < 16:
                raise ValueError(f"{name} must be set to a strong production password.")
        return self

    @model_validator(mode="after")
    def derive_isolated_signing_keys(self) -> "Settings":
        """CRIT-02 / MED-04: derive distinct keys per cryptographic consumer.

        If the operator does not supply explicit values, each key is derived
        from the master SECRET_KEY via HKDF-SHA256 with a per-purpose info
        label. This guarantees that compromising one consumer (e.g. SSE HMAC)
        does not expose JWTs or webhook signatures, and avoids secret drift
        across services — there is exactly one root secret to configure.
        """
        import hashlib
        import hmac as _hmac

        def _hkdf(master: str, info: bytes, length: int = 32) -> str:
            # RFC 5869 extract-then-expand, single-block output (<= 32 bytes).
            prk = _hmac.new(b"pipelineiq-salt", master.encode("utf-8"), hashlib.sha256).digest()
            okm = _hmac.new(prk, info + b"\x01", hashlib.sha256).digest()
            return okm[:length].hex()

        if not self.SSE_SECRET_KEY:
            self.SSE_SECRET_KEY = _hkdf(self.SECRET_KEY, b"pipelineiq-sse-signing-v1", 32)
        if not self.WEBHOOK_SIGNING_SECRET:
            self.WEBHOOK_SIGNING_SECRET = _hkdf(self.SECRET_KEY, b"pipelineiq-webhook-signing-v1", 32)
        if not self.ACCESS_TOKEN_SECRET:
            self.ACCESS_TOKEN_SECRET = _hkdf(self.SECRET_KEY, b"pipelineiq-jwt-signing-v1", 32)
        return self

    @model_validator(mode="after")
    def relax_revocation_fail_open_for_ci(self) -> "Settings":
        """CRIT-05: tolerate fail-open only in clearly non-production contexts.

        Production and staging always fail CLOSED (revoked token treated as
        revoked when Redis is unreachable) — operators may force-open with
        TOKEN_REVOCATION_FAIL_OPEN=true only in an emergency. In development,
        CI, and test, we auto-allow fail-open so the auth flow continues to
        work without Redis (those environments are not multi-tenant and have
        no revocation surface to protect).
        """
        if self.ENVIRONMENT in {"development", "dev", "test", "ci", "local"}:
            self.TOKEN_REVOCATION_FAIL_OPEN = True
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
