"""HEALING runs must be cancellable (M-152).

The cancel endpoint's status whitelist omitted HEALING, so a run stuck
in autonomous healing could never be cancelled (409) even though
_persist_results already honors a concurrent CANCELLED (cancel wins over
a late finish).
"""

import uuid as uuid_module


class TestCancelHealing:
    def _run_in(self, test_db, status):
        from backend.models import PipelineRun, User
        from backend.tests.conftest import FIXTURE_ADMIN_EMAIL

        admin = test_db.query(User).filter(User.email == FIXTURE_ADMIN_EMAIL).first()
        assert admin is not None
        run = PipelineRun(
            id=uuid_module.uuid4(),
            name="healing-run",
            status=status,
            yaml_config="pipeline:\n  name: healing-run\n  steps: []\n",
            user_id=admin.id,
        )
        test_db.add(run)
        test_db.commit()
        return str(run.id)

    def test_healing_run_cancellable(self, client, test_db):
        from backend.models import PipelineStatus

        run_id = self._run_in(test_db, PipelineStatus.HEALING)
        resp = client.post(f"/api/v1/pipelines/{run_id}/cancel")
        assert resp.status_code == 200, resp.text
        test_db.expire_all()
        from backend.models import PipelineRun
        from backend.utils.uuid_utils import as_uuid

        run = test_db.query(PipelineRun).filter(PipelineRun.id == as_uuid(run_id)).first()
        assert run.status == PipelineStatus.CANCELLED

    def test_healed_run_still_rejected(self, client, test_db):
        from backend.models import PipelineStatus

        run_id = self._run_in(test_db, PipelineStatus.HEALED)
        resp = client.post(f"/api/v1/pipelines/{run_id}/cancel")
        assert resp.status_code == 409, resp.text
