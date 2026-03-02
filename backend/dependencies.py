"""FastAPI dependency injection providers.

Centralizes all dependency factories used across API routes.
Database sessions, configuration, and shared services are injected
via FastAPI's Depends() mechanism for clean testability.
"""

# Third-party packages
from fastapi import Depends
from sqlalchemy.orm import Session

# Internal modules
from backend.database import get_db


def get_db_dependency() -> Session:
    """Provide a database session dependency for route handlers.

    This wrapper exists so that the dependency can be overridden
    in tests without patching the database module directly.

    Returns:
        A Depends() wrapper around the canonical get_db generator.
    """
    return Depends(get_db)
