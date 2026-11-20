"""Pipeline versioning with YAML diff support.

Stores versioned pipeline configurations and computes structured
diffs between versions for change tracking.
"""

import difflib
import uuid as _uuid
from dataclasses import dataclass

import yaml
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import PipelineVersion


@dataclass
class StepDiff:
    """Diff for a single modified step between versions."""

    step_name: str
    change_type: str  # "added", "removed", "modified"
    old_config: dict | None
    new_config: dict | None
    changed_fields: list[str]


@dataclass
class PipelineDiff:
    """Complete diff between two pipeline versions."""

    version_a: int
    version_b: int
    pipeline_name: str
    steps_added: list[str]
    steps_removed: list[str]
    steps_modified: list[StepDiff]
    has_changes: bool
    unified_diff: str
    change_summary: str


def _pipeline_name(yaml_text: str) -> str:
    """Extract a pipeline name leniently; "unknown" on ragged input."""
    try:
        config = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError:
        return "unknown"
    if not isinstance(config, dict):
        return "unknown"
    pipeline = config.get("pipeline", config)
    if not isinstance(pipeline, dict):
        return "unknown"
    name = pipeline.get("name", "unknown")
    return name if isinstance(name, str) and name else "unknown"


def _steps_by_name(yaml_text: str) -> dict[str, dict]:
    """Index a pipeline's steps by name, defensively.

    M-145: version diffs must survive ragged stored YAML — steps that
    are not mappings, entries without names, or YAML 1.1 boolean keys
    (bare `on:`/`yes:`/`no:` parse as True/False). Non-conforming
    entries are skipped instead of raising KeyError/TypeError (HTTP
    500) from the versions endpoint.
    """
    try:
        config = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(config, dict):
        return {}
    pipeline = config.get("pipeline", config)
    if not isinstance(pipeline, dict):
        return {}
    steps_raw = pipeline.get("steps", [])
    if isinstance(steps_raw, dict):
        steps_raw = list(steps_raw.values())
    if not isinstance(steps_raw, list):
        return {}
    steps: dict[str, dict] = {}
    for idx, entry in enumerate(steps_raw):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            name = f"<unnamed-{idx}>"
        steps.setdefault(str(name), entry)
    return steps


def diff_pipelines(
    yaml_a: str,
    yaml_b: str,
    version_a: int,
    version_b: int,
) -> PipelineDiff:
    """Compute a structured diff between two YAML pipeline configs."""
    steps_a = _steps_by_name(yaml_a)
    steps_b = _steps_by_name(yaml_b)

    names_a = set(steps_a.keys())
    names_b = set(steps_b.keys())

    steps_added = sorted(names_b - names_a, key=str)
    steps_removed = sorted(names_a - names_b, key=str)
    steps_modified = []

    for name in sorted(names_a & names_b, key=str):
        if steps_a[name] != steps_b[name]:
            # YAML 1.1 parses bare `on`, `off`, `yes`, `no` as booleans, so
            # step dicts can end up with mixed-type keys (`True`, `False`,
            # strings). Force a string sort key to avoid
            # `TypeError: '<' not supported between instances of 'str' and 'bool'`
            # when computing the union of keys, and coerce to str for
            # `changed_fields` so callers always see opaque string identifiers.
            changed_fields = [
                str(k)
                for k in sorted(
                    set(steps_a[name]) | set(steps_b[name]),
                    key=str,
                )
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
        pipeline_name=_pipeline_name(yaml_b),
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
    run_id: _uuid.UUID | str | None,
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

    normalized_run_id = _uuid.UUID(
        run_id) if isinstance(run_id, str) else run_id
    last_error: Exception | None = None

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

    raise RuntimeError(
        "Unable to save pipeline version after retries") from last_error
