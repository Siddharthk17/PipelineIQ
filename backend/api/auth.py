"""Authentication API endpoints for PipelineIQ.

Provides user registration, login, profile, and admin user management.
"""

import re
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import User
from backend.utils.uuid_utils import as_uuid, validate_uuid_format
from backend.auth import (
    get_password_hash,
    verify_password,
    create_access_token,
    get_current_user,
    get_current_admin,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from backend.services.audit_service import log_action
from backend.utils.rate_limiter import limiter
from backend.config import settings

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
            raise ValueError("Username must be alphanumeric with underscores only")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one number")
        if not any(c in "!@#$%^&*()_+-=[]{}|;:',.<>?/`~" for c in v):
            raise ValueError("Password must contain at least one special character")
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

    class Config:
        from_attributes = True


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


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


# Endpoints
@router.post("/register", status_code=201)
@limiter.limit("5/minute")
def register(
    request: Request,
    response: Response,
    body: RegisterRequest,
    db: Session = get_write_db_dependency(),
):
    """Register a new user. First user becomes admin automatically."""
    # Check uniqueness
    existing = (
        db.query(User)
        .filter((User.email == body.email) | (User.username == body.username))
        .first()
    )
    if existing:
        field = "email" if existing.email == body.email else "username"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A user with this {field} already exists",
        )

    # First user becomes admin
    user_count = db.query(User).count()
    role = "admin" if user_count == 0 else "viewer"

    user = User(
        email=body.email,
        username=body.username,
        hashed_password=get_password_hash(body.password),
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
def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    db: Session = get_write_db_dependency(),
):
    """Authenticate and return a JWT access token."""
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is disabled",
        )

    token = create_access_token(data={"sub": str(user.id), "role": user.role})
    logger.info("User logged in: %s", user.username)

    # Set HttpOnly Secure SameSite=Strict cookie for XSS protection
    response.set_cookie(
        key="pipelineiq_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )

    log_action(
        db,
        "user_login",
        user_id=user.id,
        resource_type="user",
        resource_id=user.id,
        details={"email": user.email},
        request=request,
    )

    return LoginResponse(
        access_token=token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=_user_to_response(user),
    )


@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """Get the current authenticated user's profile."""
    return _user_to_response(current_user)


@router.post("/logout")
async def logout(
    response: Response,
    current_user: User = Depends(get_current_user),
):
    """Logout endpoint - clears HttpOnly cookie."""
    logger.info("User logged out: %s", current_user.username)

    # Clear the HttpOnly cookie
    response.delete_cookie(
        key="pipelineiq_token",
        path="/",
        secure=True,
        samesite="strict",
    )

    return {"message": "Logged out successfully"}


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
    # Validate UUID format and convert for DB query
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
