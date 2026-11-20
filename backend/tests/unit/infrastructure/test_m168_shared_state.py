"""Regression tests for M-168 (narrow slice owned by this wave).

* ``FileProfile.file_id`` carries ``unique=True``: the duplicate-row
  race headline is impossible — this test locks the constraint in.
* The sibling smells (``to_thread`` with a Session, ``RLock`` sharing,
  signal misuse) do not exist in the owned modules
  (``reconciliation``, ``webhook_tasks``, ``api/webhooks``,
  ``utils/rate_limiter``, ``models``) — catalog/streaming owners handle
  their own instances; this test guards against introduction here.
"""

import pathlib
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from backend.models import FileProfile, UploadedFile, User

OWNED_FILES = [
    "backend/tasks/reconciliation.py",
    "backend/tasks/webhook_tasks.py",
    "backend/api/webhooks.py",
    "backend/utils/rate_limiter.py",
    "backend/models/__init__.py",
    "backend/models/_base.py",
    "backend/models/column_policy.py",
    "backend/models/data_contract.py",
    "backend/models/contract_breach.py",
]

FORBIDDEN_TOKENS = ("to_thread", "threading", "RLock", "signal.")


def _file(test_db):
    user = User(
        id=uuid.uuid4(), email=f"m168_{uuid.uuid4().hex[:8]}@t.local",
        username=f"m168_{uuid.uuid4().hex[:8]}", hashed_password="x",
        role="viewer", is_active=True)
    test_db.add(user)
    test_db.flush()
    uploaded = UploadedFile(
        original_filename="m168.csv",
        stored_path="/tmp/m168.csv",
        file_size_bytes=10,
        row_count=1,
        column_count=1,
        columns=["a"],
        dtypes={"a": "int64"},
        user_id=user.id,
    )
    test_db.add(uploaded)
    test_db.commit()
    return uploaded


def test_file_profile_duplicate_rejected(test_db):
    """Two profiles for one file must fail (unique=True holds)."""
    uploaded = _file(test_db)
    test_db.add(FileProfile(file_id=uploaded.id))
    test_db.commit()
    test_db.add(FileProfile(file_id=uploaded.id))
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()


def test_no_shared_state_primitives_in_owned_modules():
    """Owned modules must not gain thread/signal sharing primitives."""
    repo = pathlib.Path(__file__).resolve().parents[4]
    offenders = []
    for rel in OWNED_FILES:
        src = (repo / rel).read_text()
        for token in FORBIDDEN_TOKENS:
            if token in src:
                offenders.append(f"{rel}: {token}")
    assert not offenders, f"shared-state primitives: {offenders}"
