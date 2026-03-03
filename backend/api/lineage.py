"""Lineage query API endpoints.

Provides endpoints for retrieving the React Flow lineage graph,
tracing column ancestry, and performing forward impact analysis.
"""

import logging
import uuid
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.dependencies import get_db_dependency
from backend.models import LineageGraph, PipelineRun
from backend.pipeline.lineage import LineageRecorder
from backend.schemas import (
    ColumnLineageResponse,
    ImpactAnalysisResponse,
    LineageGraphResponse,
    ReactFlowEdgeResponse,
    ReactFlowNodeResponse,
    TransformationStepResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lineage", tags=["lineage"])


@router.get(
    "/{run_id}",
    response_model=LineageGraphResponse,
    summary="Get lineage graph",
    description="Returns the React Flow lineage graph for a pipeline run.",
)
def get_lineage_graph(
    run_id: str,
    db: Session = get_db_dependency(),
) -> LineageGraphResponse:
    """Retrieve the pre-computed React Flow lineage graph."""
    _validate_uuid_format(run_id)
    lineage_graph = _get_lineage_record(run_id, db)
    react_flow_data = lineage_graph.react_flow_data

    return LineageGraphResponse(
        nodes=[
            ReactFlowNodeResponse(
                id=n["id"],
                type=n["type"],
                data=n["data"],
                position=n["position"],
            )
            for n in react_flow_data.get("nodes", [])
        ],
        edges=[
            ReactFlowEdgeResponse(
                id=e["id"],
                source=e["source"],
                target=e["target"],
                animated=e.get("animated", False),
                style=e.get("style"),
            )
            for e in react_flow_data.get("edges", [])
        ],
    )


@router.get(
    "/{run_id}/column",
    response_model=ColumnLineageResponse,
    summary="Trace column ancestry",
    description="Trace a column backward to its source file and all transformations.",
)
def get_column_lineage(
    run_id: str,
    step: str = Query(..., description="Step name containing the column"),
    column: str = Query(..., description="Column name to trace"),
    db: Session = get_db_dependency(),
) -> ColumnLineageResponse:
    """Trace a column's ancestry back to its source file."""
    _validate_uuid_format(run_id)
    recorder = _reconstruct_recorder(run_id, db)

    try:
        lineage = recorder.get_column_ancestry(step, column)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Column '{column}' not found in step '{step}' "
                f"for run '{run_id}': {exc}"
            ),
        ) from exc

    return ColumnLineageResponse(
        column_name=lineage.column_name,
        source_file=lineage.source_file,
        source_column=lineage.source_column,
        transformation_chain=[
            TransformationStepResponse(
                step_name=ts.step_name,
                step_type=ts.step_type,
                detail=ts.detail,
            )
            for ts in lineage.transformation_chain
        ],
        total_steps=lineage.total_steps,
    )


@router.get(
    "/{run_id}/impact",
    response_model=ImpactAnalysisResponse,
    summary="Forward impact analysis",
    description="Find all downstream outputs affected by a source column.",
)
def get_impact_analysis(
    run_id: str,
    step: str = Query(..., description="Step name containing the source column"),
    column: str = Query(..., description="Column name to analyze"),
    db: Session = get_db_dependency(),
) -> ImpactAnalysisResponse:
    """Analyze the downstream impact of a column."""
    _validate_uuid_format(run_id)
    recorder = _reconstruct_recorder(run_id, db)

    try:
        impact = recorder.get_impact_analysis(step, column)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Column '{column}' not found in step '{step}' "
                f"for run '{run_id}': {exc}"
            ),
        ) from exc

    return ImpactAnalysisResponse(
        source_step=impact.source_step,
        source_column=impact.source_column,
        affected_steps=impact.affected_steps,
        affected_output_columns=impact.affected_output_columns,
    )


def _get_lineage_record(run_id: str, db: Session) -> LineageGraph:
    """Fetch the lineage graph record, raising 404 if not found."""
    _validate_run_exists(run_id, db)

    lineage_graph = (
        db.query(LineageGraph)
        .filter(LineageGraph.pipeline_run_id == run_id)
        .first()
    )
    if lineage_graph is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Lineage graph not found for run '{run_id}'. "
                f"The pipeline may not have completed successfully."
            ),
        )
    return lineage_graph


def _validate_run_exists(run_id: str, db: Session) -> None:
    """Raise 404 if the pipeline run does not exist."""
    exists = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )


def _validate_uuid_format(value: str) -> None:
    """Raise 422 if the value is not a valid UUID."""
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid UUID format: '{value}'",
        )


def _reconstruct_recorder(run_id: str, db: Session) -> LineageRecorder:
    """Reconstruct a LineageRecorder from stored graph data.

    Loads the serialized graph from the database and reconstructs
    a LineageRecorder with the full NetworkX graph for querying.
    """
    import networkx as nx

    lineage_record = _get_lineage_record(run_id, db)
    recorder = LineageRecorder()
    recorder.graph = nx.node_link_graph(lineage_record.graph_data)
    return recorder
