"""SQLAlchemy database engine, session factory, and declarative base.

Provides a single source of truth for database connectivity.
Sessions are created per-request via dependency injection in FastAPI
and must never be shared across threads or async boundaries.
"""

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import settings


engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args=(
        {"check_same_thread": False}
        if settings.DATABASE_URL.startswith("sqlite")
        else {}
    ),
)

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
