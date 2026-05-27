"""FastAPI dependency injection providers.

Centralizes all dependency factories used across API routes.
Database sessions, configuration, and shared services are injected
via FastAPI's Depends() mechanism for clean testability.
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from backend.database import get_db, get_read_db, get_write_db


def get_db_dependency(db: Session = Depends(get_db)) -> Session:
    """Provide a database session dependency for route handlers.

    This wrapper exists so that the dependency can be overridden
    in tests without patching the database module directly.
    """
    return db


def get_read_db_dependency(db: Session = Depends(get_read_db)) -> Session:
    """Provide a read-only oriented database session dependency."""
    return db


def get_write_db_dependency(db: Session = Depends(get_write_db)) -> Session:
    """Provide a write-oriented database session dependency."""
    return db
