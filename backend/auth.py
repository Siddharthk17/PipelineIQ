"""JWT authentication utilities for PipelineIQ.

Provides password hashing, token creation/verification, and FastAPI
dependency functions for protecting routes with Bearer token auth.

bcrypt operations at module level use ProcessPoolExecutor to avoid
blocking the event loop. Synchronous versions are kept for non-async
contexts (scripts, tests).
"""

import asyncio
import logging
import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.requests import Request
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import User
from backend.config import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = getattr(
    settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 1440)
BCRYPT_ROUNDS = 12
security = HTTPBearer(auto_error=False)

logger = logging.getLogger(__name__)


def _bcrypt_check_sync(plain_bytes: bytes, hashed_bytes: bytes) -> bool:
    return bcrypt.checkpw(plain_bytes, hashed_bytes)


def _bcrypt_hash_sync(plain_bytes: bytes, salt: bytes) -> bytes:
    return bcrypt.hashpw(plain_bytes, salt)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password.startswith(("$2a$", "$2b$", "$2y$")):
        return False

    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except ValueError:
        return False


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=BCRYPT_ROUNDS),
    ).decode("utf-8")


# Bcrypt ops are CPU-bound; run them in a worker pool so the event loop stays
# responsive. The pool must be created *after* the gunicorn worker fork, so we
# initialise it lazily on first use. Gunicorn is configured with
# preload_app=True, which means module-level objects (including any
# ProcessPoolExecutor created at import time) are duplicated across forks and
# their worker processes become invalid as soon as the parent reaps them,
# causing "A process in the process pool was terminated abruptly" errors.
_BCRYPT_POOL: Optional[ProcessPoolExecutor] = None


def _get_bcrypt_pool() -> ProcessPoolExecutor:
    global _BCRYPT_POOL
    if _BCRYPT_POOL is None or getattr(_BCRYPT_POOL, "_broken", False):
        _BCRYPT_POOL = ProcessPoolExecutor(max_workers=2)
    return _BCRYPT_POOL


def _reset_bcrypt_pool() -> ProcessPoolExecutor:
    """Tear down the existing pool (best-effort) and start a fresh one."""
    global _BCRYPT_POOL
    try:
        if _BCRYPT_POOL is not None:
            _BCRYPT_POOL.shutdown(wait=False)
    except Exception:
        pass
    _BCRYPT_POOL = ProcessPoolExecutor(max_workers=2)
    return _BCRYPT_POOL


async def verify_password_async(plain: str, hashed: str) -> bool:
    if not hashed.startswith(("$2a$", "$2b$", "$2y$")):
        return False

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _get_bcrypt_pool(),
            _bcrypt_check_sync,
            plain.encode("utf-8"),
            hashed.encode("utf-8"),
        )
        return result
    except Exception as e:
        msg = str(e)
        if "terminated abruptly" in msg or "broken" in msg:
            logger.warning("bcrypt pool was broken, recreating")
            try:
                pool = _reset_bcrypt_pool()
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    pool,
                    _bcrypt_check_sync,
                    plain.encode("utf-8"),
                    hashed.encode("utf-8"),
                )
                return result
            except Exception as e2:
                logger.error("Password verification retry error: %s", e2)
                return False
        logger.error("Password verification error: %s", e)
        return False


async def hash_password_async(plain: str) -> str:
    loop = asyncio.get_event_loop()
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    pool = _get_bcrypt_pool()
    try:
        hashed_bytes = await loop.run_in_executor(
            pool,
            _bcrypt_hash_sync,
            plain.encode("utf-8"),
            salt,
        )
        return hashed_bytes.decode("utf-8")
    except Exception as e:
        msg = str(e)
        if "terminated abruptly" in msg or "broken" in msg:
            logger.warning("bcrypt pool was broken during hash, recreating")
            pool = _reset_bcrypt_pool()
            hashed_bytes = await loop.run_in_executor(
                pool,
                _bcrypt_hash_sync,
                plain.encode("utf-8"),
                salt,
            )
            return hashed_bytes.decode("utf-8")
        raise


def close_bcrypt_pool() -> None:
    global _BCRYPT_POOL
    if _BCRYPT_POOL is not None:
        try:
            _BCRYPT_POOL.shutdown(wait=False)
        except Exception:
            pass
        _BCRYPT_POOL = None
        logger.info("bcrypt ProcessPoolExecutor shut down")


def create_access_token(
        data: dict,
        expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "jti": str(uuid.uuid4())})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


def _revoked_token_key(jti: str) -> str:
    return f"auth:revoked:{jti}"


def _is_token_revoked(jti: str | None) -> bool:
    if not jti:
        return False
    try:
        from backend.db.redis_pools import get_cache_redis

        return bool(get_cache_redis().exists(_revoked_token_key(jti)))
    except Exception:
        logger.warning("Token revocation check failed; allowing request")
        return False


def revoke_token(token: str) -> None:
    """Best-effort Redis-backed JWT revocation until token expiry."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        jti = payload.get("jti")
        exp = payload.get("exp")
        if not jti or not exp:
            return
        expires_at = datetime.fromtimestamp(int(exp), tz=timezone.utc)
        ttl = int((expires_at - datetime.now(timezone.utc)).total_seconds())
        if ttl <= 0:
            return
        from backend.db.redis_pools import get_cache_redis

        get_cache_redis().setex(_revoked_token_key(str(jti)), ttl, "1")
    except Exception:
        logger.warning("Token revocation failed", exc_info=True)


def verify_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[ALGORITHM])
        if _is_token_revoked(payload.get("jti")):
            return None
        return payload
    except jwt.PyJWTError:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    request: Request = None,
    db: Session = Depends(get_db),
) -> User:
    token = None
    if credentials is not None:
        token = credentials.credentials

    if token is None and request is not None:
        token = request.cookies.get("pipelineiq_token")

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = verify_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    try:
        import uuid as _uuid

        uid = _uuid.UUID(user_id) if isinstance(user_id, str) else user_id
    except (ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="Invalid token payload")
    user = db.query(User).filter(User.id == uid, User.is_active).first()
    if not user:
        raise HTTPException(
            status_code=401,
            detail="User not found or inactive")
    return user


async def get_current_admin(
        current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return current_user


async def get_current_user_sse(
    token: Optional[str] = Query(
        default=None,
        description="JWT token for SSE EventSource clients that cannot set headers",
    ),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    request: Request = None,
    db: Session = Depends(get_db),
) -> User:
    raw_token = token
    if raw_token is None and credentials is not None:
        raw_token = credentials.credentials

    if raw_token is None and request is not None:
        raw_token = request.cookies.get("pipelineiq_token")

    if raw_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = verify_token(raw_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    try:
        import uuid as _uuid

        uid = _uuid.UUID(user_id) if isinstance(user_id, str) else user_id
    except (ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user = db.query(User).filter(User.id == uid, User.is_active).first()
    if not user:
        raise HTTPException(
            status_code=401,
            detail="User not found or inactive")
    return user


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> Optional[User]:
    if credentials is None:
        return None
    payload = verify_token(credentials.credentials)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    try:
        import uuid as _uuid

        uid = _uuid.UUID(user_id) if isinstance(user_id, str) else user_id
    except (ValueError, AttributeError):
        return None
    return db.query(User).filter(User.id == uid, User.is_active).first()
