"""Seed a demo account and sample pipeline data for PipelineIQ.

Run: python -m backend.scripts.seed_demo
Or:  python scripts/seed_demo.py   (from /app inside Docker)
"""

import sys
import os
import uuid

# Ensure the project root is on sys.path when run from /app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.database import SessionLocal
from backend.models import User
from backend.auth import get_password_hash

DEMO_EMAIL = "demo@pipelineiq.app"
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "Demo1234!"


def seed_demo():
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == DEMO_EMAIL).first()
        if existing:
            print(f"✓ Demo account already exists (id={existing.id})")
            return

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
        print(f"✓ Demo account created: {DEMO_EMAIL} (role=admin)")
    except Exception as e:
        db.rollback()
        print(f"⚠ Demo seed error: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    seed_demo()
