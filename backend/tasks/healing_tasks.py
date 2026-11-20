"""Healing guard helpers (M-074).

Single home for the healing safety invariants so pipeline orchestration
can enforce them without duplicating logic:

- attempts cap: healing must stop after the configured max attempts;
- parser validation: every AI-patched YAML is semantically validated
  (step references, columns) before it may replace a running pipeline;
- identity fields: patches touching step identity, types, or
  file/module references are rejected (see also
  backend.ai.healing_prompts.validate_healing_patch, which enforces the
  same rule at the attempt_heal gate).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.ai.healing_prompts import validate_healing_patch
from backend.config import settings
from backend.pipeline.cache import get_parsed_pipeline
from backend.pipeline.parser import PipelineParser
from backend.services.pipeline_validation import collect_schema_validation_errors

_pipeline_parser = PipelineParser()


def max_healing_attempts() -> int:
    """Configured cap on healing attempts per run (M-074)."""
    try:
        return max(0, int(settings.AUTONOMOUS_HEALING_MAX_ATTEMPTS))
    except (TypeError, ValueError):
        return 0


def healing_attempts_allowed(attempts_made: int, max_attempts: int) -> bool:
    """True while another healing attempt may run under the cap."""
    if max_attempts <= 0 or attempts_made < 0:
        return False
    return attempts_made < max_attempts


def validate_patch_for_apply(patch: dict) -> None:
    """Raise ValueError unless the AI patch is safe to apply.

    Enforces schema shape, the per-attempt operation cap, and the
    immutable identity-field rule.
    """
    valid, error = validate_healing_patch(patch)
    if not valid:
        raise ValueError(f"Unsafe healing patch rejected: {error}")


def parser_validate_patched_yaml(
    patched_yaml: str,
    file_ids: list[str],
    db: Session,
) -> None:
    """Parser-validate (semantic) an AI-patched YAML (M-074).

    Raises ValueError naming the first blocking error. Must be called
    for every patched YAML before it replaces the run's config — sandbox
    execution alone does not check step references or column validity.
    Returns None when the YAML is runnable.
    """
    config = get_parsed_pipeline(patched_yaml)
    result = _pipeline_parser.validate(config, set(file_ids))
    if result.errors:
        first = result.errors[0]
        raise ValueError(
            f"Healed YAML failed parser validation "
            f"(step={first.step_name!r} field={first.field!r}): {first.message}"
        )
    schema_errors = collect_schema_validation_errors(config, db)
    if schema_errors:
        raise ValueError(f"Healed YAML failed schema validation: {schema_errors[0].message}")
