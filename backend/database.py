"""SQLAlchemy database engine, session factory, and declarative base.

Provides a single source of truth for database connectivity.
Sessions are created per-request via dependency injection in FastAPI
and must never be shared across threads or async boundaries.

Supports both PostgreSQL (production) and SQLite (testing).
"""

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import settings


def _build_engine(url: str):
    """Build a SQLAlchemy engine for a provided URL."""
    if url.startswith("sqlite"):
        return create_engine(
            url,
            echo=settings.DEBUG,
            connect_args={"check_same_thread": False},
        )
    # PostgreSQL with connection pooling
    return create_engine(
        url,
        echo=settings.DEBUG,
        pool_size=2,
        max_overflow=3,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


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

    pass


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
