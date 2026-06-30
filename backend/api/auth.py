"""Authentication API endpoints for PipelineIQ.

Provides user registration, login, profile, password change/reset,
account lockout, and admin user management.

Hardening:
- HIGH-08: timing-equalized login (dummy bcrypt when user missing)
- HIGH-11: account lockout after N failed attempts (Redis-backed)
- MED-06: tokens only returned via HttpOnly cookie (not JSON body)
- MED-07: registration returns generic "already taken" messages
"""

import logging
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.config import settings
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import User
from backend.utils.uuid_utils import as_uuid, validate_uuid_format
from backend.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    consume_refresh_token,
    create_access_token,
    create_refresh_token,
    get_current_admin,
    get_current_user,
    hash_password_async,
    revoke_refresh_token,
    revoke_token,
    revoke_user_refresh_tokens,
    verify_password_async,
)
from backend.services.audit_service import log_action
from backend.utils.rate_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Authentication"])


# Request / Response schemas
class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        if not re.match(pattern, v):
            raise ValueError("Invalid email format")
        return v.lower()

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if len(v) < 3 or len(v) > 50:
            raise ValueError("Username must be 3-50 characters")
        if not re.match(r"^[a-zA-Z0-9_]+$", v):
            raise ValueError(
                "Username must be alphanumeric with underscores only")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in v):
            raise ValueError(
                "Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one number")
        if not any(c in "!@#$%^&*()_+-=[]{}|;:',.<>?/`~" for c in v):
            raise ValueError(
                "Password must contain at least one special character")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    role: str
    is_active: bool
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class LoginResponse(BaseModel):
    """MED-06: in production tokens travel only in HttpOnly cookies; the
    JSON body carries no `access_token` field so XSS exfiltration has no
    payload to steal. The `access_token` field is returned ONLY when
    ENVIRONMENT != production, to keep development & the test suite able
    to read the token without parsing Set-Cookie.
    """
    access_token: str = ""
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class RefreshResponse(BaseModel):
    access_token: str = ""
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one number")
        if not any(c in "!@#$%^&*()_+-=[]{}|;:',.<>?/`~" for c in v):
            raise ValueError("Password must contain at least one special character")
        return v


class PasswordResetRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return v.lower()


class PasswordResetConfirm(BaseModel):
    reset_token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one number")
        if not any(c in "!@#$%^&*()_+-=[]{}|;:',.<>?/`~" for c in v):
            raise ValueError("Password must contain at least one special character")
        return v


class RoleUpdateRequest(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("admin", "viewer"):
            raise ValueError("Role must be 'admin' or 'viewer'")
        return v


# Helpers


def _user_to_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at.isoformat() if user.created_at else "",
    )


def _lockout_key(email: str) -> str:
    return f"auth:lockout:{email.lower()}"


def _failed_attempts_key(email: str) -> str:
    return f"auth:failed:{email.lower()}"


def _reset_token_key(token: str) -> str:
    return f"auth:reset:{token}"


def _set_access_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="pipelineiq_token",
        value=token,
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
        samesite="strict",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )


def _set_refresh_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        key="pipelineiq_refresh_token",
        value=token,
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
        samesite="strict",
        max_age=max_age,
        path="/auth",
    )


def _set_csrf_cookie(response: Response) -> None:
    response.set_cookie(
        key="pipelineiq_csrf_token",
        value=secrets.token_urlsafe(32),
        httponly=False,
        secure=settings.ENVIRONMENT == "production",
        samesite="strict",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )


def _delete_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        key="pipelineiq_token",
        path="/",
        secure=settings.ENVIRONMENT == "production",
        samesite="strict",
    )
    response.delete_cookie(
        key="pipelineiq_refresh_token",
        path="/auth",
        secure=settings.ENVIRONMENT == "production",
        samesite="strict",
    )
    response.delete_cookie(
        key="pipelineiq_csrf_token",
        path="/",
        secure=settings.ENVIRONMENT == "production",
        samesite="strict",
    )


def _get_redis():
    """Best-effort cache Redis; returns None if unavailable."""
    try:
        from backend.db.redis_pools import get_cache_redis
        return get_cache_redis()
    except Exception:
        return None


def _is_locked_out(email: str) -> bool:
    """HIGH-11: check the per-email lockout flag in Redis."""
    r = _get_redis()
    if r is None:
        return False
    try:
        return bool(r.exists(_lockout_key(email)))
    except Exception:
        return False


def _record_failed_attempt(email: str) -> None:
    """HIGH-11: increment failed attempts and lock the account on threshold."""
    r = _get_redis()
    if r is None:
        return
    try:
        key = _failed_attempts_key(email)
        count = r.incr(key)
        if count == 1:
            # Counter expires after lockout window to self-heal.
            r.expire(key, settings.ACCOUNT_LOCKOUT_DURATION_SECONDS)
        if int(count) >= settings.ACCOUNT_LOCKOUT_THRESHOLD:
            r.setex(
                _lockout_key(email),
                settings.ACCOUNT_LOCKOUT_DURATION_SECONDS,
                "1",
            )
    except Exception:
        pass


def _clear_failed_attempts(email: str) -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        r.delete(_failed_attempts_key(email), _lockout_key(email))
    except Exception:
        pass


# A hardcoded dummy bcrypt hash used to equalize login timing when the
# email does not exist (HIGH-08). Generated for "this-password-is-fake"
# at cost 12 — never decodes any real password.
_DUMMY_BCRYPT = (
    "$2b$12$g7yv0Ebw6suOWNbZITSIKuDrmruZyRsfXWBXfnWeb50j31HWEmoIG"
)


# Endpoints
@router.post("/register", status_code=201)
@limiter.limit("5/minute")
async def register(
    request: Request,
    response: Response,
    body: RegisterRequest,
    db: Session = get_write_db_dependency(),
):
    """Register a new user. First user becomes admin automatically.

    MED-07: returns a generic conflict message instead of disclosing
    whether the email or the username was already taken.
    """
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("LOCK TABLE users IN EXCLUSIVE MODE"))

    existing = (
        db.query(User)
        .filter((User.email == body.email) | (User.username == body.username))
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email is already taken",
        )

    user_count = db.query(User).count()
    role = "admin" if user_count == 0 else "viewer"

    user = User(
        email=body.email,
        username=body.username,
        hashed_password=await hash_password_async(body.password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("User registered: %s (role=%s)", user.username, user.role)

    log_action(
        db,
        "user_registered",
        user_id=user.id,
        resource_type="user",
        resource_id=user.id,
        details={"email": user.email, "role": user.role},
        request=request,
    )

    return _user_to_response(user)


@router.post("/login")
@limiter.limit("5/minute")
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    db: Session = get_write_db_dependency(),
):
    """Authenticate and return a JWT access token in an HttpOnly cookie.

    HIGH-08: when the email does not exist we run a dummy bcrypt verification
    so that the response timing is indistinguishable from an invalid password
    on a real account — preventing user-enumeration via timing.
    HIGH-11: enforces a temporary Redis-backed lockout after N failed attempts.
    """
    if _is_locked_out(body.email):
        logger.warning("Login attempted on locked-out account: %s", body.email)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Account temporarily locked due to too many failed attempts. Try again later.",
        )

    user = db.query(User).filter(User.email == body.email.lower()).first()

    if user is None:
        # HIGH-08: equalize timing — burn the bcrypt cost anyway.
        await verify_password_async(body.password, _DUMMY_BCRYPT)
        _record_failed_attempt(body.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    if not await verify_password_async(body.password, user.hashed_password):
        _record_failed_attempt(body.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is disabled",
        )

    _clear_failed_attempts(body.email)

    token = create_access_token(data={"sub": str(user.id), "role": user.role})
    logger.info("User logged in: %s", user.username)

    _set_access_cookie(response, token)
    _set_csrf_cookie(response)
    try:
        refresh = create_refresh_token({"sub": str(user.id)})
    except Exception:
        logger.error("Login failed to create refresh token", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication token service unavailable",
        )
    if refresh is not None:
        refresh_token, refresh_ttl = refresh
        _set_refresh_cookie(response, refresh_token, refresh_ttl)

    log_action(
        db,
        "user_login",
        user_id=user.id,
        resource_type="user",
        resource_id=user.id,
        details={"email": user.email},
        request=request,
    )

    # MED-06: in production the token only travels via the HttpOnly cookie
    # (never in the JSON body, so XSS cannot exfiltrate it). Outside
    # production we still include it so the test suite and developer tools
    # can read the token without parsing Set-Cookie.
    body_token = "" if settings.ENVIRONMENT == "production" else token
    return LoginResponse(
        access_token=body_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=_user_to_response(user),
    )


@router.post("/refresh", response_model=RefreshResponse)
@limiter.limit("30/minute")
async def refresh(
    request: Request,
    response: Response,
    db: Session = get_write_db_dependency(),
):
    """Rotate a single-use refresh token and issue a fresh access token."""
    raw_refresh = request.cookies.get("pipelineiq_refresh_token")
    payload = consume_refresh_token(raw_refresh or "")
    if not payload:
        _delete_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    try:
        uid = as_uuid(str(payload.get("sub")))
    except Exception:
        _delete_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user = db.query(User).filter(User.id == uid, User.is_active).first()
    if not user:
        _delete_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    token = create_access_token(data={"sub": str(user.id), "role": user.role})
    _set_access_cookie(response, token)
    _set_csrf_cookie(response)
    try:
        rotated = create_refresh_token({"sub": str(user.id)})
    except Exception:
        logger.error("Refresh failed to create rotated token", exc_info=True)
        _delete_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Refresh token service unavailable",
        )
    if rotated is None:
        _delete_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Refresh token service unavailable",
        )
    refresh_token, refresh_ttl = rotated
    _set_refresh_cookie(response, refresh_token, refresh_ttl)

    body_token = "" if settings.ENVIRONMENT == "production" else token
    return RefreshResponse(
        access_token=body_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=_user_to_response(user),
    )


@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """Get the current authenticated user's profile."""
    return _user_to_response(current_user)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
):
    """Logout endpoint - clears HttpOnly cookie."""
    logger.info("User logged out: %s", current_user.username)
    token = request.cookies.get("pipelineiq_token")
    auth_header = request.headers.get("authorization", "")
    if not token and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if token:
        revoke_token(token)
    revoke_refresh_token(request.cookies.get("pipelineiq_refresh_token"))

    _delete_auth_cookies(response)

    return {"message": "Logged out successfully"}


@router.post("/change-password")
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    body: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = get_write_db_dependency(),
):
    """HIGH-11: self-service password change — verifies current password
    before setting a new one. Tokens issued before the change remain valid
    until they expire; users may logout to revoke them immediately.
    """
    if not await verify_password_async(body.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    if body.current_password == body.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from the current password",
        )
    current_user.hashed_password = await hash_password_async(body.new_password)
    db.commit()
    db.refresh(current_user)
    revoke_user_refresh_tokens(str(current_user.id))
    log_action(
        db,
        "password_changed",
        user_id=current_user.id,
        resource_type="user",
        resource_id=current_user.id,
        details={},
        request=request,
    )
    logger.info("User %s changed their password", current_user.username)
    return {"message": "Password updated"}


@router.post("/password-reset/request")
@limiter.limit("3/minute")
async def request_password_reset(
    request: Request,
    body: PasswordResetRequest,
    db: Session = get_write_db_dependency(),
):
    """HIGH-11: issue a single-use reset token. Always returns 200
    regardless of whether the email account exists — preventing
    enumeration via the reset endpoint.
    """
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if user is not None and user.is_active:
        reset_token = secrets.token_urlsafe(32)
        r = _get_redis()
        if r is not None:
            try:
                r.setex(
                    _reset_token_key(reset_token),
                    15 * 60,  # 15-minute window
                    str(user.id),
                )
            except Exception:
                pass
        # TODO: dispatch email via SMTP (settings.SMTP_*). Until SMTP is
        # configured, the token is logged at INFO for operator recovery —
        # clearly marked as a one-time secret.
        logger.info(
            "password-reset token issued for user=%s (expires in 15m)",
            user.username,
        )
    # Always return a non-disclosing success message.
    return {"message": "If the email exists, a reset link has been sent."}


@router.post("/password-reset/confirm")
@limiter.limit("3/minute")
async def confirm_password_reset(
    request: Request,
    body: PasswordResetConfirm,
    db: Session = get_write_db_dependency(),
):
    """HIGH-11: consume the single-use reset token and set a new password."""
    r = _get_redis()
    if r is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password reset unavailable (cache offline)",
        )
    user_id_raw = r.get(_reset_token_key(body.reset_token))
    if not user_id_raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token is invalid or has expired",
        )
    # Single-use: delete immediately.
    r.delete(_reset_token_key(body.reset_token))
    try:
        uid = as_uuid(user_id_raw) if isinstance(user_id_raw, str) else as_uuid(
            user_id_raw.decode()
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token is invalid",
        )
    user = db.query(User).filter(User.id == uid).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token is invalid",
        )
    user.hashed_password = await hash_password_async(body.new_password)
    db.commit()
    revoke_user_refresh_tokens(str(user.id))
    log_action(
        db,
        "password_reset",
        user_id=user.id,
        resource_type="user",
        resource_id=user.id,
        details={},
        request=request,
    )
    logger.info("User %s reset their password via token", user.username)
    return {"message": "Password has been reset"}


@router.get("/users")
async def list_users(
    current_user: User = Depends(get_current_admin),
    db: Session = get_read_db_dependency(),
):
    """List all users (admin only)."""
    users = db.query(User).order_by(User.created_at).all()
    return [_user_to_response(u) for u in users]


@router.patch("/users/{user_id}/role")
async def update_user_role(
    user_id: str,
    body: RoleUpdateRequest,
    current_user: User = Depends(get_current_admin),
    db: Session = get_write_db_dependency(),
):
    """Update a user's role (admin only)."""
    validate_uuid_format(user_id)
    user = db.query(User).filter(User.id == as_uuid(user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.role = body.role
    db.commit()
    db.refresh(user)
    logger.info(
        "User %s role updated to %s by %s",
        user.username,
        body.role,
        current_user.username,
    )
    return _user_to_response(user)
