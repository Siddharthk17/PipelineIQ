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


def _build_engine():
    """Build the SQLAlchemy engine based on DATABASE_URL."""
    url = settings.DATABASE_URL
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
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
    )


engine = _build_engine()

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


class Base(DeclarativeBase):
    """Declarative base class for all SQLAlchemy ORM models."""

    pass


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and ensure it is closed after use.

    This is the canonical dependency for FastAPI route handlers.
    The session is rolled back implicitly on exceptions and always closed.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all_tables() -> None:
    """Create all tables defined by ORM models.

    Called once at application startup. In production, prefer Alembic migrations.
    """
    Base.metadata.create_all(bind=engine)
