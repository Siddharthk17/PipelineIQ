"""
AI feature endpoints for PipelineIQ.
All AI calls are non-blocking — they queue a Celery task and return immediately.
The client polls for completion using the task_id.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.config import settings
from backend.dependencies import get_read_db_dependency
from backend.models import PipelineRun, UploadedFile, User
from backend.pipeline.cache import get_parsed_pipeline
from backend.pipeline.exceptions import ClientDisconnected
from backend.utils.rate_limiter import limiter
from backend.utils.uuid_utils import as_uuid

router = APIRouter(prefix="/api/ai", tags=["AI"])
logger = logging.getLogger(__name__)

# M-108: overall bound on a single generate/repair request. The Gemini
# worker call itself times out at 120s; the router allows headroom for
# validation + self-fix, then fails fast with 504 instead of holding the
# request (and the client) forever.
AI_REQUEST_TIMEOUT_SECONDS = 150.0

# M-039: poll interval for the client-disconnect watcher.
_DISCONNECT_POLL_SECONDS = 0.1


async def _wait_for_disconnect(request: Request) -> None:
    """Complete (by raising) only when the client actually goes away.

    The previous one-shot `is_disconnected()` check is non-blocking: it
    completes instantly when the client is still connected, so racing it
    with FIRST_COMPLETED always cancelled the real AI task (M-039).
    Polling until a real disconnect keeps generation alive while still
    abandoning Gemini quota promptly when the client leaves.
    """
    while True:
        if await request.is_disconnected():
            raise ClientDisconnected()
        await asyncio.sleep(_DISCONNECT_POLL_SECONDS)


async def _run_with_disconnect_guard(request: Request, coro):
    """Await an AI coroutine, abandoning it if the client disconnects.

    Returns the coroutine result. Raises ClientDisconnected (mapped to
    499 by middleware) when the client goes away first, HTTPException(504)
    when AI_REQUEST_TIMEOUT_SECONDS elapses. Only the pending task is
    ever cancelled — a finished generation result is never discarded.
    """
    ai_task = asyncio.create_task(coro)
    disconnect_task = asyncio.create_task(_wait_for_disconnect(request))
    try:
        done, _pending = await asyncio.wait_for(
            asyncio.wait(
                {ai_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            ),
            timeout=AI_REQUEST_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        for task in (ai_task, disconnect_task):
            if not task.done():
                task.cancel()
        raise HTTPException(
            status_code=504,
            detail="AI request timed out. Please try again later.",
        ) from exc
    try:
        if disconnect_task in done:
            # Client is gone: stop burning Gemini quota.
            if not ai_task.done():
                ai_task.cancel()
            # Raises ClientDisconnected -> 499 via middleware.
            await disconnect_task
            raise AssertionError("unreachable: disconnect completed silently")
        # Generation finished: stop the watcher and return its result.
        if not disconnect_task.done():
            disconnect_task.cancel()
        return await ai_task
    finally:
        for task in (ai_task, disconnect_task):
            if not task.done():
                task.cancel()


async def generate_pipeline_from_description(**kwargs):
    from backend.ai.generation import (
        generate_pipeline_from_description as _generate_pipeline_from_description,
    )

    return await _generate_pipeline_from_description(**kwargs)


async def repair_pipeline_from_error(**kwargs):
    from backend.ai.generation import (
        repair_pipeline_from_error as _repair_pipeline_from_error,
    )

    return await _repair_pipeline_from_error(**kwargs)


def suggest_column(typed: str, available_columns: list[str]) -> str | None:
    from backend.ai.autocomplete import suggest_column as _suggest_column

    return _suggest_column(typed, available_columns)


def suggest_columns_batch(
    typed_columns: list[str], available_columns: list[str]
) -> dict[str, str | None]:
    from backend.ai.autocomplete import (
        suggest_columns_batch as _suggest_columns_batch,
    )

    return _suggest_columns_batch(typed_columns, available_columns)


# Request/Response Models


class GeneratePipelineRequest(BaseModel):
    description: str = Field(
        ...,
        min_length=10,
        max_length=1000,
        description="Natural language description of what the pipeline should do",
    )
    file_ids: list[str] = Field(
        ..., min_length=1, description="UUIDs of uploaded files the pipeline can reference"
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


AI_UNAVAILABLE_MESSAGE = (
    "AI features are unavailable: GEMINI_API_KEY is not configured. "
    "Set it in .env and restart the gemini worker."
)


def _raise_if_ai_unavailable() -> None:
    """Fail fast when no Gemini key is configured.

    Without a key every AI request is doomed: the endpoint would enqueue a
    task that fails immediately, spamming ERROR tracebacks in the worker
    logs while the client hangs and eventually disconnects (499). A 503
    lets the UI show the real cause instantly.
    """
    from backend.clients.gemini_client import gemini_configured

    if not gemini_configured():
        raise HTTPException(status_code=503, detail=AI_UNAVAILABLE_MESSAGE)


# Endpoints


@router.post("/generate", response_model=GeneratePipelineResponse)
@limiter.limit(settings.RATE_LIMIT_PIPELINE_RUN)
async def generate_pipeline(
    request: Request,
    body: GeneratePipelineRequest,
    response: Response,
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
    _raise_if_ai_unavailable()
    # Verify all file_ids belong to the current user
    for file_id in body.file_ids:
        file_record = _get_file_or_404(file_id, current_user.id, db)
        if not file_record:
            raise HTTPException(404, f"File not found: {file_id}")

    # Guard the AI call against client disconnect (abandon quota promptly)
    # and bound the total wait (M-039, M-108).
    result = await _run_with_disconnect_guard(
        request,
        generate_pipeline_from_description(
            description=body.description,
            file_ids=body.file_ids,
            db=db,
            request_id=getattr(request.state, "request_id", None),
        ),
    )

    return GeneratePipelineResponse(
        yaml=result.yaml,
        valid=result.valid,
        attempts=result.attempts,
        error=result.error,
    )


@router.post("/runs/{run_id}/repair", response_model=RepairPipelineResponse)
@limiter.limit(settings.RATE_LIMIT_PIPELINE_RUN)
async def repair_failed_run(
    request: Request,
    response: Response,
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = get_read_db_dependency(),
):
    """
    Generate a corrected YAML for a failed pipeline run.

    Only runs with status='FAILED' can be repaired.
    Returns the corrected YAML plus a line-by-line diff for the user to review.
    """
    _raise_if_ai_unavailable()
    try:
        run_uuid = as_uuid(run_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Invalid run_id format") from exc

    # Get the failed run
    run = (
        db.query(PipelineRun)
        .filter(PipelineRun.id == run_uuid, PipelineRun.user_id == current_user.id)
        .first()
    )
    if not run:
        raise HTTPException(404, "Run not found")

    # Actually the string in DB is "FAILED" for enum PipelineStatus
    if run.status.value != "FAILED":
        raise HTTPException(
            400,
            f"Cannot repair a run with status '{run.status.value}'. Only 'failed' runs can be repaired.",
        )

    # Extract error information from the run
    # PipelineRun error details are usually in `error_message` or
    # `step_results`
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

    # Guard the AI call against client disconnect (abandon quota promptly)
    # and bound the total wait (M-039, M-108).
    result = await _run_with_disconnect_guard(
        request,
        repair_pipeline_from_error(
            original_yaml=run.yaml_config,
            failed_step=failed_step,
            error_type=error_type,
            error_message=error_message,
            file_ids=file_ids,
            db=db,
            request_id=getattr(request.state, "request_id", None),
        ),
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

    The Jaro-Winkler scan is CPU-bound: inputs are capped in
    backend.ai.autocomplete (M-016) and the work runs in a worker thread so
    the event loop is never blocked.
    """
    import jellyfish

    suggestion = await asyncio.to_thread(suggest_column, request.typed, request.available_columns)
    confidence = None

    if suggestion:
        confidence = jellyfish.jaro_winkler_similarity(request.typed.lower(), suggestion.lower())

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

    Offloaded to a worker thread (M-016): batch cost is O(queries *
    columns) and must not stall the loop.
    """
    suggestions = await asyncio.to_thread(
        suggest_columns_batch,
        request.typed_columns,
        request.available_columns,
    )
    return AutocompleteBatchResponse(suggestions=suggestions)


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


def _get_file_or_404(file_id: str, user_id, db: Session):
    """Get a file record belonging to the user, or return None."""
    try:
        file_uuid = as_uuid(file_id)
        owner_id = as_uuid(user_id)
    except (ValueError, TypeError):
        return None

    return (
        db.query(UploadedFile)
        .filter(UploadedFile.id == file_uuid, UploadedFile.user_id == owner_id)
        .first()
    )
