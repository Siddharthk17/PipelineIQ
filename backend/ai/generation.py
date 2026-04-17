"""
AI pipeline generation service.
Builds prompts, calls Gemini via Celery task, validates responses,
retries once on validation failure.
"""
import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.models import FileProfile, UploadedFile
from backend.pipeline.cache import get_parsed_pipeline
from backend.tasks.gemini_tasks import call_gemini_task
from backend.ai.prompts import (
    GENERATION_SYSTEM_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    SELF_FIX_PROMPT,
    STEP_TYPE_REFERENCE,
)

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    yaml: str
    valid: bool
    attempts: int
    error: str | None = None


@dataclass
class RepairResult:
    corrected_yaml: str
    diff_lines: list[dict]   # [{line_number, type: 'added'|'removed'|'unchanged', content}]
    valid: bool
    error: str | None = None


def build_file_schemas_section(file_ids: list[str], db: Session) -> str:
    """
    Build the file schemas section injected into generation/repair prompts.

    For each file_id: include the filename and all column names with their
    semantic type. This is what prevents Gemini from hallucinating column names.
    """
    if not file_ids:
        return "No files available."

    sections = []

    for i, file_id in enumerate(file_ids, 1):
        try:
            # Get file record
            file_record = db.query(UploadedFile).filter(UploadedFile.id == file_id).first()

            if not file_record:
                sections.append(f"File {i}: [file_id: {file_id}] — file record not found")
                continue

            # Get profile
            profile_record = db.query(FileProfile).filter(FileProfile.file_id == file_id).first()

            if not profile_record or profile_record.status != "complete":
                sections.append(
                    f"File {i}: {file_record.original_filename} (file_id: {file_id})\n"
                    f"  Profile not yet available — upload and wait for profiling to complete"
                )
                continue

            # Build column list with semantic types
            profile = profile_record.profile or {}
            column_descriptions = []
            for col_name, col_data in profile.items():
                semantic_type = col_data.get("semantic_type", "text")
                null_pct = col_data.get("null_pct", 0.0)
                desc = f"{col_name} ({semantic_type}"
                if null_pct > 20:
                    desc += f", {null_pct:.0f}% null"
                desc += ")"
                column_descriptions.append(desc)

            sections.append(
                f"File {i}: {file_record.original_filename} "
                f"(file_id: {file_id})\n"
                f"  Rows: {profile_record.row_count:,}\n"
                f"  Columns: {', '.join(column_descriptions)}"
            )

        except Exception as e:
            logger.error(f"Error building schema for file_id={file_id}: {e}")
            sections.append(f"File {i}: [file_id: {file_id}] — error loading schema")

    return "\n\n".join(sections)


async def generate_pipeline_from_description(
    description: str,
    file_ids: list[str],
    db: Session,
) -> GenerationResult:
    """
    Generate a PipelineIQ pipeline YAML from a natural language description.
    """
    # Build the file schemas section
    file_schemas_section = build_file_schemas_section(file_ids, db)

    # Build the full prompt
    prompt = GENERATION_SYSTEM_PROMPT.format(
        step_type_reference=STEP_TYPE_REFERENCE,
        file_schemas_section=file_schemas_section,
        user_request=description,
    )

    # ── Attempt 1: Generate ───────────────────────────────────────────────
    try:
        raw_yaml = await _call_gemini_async(prompt, temperature=0.1, max_tokens=2000)
        raw_yaml = _clean_yaml_response(raw_yaml)
    except Exception as e:
        return GenerationResult(
            yaml="",
            valid=False,
            attempts=1,
            error=f"AI service error: {str(e)}"
        )

    # Validate Attempt 1
    try:
        get_parsed_pipeline(raw_yaml)
        return GenerationResult(yaml=raw_yaml, valid=True, attempts=1)
    except Exception as validation_error:
        logger.warning(f"Attempt 1 validation failed: {validation_error}")

    # ── Attempt 2: Self-fix ───────────────────────────────────────────────
    fix_prompt = SELF_FIX_PROMPT.format(
        validation_error=str(validation_error),
        invalid_yaml=raw_yaml,
    )

    try:
        corrected_yaml = await _call_gemini_async(fix_prompt, temperature=0.0, max_tokens=2000)
        corrected_yaml = _clean_yaml_response(corrected_yaml)
    except Exception as e:
        return GenerationResult(
            yaml=raw_yaml,
            valid=False,
            attempts=2,
            error=f"Self-fix failed: {str(e)}"
        )

    # Validate Attempt 2
    try:
        get_parsed_pipeline(corrected_yaml)
        return GenerationResult(yaml=corrected_yaml, valid=True, attempts=2)
    except Exception as e2:
        return GenerationResult(
            yaml=corrected_yaml,
            valid=False,
            attempts=2,
            error=str(e2),
        )


async def repair_pipeline_from_error(
    original_yaml: str,
    failed_step: str,
    error_type: str,
    error_message: str,
    file_ids: list[str],
    db: Session,
) -> RepairResult:
    """
    Generate a corrected YAML for a failed pipeline run.
    """
    file_schemas_section = build_file_schemas_section(file_ids, db)

    prompt = REPAIR_SYSTEM_PROMPT.format(
        original_yaml=original_yaml,
        failed_step=failed_step,
        error_type=error_type,
        error_message=error_message,
        file_schemas_section=file_schemas_section,
    )

    try:
        corrected_yaml = await _call_gemini_async(prompt, temperature=0.0, max_tokens=3000)
        corrected_yaml = _clean_yaml_response(corrected_yaml)
    except Exception as e:
        return RepairResult(
            corrected_yaml=original_yaml,
            diff_lines=[],
            valid=False,
            error=str(e),
        )

    # Validate
    valid = True
    validation_error = None
    try:
        get_parsed_pipeline(corrected_yaml)
    except Exception as e:
        valid = False
        validation_error = str(e)

    # Compute diff
    diff_lines = compute_yaml_diff(original_yaml, corrected_yaml)

    return RepairResult(
        corrected_yaml=corrected_yaml,
        diff_lines=diff_lines,
        valid=valid,
        error=validation_error,
    )


def compute_yaml_diff(original: str, corrected: str) -> list[dict]:
    """
    Compute a line-by-line diff between the original and corrected YAML.
    Returns a list of lines with type: 'added', 'removed', or 'unchanged'.
    """
    import difflib
    orig_lines = original.splitlines()
    corr_lines = corrected.splitlines()

    diff = []
    matcher = difflib.SequenceMatcher(None, orig_lines, corr_lines)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == 'equal':
            for line in orig_lines[i1:i2]:
                diff.append({"type": "unchanged", "content": line})
        elif op == 'insert':
            for line in corr_lines[j1:j2]:
                diff.append({"type": "added", "content": line})
        elif op == 'delete':
            for line in orig_lines[i1:i2]:
                diff.append({"type": "removed", "content": line})
        elif op == 'replace':
            for line in orig_lines[i1:i2]:
                diff.append({"type": "removed", "content": line})
            for line in corr_lines[j1:j2]:
                diff.append({"type": "added", "content": line})

    return diff


async def _call_gemini_async(prompt: str, temperature: float, max_tokens: int) -> str:
    """
    Submit a Gemini task and await the result.
    Uses Celery task async to respect the gemini queue's rate limits.
    """
    task = call_gemini_task.apply_async(
        args=[prompt],
        kwargs={"temperature": temperature, "max_output_tokens": max_tokens},
        queue="gemini",
    )
    # Wait for result with timeout (Celery task, not coroutine)
    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: task.get(timeout=120)
    )
    return result


def _clean_yaml_response(raw: str) -> str:
    """
    Strip common Gemini formatting artifacts from the response.

    Gemini sometimes wraps YAML in markdown code fences despite instructions.
    This function removes them and extracts the raw YAML.
    """
    text = raw.strip()

    # Remove markdown code fences
    if text.startswith("```yaml"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]

    # Find the start of the pipeline YAML (should start with "pipeline:")
    pipeline_start = text.find("pipeline:")
    if pipeline_start > 0:
        # Strip any preamble before "pipeline:"
        text = text[pipeline_start:]

    return text.strip()
