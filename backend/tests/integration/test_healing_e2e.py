"""Integration tests for healing API wiring."""

from backend.models import HealingAttempt, HealingAttemptStatus, PipelineRun, PipelineStatus


def test_healing_history_endpoint_returns_structured_attempts(client, test_db):
    pipeline_run = PipelineRun(
        name="healing-run",
        status=PipelineStatus.HEALED,
        yaml_config="pipeline:\n  name: healing-run\n  steps: []\n",
    )
    test_db.add(pipeline_run)
    test_db.commit()
    test_db.refresh(pipeline_run)

    attempt = HealingAttempt(
        run_id=pipeline_run.id,
        pipeline_name="healing-run",
        attempt_number=1,
        status=HealingAttemptStatus.APPLIED,
        failed_step="filter_step",
        error_type="ColumnNotFoundError",
        error_message="Column 'revenue' not found",
        removed_columns=["revenue"],
        added_columns=["rev_usd"],
        renamed_candidates=[{"old_name": "revenue", "new_name": "rev_usd", "confidence": 0.93}],
        gemini_patch={"change_description": "Rename revenue to rev_usd", "patches": []},
        sandbox_result={"success": True, "output_rows": 100},
        applied=True,
        confidence=0.93,
    )
    test_db.add(attempt)
    test_db.commit()

    response = client.get(f"/api/v1/pipelines/{pipeline_run.id}/healing-history")
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == str(pipeline_run.id)
    assert len(payload["healing_attempts"]) == 1
    first_attempt = payload["healing_attempts"][0]
    assert first_attempt["failed_step"] == "filter_step"
    assert first_attempt["applied"] is True
    assert first_attempt["removed_columns"] == ["revenue"]
