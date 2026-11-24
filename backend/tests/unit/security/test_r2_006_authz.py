"""R2-006: schedules admin bypass + webhooks 404 anti-oracle."""
import uuid as uuid_module
from types import SimpleNamespace


def _user(role="viewer"):
    return SimpleNamespace(id=uuid_module.uuid4(), role=role,
                           email=f"{role}@t.local")


def test_admin_may_read_foreign_schedule(test_db):
    from backend.api.schedules import _get_schedule_or_404
    from backend.models import PipelineSchedule

    owner = _user()
    admin = _user(role="admin")
    sched = PipelineSchedule(
        id=uuid_module.uuid4(), pipeline_name="p",
        yaml_config="pipeline:\n  name: p\n  steps: []\n",
        cron_expression="0 * * * *", user_id=owner.id)
    test_db.add(sched)
    test_db.commit()
    found = _get_schedule_or_404(str(sched.id), admin, test_db)
    assert found.id == sched.id


def test_owner_scoped_schedule_404(test_db):
    import pytest
    from fastapi import HTTPException

    from backend.api.schedules import _get_schedule_or_404

    owner = _user()
    stranger = _user()
    from backend.models import PipelineSchedule
    sched = PipelineSchedule(
        id=uuid_module.uuid4(), pipeline_name="p",
        yaml_config="pipeline:\n  name: p\n  steps: []\n",
        cron_expression="0 * * * *", user_id=owner.id)
    test_db.add(sched)
    test_db.commit()
    with pytest.raises(HTTPException) as exc:
        _get_schedule_or_404(str(sched.id), stranger, test_db)
    assert exc.value.status_code == 404


def test_webhook_foreign_id_is_404_not_403(test_db):
    """R2-006: foreign webhook IDs return 404, never 403 (no oracle)."""
    import pytest
    from fastapi import HTTPException

    from backend.api.webhooks import delete_webhook
    from backend.models import Webhook

    owner_id = uuid_module.uuid4()
    stranger = SimpleNamespace(id=uuid_module.uuid4(), role="viewer")
    hook = Webhook(id=uuid_module.uuid4(), user_id=owner_id,
                   url="https://example.com/hook", events=["run.completed"])
    test_db.add(hook)
    test_db.commit()
    with pytest.raises(HTTPException) as exc:
        delete_webhook(str(hook.id), db=test_db, current_user=stranger)
    assert exc.value.status_code == 404
    assert exc.value.detail == "Webhook not found"
