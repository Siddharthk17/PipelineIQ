"""Pipeline versioning with YAML diff support.

Stores versioned pipeline configurations and computes structured
diffs between versions for change tracking.
"""

import difflib
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import PipelineVersion


@dataclass
class StepDiff:
    """Diff for a single modified step between versions."""

    step_name: str
    change_type: str  # "added", "removed", "modified"
    old_config: Optional[dict]
    new_config: Optional[dict]
    changed_fields: List[str]


@dataclass
class PipelineDiff:
    """Complete diff between two pipeline versions."""

    version_a: int
    version_b: int
    pipeline_name: str
    steps_added: List[str]
    steps_removed: List[str]
    steps_modified: List[StepDiff]
    has_changes: bool
    unified_diff: str
    change_summary: str


def diff_pipelines(
    yaml_a: str,
    yaml_b: str,
    version_a: int,
    version_b: int,
) -> PipelineDiff:
    """Compute a structured diff between two YAML pipeline configs."""
    config_a = yaml.safe_load(yaml_a) or {}
    config_b = yaml.safe_load(yaml_b) or {}

    # Handle both top-level and nested pipeline configs
    pipeline_a = config_a.get("pipeline", config_a)
    pipeline_b = config_b.get("pipeline", config_b)

    steps_a = {s["name"]: s for s in pipeline_a.get("steps", [])}
    steps_b = {s["name"]: s for s in pipeline_b.get("steps", [])}

    names_a = set(steps_a.keys())
    names_b = set(steps_b.keys())

    steps_added = sorted(names_b - names_a)
    steps_removed = sorted(names_a - names_b)
    steps_modified = []

    for name in sorted(names_a & names_b):
        if steps_a[name] != steps_b[name]:
            changed_fields = [
                k for k in sorted(set(steps_a[name]) | set(steps_b[name]))
                if steps_a[name].get(k) != steps_b[name].get(k)
            ]
            steps_modified.append(StepDiff(
                step_name=name,
                change_type="modified",
                old_config=steps_a[name],
                new_config=steps_b[name],
                changed_fields=changed_fields,
            ))

    unified = "\n".join(difflib.unified_diff(
        yaml_a.splitlines(),
        yaml_b.splitlines(),
        fromfile=f"v{version_a}",
        tofile=f"v{version_b}",
        lineterm="",
    ))

    parts = []
    if steps_added:
        parts.append(f"{len(steps_added)} steps added")
    if steps_removed:
        parts.append(f"{len(steps_removed)} steps removed")
    if steps_modified:
        parts.append(f"{len(steps_modified)} steps modified")
    summary = ", ".join(parts) if parts else "No changes"

    return PipelineDiff(
        version_a=version_a,
        version_b=version_b,
        pipeline_name=pipeline_b.get("name", "unknown"),
        steps_added=steps_added,
        steps_removed=steps_removed,
        steps_modified=steps_modified,
        has_changes=bool(steps_added or steps_removed or steps_modified),
        unified_diff=unified,
        change_summary=summary,
    )


def save_version(
    pipeline_name: str,
    yaml_config: str,
    run_id: Optional[str],
    db: Session,
    *,
    max_retries: int = 5,
) -> PipelineVersion:
    """Save a new pipeline version, retrying on version-number races.

    Concurrent runs for the same pipeline name can race on
    (pipeline_name, version_number). On uniqueness conflicts, rollback and
    retry with a freshly computed version number.
    """
    if max_retries < 1:
        raise ValueError("max_retries must be >= 1")

    normalized_run_id = _uuid.UUID(run_id) if isinstance(run_id, str) else run_id
    last_error: Optional[Exception] = None

    for attempt in range(max_retries):
        latest = (
            db.query(PipelineVersion)
            .filter(PipelineVersion.pipeline_name == pipeline_name)
            .order_by(PipelineVersion.version_number.desc())
            .first()
        )

        next_version = (latest.version_number + 1) if latest else 1
        change_summary = "Initial version"
        if latest:
            diff = diff_pipelines(
                latest.yaml_config,
                yaml_config,
                latest.version_number,
                next_version,
            )
            change_summary = diff.change_summary

        version = PipelineVersion(
            pipeline_name=pipeline_name,
            version_number=next_version,
            yaml_config=yaml_config,
            run_id=normalized_run_id,
            change_summary=change_summary,
        )
        db.add(version)

        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            last_error = exc
            if attempt == max_retries - 1:
                raise
            continue

        db.refresh(version)
        return version

    raise RuntimeError("Unable to save pipeline version after retries") from last_error
