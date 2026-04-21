"""Autonomous healing helpers for failed pipeline executions.

This module classifies failed runs, asks the existing AI repair service for
candidate YAML patches, and validates candidates before retry execution.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from backend.ai.generation import RepairResult, repair_pipeline_from_error
from backend.models import UploadedFile
from backend.pipeline.parser import PipelineParser
from backend.pipeline.planner import generate_execution_plan
from backend.pipeline.runner import PipelineExecutionSummary

_NON_HEALABLE_ERROR_TYPES = frozenset(
    {
        "FileReadError",
        "UnsupportedFileFormatError",
        "PermissionDeniedError",
        "StepTimeoutError",
    }
)

_NON_HEALABLE_MESSAGE_MARKERS = (
    "permission denied",
    "no such file",
    "file not found",
    "upload",
    "timeout",
)

_FAILED_STEP_RE = re.compile(r"Step\s+'([^']+)'")


@dataclass
class HealingClassification:
    """Result of deciding if a failed run should be auto-healed."""

    healable: bool
    reason: str


@dataclass
class HealingCandidate:
    """Candidate patch returned by AI repair + local validation metadata."""

    corrected_yaml: str
    diff_lines: list[dict]
    ai_valid: bool
    ai_error: Optional[str]


@dataclass
class CandidateValidation:
    """Validation details for a candidate YAML patch."""

    is_valid: bool
    errors: list[str]
    warnings: list[str]
    sandbox_passed: bool
    sandbox_error: Optional[str]


def classify_failed_summary(summary: PipelineExecutionSummary) -> HealingClassification:
    """Determine whether a failed summary can be auto-healed safely."""
    error = summary.error
    if error is None:
        return HealingClassification(healable=False, reason="Run failed without an error object")

    error_type = error.__class__.__name__
    if error_type in _NON_HEALABLE_ERROR_TYPES:
        return HealingClassification(
            healable=False,
            reason=f"Error type '{error_type}' is not healable automatically",
        )

    message = str(error).lower()
    for marker in _NON_HEALABLE_MESSAGE_MARKERS:
        if marker in message:
            return HealingClassification(
                healable=False,
                reason=f"Error message contains non-healable marker '{marker}'",
            )

    return HealingClassification(healable=True, reason="Error looks healable")


def extract_failed_step_name(summary: PipelineExecutionSummary) -> Optional[str]:
    """Extract the failed step name from the runner summary."""
    if summary.error is None:
        return None

    error = summary.error
    explicit = getattr(error, "step_name", None)
    if isinstance(explicit, str) and explicit:
        return explicit

    match = _FAILED_STEP_RE.search(str(error))
    if match:
        return match.group(1)
    return None


def collect_registered_file_ids(db: Session) -> set[str]:
    """Collect all known uploaded file IDs as strings."""
    return {str(row[0]) for row in db.query(UploadedFile.id).all()}


def collect_file_ids_from_yaml(yaml_config: str) -> list[str]:
    """Collect referenced file IDs from YAML load steps."""
    parser = PipelineParser()
    config = parser.parse(yaml_config)
    file_ids: list[str] = []
    for step in config.steps:
        file_id = getattr(step, "file_id", None)
        if isinstance(file_id, str) and file_id:
            file_ids.append(file_id)
    return file_ids


def generate_healing_candidate(
    *,
    original_yaml: str,
    failed_step: str,
    error_type: str,
    error_message: str,
    file_ids: list[str],
    db: Session,
) -> HealingCandidate:
    """Call the existing AI repair service and normalize the response."""
    result: RepairResult = asyncio.run(
        repair_pipeline_from_error(
            original_yaml=original_yaml,
            failed_step=failed_step,
            error_type=error_type,
            error_message=error_message,
            file_ids=file_ids,
            db=db,
        )
    )
    return HealingCandidate(
        corrected_yaml=result.corrected_yaml,
        diff_lines=result.diff_lines,
        ai_valid=result.valid,
        ai_error=result.error,
    )


def validate_healing_candidate(
    *,
    candidate_yaml: str,
    registered_file_ids: set[str],
    db: Session,
) -> CandidateValidation:
    """Validate parser + semantic checks + dry-run planner for a candidate."""
    parser = PipelineParser()
    errors: list[str] = []
    warnings: list[str] = []
    sandbox_passed = False
    sandbox_error: Optional[str] = None

    try:
        config = parser.parse(candidate_yaml)
    except Exception as exc:
        return CandidateValidation(
            is_valid=False,
            errors=[f"Parse failed: {exc}"],
            warnings=[],
            sandbox_passed=False,
            sandbox_error=None,
        )

    validation_result = parser.validate(config, registered_file_ids)
    errors.extend(f"{e.field}: {e.message}" for e in validation_result.errors)
    warnings.extend(w.message for w in validation_result.warnings)

    try:
        plan = generate_execution_plan(candidate_yaml, db)
        sandbox_passed = plan.will_succeed
        if not plan.will_succeed:
            sandbox_error = "Dry-run planner predicts failure"
    except Exception as exc:
        sandbox_error = f"Dry-run planner failed: {exc}"

    if sandbox_error:
        errors.append(sandbox_error)

    return CandidateValidation(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        sandbox_passed=sandbox_passed,
        sandbox_error=sandbox_error,
    )
