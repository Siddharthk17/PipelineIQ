"""Regression tests for M-080: dropped unique constraints.

``pipeline_permissions`` must be unique per (pipeline_name, user_id) and
``healing_attempts`` per (pipeline_run_id, attempt_number); without the
constraints concurrent writers can silently store duplicates.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from backend.models import (
    HealingAttempt,
    HealingAttemptStatus,
    PermissionLevel,
    PipelinePermission,
    PipelineRun,
    PipelineStatus,
    User,
)


def _user(test_db) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"m080_{uuid.uuid4().hex[:8]}@t.local",
        username=f"m080_{uuid.uuid4().hex[:8]}",
        hashed_password="x",
        role="viewer",
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    return user


def test_duplicate_permission_rejected(test_db):
    """Second grant for the same (pipeline, user) must fail."""
    user = _user(test_db)
    test_db.add(PipelinePermission(
        pipeline_name="pipe", user_id=user.id,
        permission_level=PermissionLevel.RUNNER))
    test_db.commit()
    test_db.add(PipelinePermission(
        pipeline_name="pipe", user_id=user.id,
        permission_level=PermissionLevel.VIEWER))
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()


def test_same_user_different_pipeline_allowed(test_db):
    """Same user on different pipelines must still succeed."""
    user = _user(test_db)
    for pipe in ("pipe-a", "pipe-b"):
        test_db.add(PipelinePermission(
            pipeline_name=pipe, user_id=user.id,
            permission_level=PermissionLevel.RUNNER))
    test_db.commit()
    assert test_db.query(PipelinePermission).count() == 2


def test_duplicate_healing_attempt_rejected(test_db):
    """Second attempt row with the same (run, attempt_number) must fail."""
    user = _user(test_db)
    run = PipelineRun(
        id=uuid.uuid4(), name="heal_pipe", status=PipelineStatus.FAILED,
        yaml_config="pipeline:\n  name: heal_pipe\n  steps: []",
        user_id=user.id,
    )
    test_db.add(run)
    test_db.commit()
    test_db.add(HealingAttempt(
        run_id=run.id, attempt_number=1,
        status=HealingAttemptStatus.CREATED))
    test_db.commit()
    test_db.add(HealingAttempt(
        run_id=run.id, attempt_number=1,
        status=HealingAttemptStatus.CREATED))
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()


def test_sequential_healing_attempts_allowed(test_db):
    """Distinct attempt numbers for one run must still succeed."""
    user = _user(test_db)
    run = PipelineRun(
        id=uuid.uuid4(), name="heal_pipe2", status=PipelineStatus.FAILED,
        yaml_config="pipeline:\n  name: heal_pipe2\n  steps: []",
        user_id=user.id,
    )
    test_db.add(run)
    test_db.commit()
    for n in (1, 2):
        test_db.add(HealingAttempt(
            run_id=run.id, attempt_number=n,
            status=HealingAttemptStatus.CREATED))
    test_db.commit()
    assert test_db.query(HealingAttempt).count() == 2
