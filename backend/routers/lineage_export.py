"""OpenLineage export endpoints.

GET /api/runs/{run_id}/openlineage -- single run event (JSON)
GET /api/lineage/export -- all runs as NDJSON bulk export
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import get_read_db
from backend.auth import get_current_user
from backend.models import User, PipelineRun
from backend.repositories.catalog import get_cached_run_lineage
from backend.openlineage.builder import build_openlineage_event

router = APIRouter(prefix="/api", tags=["Lineage"])
logger = logging.getLogger(__name__)


@router.get("/runs/{run_id}/openlineage")
async def get_run_openlineage_event(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db),
):
    """Export a pipeline run as an OpenLineage 1.0 RunEvent."""
    result = db.execute(
        select(PipelineRun)
        .where(PipelineRun.id == run_id)
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
async def export_all_openlineage(
    limit: int = Query(default=1000, le=10000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db),
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
            import networkx as nx
            lineage_graph = nx.DiGraph()

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
