"""Dry-run execution planner for pipeline preview.

Generates an estimated execution plan without processing data.
Uses file metadata for row count estimates and heuristics for
step-level row/duration estimation.
"""

import re
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from backend.models import UploadedFile


@dataclass
class StepPlan:
    """Estimated execution plan for a single step."""

    step_index: int
    step_name: str
    step_type: str
    input_step: str | None
    input_file_id: str | None
    estimated_rows_in: int | None
    estimated_rows_out: int | None
    estimated_columns: list[str]
    estimated_duration_ms: int
    warnings: list[str]
    will_fail: bool
    fail_reason: str | None


@dataclass
class ExecutionPlan:
    """Complete dry-run execution plan for a pipeline."""

    pipeline_name: str
    total_steps: int
    estimated_total_duration_ms: int
    steps: list[StepPlan]
    files_read: list[str]
    files_written: list[str]
    estimated_rows_processed: int
    warnings: list[str]
    will_succeed: bool


def generate_execution_plan(
    yaml_config: str,
    db: Session,
    user_id=None,
    is_admin: bool = False,
    parsed_config=None,
) -> ExecutionPlan:
    """Generate an execution plan from a YAML config without running anything.

    When user_id is given (and not an admin), file metadata is scoped to
    that user's own uploads so estimates never leak other users' data.

    M-110: parsing goes through the single shared path
    (`PipelineParser.parse_raw` + `extract_envelope`), so diff-stripping,
    quote repair, size caps, and recursion guards behave exactly as in
    `/validate`. Ragged step entries (non-dict steps, steps-as-mapping)
    are reported as failed plan steps instead of raising 500s.
    """
    from backend.pipeline.parser import PipelineParser, extract_envelope

    if parsed_config is not None:
        pipeline_name = parsed_config.name or "unnamed"
        steps_raw = []
        for step in parsed_config.steps:
            raw_step: dict = {
                "name": step.name,
                "type": getattr(step.step_type, "value", step.step_type),
            }
            for attr in ("input", "left", "right", "file_id", "columns",
                         "mapping", "group_by", "aggregations", "by",
                         "filename", "index", "id_vars", "value_vars",
                         "var_name", "value_name", "n", "fraction", "query",
                         "how"):
                value = getattr(step, attr, None)
                if value is not None:
                    raw_step[attr] = (
                        value.value if isinstance(value, Enum) else value)
            steps_raw.append(raw_step)
    else:
        raw = PipelineParser().parse_raw(yaml_config)
        pipeline_name, steps_raw = extract_envelope(raw)
        pipeline_name = pipeline_name or "unnamed"
        if not isinstance(steps_raw, list):
            # M-110/M-113: "steps" as a mapping or scalar used to crash
            # the planner with AttributeError (HTTP 500). Plan zero
            # steps with a warning instead; /plan already rejects such
            # configs at validation time.
            steps_raw = []
            envelope_warning = (
                "Pipeline 'steps' must be a list; no steps to plan.")
        else:
            envelope_warning = None

    # Load file metadata from DB
    files_query = db.query(UploadedFile)
    if user_id is not None and not is_admin:
        files_query = files_query.filter(UploadedFile.user_id == user_id)
    uploaded_files = files_query.all()
    file_map: dict[str, UploadedFile] = {str(f.id): f for f in uploaded_files}

    step_plans: list[StepPlan] = []
    step_rows: dict[str, int] = {}
    step_columns: dict[str, list[str]] = {}
    files_read: list[str] = []
    files_written: list[str] = []
    warnings: list[str] = []

    if envelope_warning is not None:
        warnings.append(envelope_warning)

    for idx, step in enumerate(steps_raw):
        if not isinstance(step, dict):
            # M-110/M-113: a non-mapping step entry (e.g. a bare string
            # in the steps list) used to crash the planner with
            # AttributeError. Mark it failed and keep planning the rest.
            step_plans.append(
                StepPlan(
                    step_index=idx,
                    step_name=f"step_{idx}",
                    step_type="unknown",
                    input_step=None,
                    input_file_id=None,
                    estimated_rows_in=0,
                    estimated_rows_out=0,
                    estimated_columns=[],
                    estimated_duration_ms=0,
                    warnings=[],
                    will_fail=True,
                    fail_reason=(
                        "Step entry must be a mapping with 'name' and "
                        f"'type'; got {type(step).__name__}"
                    ),
                )
            )
            continue
        step_name = step.get("name", f"step_{idx}")
        if not isinstance(step_name, str) or not step_name:
            step_name = f"step_{idx}"
        step_type = step.get("type", "unknown")
        if not isinstance(step_type, str):
            step_type = str(step_type)
        input_step = step.get("input")
        input_file_id = step.get("file_id")
        will_fail = False
        fail_reason = None
        step_warnings: list[str] = []
        estimated_cols: list[str] = []

        if not isinstance(input_step, str):
            input_step = None
        if not isinstance(input_file_id, str):
            input_file_id = None
        # Normalized registry key: "" never matches a real step, so a
        # missing/ragged input reference yields the same defaults as before.
        lookup_key = input_step or ""

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
                rows_in = f.row_count or 0
                rows_out = f.row_count or 0
                estimated_cols = list(f.columns or [])
                files_read.append(input_file_id)
        elif step_type == "filter":
            rows_in = step_rows.get(lookup_key, 0)
            rows_out = int(rows_in * 0.7)
            estimated_cols = step_columns.get(lookup_key, [])
        elif step_type == "join":
            left = step.get("left", "")
            right = step.get("right", "")
            if not isinstance(left, str):
                left = ""
            if not isinstance(right, str):
                right = ""
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
            rows_in = step_rows.get(lookup_key, 0)
            rows_out = max(1, int(rows_in * 0.1))
            group_by = step.get("group_by", [])
            aggs = step.get("aggregations", [])
            if isinstance(group_by, str):
                group_by = [group_by]
            if not isinstance(group_by, list):
                group_by = []
            if isinstance(aggs, dict):
                aggs = [{"column": c, "function": f}
                        for c, f in aggs.items()]
            if not isinstance(aggs, list):
                aggs = []
            estimated_cols = [c for c in group_by if isinstance(c, str)]
            for agg in aggs:
                if isinstance(agg, dict):
                    col = agg.get("column", "")
                    func = agg.get("function", "")
                    estimated_cols.append(f"{col}_{func}")
        elif step_type == "sort":
            rows_in = step_rows.get(lookup_key, 0)
            rows_out = rows_in
            estimated_cols = step_columns.get(lookup_key, [])
        elif step_type == "select":
            rows_in = step_rows.get(lookup_key, 0)
            rows_out = rows_in
            cols = step.get("columns", [])
            estimated_cols = cols if isinstance(cols, list) else []
        elif step_type == "rename":
            rows_in = step_rows.get(lookup_key, 0)
            rows_out = rows_in
            mapping = step.get("mapping", {})
            if not isinstance(mapping, dict):
                mapping = {}
            input_cols = step_columns.get(lookup_key, [])
            estimated_cols = [mapping.get(c, c) for c in input_cols]
        elif step_type == "validate":
            rows_in = step_rows.get(lookup_key, 0)
            rows_out = rows_in
            estimated_cols = step_columns.get(lookup_key, [])
        elif step_type == "save":
            rows_in = step_rows.get(lookup_key, 0)
            rows_out = rows_in
            estimated_cols = step_columns.get(lookup_key, [])
            filename = step.get("filename", "output.csv")
            files_written.append(
                filename if isinstance(filename, str) else "output.csv")
        elif step_type == "pivot":
            rows_in = step_rows.get(lookup_key, 0)
            unique_pivot_values = max(1, int(rows_in * 0.1))
            rows_out = max(1, int(rows_in / unique_pivot_values))
            index_cols = step.get("index", [])
            if isinstance(index_cols, str):
                index_cols = [index_cols]
            if not isinstance(index_cols, list):
                index_cols = []
            pivot_col = step.get("columns", "")
            if not isinstance(pivot_col, str):
                pivot_col = ""
            estimated_cols = [c for c in index_cols
                              if isinstance(c, str)] + [
                f"{pivot_col}_{v}" for v in range(unique_pivot_values)
            ]
        elif step_type == "unpivot":
            rows_in = step_rows.get(lookup_key, 0)
            value_vars = step.get("value_vars", [])
            if not isinstance(value_vars, list):
                value_vars = []
            rows_out = rows_in * max(1, len(value_vars))
            id_vars = step.get("id_vars", [])
            if not isinstance(id_vars, list):
                id_vars = []
            var_name = step.get("var_name", "variable")
            value_name = step.get("value_name", "value")
            estimated_cols = [
                *(c for c in id_vars if isinstance(c, str)),
                var_name if isinstance(var_name, str) else "variable",
                value_name if isinstance(value_name, str) else "value",
            ]
        elif step_type == "deduplicate":
            rows_in = step_rows.get(lookup_key, 0)
            rows_out = max(1, int(rows_in * 0.85))
            estimated_cols = step_columns.get(lookup_key, [])
        elif step_type == "fill_nulls":
            rows_in = step_rows.get(lookup_key, 0)
            rows_out = rows_in
            estimated_cols = step_columns.get(lookup_key, [])
        elif step_type == "sample":
            rows_in = step_rows.get(lookup_key, 0)
            n = step.get("n")
            fraction = step.get("fraction")
            if isinstance(n, bool):
                n = None
            if isinstance(fraction, bool):
                fraction = None
            if n is not None:
                try:
                    n = int(n)
                except (TypeError, ValueError):
                    n = None
            if fraction is not None:
                try:
                    fraction = float(fraction)
                except (TypeError, ValueError):
                    fraction = None
            if n is not None:
                rows_out = min(n, rows_in)
            elif fraction is not None:
                rows_out = int(rows_in * fraction)
            else:
                rows_out = rows_in
            estimated_cols = step_columns.get(lookup_key, [])
        elif step_type == "sql":
            rows_in = step_rows.get(lookup_key, 0)
            rows_out = max(1, int(rows_in * 0.8))
            query = str(step.get("query", ""))
            limit_match = re.search(r"\blimit\s+(\d+)", query, re.IGNORECASE)
            if limit_match:
                rows_out = min(rows_out, int(limit_match.group(1)))
            estimated_cols = step_columns.get(lookup_key, [])
        else:
            rows_in = step_rows.get(lookup_key, 0)
            rows_out = rows_in
            estimated_cols = step_columns.get(lookup_key, [])

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
    elif step_type == "sql":
        return max(8, row_count // 2500)
    else:
        return max(2, row_count // 10000)
