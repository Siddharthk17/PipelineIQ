"""Seed a demo account and sample pipeline data for PipelineIQ.

Run: python -m backend.scripts.seed_demo
Or:  python scripts/seed_demo.py   (from /app inside Docker)

EXPLICIT OPT-IN ONLY (M-002): this script is a no-op unless
``PIPELINEIQ_SEED_DEMO=1`` is set, and it ALWAYS refuses when
``ENVIRONMENT=production`` — so the published demo credentials can
never materialize an admin account on a production deployment. The
compose ``api`` command is likewise gated on the same flag (default
off). The public demo keeps its documented README credentials
(BY-DESIGN M-157); only the production seeding path is guarded.
"""

import os
import sys
import uuid

from backend.auth import get_password_hash
from backend.database import SessionLocal
from backend.models import User

# Ensure the project root is on sys.path when run from /app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


DEMO_EMAIL = "demo@pipelineiq.app"
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "Demo1234!"

SEED_DEMO_FLAG = "PIPELINEIQ_SEED_DEMO"


def _seed_allowed() -> tuple[bool, str]:
    """Return (allowed, reason) for creating the demo account."""
    environment = os.getenv("ENVIRONMENT", "development").lower()
    if environment == "production":
        return False, "refusing: never seed demo credentials in production"
    if os.getenv(SEED_DEMO_FLAG, "0") != "1":
        return (
            False,
            f"skipping: set {SEED_DEMO_FLAG}=1 to explicitly allow demo seeding",
        )
    return True, ""


def seed_demo() -> bool:
    """Create the demo account when explicitly opted in. Returns True if created."""
    allowed, reason = _seed_allowed()
    if not allowed:
        print(f"Demo seed {reason}.")
        return False

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == DEMO_EMAIL).first()
        if existing:
            print(f"Demo account already exists (id={existing.id})")
            return False

        user = User(
            id=uuid.uuid4(),
            email=DEMO_EMAIL,
            username=DEMO_USERNAME,
            hashed_password=get_password_hash(DEMO_PASSWORD),
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"WARNING: demo seed error: {e}")
        return False
    else:
        print(f"Demo account created: {DEMO_EMAIL} (role=admin)")
        return True
    finally:
        db.close()


if __name__ == "__main__":
    seed_demo()
