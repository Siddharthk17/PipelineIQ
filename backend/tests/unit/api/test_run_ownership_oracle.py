"""Regression tests: cross-user run access returns 404, not 403 (S-new).

A 403 on a run UUID confirms the run exists (existence oracle). The files
API already returns 404 for foreign objects; run-scoped endpoints must
match so one user's run IDs cannot be probed by another user.
"""

import uuid

import pytest
from fastapi import HTTPException

from backend.api.pipelines import _check_pipeline_permission, _check_run_permission
from backend.models import PipelineRun, PipelineStatus, User


def _user(role: str = "viewer") -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@t.io",
        username=f"u_{uuid.uuid4().hex[:8]}",
        hashed_password="hashed",
        role=role,
        is_active=True,
    )


def _run(owner_id: uuid.UUID) -> PipelineRun:
    return PipelineRun(
        id=uuid.uuid4(),
        user_id=owner_id,
        name=f"pipe_{uuid.uuid4().hex[:8]}",
        status=PipelineStatus.COMPLETED,
        yaml_config="pipeline:\n  name: x\n  steps: []",
    )


def test_cross_user_run_access_returns_404(test_db):
    owner, stranger = _user(), _user()
    run = _run(owner.id)
    with pytest.raises(HTTPException) as exc:
        _check_run_permission(test_db, stranger, run, ["owner", "runner", "viewer"])
    assert exc.value.status_code == 404


def test_owner_run_access_allowed(test_db):
    owner = _user()
    _check_run_permission(test_db, owner, _run(owner.id), ["owner", "runner", "viewer"])


def test_admin_run_access_allowed(test_db):
    admin, owner = _user(role="admin"), _user()
    _check_run_permission(test_db, admin, _run(owner.id), ["owner", "runner", "viewer"])


def test_no_grant_on_name_returns_404_not_403(test_db):
    user = _user()
    with pytest.raises(HTTPException) as exc:
        _check_pipeline_permission(test_db, user, "someone_elses_pipe", ["owner"])
    assert exc.value.status_code == 404
    assert "someone_elses_pipe" not in exc.value.detail


def test_contract_owner_may_access_contracted_run(test_db):
    from backend.models import PipelineContract

    owner, runner = _user(), _user()
    pipe_name = f"contracted_{uuid.uuid4().hex[:8]}"
    test_db.add(PipelineContract(
        pipeline_name=pipe_name,
        version=1,
        yaml_content="columns:\n  a:\n    type: integer\n",
        severity="block",
        user_id=owner.id,
    ))
    test_db.commit()
    # The contract owner can access a run of that pipeline even when the
    # run row belongs to someone else (e.g. a teammate's run).
    contracted_run = _run(runner.id)
    contracted_run.name = pipe_name
    _check_run_permission(test_db, owner, contracted_run, ["owner", "runner", "viewer"])
    # A stranger with no contract and no run ownership still gets 404.
    stranger = _user()
    foreign = _run(runner.id)
    foreign.name = f"other_{uuid.uuid4().hex[:8]}"
    with pytest.raises(HTTPException) as exc:
        _check_run_permission(test_db, stranger, foreign, ["owner", "runner", "viewer"])
    assert exc.value.status_code == 404
