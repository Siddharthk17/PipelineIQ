"""Apply a structured JSON patch to a pipeline YAML document."""

from __future__ import annotations

import logging
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def apply_patch(yaml_text: str, patch: dict) -> str:
    """Apply a validated healing patch and return the patched YAML string."""
    document = yaml.safe_load(yaml_text)
    if not isinstance(document, dict):
        raise ValueError("Invalid pipeline YAML: root must be a mapping")

    pipeline_config = document.get("pipeline", document)
    if not isinstance(pipeline_config, dict):
        raise ValueError("Invalid pipeline YAML: missing 'pipeline' mapping")

    steps = pipeline_config.get("steps")
    if not isinstance(steps, list):
        raise ValueError("Invalid pipeline YAML: missing 'steps' list")

    step_map = {
        step.get("name"): step
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("name"), str)
    }

    for patch_item in patch.get("patches", []):
        _apply_patch_item(step_map=step_map, patch_item=patch_item)

    return yaml.safe_dump(
        document,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=10_000,
    )


def _apply_patch_item(*, step_map: dict[str, dict], patch_item: dict[str, Any]) -> None:
    step_name = str(patch_item["step_name"])
    field_name = str(patch_item["field"])
    old_value = patch_item["old_value"]
    new_value = patch_item["new_value"]

    if step_name not in step_map:
        raise ValueError(
            f"Patch references step '{step_name}' which does not exist. "
            f"Available steps: {sorted(step_map.keys())}"
        )

    step = step_map[step_name]
    handlers = {
        "column": _patch_scalar_field,
        "on": _patch_scalar_field,
        "left": _patch_scalar_field,
        "right": _patch_scalar_field,
        "input": _patch_scalar_field,
        "value": _patch_scalar_field,
        "file_id": _patch_scalar_field,
        "columns": _patch_list_field,
        "group_by": _patch_list_field,
        "by": _patch_sort_field,
        "mapping": _patch_mapping_field,
        "aggregations": _patch_aggregations_field,
    }
    handler = handlers.get(field_name, _patch_scalar_field)
    handler(step=step, field_name=field_name, old_value=old_value, new_value=new_value)


def _patch_scalar_field(*, step: dict, field_name: str, old_value: Any, new_value: Any) -> None:
    if field_name not in step:
        raise ValueError(f"Step '{step.get('name')}' has no '{field_name}' field")
    current_value = step.get(field_name)
    if current_value != old_value:
        logger.warning(
            "Healing patch expected %r in %s.%s but found %r; applying replacement anyway",
            old_value,
            step.get("name"),
            field_name,
            current_value,
        )
    step[field_name] = new_value


def _patch_list_field(*, step: dict, field_name: str, old_value: Any, new_value: Any) -> None:
    current_value = step.get(field_name)
    if not isinstance(current_value, list):
        raise ValueError(f"Step '{step.get('name')}' has no list field '{field_name}'")

    step[field_name] = [new_value if item == old_value else item for item in current_value]


def _patch_sort_field(*, step: dict, field_name: str, old_value: Any, new_value: Any) -> None:
    current_value = step.get(field_name)
    if isinstance(current_value, list):
        step[field_name] = [new_value if item == old_value else item for item in current_value]
        return

    if current_value != old_value:
        logger.warning(
            "Healing patch expected %r in %s.%s but found %r; applying replacement anyway",
            old_value,
            step.get("name"),
            field_name,
            current_value,
        )
    step[field_name] = new_value


def _patch_mapping_field(*, step: dict, field_name: str, old_value: Any, new_value: Any) -> None:
    current_value = step.get(field_name)
    if not isinstance(current_value, dict):
        raise ValueError(f"Step '{step.get('name')}' has no dict field '{field_name}'")

    updated_mapping: dict[Any, Any] = {}
    for key, value in current_value.items():
        updated_key = new_value if key == old_value else key
        updated_value = new_value if value == old_value else value
        updated_mapping[updated_key] = updated_value
    step[field_name] = updated_mapping


def _patch_aggregations_field(
    *,
    step: dict,
    field_name: str,
    old_value: Any,
    new_value: Any,
) -> None:
    current_value = step.get(field_name)
    if not isinstance(current_value, list):
        raise ValueError(f"Step '{step.get('name')}' has no list field '{field_name}'")

    for aggregation in current_value:
        if isinstance(aggregation, dict) and aggregation.get("column") == old_value:
            aggregation["column"] = new_value
