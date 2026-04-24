"""Autonomous healing orchestration for schema-drift pipeline failures."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import orjson
from celery.result import allow_join_result
from sqlalchemy.orm import Session

from backend.ai.generation import compute_yaml_diff
from backend.ai.healing_prompts import build_healing_prompt, validate_healing_patch
from backend.execution.patch_applier import apply_patch
from backend.execution.sandbox import run_patch_in_sandbox
from backend.execution.schema_diff import compute_schema_diff
from backend.models import FileProfile, HealingAttempt, HealingAttemptStatus, SchemaSnapshot, UploadedFile
from backend.tasks.gemini_tasks import call_gemini_task
from backend.utils.time_utils import utcnow
from backend.utils.uuid_utils import as_uuid

logger = logging.getLogger(__name__)

MAX_HEALING_RETRIES = 3


def _is_quota_error(error: Exception) -> tuple[bool, str]:
    """Check if error is a quota/API limit issue.
    Returns (is_quota_error, user_friendly_message)
    """
    error_str = str(error).lower()
    quota_indicators = [
        "429",
        "quota",
        "resource_exhausted",
        "limit: 0",
        "free-tier",
        "rate_limit",
        "RESOURCE_EXHAUSTED",
    ]
    if any(indicator.lower() in error_str for indicator in quota_indicators):
        if "limit: 0" in error_str:
            return True, "AI service temporarily unavailable (quota exceeded). Try again later."
        if "429" in error_str:
            return True, "AI service rate limited. Waiting before retry..."
        return True, "AI quota exceeded. Please wait and try again."
    return False, ""


def _get_user_friendly_error(error: Exception) -> str:
    """Convert technical errors to user-friendly messages."""
    is_quota, quota_msg = _is_quota_error(error)
    if is_quota:
        return quota_msg
    
    error_str = str(error).lower()
    
    if "json" in error_str and "parse" in error_str:
        return "AI returned invalid response format"
    
    if "timeout" in error_str:
        return "AI service request timed out"
    
    if "connection" in error_str or "network" in error_str:
        return "Network error connecting to AI service"
    
    # Default: return truncated original error
    return f"AI error: {str(error)[:100]}"


@dataclass
class HealingResult:
    """Result returned after the healing agent exhausts its attempts."""

    success: bool
    patched_yaml: str | None = None
    confidence: float = 0.0
    description: str = ""
    attempts: int = 0
    error: str | None = None
    schema_diff: dict = field(default_factory=dict)
    patch: dict | None = None


def attempt_heal(
    *,
    run_id: str,
    pipeline_name: str,
    failed_step: str,
    error: Exception,
    pipeline_yaml: str,
    file_ids: list[str],
    db: Session,
) -> HealingResult:
    """Attempt to repair a failed pipeline by generating and validating JSON patches."""
    old_schema, new_schema = _load_schema_pair(
        run_id=run_id,
        file_ids=file_ids,
        db=db,
        error=error,
    )
    schema_diff = compute_schema_diff(old_schema, new_schema)

    for attempt_number in range(1, MAX_HEALING_RETRIES + 1):
        prompt = build_healing_prompt(
            broken_yaml=pipeline_yaml,
            error_type=type(error).__name__,
            error_message=str(error),
            failed_step_name=failed_step,
            old_schema=old_schema,
            new_schema=new_schema,
            schema_diff=schema_diff,
        )

        try:
            raw_response = _call_gemini_for_healing(prompt)
        except Exception as exc:
            # Check if this is a quota error - if so, stop trying immediately
            is_quota, _ = _is_quota_error(exc)
            user_msg = _get_user_friendly_error(exc)
            
            _record_attempt(
                db=db,
                run_id=run_id,
                pipeline_name=pipeline_name,
                attempt_number=attempt_number,
                status=HealingAttemptStatus.FAILED,
                failed_step=failed_step,
                error=error,
                old_schema=old_schema,
                new_schema=new_schema,
                schema_diff=schema_diff,
                ai_error=user_msg,
            )
            
            # Don't retry on quota errors - they won't succeed anyway
            if is_quota:
                logger.warning(
                    f"Healing stopped: AI quota exceeded. "
                    f"User message: {user_msg}"
                )
                break
            continue

        try:
            patch = _parse_gemini_patch(raw_response)
        except Exception as exc:
            _record_attempt(
                db=db,
                run_id=run_id,
                pipeline_name=pipeline_name,
                attempt_number=attempt_number,
                status=HealingAttemptStatus.AI_INVALID,
                failed_step=failed_step,
                error=error,
                old_schema=old_schema,
                new_schema=new_schema,
                schema_diff=schema_diff,
                ai_error=f"Invalid JSON patch: {exc}",
                gemini_patch={"raw_response": raw_response[:1000]},
            )
            continue

        is_valid, validation_error = validate_healing_patch(patch)
        if not is_valid:
            _record_attempt(
                db=db,
                run_id=run_id,
                pipeline_name=pipeline_name,
                attempt_number=attempt_number,
                status=HealingAttemptStatus.AI_INVALID,
                failed_step=failed_step,
                error=error,
                old_schema=old_schema,
                new_schema=new_schema,
                schema_diff=schema_diff,
                ai_error=validation_error,
                gemini_patch=patch,
            )
            continue

        if not patch.get("patches"):
            _record_attempt(
                db=db,
                run_id=run_id,
                pipeline_name=pipeline_name,
                attempt_number=attempt_number,
                status=HealingAttemptStatus.AI_INVALID,
                failed_step=failed_step,
                error=error,
                old_schema=old_schema,
                new_schema=new_schema,
                schema_diff=schema_diff,
                ai_error="Gemini returned no patch operations",
                gemini_patch=patch,
            )
            continue

        try:
            patched_yaml = apply_patch(pipeline_yaml, patch)
        except Exception as exc:
            _record_attempt(
                db=db,
                run_id=run_id,
                pipeline_name=pipeline_name,
                attempt_number=attempt_number,
                status=HealingAttemptStatus.VALIDATION_FAILED,
                failed_step=failed_step,
                error=error,
                old_schema=old_schema,
                new_schema=new_schema,
                schema_diff=schema_diff,
                ai_error=f"Patch application failed: {exc}",
                gemini_patch=patch,
                validation_errors=[str(exc)],
            )
            continue

        sandbox_result = run_patch_in_sandbox(
            patched_yaml=patched_yaml,
            file_ids=file_ids,
            db=db,
        )
        if not sandbox_result.success:
            _record_attempt(
                db=db,
                run_id=run_id,
                pipeline_name=pipeline_name,
                attempt_number=attempt_number,
                status=HealingAttemptStatus.VALIDATION_FAILED,
                failed_step=failed_step,
                error=error,
                old_schema=old_schema,
                new_schema=new_schema,
                schema_diff=schema_diff,
                gemini_patch=patch,
                sandbox_result=_sandbox_payload(sandbox_result),
                proposed_yaml=patched_yaml,
                diff_lines=compute_yaml_diff(pipeline_yaml, patched_yaml),
                parser_valid=True,
                sandbox_passed=False,
                validation_errors=[sandbox_result.error] if sandbox_result.error else None,
            )
            continue

        _record_attempt(
            db=db,
            run_id=run_id,
            pipeline_name=pipeline_name,
            attempt_number=attempt_number,
            status=HealingAttemptStatus.APPLIED,
            failed_step=failed_step,
            error=error,
            old_schema=old_schema,
            new_schema=new_schema,
            schema_diff=schema_diff,
            gemini_patch=patch,
            sandbox_result=_sandbox_payload(sandbox_result),
            proposed_yaml=patched_yaml,
            diff_lines=compute_yaml_diff(pipeline_yaml, patched_yaml),
            parser_valid=True,
            sandbox_passed=True,
            applied=True,
            confidence=float(patch.get("confidence", 0.0)),
            healed_at=utcnow(),
        )
        return HealingResult(
            success=True,
            patched_yaml=patched_yaml,
            confidence=float(patch.get("confidence", 0.0)),
            description=str(patch.get("change_description", "")),
            attempts=attempt_number,
            schema_diff=schema_diff,
            patch=patch,
        )

    return HealingResult(
        success=False,
        attempts=MAX_HEALING_RETRIES,
        error=f"Healing failed after {MAX_HEALING_RETRIES} attempts",
        schema_diff=schema_diff,
    )


def _call_gemini_for_healing(prompt: str) -> str:
    task = call_gemini_task.apply_async(
        args=[prompt],
        kwargs={"temperature": 0.0, "max_output_tokens": 1000},
        queue="gemini",
    )
    with allow_join_result():
        return task.get(timeout=120)


def _parse_gemini_patch(raw_response: str) -> dict:
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    return orjson.loads(cleaned)


def _record_attempt(
    *,
    db: Session,
    run_id: str,
    pipeline_name: str,
    attempt_number: int,
    status: HealingAttemptStatus,
    failed_step: str,
    error: Exception,
    old_schema: dict,
    new_schema: dict,
    schema_diff: dict,
    gemini_patch: dict | None = None,
    sandbox_result: dict | None = None,
    ai_error: str | None = None,
    proposed_yaml: str | None = None,
    diff_lines: list[dict] | None = None,
    parser_valid: bool | None = None,
    sandbox_passed: bool | None = None,
    validation_errors: list[str] | None = None,
    applied: bool = False,
    confidence: float | None = None,
    healed_at=None,
) -> None:
    attempt = HealingAttempt(
        run_id=as_uuid(run_id),
        pipeline_name=pipeline_name,
        attempt_number=attempt_number,
        status=status,
        failed_step=failed_step or None,
        error_type=type(error).__name__,
        error_message=str(error),
        old_schema=old_schema,
        new_schema=new_schema,
        removed_columns=schema_diff.get("removed_columns", []),
        added_columns=schema_diff.get("added_columns", []),
        renamed_candidates=schema_diff.get("renamed_candidates", []),
        gemini_patch=gemini_patch,
        sandbox_result=sandbox_result,
        applied=applied,
        confidence=confidence,
        healed_at=healed_at,
        classification_reason=schema_diff.get("summary"),
        proposed_yaml=proposed_yaml,
        diff_lines=diff_lines,
        ai_valid=gemini_patch is not None and ai_error is None,
        ai_error=ai_error,
        parser_valid=parser_valid,
        sandbox_passed=sandbox_passed,
        validation_errors=validation_errors,
        completed_at=utcnow(),
    )
    db.add(attempt)
    db.commit()


def _sandbox_payload(sandbox_result) -> dict:
    return {
        "success": sandbox_result.success,
        "output_rows": sandbox_result.output_rows,
        "output_columns": sandbox_result.output_columns,
        "error": sandbox_result.error,
        "duration_ms": sandbox_result.duration_ms,
    }


def _load_schema_pair(*, run_id: str, file_ids: list[str], db: Session, error: Exception) -> tuple[dict, dict]:
    old_schema: dict = {}
    new_schema: dict = {}

    for file_id in file_ids:
        uploaded_file = (
            db.query(UploadedFile)
            .filter(UploadedFile.id == as_uuid(file_id))
            .first()
        )
        if uploaded_file is None:
            continue

        profile_record = db.query(FileProfile).filter(FileProfile.file_id == uploaded_file.id).first()
        snapshot = (
            db.query(SchemaSnapshot)
            .filter(
                SchemaSnapshot.run_id == as_uuid(run_id),
                SchemaSnapshot.file_id == uploaded_file.id,
            )
            .order_by(SchemaSnapshot.captured_at.desc())
            .first()
        )

        old_schema.update(_normalize_snapshot_schema(snapshot=snapshot, uploaded_file=uploaded_file, profile_record=profile_record))
        new_schema.update(_normalize_current_schema(uploaded_file=uploaded_file, profile_record=profile_record))

    _overlay_error_context(new_schema=new_schema, error=error)
    return old_schema, new_schema


def _normalize_snapshot_schema(*, snapshot, uploaded_file: UploadedFile, profile_record: FileProfile | None) -> dict:
    if snapshot is None:
        columns = uploaded_file.columns or []
        dtypes = uploaded_file.dtypes or {}
    else:
        columns = snapshot.columns or []
        dtypes = snapshot.dtypes or {}

    # Safely extract profile - handle various storage types
    profile = {}
    if profile_record and profile_record.status == "complete":
        raw_profile = profile_record.profile
        if isinstance(raw_profile, dict):
            profile = raw_profile
        elif isinstance(raw_profile, list):
            # Convert list to dict by column name
            for item in raw_profile:
                if isinstance(item, dict) and "column" in item:
                    profile[item["column"]] = item
    
    normalized: dict[str, dict] = {}
    for column in columns:
        profile_entry = profile.get(column, {}) if isinstance(profile, dict) else {}
        normalized[column] = {
            "dtype": dtypes.get(column),
            "semantic_type": profile_entry.get("semantic_type", "unknown") if isinstance(profile_entry, dict) else "unknown",
            "null_pct": profile_entry.get("null_pct") if isinstance(profile_entry, dict) else None,
        }
    return normalized


def _normalize_current_schema(*, uploaded_file: UploadedFile, profile_record: FileProfile | None) -> dict:
    # Safely extract profile - handle various storage types
    raw_profile = None
    if profile_record and profile_record.status == "complete":
        raw_profile = profile_record.profile
    
    profile = {}
    if isinstance(raw_profile, dict):
        profile = raw_profile
    elif isinstance(raw_profile, list):
        for item in raw_profile:
            if isinstance(item, dict) and "column" in item:
                profile[item["column"]] = item
    
    if profile:
        normalized = {}
        for column, profile_entry in profile.items():
            if not isinstance(profile_entry, dict):
                profile_entry = {}
            normalized[column] = {
                "dtype": (uploaded_file.dtypes or {}).get(column),
                "semantic_type": profile_entry.get("semantic_type", "unknown"),
                "null_pct": profile_entry.get("null_pct"),
            }
        return normalized
        return normalized

    return {
        column: {
            "dtype": (uploaded_file.dtypes or {}).get(column),
            "semantic_type": "unknown",
            "null_pct": None,
        }
        for column in (uploaded_file.columns or [])
    }


def _overlay_error_context(*, new_schema: dict, error: Exception) -> None:
    available_columns = getattr(error, "available_columns", None)
    if not isinstance(available_columns, list):
        return

    for column in available_columns:
        new_schema.setdefault(
            column,
            {"dtype": None, "semantic_type": "unknown", "null_pct": None},
        )
