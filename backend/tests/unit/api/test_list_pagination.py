"""Regression tests for M-085: unbounded .all() list endpoints.

Every list endpoint must honor page/limit (clamped server-side, hard cap
<= 200) and counters must account for every PipelineStatus value so the
status buckets sum to total_runs.

These tests FAIL on the pre-fix code (extra query params are ignored, so
limit=1 still returns every row; TIMEOUT runs are invisible to the
counters) and PASS after the fix.
"""

from __future__ import annotations

import uuid

from backend.models import (
    ContractViolationRecord,
    HealingAttempt,
    NotificationConfig,
    NotificationType,
    PipelineContract,
    PipelineRun,
    PipelineSchedule,
    PipelineStatus,
    PipelineVersion,
    UploadedFile,
    User,
)
from backend.tests.conftest import FIXTURE_ADMIN_EMAIL

YAML = "pipeline:\n  name: m085pipe\n  steps: []\n"


def _admin(test_db) -> User:
    user = test_db.query(User).filter(User.email == FIXTURE_ADMIN_EMAIL).first()
    assert user is not None
    return user


def _seed_run(test_db, user_id, status: PipelineStatus, name: str = "m085pipe") -> PipelineRun:
    run = PipelineRun(
        name=name, status=status, yaml_config=YAML, user_id=user_id
    )
    test_db.add(run)
    test_db.commit()
    test_db.refresh(run)
    return run


class TestListPagination:
    def test_files_limit_one(self, client, test_db):
        admin = _admin(test_db)
        for i in range(3):
            test_db.add(
                UploadedFile(
                    original_filename=f"f{i}.csv",
                    stored_path=f"m085-{i}.csv",
                    file_size_bytes=10,
                    row_count=1,
                    column_count=1,
                    columns=["a"],
                    dtypes={"a": "int64"},
                    user_id=admin.id,
                )
            )
        test_db.commit()
        resp = client.get("/api/v1/files/", params={"limit": 1})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["files"]) == 1, f"limit=1 ignored: {len(body['files'])}"
        assert body["total"] == 3

    def test_files_huge_limit_capped(self, client, test_db):
        admin = _admin(test_db)
        test_db.add(
            UploadedFile(
                original_filename="g.csv",
                stored_path="m085-g.csv",
                file_size_bytes=10,
                row_count=1,
                column_count=1,
                columns=["a"],
                dtypes={"a": "int64"},
                user_id=admin.id,
            )
        )
        test_db.commit()
        resp = client.get("/api/v1/files/", params={"limit": 10000})
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["files"]) <= 200

    def test_schedules_limit_one(self, client, test_db):
        admin = _admin(test_db)
        for i in range(3):
            test_db.add(
                PipelineSchedule(
                    user_id=admin.id,
                    pipeline_name=f"m085sched{i}",
                    yaml_config=YAML,
                    cron_expression="0 0 * * *",
                )
            )
        test_db.commit()
        resp = client.get("/api/v1/schedules/", params={"limit": 1})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["schedules"]) == 1, f"limit=1 ignored: {len(body['schedules'])}"
        assert body["total"] == 3

    def test_versions_limit_one(self, client, test_db):
        for i in (1, 2, 3):
            test_db.add(
                PipelineVersion(
                    pipeline_name="m085pipe",
                    version_number=i,
                    yaml_config=YAML,
                )
            )
        test_db.commit()
        resp = client.get("/api/v1/versions/m085pipe", params={"limit": 1})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["versions"]) == 1, f"limit=1 ignored: {len(body['versions'])}"
        assert body["total_versions"] == 3

    def test_notifications_cap(self, client, test_db):
        admin = _admin(test_db)
        for _ in range(210):
            test_db.add(
                NotificationConfig(
                    user_id=admin.id,
                    type=NotificationType.EMAIL,
                    config={"email_to": "a@t.local"},
                    events=["pipeline_completed"],
                )
            )
        test_db.commit()
        resp = client.get("/api/v1/notifications/")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["configs"]) <= 200, (
            f"uncapped list returned {len(body['configs'])}"
        )
        assert body["total"] == 210
        one = client.get("/api/v1/notifications/", params={"limit": 1})
        assert one.status_code == 200, one.text
        assert len(one.json()["configs"]) == 1

    def test_contracts_limit_one(self, client, test_db):
        admin = _admin(test_db)
        for i in (1, 2, 3):
            test_db.add(
                PipelineContract(
                    pipeline_name="m085contract",
                    version=i,
                    yaml_content="dataset: x\n",
                    user_id=admin.id,
                    is_active=(i == 3),
                )
            )
        test_db.commit()
        resp = client.get(
            "/api/v1/contracts/pipelines/m085contract", params={"limit": 1}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["contracts"]) == 1, f"limit=1 ignored: {len(body['contracts'])}"
        assert body["total"] == 3

    def test_run_violations_limit_one(self, client, test_db):
        admin = _admin(test_db)
        run = _seed_run(test_db, admin.id, PipelineStatus.FAILED)
        for i in range(3):
            test_db.add(
                ContractViolationRecord(
                    run_id=run.id,
                    step_name="s",
                    step_index=i,
                    step_type="filter",
                    column="c",
                    rule="not_null",
                    severity="error",
                    message="m",
                )
            )
        test_db.commit()
        resp = client.get(
            f"/api/v1/contracts/runs/{run.id}", params={"limit": 1}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["violations"]) == 1, (
            f"limit=1 ignored: {len(body['violations'])}"
        )
        assert body["total_violations"] == 3

    def test_step_violations_limit_one(self, client, test_db):
        admin = _admin(test_db)
        run = _seed_run(test_db, admin.id, PipelineStatus.FAILED, name="m085step")
        for i in range(3):
            test_db.add(
                ContractViolationRecord(
                    run_id=run.id,
                    step_name="only",
                    step_index=i,
                    step_type="filter",
                    column="c",
                    rule="not_null",
                    severity="error",
                    message="m",
                )
            )
        test_db.commit()
        resp = client.get(
            f"/api/v1/contracts/runs/{run.id}/steps/only", params={"limit": 1}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["violations"]) == 1
        assert body["total_violations"] == 3

    def test_breaches_limit_one(self, client, test_db):
        admin = _admin(test_db)
        run = _seed_run(test_db, admin.id, PipelineStatus.FAILED, name="m085breach")
        for i in range(3):
            test_db.add(
                ContractViolationRecord(
                    run_id=run.id,
                    step_name="s",
                    step_index=i,
                    step_type="filter",
                    column="c",
                    rule="not_null",
                    severity="error",
                    message="m",
                )
            )
        test_db.commit()
        resp = client.get(
            "/api/v1/contracts/pipelines/m085breach/breaches", params={"limit": 1}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["violations"]) == 1
        assert body["total_violations"] == 3

    def test_users_limit_one(self, client, test_db):
        for i in range(2):
            test_db.add(
                User(
                    email=f"m085.u{i}@t.local",
                    username=f"m085_u{i}_{uuid.uuid4().hex[:6]}",
                    hashed_password="x",
                    role="viewer",
                )
            )
        test_db.commit()
        resp = client.get("/auth/users", params={"limit": 1})
        assert resp.status_code == 200, resp.text
        assert len(resp.json()) == 1, f"limit=1 ignored: {len(resp.json())}"

    def test_healing_attempts_limit_one(self, client, test_db):
        admin = _admin(test_db)
        run = _seed_run(test_db, admin.id, PipelineStatus.FAILED)
        for i in (1, 2, 3):
            test_db.add(
                HealingAttempt(
                    pipeline_run_id=run.id,
                    attempt_number=i,
                )
            )
        test_db.commit()
        resp = client.get(
            f"/api/v1/pipelines/{run.id}/healing-attempts", params={"limit": 1}
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()) == 1, f"limit=1 ignored: {len(resp.json())}"


class TestStatusCounters:
    STATUSES = [
        PipelineStatus.PENDING,
        PipelineStatus.RUNNING,
        PipelineStatus.HEALING,
        PipelineStatus.HEALED,
        PipelineStatus.COMPLETED,
        PipelineStatus.CONTRACT_VIOLATION,
        PipelineStatus.FAILED,
        PipelineStatus.CANCELLED,
        PipelineStatus.TIMEOUT,
        PipelineStatus.STREAMING_ACTIVE,
        PipelineStatus.STREAMING_PAUSED,
        PipelineStatus.STREAMING_STOPPED,
    ]

    def _seed_all(self, test_db):
        admin = _admin(test_db)
        for st in self.STATUSES:
            _seed_run(test_db, admin.id, st)

    def test_dashboard_counts_every_status(self, client, test_db):
        self._seed_all(test_db)
        resp = client.get("/api/v1/dashboard/stats")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_runs"] == len(self.STATUSES)
        buckets = body["pipelines_by_status"]
        assert sum(buckets.values()) == body["total_runs"], (
            f"buckets sum {sum(buckets.values())} != total {body['total_runs']}"
        )
        assert buckets.get("TIMEOUT", 0) == 1
        for key in (
            "COMPLETED", "FAILED", "PENDING", "RUNNING", "HEALING", "CANCELLED",
        ):
            assert key in buckets, f"missing bucket {key}"

    def test_pipeline_stats_counts_every_status(self, client, test_db):
        self._seed_all(test_db)
        resp = client.get("/api/v1/pipelines/stats")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_runs"] == len(self.STATUSES)
        for key in (
            "completed", "failed", "pending", "running", "healing",
            "cancelled", "timeout",
        ):
            assert key in body, f"missing stats key: {key}"
        counted = (
            body["completed"] + body["failed"] + body["pending"]
            + body["running"] + body["healing"] + body["cancelled"]
            + body["timeout"]
        )
        assert counted + body.get("contract_violation", 0) + sum(
            body.get(k, 0) for k in (
                "streaming_active", "streaming_paused", "streaming_stopped")
        ) == body["total_runs"]
        assert body["timeout"] == 1
        assert body["success_rate"] == round(
            body["completed"] / body["total_runs"] * 100, 1)
