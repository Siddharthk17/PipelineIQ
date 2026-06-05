"""Pipeline template API endpoints.

Provides a curated library of ready-to-use pipeline templates
that users can browse, preview, fork (fill placeholders), and import.
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.auth import get_current_user
from backend.models import User
from backend.templates.loader import (
    fork_template,
    get_all_templates,
    get_pipeline_yaml_from_template,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])
TEMPLATE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ForkTemplateRequest(BaseModel):
    pipeline_name: str = Field(..., min_length=1, max_length=500)
    file_mappings: dict[str, str] = Field(
        ...,
        description="Map placeholder names to actual file UUIDs",
    )


def _validate_template_id(template_id: str) -> None:
    if not TEMPLATE_ID_RE.fullmatch(template_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid template_id",
        )


@router.get(
    "/",
    summary="List all pipeline templates",
    description="Returns a curated list of pipeline templates for common data workflows.",
)
def list_templates() -> dict:
    """List all available pipeline templates (metadata only)."""
    templates = get_all_templates()
    return {
        "templates": [
            {
                "id": t["id"],
                "name": t["name"],
                "description": t["description"],
                "category": t["category"],
                "required_files": t.get("required_files", []),
            }
            for t in templates
        ],
        "total": len(templates),
    }


@router.get(
    "/{template_id}",
    summary="Get a pipeline template",
    description="Returns the full template including YAML configuration.",
)
def get_template(template_id: str) -> dict:
    """Get a specific pipeline template by ID."""
    _validate_template_id(template_id)
    try:
        pipeline_yaml, meta = get_pipeline_yaml_from_template(template_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_id}' not found",
        )
    return {
        "id": meta["id"],
        "name": meta["name"],
        "description": meta["description"],
        "category": meta["category"],
        "required_files": meta.get("required_files", []),
        "yaml_config": pipeline_yaml,
    }


@router.post(
    "/{template_id}/fork",
    summary="Fork a pipeline template",
    description=(
        "Fill in the file ID placeholders and return a ready-to-run "
        "pipeline YAML with a custom name."
    ),
)
def fork_pipeline_template(
    template_id: str,
    request: ForkTemplateRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Fork a template by filling file ID placeholders."""
    _validate_template_id(template_id)
    try:
        pipeline_yaml = fork_template(
            template_id=template_id,
            pipeline_name=request.pipeline_name,
            file_mappings=request.file_mappings,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_id}' not found",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    # Validate YAML parses
    import yaml
    try:
        yaml.safe_load(pipeline_yaml)
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Forked template produces invalid YAML: {exc}",
        )

    if "{{" in pipeline_yaml or "}}" in pipeline_yaml:
        remaining = re.findall(r'\{\{(\w+)\}\}', pipeline_yaml)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unfilled placeholders remain: {remaining}",
        )

    return {
        "pipeline_name": request.pipeline_name,
        "yaml": pipeline_yaml,
        "template_id": template_id,
    }
