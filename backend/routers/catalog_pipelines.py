"""Pipeline catalog — searchable list of all pipelines with AI descriptions."""

import hashlib

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.auth import get_current_user
from backend.database import get_read_db
from backend.db.redis_pools import get_cache_redis
from backend.models import PipelineSchedule, PipelineRun, User

router = APIRouter(prefix="/api/catalog/pipelines", tags=["Pipeline Catalog"])


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
    for run in rows:
        schedule = schedules.get(run.name)
        cards.append(
            {
                "pipeline_name": run.name,
                "last_run_id": str(run.id),
                "last_run_status": run.status,
                "last_run_at": run.created_at.isoformat(),
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
    cache_key = f"pipeline_desc:{hashlib.md5(pipeline_name.encode()).hexdigest()}"
    redis = get_cache_redis()

    try:
        cached = redis.get(cache_key)
        if cached:
            return {"pipeline_name": pipeline_name, "description": cached.decode("utf-8")}
    except Exception:
        pass

    yaml_row = (
        db.query(PipelineRun.yaml_config)
        .filter(
            PipelineRun.name == pipeline_name,
            PipelineRun.user_id == current_user.id,
        )
        .order_by(PipelineRun.created_at.desc())
        .first()
    )

    if not yaml_row:
        return {
            "pipeline_name": pipeline_name,
            "description": "No runs found for this pipeline.",
        }

    yaml_text = yaml_row[0] if isinstance(yaml_row, tuple) else yaml_row.yaml_config

    prompt = (
        "Describe this data pipeline in exactly 50 words or less, "
        "written for a non-technical business user. "
        "Focus on what data it processes and what result it produces. "
        "Do not mention technical terms like YAML, DuckDB, or Celery.\n\n"
        f"Pipeline YAML:\n{yaml_text[:2000]}"
    )

    try:
        from backend.tasks.gemini_tasks import call_gemini_task

        task = call_gemini_task.apply_async(
            args=[prompt],
            kwargs={"temperature": 0.1, "max_output_tokens": 100},
            queue="gemini",
        )
        description = task.get(timeout=30)
        description = description.strip()

        redis.setex(cache_key, 86400, description.encode("utf-8"))
    except Exception:
        step_count = yaml_text.count("type:")
        description = (
            f"A pipeline named '{pipeline_name}' "
            f"with {step_count} processing steps."
        )

    return {"pipeline_name": pipeline_name, "description": description}
