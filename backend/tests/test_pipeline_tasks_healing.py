"""Unit tests for autonomous healing orchestration in pipeline tasks."""

from backend.config import settings
from backend.models import HealingAttempt, HealingAttemptStatus, PipelineRun, PipelineStatus
from backend.pipeline.exceptions import ColumnNotFoundError, FileReadError
from backend.pipeline.healing import CandidateValidation, HealingCandidate
from backend.pipeline.lineage import LineageRecorder
from backend.pipeline.runner import PipelineExecutionSummary, PipelineStatus as RunnerPipelineStatus
from backend.tasks.pipeline_tasks import _execute_with_autonomous_healing


def _failed_summary(error):
    return PipelineExecutionSummary(
        run_id="run-1",
        pipeline_name="p1",
        status=RunnerPipelineStatus.FAILED,
        step_results=[],
        lineage=LineageRecorder(),
        total_duration_ms=10,
        total_rows_processed=0,
        error=error,
    )


def _completed_summary():
    return PipelineExecutionSummary(
        run_id="run-1",
        pipeline_name="p1",
        status=RunnerPipelineStatus.COMPLETED,
        step_results=[],
        lineage=LineageRecorder(),
        total_duration_ms=20,
        total_rows_processed=10,
        error=None,
    )


def test_healing_marks_non_healable_and_stops(monkeypatch, test_db):
    pipeline_run = PipelineRun(
        name="healing-run",
        status=PipelineStatus.RUNNING,
        yaml_config="pipeline:\n  name: p1\n  steps: []\n",
    )
    test_db.add(pipeline_run)
    test_db.commit()
    test_db.refresh(pipeline_run)

    failed = _failed_summary(FileReadError("load_sales", "/tmp/missing.csv", "missing"))

    monkeypatch.setattr(settings, "AUTONOMOUS_HEALING_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AUTONOMOUS_HEALING_MAX_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks._run_pipeline",
        lambda db, run: failed,
    )
    published_events = []
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks._publish_progress_payload",
        lambda run_id, payload: published_events.append(payload),
    )

    summary = _execute_with_autonomous_healing(test_db, pipeline_run)
    assert summary is failed

    attempts = (
        test_db.query(HealingAttempt)
        .filter(HealingAttempt.pipeline_run_id == pipeline_run.id)
        .all()
    )
    assert len(attempts) == 1
    assert attempts[0].status == HealingAttemptStatus.NON_HEALABLE
    assert attempts[0].failed_step_name == "load_sales"
    assert any(e["event_type"] == "healing_non_healable" for e in published_events)


def test_healing_records_ai_invalid_candidate(monkeypatch, test_db):
    pipeline_run = PipelineRun(
        name="healing-run",
        status=PipelineStatus.RUNNING,
        yaml_config="pipeline:\n  name: p1\n  steps: []\n",
    )
    test_db.add(pipeline_run)
    test_db.commit()
    test_db.refresh(pipeline_run)

    failed = _failed_summary(
        ColumnNotFoundError("filter_step", "ammount", ["amount", "status"])
    )

    monkeypatch.setattr(settings, "AUTONOMOUS_HEALING_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AUTONOMOUS_HEALING_MAX_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks._run_pipeline",
        lambda db, run: failed,
    )
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks.collect_registered_file_ids",
        lambda db: set(),
    )
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks.generate_healing_candidate",
        lambda **kwargs: HealingCandidate(
            corrected_yaml=kwargs["original_yaml"],
            diff_lines=[],
            ai_valid=False,
            ai_error="invalid patch",
        ),
    )
    published_events = []
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks._publish_progress_payload",
        lambda run_id, payload: published_events.append(payload),
    )

    summary = _execute_with_autonomous_healing(test_db, pipeline_run)
    assert summary is failed

    attempts = (
        test_db.query(HealingAttempt)
        .filter(HealingAttempt.pipeline_run_id == pipeline_run.id)
        .all()
    )
    assert len(attempts) == 1
    assert attempts[0].status == HealingAttemptStatus.AI_INVALID
    assert attempts[0].ai_error == "invalid patch"
    assert any(e["event_type"] == "healing_attempt_invalid" for e in published_events)


def test_healing_applies_patch_and_succeeds(monkeypatch, test_db):
    pipeline_run = PipelineRun(
        name="healing-run",
        status=PipelineStatus.RUNNING,
        yaml_config="pipeline:\n  name: p1\n  steps: []\n",
    )
    test_db.add(pipeline_run)
    test_db.commit()
    test_db.refresh(pipeline_run)

    failed = _failed_summary(
        ColumnNotFoundError("filter_step", "ammount", ["amount", "status"])
    )
    completed = _completed_summary()
    run_results = iter([failed, completed])

    monkeypatch.setattr(settings, "AUTONOMOUS_HEALING_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AUTONOMOUS_HEALING_MAX_ATTEMPTS", 2, raising=False)
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks._run_pipeline",
        lambda db, run: next(run_results),
    )
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks.collect_registered_file_ids",
        lambda db: set(),
    )
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks.generate_healing_candidate",
        lambda **kwargs: HealingCandidate(
            corrected_yaml="pipeline:\n  name: healed\n  steps: []\n",
            diff_lines=[{"line": 1, "old": "p1", "new": "healed"}],
            ai_valid=True,
            ai_error=None,
        ),
    )
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks.validate_healing_candidate",
        lambda **kwargs: CandidateValidation(
            is_valid=True,
            errors=[],
            warnings=[],
            sandbox_passed=True,
            sandbox_error=None,
        ),
    )
    published_events = []
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks._publish_progress_payload",
        lambda run_id, payload: published_events.append(payload),
    )

    summary = _execute_with_autonomous_healing(test_db, pipeline_run)
    assert summary.status == RunnerPipelineStatus.COMPLETED

    test_db.refresh(pipeline_run)
    assert "healed" in pipeline_run.yaml_config

    attempts = (
        test_db.query(HealingAttempt)
        .filter(HealingAttempt.pipeline_run_id == pipeline_run.id)
        .all()
    )
    assert len(attempts) == 1
    assert attempts[0].status == HealingAttemptStatus.APPLIED
    assert attempts[0].parser_valid is True
    assert attempts[0].sandbox_passed is True
    event_types = [event["event_type"] for event in published_events]
    assert "healing_attempt_started" in event_types
    assert "healing_attempt_applied" in event_types
    assert "healing_succeeded" in event_types
