"""Dry-run execution planner for pipeline preview.

Generates an estimated execution plan without processing data.
Uses file metadata for row count estimates and heuristics for
step-level row/duration estimation.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml
from sqlalchemy.orm import Session

from backend.models import UploadedFile


@dataclass
class StepPlan:
    """Estimated execution plan for a single step."""

    step_index: int
    step_name: str
    step_type: str
    input_step: Optional[str]
    input_file_id: Optional[str]
    estimated_rows_in: Optional[int]
    estimated_rows_out: Optional[int]
    estimated_columns: List[str]
    estimated_duration_ms: int
    warnings: List[str]
    will_fail: bool
    fail_reason: Optional[str]


@dataclass
class ExecutionPlan:
    """Complete dry-run execution plan for a pipeline."""

    pipeline_name: str
    total_steps: int
    estimated_total_duration_ms: int
    steps: List[StepPlan]
    files_read: List[str]
    files_written: List[str]
    estimated_rows_processed: int
    warnings: List[str]
    will_succeed: bool


def generate_execution_plan(
    yaml_config: str,
    db: Session,
) -> ExecutionPlan:
    """Generate an execution plan from a YAML config without running anything."""
    raw = yaml.safe_load(yaml_config)
    pipeline_raw = raw.get("pipeline", raw)
    pipeline_name = pipeline_raw.get("name", "unnamed")
    steps_raw = pipeline_raw.get("steps", [])

    # Load file metadata from DB
    uploaded_files = db.query(UploadedFile).all()
    file_map: Dict[str, UploadedFile] = {str(f.id): f for f in uploaded_files}

    step_plans: List[StepPlan] = []
    step_rows: Dict[str, int] = {}
    step_columns: Dict[str, List[str]] = {}
    files_read: List[str] = []
    files_written: List[str] = []
    warnings: List[str] = []

    for idx, step in enumerate(steps_raw):
        step_name = step.get("name", f"step_{idx}")
        step_type = step.get("type", "unknown")
        input_step = step.get("input")
        input_file_id = step.get("file_id")
        will_fail = False
        fail_reason = None
        step_warnings: List[str] = []
        estimated_cols: List[str] = []

        # Estimate rows based on step type
        if step_type == "load":
            if not input_file_id:
                will_fail = True
                fail_reason = "Load step has no file_id"
                rows_in = 0
                rows_out = 0
            elif input_file_id not in file_map:
                will_fail = True
                fail_reason = f"file_id '{input_file_id}' not found"
                rows_in = 0
                rows_out = 0
            else:
                f = file_map[input_file_id]
                rows_in = f.row_count
                rows_out = f.row_count
                estimated_cols = list(f.columns)
                files_read.append(input_file_id)
        elif step_type == "filter":
            rows_in = step_rows.get(input_step, 0) if input_step else 0
            rows_out = int(rows_in * 0.7)
            estimated_cols = step_columns.get(input_step, [])
        elif step_type == "join":
            left = step.get("left", "")
            right = step.get("right", "")
            left_rows = step_rows.get(left, 0)
            right_rows = step_rows.get(right, 0)
            how = step.get("how", "inner")
            rows_in = left_rows + right_rows
            if how == "inner":
                rows_out = min(left_rows, right_rows)
            elif how == "left":
                rows_out = left_rows
            elif how == "outer":
                rows_out = left_rows + right_rows
            else:
                rows_out = left_rows
            left_cols = step_columns.get(left, [])
            right_cols = step_columns.get(right, [])
            estimated_cols = list(dict.fromkeys(left_cols + right_cols))
        elif step_type == "aggregate":
            rows_in = step_rows.get(input_step, 0) if input_step else 0
            rows_out = max(1, int(rows_in * 0.1))
            group_by = step.get("group_by", [])
            aggs = step.get("aggregations", [])
            estimated_cols = list(group_by)
            for agg in aggs:
                if isinstance(agg, dict):
                    col = agg.get("column", "")
                    func = agg.get("function", "")
                    estimated_cols.append(f"{col}_{func}")
        elif step_type == "sort":
            rows_in = step_rows.get(input_step, 0) if input_step else 0
            rows_out = rows_in
            estimated_cols = step_columns.get(input_step, [])
        elif step_type == "select":
            rows_in = step_rows.get(input_step, 0) if input_step else 0
            rows_out = rows_in
            estimated_cols = step.get("columns", [])
        elif step_type == "rename":
            rows_in = step_rows.get(input_step, 0) if input_step else 0
            rows_out = rows_in
            mapping = step.get("mapping", {})
            input_cols = step_columns.get(input_step, [])
            estimated_cols = [mapping.get(c, c) for c in input_cols]
        elif step_type == "validate":
            rows_in = step_rows.get(input_step, 0) if input_step else 0
            rows_out = rows_in
            estimated_cols = step_columns.get(input_step, [])
        elif step_type == "save":
            rows_in = step_rows.get(input_step, 0) if input_step else 0
            rows_out = rows_in
            estimated_cols = step_columns.get(input_step, [])
            filename = step.get("filename", "output.csv")
            files_written.append(filename)
        elif step_type == "pivot":
            rows_in = step_rows.get(input_step, 0) if input_step else 0
            unique_pivot_values = max(1, int(rows_in * 0.1))
            rows_out = max(1, int(rows_in / unique_pivot_values))
            index_cols = step.get("index", [])
            pivot_col = step.get("columns", "")
            estimated_cols = index_cols + [
                f"{pivot_col}_{v}" for v in range(unique_pivot_values)
            ]
        elif step_type == "unpivot":
            rows_in = step_rows.get(input_step, 0) if input_step else 0
            value_vars = step.get("value_vars", [])
            rows_out = rows_in * max(1, len(value_vars))
            id_vars = step.get("id_vars", [])
            estimated_cols = id_vars + [
                step.get("var_name", "variable"),
                step.get("value_name", "value"),
            ]
        elif step_type == "deduplicate":
            rows_in = step_rows.get(input_step, 0) if input_step else 0
            rows_out = max(1, int(rows_in * 0.85))
            estimated_cols = step_columns.get(input_step, [])
        elif step_type == "fill_nulls":
            rows_in = step_rows.get(input_step, 0) if input_step else 0
            rows_out = rows_in
            estimated_cols = step_columns.get(input_step, [])
        elif step_type == "sample":
            rows_in = step_rows.get(input_step, 0) if input_step else 0
            n = step.get("n")
            fraction = step.get("fraction")
            if n is not None:
                rows_out = min(n, rows_in)
            elif fraction is not None:
                rows_out = int(rows_in * fraction)
            else:
                rows_out = rows_in
            estimated_cols = step_columns.get(input_step, [])
        else:
            rows_in = step_rows.get(input_step, 0) if input_step else 0
            rows_out = rows_in
            estimated_cols = step_columns.get(input_step, [])

        step_rows[step_name] = rows_out
        step_columns[step_name] = estimated_cols

        # Duration estimation
        duration = _estimate_duration(step_type, rows_in)

        step_plans.append(
            StepPlan(
                step_index=idx,
                step_name=step_name,
                step_type=step_type,
                input_step=input_step,
                input_file_id=input_file_id,
                estimated_rows_in=rows_in,
                estimated_rows_out=rows_out,
                estimated_columns=estimated_cols,
                estimated_duration_ms=duration,
                warnings=step_warnings,
                will_fail=will_fail,
                fail_reason=fail_reason,
            )
        )

    total_duration = sum(s.estimated_duration_ms for s in step_plans)
    total_rows = sum(s.estimated_rows_in or 0 for s in step_plans)
    will_succeed = not any(s.will_fail for s in step_plans)

    return ExecutionPlan(
        pipeline_name=pipeline_name,
        total_steps=len(step_plans),
        estimated_total_duration_ms=total_duration,
        steps=step_plans,
        files_read=files_read,
        files_written=files_written,
        estimated_rows_processed=total_rows,
        warnings=warnings,
        will_succeed=will_succeed,
    )


def _estimate_duration(step_type: str, row_count: int) -> int:
    """Estimate step execution duration in milliseconds."""
    if step_type == "load":
        return max(10, row_count // 1000)
    elif step_type == "filter":
        return max(5, row_count // 5000)
    elif step_type == "join":
        return max(10, row_count // 2000)
    elif step_type == "aggregate":
        return max(10, row_count // 3000)
    elif step_type == "sort":
        return max(5, row_count // 8000)
    else:
        return max(2, row_count // 10000)
