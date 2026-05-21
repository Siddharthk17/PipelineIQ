"""OpenLineage export endpoints.

GET /api/runs/{run_id}/openlineage -- single run event (JSON)
GET /api/lineage/export -- all runs as NDJSON bulk export
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.config import settings
from backend.dependencies import get_read_db_dependency
from backend.models import User, PipelineRun
from backend.openlineage.builder import build_openlineage_event
from backend.repositories.catalog import get_cached_run_lineage
from backend.utils.rate_limiter import limiter
from backend.utils.uuid_utils import as_uuid

router = APIRouter(prefix="/api", tags=["Lineage"])
logger = logging.getLogger(__name__)


@router.get("/runs/{run_id}/openlineage")
@limiter.limit(settings.RATE_LIMIT_READ)
def get_run_openlineage_event(
    request: Request,
    response: Response,
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = get_read_db_dependency(),
):
    """Export a pipeline run as an OpenLineage 1.0 RunEvent.

    Compatible with DataHub, Marquez, OpenMetadata, and any OpenLineage consumer.
    """
    run_uuid = as_uuid(run_id)
    result = db.execute(
        select(PipelineRun)
        .where(PipelineRun.id == run_uuid)
        .where(PipelineRun.user_id == current_user.id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")

    try:
        lineage_graph = get_cached_run_lineage(run_id, db)
    except Exception as e:
        logger.warning("Could not load lineage for run %s: %s", run_id, e)
        import networkx as nx
        lineage_graph = nx.DiGraph()

    duration_ms = None
    if run.started_at and run.completed_at:
        duration_ms = int((run.completed_at - run.started_at).total_seconds() * 1000)

    event = build_openlineage_event(
        run_id=run_id,
        pipeline_name=run.name or "unknown",
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        started_at=run.started_at or run.created_at,
        completed_at=run.completed_at,
        duration_ms=duration_ms,
        output_row_count=run.total_rows_out,
        lineage_graph=lineage_graph,
        file_ids=[],
    )

    return event


@router.get("/lineage/export")
@limiter.limit("30/minute")
def export_all_openlineage(
    request: Request,
    response: Response,
    limit: int = Query(default=1000, le=10000),
    current_user: User = Depends(get_current_user),
    db: Session = get_read_db_dependency(),
):
    """Bulk export all successful pipeline runs as NDJSON.

    NDJSON is the OpenLineage bulk ingest standard, compatible with
    DataHub, Marquez, and OpenMetadata batch ingestion.
    """
    completed_statuses = ["COMPLETED", "HEALED"]

    result = db.execute(
        select(PipelineRun)
        .where(PipelineRun.user_id == current_user.id)
        .where(PipelineRun.status.in_(completed_statuses))
        .order_by(PipelineRun.created_at.desc())
        .limit(limit)
    )
    runs = result.scalars().all()

    events = []
    for run in runs:
        try:
            lineage_graph = get_cached_run_lineage(str(run.id), db)

            duration_ms = None
            if run.started_at and run.completed_at:
                duration_ms = int((run.completed_at - run.started_at).total_seconds() * 1000)

            event = build_openlineage_event(
                run_id=str(run.id),
                pipeline_name=run.name or "unknown",
                status=run.status.value if hasattr(run.status, "value") else str(run.status),
                started_at=run.started_at or run.created_at,
                completed_at=run.completed_at,
                duration_ms=duration_ms,
                output_row_count=run.total_rows_out,
                lineage_graph=lineage_graph,
                file_ids=[],
            )
            events.append(json.dumps(event))
        except Exception as e:
            logger.warning("Skipping run %s in bulk export: %s", run.id, e)
            continue

    ndjson_content = "\n".join(events)
    return Response(
        content=ndjson_content,
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": 'attachment; filename="pipelineiq-lineage.ndjson"',
            "X-Event-Count": str(len(events)),
        }
    )
