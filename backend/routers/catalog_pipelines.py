"""Pipeline catalog — searchable list of all pipelines with AI descriptions."""

import hashlib
from typing import cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.database import get_read_db
from backend.db.redis_pools import get_cache_redis
from backend.models import PipelineRun, PipelineSchedule, User

router = APIRouter(prefix="/api/catalog/pipelines", tags=["Pipeline Catalog"])


def _cache_key(user_id: str, pipeline_name: str) -> str:
    cache_input = f"{user_id}:{pipeline_name}".encode()
    return f"pipeline_desc:{hashlib.sha256(cache_input).hexdigest()}"


def _fallback_description(pipeline_name: str, yaml_text: str | None) -> str:
    step_count = (yaml_text or "").count("type:")
    return (
        f"A pipeline named '{pipeline_name}' "
        f"with {step_count} processing steps."
    )


def _resolve_description(
    redis_client,
    user_id: str,
    pipeline_name: str,
    yaml_text: str | None,
) -> tuple[str, str | None]:
    """Return (description, prompt_or_None).

    Cached AI text wins; otherwise returns the instant fallback and a prompt
    the caller may enqueue for background generation (None when there is
    nothing to describe or no key configured).
    """
    cache_key = _cache_key(user_id, pipeline_name)
    try:
        cached = cast("str | None", redis_client.get(cache_key))
        if cached:
            return cached, None
    except Exception:
        pass

    if not isinstance(yaml_text, str):
        yaml_text = None

    if yaml_text is None:
        return f"No runs found for pipeline '{pipeline_name}'.", None

    if not yaml_text.strip():
        # A run row with empty YAML has nothing to describe; don't waste a
        # background task on it.
        return _fallback_description(pipeline_name, yaml_text), None

    prompt = (
        "Describe this data pipeline in exactly 50 words or less, "
        "written for a non-technical business user. "
        "Focus on what data it processes and what result it produces. "
        "Do not mention technical terms like YAML, DuckDB, or Celery.\n\n"
        f"Pipeline YAML:\n{yaml_text[:2000]}"
    )
    return _fallback_description(pipeline_name, yaml_text), prompt


def _enqueue_description(
    redis_client, cache_key: str, prompt: str, tenant_id: str
) -> None:
    """Queue background AI description generation (best-effort, once per key)."""
    from backend.clients.gemini_client import gemini_configured

    if not gemini_configured():
        return
    try:
        queued_key = f"{cache_key}:queued"
        if bool(redis_client.set(queued_key, b"1", ex=300, nx=True)):
            from backend.tasks.gemini_tasks import generate_pipeline_description_task

            generate_pipeline_description_task.apply_async(
                args=[cache_key, prompt],
                kwargs={"tenant_id": tenant_id},
                queue="gemini",
            )
    except Exception:
        pass


@router.get("")
def list_catalog_pipelines(
    q: str | None = Query(None, description="Search pipeline names"),
    status: str | None = Query(None, description="Filter by last run status"),
    limit: int = Query(default=50, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db),
):
    latest_runs_sql = text(
        """
        SELECT DISTINCT ON (name)
            id, name, status, created_at, yaml_config, user_id
        FROM pipeline_runs
        WHERE user_id = :user_id
        ORDER BY name, created_at DESC
        """
    )
    rows = db.execute(latest_runs_sql, {"user_id": str(current_user.id)}).fetchall()

    if q:
        q_lower = q.lower()
        rows = [r for r in rows if q_lower in r.name.lower()]

    if status:
        rows = [r for r in rows if r.status == status]

    rows = rows[:limit]

    pipeline_names = [r.name for r in rows]
    schedules = {}
    if pipeline_names:
        schedule_rows = (
            db.query(PipelineSchedule)
            .filter(
                PipelineSchedule.pipeline_name.in_(pipeline_names),
                PipelineSchedule.user_id == current_user.id,
            )
            .all()
        )
        schedules = {s.pipeline_name: s for s in schedule_rows}

    cards = []
    redis = get_cache_redis()
    user_id = str(current_user.id)
    for run in rows:
        schedule = schedules.get(run.name)
        yaml_text = run.yaml_config
        if not isinstance(yaml_text, str):
            yaml_text = None
        description, prompt = _resolve_description(redis, user_id, run.name, yaml_text)
        if prompt is not None:
            _enqueue_description(redis, _cache_key(user_id, run.name), prompt, user_id)
        cards.append(
            {
                "pipeline_name": run.name,
                "last_run_id": str(run.id),
                "last_run_status": run.status,
                "last_run_at": run.created_at.isoformat(),
                "description": description,
                "schedule": (
                    {
                        "active": schedule.is_active if schedule else False,
                        "cron_human": schedule.cron_human if schedule else None,
                        "next_run_at": (
                            schedule.next_run_at.isoformat()
                            if schedule and schedule.next_run_at
                            else None
                        ),
                    }
                    if schedule
                    else None
                ),
            }
        )

    return {"pipelines": cards, "total": len(cards)}


@router.get("/{pipeline_name}/description")
def get_pipeline_description(
    pipeline_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db),
):
    redis = get_cache_redis()

    yaml_row = (
        db.query(PipelineRun.yaml_config)
        .filter(
            PipelineRun.name == pipeline_name,
            PipelineRun.user_id == current_user.id,
        )
        .order_by(PipelineRun.created_at.desc())
        .first()
    )

    yaml_text = None
    if yaml_row:
        yaml_text = yaml_row[0] if isinstance(yaml_row, tuple) else yaml_row.yaml_config

    user_id = str(current_user.id)
    cache_key = _cache_key(user_id, pipeline_name)
    description, prompt = _resolve_description(redis, user_id, pipeline_name, yaml_text)
    if prompt is not None:
        _enqueue_description(redis, cache_key, prompt, user_id)

    return {"pipeline_name": pipeline_name, "description": description}
