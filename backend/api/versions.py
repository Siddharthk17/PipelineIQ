"""Pipeline versioning API endpoints.

Provides access to pipeline version history, diffs, and restore.
"""

import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import PipelineVersion, User, PipelinePermission
from backend.pipeline.versioning import diff_pipelines, save_version

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/versions", tags=["versions"])


def _check_pipeline_permission(
    db: Session,
    user: User,
    pipeline_name: str,
    required_levels: list[str],
) -> None:
    """Verify user has required permission level for the pipeline."""
    if user.role == "admin":
        return

    permission = (
        db.query(PipelinePermission)
        .filter(
            PipelinePermission.pipeline_name == pipeline_name,
            PipelinePermission.user_id == user.id,
        )
        .first()
    )

    if not permission or permission.permission_level not in required_levels:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User lacks required permissions ({', '.join(required_levels)}) to access pipeline '{pipeline_name}'",
        )


@router.get("/{pipeline_name}")
def list_versions(
    pipeline_name: str,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """List all versions of a pipeline."""
    _check_pipeline_permission(
        db, current_user, pipeline_name, ["owner", "runner", "viewer"]
    )
    versions = (
        db.query(PipelineVersion)
        .filter(PipelineVersion.pipeline_name == pipeline_name)
        .order_by(PipelineVersion.version_number.desc())
        .all()
    )
    return {
        "pipeline_name": pipeline_name,
        "total_versions": len(versions),
        "versions": [
            {
                "id": str(v.id),
                "version_number": v.version_number,
                "pipeline_name": v.pipeline_name,
                "run_id": str(v.run_id) if v.run_id else None,
                "change_summary": v.change_summary,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ],
    }


@router.get("/{pipeline_name}/{version_number}")
def get_version(
    pipeline_name: str,
    version_number: int,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """Get a specific pipeline version."""
    _check_pipeline_permission(
        db, current_user, pipeline_name, ["owner", "runner", "viewer"]
    )
    version = (
        db.query(PipelineVersion)
        .filter(
            PipelineVersion.pipeline_name == pipeline_name,
            PipelineVersion.version_number == version_number,
        )
        .first()
    )
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    return {
        "id": str(version.id),
        "version_number": version.version_number,
        "pipeline_name": version.pipeline_name,
        "yaml_config": version.yaml_config,
        "run_id": str(version.run_id) if version.run_id else None,
        "change_summary": version.change_summary,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }


@router.get("/{pipeline_name}/diff/{version_a}/{version_b}")
def diff_versions(
    pipeline_name: str,
    version_a: int,
    version_b: int,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """Diff two versions of a pipeline."""
    _check_pipeline_permission(
        db, current_user, pipeline_name, ["owner", "runner", "viewer"]
    )
    va = (
        db.query(PipelineVersion)
        .filter(
            PipelineVersion.pipeline_name == pipeline_name,
            PipelineVersion.version_number == version_a,
        )
        .first()
    )
    vb = (
        db.query(PipelineVersion)
        .filter(
            PipelineVersion.pipeline_name == pipeline_name,
            PipelineVersion.version_number == version_b,
        )
        .first()
    )

    if not va or not vb:
        raise HTTPException(status_code=404, detail="Version not found")

    diff = diff_pipelines(va.yaml_config, vb.yaml_config, version_a, version_b)

    return {
        "version_a": diff.version_a,
        "version_b": diff.version_b,
        "pipeline_name": diff.pipeline_name,
        "steps_added": diff.steps_added,
        "steps_removed": diff.steps_removed,
        "steps_modified": [
            {
                "step_name": s.step_name,
                "change_type": s.change_type,
                "changed_fields": s.changed_fields,
            }
            for s in diff.steps_modified
        ],
        "has_changes": diff.has_changes,
        "unified_diff": diff.unified_diff,
        "change_summary": diff.change_summary,
    }


@router.post("/{pipeline_name}/restore/{version_number}")
def restore_version(
    pipeline_name: str,
    version_number: int,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """Restore a pipeline to a previous version by creating a new version."""
    _check_pipeline_permission(db, current_user, pipeline_name, ["owner", "runner"])
    old_version = (
        db.query(PipelineVersion)
        .filter(
            PipelineVersion.pipeline_name == pipeline_name,
            PipelineVersion.version_number == version_number,
        )
        .first()
    )
    if not old_version:
        raise HTTPException(status_code=404, detail="Version not found")

    new_version = save_version(
        pipeline_name=pipeline_name,
        yaml_config=old_version.yaml_config,
        run_id=None,
        db=db,
    )

    return {
        "message": f"Restored to version {version_number}",
        "yaml_config": new_version.yaml_config,
        "new_version": {
            "id": str(new_version.id),
            "version_number": new_version.version_number,
            "pipeline_name": new_version.pipeline_name,
            "change_summary": new_version.change_summary,
            "created_at": new_version.created_at.isoformat()
            if new_version.created_at
            else None,
        },
    }
