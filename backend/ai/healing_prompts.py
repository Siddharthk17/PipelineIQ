"""Prompt builder and response validator for autonomous pipeline healing."""

from __future__ import annotations

import orjson

# M-074: fields an AI healing patch must never mutate. Step identity
# (name), step semantics (type), and data/module references (file_id,
# wasm_file_id) are ownership and execution boundaries — a schema-drift
# fix may only retarget data columns (column, columns, mapping, ...).
IMMUTABLE_PATCH_FIELDS = frozenset({"name", "type", "file_id", "wasm_file_id"})

# M-074: upper bound on patch operations per healing attempt so one AI
# response cannot rewrite the whole pipeline.
MAX_HEALING_PATCHES = 10

HEALING_SYSTEM_PROMPT = """You are an autonomous data pipeline repair agent for PipelineIQ.
A data pipeline failed because the source data schema changed.
Return the minimal JSON patch needed to repair the YAML.

Output only a JSON object with this shape:
{{
  "confidence": <float 0.0-1.0>,
  "change_description": "<one sentence>",
  "patches": [
    {{
      "step_name": "<step name>",
      "field": "<field to change>",
      "old_value": "<existing value>",
      "new_value": "<replacement value>"
    }}
  ]
}}

If a safe patch cannot be determined, output:
{{
  "confidence": 0.0,
  "change_description": "Cannot determine fix from available information",
  "patches": []
}}

Do not output YAML.
Do not explain your reasoning.
Output only JSON.

Broken pipeline YAML:
{broken_yaml}

Failure information:
- error_type: {error_type}
- error_message: {error_message}
- failed_step: {failed_step_name}

Old schema:
{old_schema_json}

New schema:
{new_schema_json}

Schema diff:
- removed_columns: {removed_columns}
- added_columns: {added_columns}
- renamed_candidates:
{renamed_candidates_formatted}

Patch every step that references the renamed or missing column, not only the failed step."""


def build_healing_prompt(
    *,
    broken_yaml: str,
    error_type: str,
    error_message: str,
    failed_step_name: str,
    old_schema: dict,
    new_schema: dict,
    schema_diff: dict,
) -> str:
    """Build the deterministic healing prompt sent to Gemini."""
    renamed_candidates = schema_diff.get("renamed_candidates", [])
    if renamed_candidates:
        formatted_candidates = "\n".join(
            (
                f"  - {candidate['old_name']} -> {candidate['new_name']} "
                f"(similarity={candidate['similarity']}, "
                f"type_match={candidate['type_match']}, "
                f"confidence={candidate['confidence']})"
            )
            for candidate in renamed_candidates[:5]
        )
    else:
        formatted_candidates = "  - none"

    return HEALING_SYSTEM_PROMPT.format(
        broken_yaml=broken_yaml,
        error_type=error_type,
        error_message=error_message,
        failed_step_name=failed_step_name,
        old_schema_json=orjson.dumps(
            old_schema,
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
        ).decode("utf-8"),
        new_schema_json=orjson.dumps(
            new_schema,
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
        ).decode("utf-8"),
        removed_columns=orjson.dumps(schema_diff.get("removed_columns", [])).decode("utf-8"),
        added_columns=orjson.dumps(schema_diff.get("added_columns", [])).decode("utf-8"),
        renamed_candidates_formatted=formatted_candidates,
    )


def validate_healing_patch(patch: dict) -> tuple[bool, str]:
    """Validate the JSON patch schema returned by Gemini."""
    if not isinstance(patch, dict):
        return False, f"Expected dict, got {type(patch).__name__}"

    confidence = patch.get("confidence")
    if not isinstance(confidence, (int, float)):
        return False, "Missing or invalid 'confidence' field"
    if confidence < 0.0 or confidence > 1.0:
        return False, f"'confidence' must be between 0.0 and 1.0, got {confidence}"

    description = patch.get("change_description")
    if not isinstance(description, str):
        return False, "Missing or invalid 'change_description' field"

    patches = patch.get("patches")
    if not isinstance(patches, list):
        return False, "Missing or invalid 'patches' field"

    if len(patches) > MAX_HEALING_PATCHES:
        return False, (f"Too many patch operations: {len(patches)} (max {MAX_HEALING_PATCHES})")

    required_patch_fields = {"step_name", "field", "old_value", "new_value"}
    for index, patch_item in enumerate(patches):
        if not isinstance(patch_item, dict):
            return False, f"patches[{index}] must be an object"
        missing = required_patch_fields - set(patch_item.keys())
        if missing:
            return False, f"patches[{index}] missing fields: {sorted(missing)}"
        field_name = str(patch_item.get("field", ""))
        if field_name in IMMUTABLE_PATCH_FIELDS:
            return False, (
                f"patches[{index}] targets immutable identity field "
                f"'{field_name}': healing may only retarget data columns, "
                f"never step identity, types, or file/module references"
            )

    return True, ""
