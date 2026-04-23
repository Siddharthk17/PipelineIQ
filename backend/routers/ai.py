"""
AI feature endpoints for PipelineIQ.
All AI calls are non-blocking — they queue a Celery task and return immediately.
The client polls for completion using the task_id.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.dependencies import get_read_db_dependency
from backend.auth import get_current_user
from backend.models import User, PipelineRun, UploadedFile
from backend.ai.generation import (
    generate_pipeline_from_description,
    repair_pipeline_from_error,
)
from backend.ai.autocomplete import suggest_column, suggest_columns_batch
from backend.pipeline.cache import get_parsed_pipeline
from backend.utils.uuid_utils import as_uuid

router = APIRouter(prefix="/api/ai", tags=["AI"])
logger = logging.getLogger(__name__)


# Request/Response Models

class GeneratePipelineRequest(BaseModel):
    description: str = Field(
        ...,
        min_length=10,
        max_length=1000,
        description="Natural language description of what the pipeline should do"
    )
    file_ids: list[str] = Field(
        ...,
        min_items=1,
        description="UUIDs of uploaded files the pipeline can reference"
    )

class GeneratePipelineResponse(BaseModel):
    yaml: str
    valid: bool
    attempts: int
    error: str | None = None


class RepairPipelineRequest(BaseModel):
    run_id: str


class RepairPipelineResponse(BaseModel):
    corrected_yaml: str
    diff_lines: list[dict]
    valid: bool
    error: str | None = None


class AutocompleteRequest(BaseModel):
    typed: str
    available_columns: list[str]

class AutocompleteResponse(BaseModel):
    suggestion: str | None
    confidence: float | None


class AutocompleteBatchRequest(BaseModel):
    typed_columns: list[str]
    available_columns: list[str]


class AutocompleteBatchResponse(BaseModel):
    suggestions: dict[str, str | None]


class ValidateYamlRequest(BaseModel):
    yaml_text: str

class ValidateYamlResponse(BaseModel):
    valid: bool
    error: str | None = None
    step_count: int = 0


# Endpoints

@router.post("/generate", response_model=GeneratePipelineResponse)
async def generate_pipeline(
    request: GeneratePipelineRequest,
    current_user: User = Depends(get_current_user),
    db: Session = get_read_db_dependency(),
):
    """
    Generate a PipelineIQ pipeline YAML from a natural language description.

    This endpoint is synchronous from the client's perspective (it waits for
    Gemini to respond) but uses the Gemini Celery queue internally to enforce
    rate limits and enable caching.

    Typical response time: 3-8 seconds for Gemini to generate.
    """
    # Verify all file_ids belong to the current user
    for file_id in request.file_ids:
        file_record = _get_file_or_404(file_id, current_user.id, db)
        if not file_record:
            raise HTTPException(404, f"File not found: {file_id}")

    result = await generate_pipeline_from_description(
        description=request.description,
        file_ids=request.file_ids,
        db=db,
    )

    return GeneratePipelineResponse(
        yaml=result.yaml,
        valid=result.valid,
        attempts=result.attempts,
        error=result.error,
    )


@router.post("/runs/{run_id}/repair", response_model=RepairPipelineResponse)
async def repair_failed_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = get_read_db_dependency(),
):
    """
    Generate a corrected YAML for a failed pipeline run.

    Only runs with status='FAILED' can be repaired.
    Returns the corrected YAML plus a line-by-line diff for the user to review.
    """
    try:
        run_uuid = as_uuid(run_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid run_id format")

    # Get the failed run
    run = db.query(PipelineRun).filter(PipelineRun.id == run_uuid, PipelineRun.user_id == current_user.id).first()
    if not run:
        raise HTTPException(404, "Run not found")
    
    # Actually the string in DB is "FAILED" for enum PipelineStatus
    if run.status.value != "FAILED":
        raise HTTPException(400, f"Cannot repair a run with status '{run.status.value}'. Only 'failed' runs can be repaired.")

    # Extract error information from the run
    # PipelineRun error details are usually in `error_message` or `step_results`
    failed_step = "unknown"
    error_type = "Exception"
    error_message = run.error_message or "Unknown error"
    
    # Try to find the step that failed
    for step_result in run.step_results:
        if step_result.status.value == "FAILED":
            failed_step = step_result.step_name
            if step_result.error_message:
                error_message = step_result.error_message
            break

    # Get file IDs referenced by load steps.
    file_ids: list[str] = []
    try:
        parsed_pipeline = get_parsed_pipeline(run.yaml_config)
        for step in parsed_pipeline.steps:
            step_file_id = getattr(step, "file_id", None)
            if step_file_id:
                file_ids.append(step_file_id)
    except Exception:
        pass

    result = await repair_pipeline_from_error(
        original_yaml=run.yaml_config,
        failed_step=failed_step,
        error_type=error_type,
        error_message=error_message,
        file_ids=file_ids,
        db=db,
    )

    return RepairPipelineResponse(
        corrected_yaml=result.corrected_yaml,
        diff_lines=result.diff_lines,
        valid=result.valid,
        error=result.error,
    )


@router.post("/autocomplete/column", response_model=AutocompleteResponse)
async def autocomplete_column(
    request: AutocompleteRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Suggest the closest column name for a probable typo.
    Returns the suggestion and a confidence score, or null if no close match.
    """
    import jellyfish

    suggestion = suggest_column(request.typed, request.available_columns)
    confidence = None

    if suggestion:
        confidence = jellyfish.jaro_winkler_similarity(
            request.typed.lower(), suggestion.lower()
        )

    return AutocompleteResponse(
        suggestion=suggestion,
        confidence=round(confidence, 4) if confidence else None,
    )


@router.post("/autocomplete/columns", response_model=AutocompleteBatchResponse)
async def autocomplete_columns(
    request: AutocompleteBatchRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Batch autocomplete for multiple possibly-typed column names.
    """
    return AutocompleteBatchResponse(
        suggestions=suggest_columns_batch(
            request.typed_columns,
            request.available_columns,
        )
    )


@router.post("/validate-yaml", response_model=ValidateYamlResponse)
async def validate_yaml(
    request: ValidateYamlRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Validate pipeline YAML and return any errors.
    Uses the cached parser — fast even for large YAML.
    """
    try:
        pipeline = get_parsed_pipeline(request.yaml_text)
        # Count steps — adjust to match what parse_pipeline_yaml returns
        step_count = len(getattr(pipeline, "steps", []))
        return ValidateYamlResponse(valid=True, step_count=step_count)
    except Exception as e:
        return ValidateYamlResponse(valid=False, error=str(e), step_count=0)


def _get_file_or_404(file_id: str, user_id: str, db: Session):
    """Get a file record belonging to the user, or return None."""
    try:
        file_uuid = as_uuid(file_id)
    except (ValueError, TypeError):
        return None

    return (
        db.query(UploadedFile)
        .filter(UploadedFile.id == file_uuid, UploadedFile.user_id == user_id)
        .first()
    )
