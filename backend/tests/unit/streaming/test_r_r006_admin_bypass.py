"""Regression test for R-R-006: admins can act on foreign streaming runs.

The route-level gate used to 404 any non-owner, even admins, while the
daemon-side transitions already allowed admins — an inconsistent
authorization split. The gate now mirrors the daemon: admins bypass.
"""
import uuid

import pytest

from backend.models import PipelineRun, PipelineStatus, User


def _user(db, role):
    u = User(
        id=uuid.uuid4(), email=f"rr6_{uuid.uuid4().hex[:8]}@t.local",
        username=f"rr6_{uuid.uuid4().hex[:8]}", hashed_password="x",
        role=role, is_active=True)
    db.add(u)
    db.flush()
    return u


def _run(db, owner):
    r = PipelineRun(
        id=uuid.uuid4(), name="rr6-stream",
        status=PipelineStatus.STREAMING_ACTIVE,
        yaml_config="pipeline:\n  name: rr6-stream\n  steps: []\n",
        user_id=owner.id,
    )
    db.add(r)
    db.commit()
    return r


@pytest.mark.asyncio
async def test_admin_bypasses_owner_gate(test_db):
    from backend.routers import streaming as streaming_router

    owner = _user(test_db, "viewer")
    admin = _user(test_db, "admin")
    stranger = _user(test_db, "viewer")
    run = _run(test_db, owner)

    got = await streaming_router._get_run_or_404(
        str(run.id), admin, test_db)
    assert got.id == run.id

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await streaming_router._get_run_or_404(
            str(run.id), stranger, test_db)
    assert exc.value.status_code == 404
