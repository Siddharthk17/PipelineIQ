"""Shared pipeline semantic validation helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.models import UploadedFile
from backend.pipeline.parser import StepType, ValidationError
from backend.utils.uuid_utils import as_uuid


def serialize_validation_errors(errors: list[ValidationError]) -> list[dict[str, Any]]:
    """Convert typed validation errors into dictionaries for API responses."""
    return [
        {
            "step_name": error.step_name,
            "field": error.field,
            "message": error.message,
            "suggestion": error.suggestion,
        }
        for error in errors
    ]


def collect_schema_validation_errors(
    config: Any,
    db: Session,
    *,
    user_id: str | None = None,
) -> list[ValidationError]:
    """Simulate step-by-step schema flow to catch column mismatches early."""
    errors: list[ValidationError] = []
    schema: dict[str, list[str]] = {}

    for step in getattr(config, "steps", []):
        step_type = _step_type_name(getattr(step, "step_type", None))
        step_name = getattr(step, "name", "unknown")

        if step_type == StepType.LOAD.value:
            file_id = getattr(step, "file_id", None)
            if file_id:
                try:
                    file_query = db.query(UploadedFile).filter(
                        UploadedFile.id == as_uuid(file_id)
                    )
                    if user_id is not None:
                        file_query = file_query.filter(UploadedFile.user_id == user_id)
                    file_record = file_query.first()
                except Exception:
                    file_record = None

                if not file_record:
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="file_id",
                            message=f"Referenced file '{file_id}' was not found.",
                        )
                    )
                    schema[step_name] = []
                else:
                    schema[step_name] = list(file_record.columns or [])
            else:
                schema[step_name] = []
            continue

        if step_type == StepType.JOIN.value:
            left = getattr(step, "left", "")
            right = getattr(step, "right", "")
            join_key = getattr(step, "on", "")
            left_cols = schema.get(left, [])
            right_cols = schema.get(right, [])

            if join_key and join_key not in left_cols:
                errors.append(
                    ValidationError(
                        step_name=step_name,
                        field="on",
                        message=(
                            f"Join key '{join_key}' was not found in left input "
                            f"'{left}'. Available columns: {left_cols}"
                        ),
                    )
                )
            if join_key and join_key not in right_cols:
                errors.append(
                    ValidationError(
                        step_name=step_name,
                        field="on",
                        message=(
                            f"Join key '{join_key}' was not found in right input "
                            f"'{right}'. Available columns: {right_cols}"
                        ),
                    )
                )

            merged = list(left_cols)
            for column in right_cols:
                if column not in merged and column != join_key:
                    merged.append(column)
            schema[step_name] = merged
            continue

        input_step = getattr(step, "input", None)
        available = list(schema.get(input_step, [])) if input_step else []

        if step_type == StepType.FILTER.value:
            _append_missing_column_error(
                errors, step_name, "column", getattr(step, "column", ""), input_step, available
            )
            schema[step_name] = available
        elif step_type == StepType.SELECT.value:
            requested = list(getattr(step, "columns", []) or [])
            for column in requested:
                _append_missing_column_error(
                    errors, step_name, "columns", column, input_step, available
                )
            schema[step_name] = [column for column in requested if column in available]
        elif step_type == StepType.RENAME.value:
            mapping = getattr(step, "mapping", {}) or {}
            renamed = list(available)
            for old_name, new_name in mapping.items():
                _append_missing_column_error(
                    errors, step_name, "mapping", old_name, input_step, available
                )
                if old_name in renamed:
                    renamed[renamed.index(old_name)] = new_name
            schema[step_name] = renamed
        elif step_type == StepType.AGGREGATE.value:
            group_by = list(getattr(step, "group_by", []) or [])
            aggregations = list(getattr(step, "aggregations", []) or [])
            for column in group_by:
                _append_missing_column_error(
                    errors, step_name, "group_by", column, input_step, available
                )

            output_columns = [column for column in group_by if column in available]
            for aggregation in aggregations:
                if not isinstance(aggregation, dict):
                    continue
                column = str(aggregation.get("column", "") or "")
                function = str(aggregation.get("function", "") or "")
                _append_missing_column_error(
                    errors,
                    step_name,
                    "aggregations.column",
                    column,
                    input_step,
                    available,
                )
                if column and function and column in available:
                    output_columns.append(f"{column}_{function}")
            schema[step_name] = output_columns
        elif step_type == StepType.SORT.value:
            _append_missing_column_error(
                errors, step_name, "by", getattr(step, "by", ""), input_step, available
            )
            schema[step_name] = available
        elif step_type == StepType.VALIDATE.value:
            for rule in list(getattr(step, "rules", []) or []):
                if not isinstance(rule, dict):
                    continue
                column = str(rule.get("column", "") or "")
                if column:
                    _append_missing_column_error(
                        errors, step_name, "rules.column", column, input_step, available
                    )
            schema[step_name] = available
        elif step_type == StepType.PIVOT.value:
            index_columns = list(getattr(step, "index", []) or [])
            for column in index_columns:
                _append_missing_column_error(
                    errors, step_name, "index", column, input_step, available
                )
            _append_missing_column_error(
                errors, step_name, "columns", getattr(step, "columns", ""), input_step, available
            )
            values_column = getattr(step, "values", "")
            _append_missing_column_error(
                errors, step_name, "values", values_column, input_step, available
            )
            schema[step_name] = [column for column in index_columns if column in available]
            if values_column and values_column in available:
                schema[step_name].append(f"{values_column}_pivoted")
        elif step_type == StepType.UNPIVOT.value:
            id_vars = list(getattr(step, "id_vars", []) or [])
            value_vars = list(getattr(step, "value_vars", []) or [])
            for column in id_vars + value_vars:
                _append_missing_column_error(
                    errors, step_name, "value_vars", column, input_step, available
                )
            schema[step_name] = [column for column in id_vars if column in available] + [
                getattr(step, "var_name", "variable"),
                getattr(step, "value_name", "value"),
            ]
        elif step_type == StepType.DEDUPLICATE.value:
            for column in list(getattr(step, "subset", []) or []):
                _append_missing_column_error(
                    errors, step_name, "subset", column, input_step, available
                )
            schema[step_name] = available
        elif step_type == StepType.FILL_NULLS.value:
            for column in list(getattr(step, "columns", []) or []):
                _append_missing_column_error(
                    errors, step_name, "columns", column, input_step, available
                )
            schema[step_name] = available
        elif step_type == StepType.SAMPLE.value:
            stratify_by = getattr(step, "stratify_by", None)
            if stratify_by:
                _append_missing_column_error(
                    errors, step_name, "stratify_by", stratify_by, input_step, available
                )
            schema[step_name] = available
        elif step_type == StepType.WASM_COMPUTE.value:
            for column in list(getattr(step, "input_columns", []) or []):
                _append_missing_column_error(
                    errors, step_name, "input_columns", column, input_step, available
                )
            output_column = getattr(step, "output_column", "")
            schema[step_name] = available + ([output_column] if output_column else [])
        else:
            schema[step_name] = available

    return errors


def _append_missing_column_error(
    errors: list[ValidationError],
    step_name: str,
    field: str,
    column: str,
    input_step: str | None,
    available: list[str],
) -> None:
    if column and column not in available:
        errors.append(
            ValidationError(
                step_name=step_name,
                field=field,
                message=(
                    f"Column '{column}' was not found in input "
                    f"'{input_step or 'unknown'}'. Available columns: {available}"
                ),
            )
        )


def _step_type_name(step_type: Any) -> str:
    if hasattr(step_type, "value"):
        return str(step_type.value)
    return str(step_type or "")
