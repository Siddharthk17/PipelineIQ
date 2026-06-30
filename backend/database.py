"""SQLAlchemy database engine, session factory, and declarative base.

Provides a single source of truth for database connectivity.
Sessions are created per-request via dependency injection in FastAPI
and must never be shared across threads or async boundaries.

Supports both PostgreSQL (production) and SQLite (testing).
"""

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import settings


def _build_engine(url: str):
    """Build a SQLAlchemy engine for a provided URL.

    CRIT-08: pool sizes lowered to avoid oversubscribing Postgres'
    max_connections=100 across many Gunicorn + Celery workers. Under
    PgBouncer transaction pooling, app-side connections are multiplexed
    anyway — a small pool per process is the correct posture.

    When PgBouncer transaction pooling is active we additionally disable
    prepared-statement caching (CRIT-15) so RLS session authorization
    vars cannot bleed across handoffs.
    """
    if url.startswith("sqlite"):
        return create_engine(
            url,
            echo=settings.DEBUG,
            connect_args={"check_same_thread": False},
        )

    parsed_url = make_url(url)
    is_pg = parsed_url.get_backend_name() == "postgresql"
    driver = parsed_url.get_driver_name()
    pool_size = 2
    max_overflow = 3

    engine_kwargs = dict(
        echo=settings.DEBUG,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=30,
    )
    if is_pg and driver in {"psycopg", "asyncpg"}:
        # CRIT-15: under PgBouncer transaction pooling, server-side prepared
        # statements (and the SET session.authorization DDL built on top of
        # them) bleed across handoffs. Only drivers that expose an automatic
        # prepared-statement cache receive driver-specific disable flags;
        # psycopg2 does not support these connect_args and would fail startup.
        engine_kwargs.setdefault("connect_args", {})
        if driver == "psycopg":
            engine_kwargs["connect_args"].setdefault("prepare_threshold", None)
        elif driver == "asyncpg":
            engine_kwargs["connect_args"].setdefault("statement_cache_size", 0)

    return create_engine(url, **engine_kwargs)


write_engine = _build_engine(settings.DATABASE_WRITE_URL)
read_engine = (
    write_engine
    if settings.DATABASE_READ_URL == settings.DATABASE_WRITE_URL
    else _build_engine(settings.DATABASE_READ_URL)
)

WriteSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=write_engine,
)

ReadSessionLocal = (
    WriteSessionLocal
    if read_engine is write_engine
    else sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=read_engine,
    )
)

# Backward-compat aliases
engine = write_engine
SessionLocal = WriteSessionLocal


class Base(DeclarativeBase):
    """Declarative base class for all SQLAlchemy ORM models."""


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and ensure it is closed after use.

    This is the canonical dependency for FastAPI route handlers.
    The session is rolled back implicitly on exceptions and always closed.
    """
    db = WriteSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_write_db() -> Generator[Session, None, None]:
    """Yield a write database session and ensure it is closed."""
    db = WriteSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_read_db() -> Generator[Session, None, None]:
    """Yield a read database session and ensure it is closed."""
    db = ReadSessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all_tables() -> None:
    """Create all tables defined by ORM models.

    Called once at application startup. In production, prefer Alembic migrations.
    """
    Base.metadata.create_all(bind=write_engine)


def ensure_pipeline_status_values() -> None:
    """Ensure CONTRACT_VIOLATION exists in the pipelinestatus Postgres enum.

    ALTER TYPE … ADD VALUE cannot run inside a transaction. When pgbouncer
    pools connections in transaction mode this causes the migration to fail
    silently. This function runs at startup, outside any migration transaction,
    connecting directly to Postgres to guarantee the value exists regardless
    of whether the database was freshly created or restored from backup.
    """
    from sqlalchemy import text

    if "postgresql" not in str(write_engine.url):
        return

    required_values = {
        "CONTRACT_VIOLATION",
    }

    with write_engine.connect() as conn:
        conn.execute(text("COMMIT"))
        try:
            existing_rows = conn.execute(
                text("SELECT unnest(enum_range(NULL::pipelinestatus))")
            ).fetchall()
            existing = {r[0] for r in existing_rows}
            for value in sorted(required_values - existing):
                conn.execute(
                    text(f"ALTER TYPE pipelinestatus ADD VALUE '{value}'")
                )
                import logging
                logging.getLogger(__name__).info(
                    "Added pipelinestatus value: %s", value
                )
        finally:
            conn.execute(text("BEGIN"))
