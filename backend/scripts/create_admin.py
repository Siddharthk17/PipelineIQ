"""Bootstrap the initial admin user for PipelineIQ.

Explicit bootstrap only — registration never grants admin (see M-048).

Usage:
    PIPELINEIQ_BOOTSTRAP_ADMIN=1 ADMIN_EMAIL=admin@example.com \\
        ADMIN_PASSWORD='Str0ngP@ss!' python -m backend.scripts.create_admin

The ``PIPELINEIQ_BOOTSTRAP_ADMIN=1`` flag is mandatory (default off).
Refuses to run when an admin already exists, so it cannot be reused to
escalate or duplicate privileged accounts. Password strength rules match
the ``/auth/register`` endpoint.
"""

import os
import re
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

BOOTSTRAP_FLAG = "PIPELINEIQ_BOOTSTRAP_ADMIN"


def _validate_password(password: str) -> str | None:
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if not any(c.isupper() for c in password):
        return "Password must contain at least one uppercase letter"
    if not any(c.isdigit() for c in password):
        return "Password must contain at least one number"
    if not any(c in "!@#$%^&*()_+-=[]{}|;:',.<>?/`~" for c in password):
        return "Password must contain at least one special character"
    return None


def main() -> int:
    if os.getenv(BOOTSTRAP_FLAG, "0") != "1":
        print(
            f"Refusing: set {BOOTSTRAP_FLAG}=1 to explicitly allow "
            "initial-admin bootstrap.",
            file=sys.stderr,
        )
        return 2

    from backend.auth import get_password_hash
    from backend.database import SessionLocal
    from backend.models import User

    email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    username = os.getenv("ADMIN_USERNAME", "admin").strip()
    password = os.getenv("ADMIN_PASSWORD", "")

    if not re.match(
        r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email
    ):
        print("Refusing: ADMIN_EMAIL is missing or invalid.", file=sys.stderr)
        return 2
    if not re.match(r"^[a-zA-Z0-9_]+$", username) or not 3 <= len(username) <= 50:
        print(
            "Refusing: ADMIN_USERNAME must be 3-50 chars "
            "(alphanumeric + underscore).",
            file=sys.stderr,
        )
        return 2
    pw_error = _validate_password(password)
    if pw_error:
        print(f"Refusing: {pw_error}.", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        existing_admin = db.query(User).filter(User.role == "admin").first()
        if existing_admin:
            print(
                "Refusing: an admin account already exists "
                f"({existing_admin.email}).",
                file=sys.stderr,
            )
            return 1
        if db.query(User).filter(
            (User.email == email) | (User.username == username)
        ).first():
            print(
                "Refusing: email or username already taken.", file=sys.stderr
            )
            return 1
        user = User(
            id=uuid.uuid4(),
            email=email,
            username=username,
            hashed_password=get_password_hash(password),
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
        print(f"Initial admin created: {email}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
