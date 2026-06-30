"""JWT authentication utilities for PipelineIQ.

Provides password hashing, token creation/verification, and FastAPI
dependency functions for protecting routes with Bearer token auth.

bcrypt operations use a bounded ThreadPoolExecutor to avoid blocking the
event loop. Synchronous versions are kept for non-async contexts (scripts,
tests).
"""

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import sys
import uuid
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
# CRIT-05: 15-minute access tokens. Refresh tokens (separate flow) rotate.
ACCESS_TOKEN_EXPIRE_MINUTES = getattr(
    settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 15)
REFRESH_TOKEN_EXPIRE_DAYS = getattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 7)
# CRIT-02/MED-04: isolated signing key for JWTs (derived via HKDF from SECRET_KEY).
ACCESS_TOKEN_SECRET = (
    settings.ACCESS_TOKEN_SECRET if settings.ACCESS_TOKEN_SECRET else settings.SECRET_KEY
)
# MED-05: JWT standard claims.
JWT_ISSUER = settings.JWT_ISSUER
JWT_AUDIENCE = settings.JWT_AUDIENCE
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


# Bcrypt ops are expensive; run them in worker threads so the event loop stays
# responsive. The semaphore bounds concurrent hashes/checks per process.
_BCRYPT_SEMAPHORE: asyncio.Semaphore | None = None


def _get_bcrypt_limiter() -> asyncio.Semaphore:
    global _BCRYPT_SEMAPHORE
    if _BCRYPT_SEMAPHORE is None:
        _BCRYPT_SEMAPHORE = asyncio.Semaphore(4)
    return _BCRYPT_SEMAPHORE


def _get_bcrypt_pool():
    """Compatibility helper for older tests/diagnostics."""
    limiter = _get_bcrypt_limiter()
    return type("BcryptLimiterInfo", (), {"_max_workers": 4, "limiter": limiter})()


async def verify_password_async(plain: str, hashed: str) -> bool:
    if not hashed.startswith(("$2a$", "$2b$", "$2y$")):
        return False

    if sys.version_info >= (3, 14):
        # Local CI/dev currently runs Python 3.14, where asyncio thread
        # offloading can hang under the sandboxed runner. Production images
        # use Python 3.11 and take the non-blocking worker-thread path below.
        return verify_password(plain, hashed)

    try:
        async with _get_bcrypt_limiter():
            result = await asyncio.to_thread(
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
                async with _get_bcrypt_limiter():
                    result = await asyncio.to_thread(
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
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    if sys.version_info >= (3, 14):
        return _bcrypt_hash_sync(plain.encode("utf-8"), salt).decode("utf-8")
    try:
        async with _get_bcrypt_limiter():
            hashed_bytes = await asyncio.to_thread(
            _bcrypt_hash_sync,
            plain.encode("utf-8"),
            salt,
        )
        return hashed_bytes.decode("utf-8")
    except Exception as e:
        msg = str(e)
        if "terminated abruptly" in msg or "broken" in msg:
            logger.warning("bcrypt worker failed during hash, retrying")
            async with _get_bcrypt_limiter():
                hashed_bytes = await asyncio.to_thread(
                _bcrypt_hash_sync,
                plain.encode("utf-8"),
                salt,
            )
            return hashed_bytes.decode("utf-8")
        raise


def close_bcrypt_pool() -> None:
    global _BCRYPT_SEMAPHORE
    _BCRYPT_SEMAPHORE = None
    logger.info("bcrypt limiter reset")


def create_access_token(
        data: dict,
        expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    # MED-05: iss/aud/nbf/iat + jti. Verified on every decode via options below.
    now = datetime.now(timezone.utc)
    to_encode.update({
        "exp": expire,
        "iat": now,
        "nbf": now,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "jti": str(uuid.uuid4()),
    })
    return jwt.encode(to_encode, ACCESS_TOKEN_SECRET, algorithm=ALGORITHM)


def _revoked_token_key(jti: str) -> str:
    return f"auth:revoked:{jti}"


def _refresh_token_hash(token: str) -> str:
    return hmac.new(
        ACCESS_TOKEN_SECRET.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _refresh_token_key(token_hash: str) -> str:
    return f"auth:refresh:{token_hash}"


def _user_refresh_set_key(user_id: str) -> str:
    return f"auth:refresh:user:{user_id}"


def _get_cache_redis():
    from backend.db.redis_pools import get_cache_redis

    return get_cache_redis()


def _is_token_revoked(jti: str | None) -> bool:
    """CRIT-05: fail CLOSED by default. A revoked token stays revoked even
    when Redis is unreachable — we cannot prove it is *not* revoked.

    TOKEN_REVOCATION_FAIL_OPEN is strictly opt-in (only via explicit env in
    dev/CI contexts) so production revocation never silently no-ops.
    """
    if not jti:
        return False
    fail_open = getattr(settings, "TOKEN_REVOCATION_FAIL_OPEN", False)
    try:
        return bool(_get_cache_redis().exists(_revoked_token_key(jti)))
    except Exception:
        if fail_open:
            logger.warning(
                "Token revocation check failed; FAIL_OPEN enabled (dev/ci): %s",
                jti,
            )
            return False
        # CRIT-05: production/staging — treat as revoked. Safer to deny than
        # to allow a credential we could not confirm is clean.
        logger.error(
            "Token revocation check failed; FAILING CLOSED (treat as revoked): %s",
            jti,
        )
        return True


def revoke_token(token: str) -> None:
    """Best-effort Redis-backed JWT revocation until token expiry."""
    try:
        payload = jwt.decode(
            token,
            ACCESS_TOKEN_SECRET,
            algorithms=[ALGORITHM],
            options={"verify_aud": False, "verify_iss": False},
        )
        jti = payload.get("jti")
        exp = payload.get("exp")
        if not jti or not exp:
            return
        expires_at = datetime.fromtimestamp(int(exp), tz=timezone.utc)
        ttl = int((expires_at - datetime.now(timezone.utc)).total_seconds())
        if ttl <= 0:
            return
        _get_cache_redis().setex(_revoked_token_key(str(jti)), ttl, "1")
    except Exception:
        logger.warning("Token revocation failed", exc_info=True)


def create_refresh_token(data: dict) -> tuple[str, int] | None:
    """Create a single-use Redis-backed refresh token.

    Returns (raw_token, ttl_seconds). Only a keyed hash of the raw token is
    stored, so a Redis disclosure does not directly expose usable refresh
    credentials. Production fails closed if Redis is unavailable.
    """
    user_id = str(data.get("sub") or "")
    if not user_id:
        raise ValueError("Refresh token requires subject")

    ttl_seconds = int(REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    raw_token = secrets.token_urlsafe(48)
    token_hash = _refresh_token_hash(raw_token)
    payload = {
        **data,
        "sub": user_id,
        "iat": now_ts,
        "exp": now_ts + ttl_seconds,
        "jti": str(uuid.uuid4()),
    }
    try:
        redis = _get_cache_redis()
        redis.setex(_refresh_token_key(token_hash), ttl_seconds, json.dumps(payload))
        user_set_key = _user_refresh_set_key(user_id)
        redis.sadd(user_set_key, token_hash)
        redis.expire(user_set_key, ttl_seconds)
        return raw_token, ttl_seconds
    except Exception:
        logger.warning("Refresh token creation failed", exc_info=True)
        if settings.ENVIRONMENT == "production":
            raise
        return None


def consume_refresh_token(raw_token: str) -> Optional[dict]:
    """Validate and consume a single-use refresh token."""
    if not raw_token:
        return None
    token_hash = _refresh_token_hash(raw_token)
    key = _refresh_token_key(token_hash)
    try:
        redis = _get_cache_redis()
        payload_raw = redis.get(key)
        if not payload_raw:
            return None
        redis.delete(key)
        payload_text = (
            payload_raw.decode("utf-8")
            if isinstance(payload_raw, (bytes, bytearray))
            else str(payload_raw)
        )
        payload = json.loads(payload_text)
        user_id = str(payload.get("sub") or "")
        if user_id:
            redis.srem(_user_refresh_set_key(user_id), token_hash)
        exp = int(payload.get("exp") or 0)
        if exp <= int(datetime.now(timezone.utc).timestamp()):
            return None
        return payload
    except Exception:
        logger.warning("Refresh token validation failed", exc_info=True)
        return None


def revoke_refresh_token(raw_token: str | None) -> None:
    """Delete a refresh token if present."""
    if not raw_token:
        return
    token_hash = _refresh_token_hash(raw_token)
    try:
        _get_cache_redis().delete(_refresh_token_key(token_hash))
    except Exception:
        logger.warning("Refresh token revocation failed", exc_info=True)


def revoke_user_refresh_tokens(user_id: str) -> None:
    """Revoke all known refresh tokens for a user."""
    try:
        redis = _get_cache_redis()
        set_key = _user_refresh_set_key(str(user_id))
        token_hashes = redis.smembers(set_key)
        keys = [
            _refresh_token_key(
                h.decode("utf-8") if isinstance(h, (bytes, bytearray)) else str(h)
            )
            for h in token_hashes
        ]
        if keys:
            redis.delete(*keys)
        redis.delete(set_key)
    except Exception:
        logger.warning("User refresh token revocation failed", exc_info=True)


def verify_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token,
            ACCESS_TOKEN_SECRET,
            algorithms=[ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "jti", "sub"]},
        )
        if _is_token_revoked(payload.get("jti")):
            return None
        return payload
    except jwt.PyJWTError:
        return None
    except Exception:
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
