"""Regression tests for pipeline result persistence ordering."""

from backend.models import PipelineRun, PipelineStatus
from backend.pipeline.lineage import LineageRecorder
from backend.pipeline.runner import (
    PipelineExecutionSummary,
    PipelineStatus as RunnerPipelineStatus,
)
from backend.tasks.pipeline_tasks import _persist_results


def test_persist_results_publishes_terminal_event_after_commit(monkeypatch, test_db):
    pipeline_run = PipelineRun(
        name="lineage-ordering",
        status=PipelineStatus.RUNNING,
        yaml_config="pipeline:\n  name: lineage_ordering\n  steps: []\n",
    )
    test_db.add(pipeline_run)
    test_db.commit()
    test_db.refresh(pipeline_run)

    summary = PipelineExecutionSummary(
        run_id=str(pipeline_run.id),
        pipeline_name="lineage-ordering",
        status=RunnerPipelineStatus.COMPLETED,
        step_results=[],
        lineage=LineageRecorder(),
        total_duration_ms=5,
        total_rows_processed=0,
        error=None,
    )

    order: list[str] = []
    original_commit = test_db.commit

    def recording_commit():
        order.append("commit")
        return original_commit()

    monkeypatch.setattr(test_db, "commit", recording_commit)
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks._publish_terminal_event",
        lambda *_args, **_kwargs: order.append("publish"),
    )
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks._save_version_if_needed",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "backend.tasks.notification_tasks.deliver_notifications_task.delay",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "backend.tasks.webhook_tasks.deliver_webhooks_task.delay",
        lambda **_kwargs: None,
    )

    _persist_results(test_db, pipeline_run, summary)

    assert order[:2] == ["commit", "publish"]
