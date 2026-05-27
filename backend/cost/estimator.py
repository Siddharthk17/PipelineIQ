"""Pre-run pipeline cost estimation using historical step data.

Estimates execution time, memory, and provides optimization tips
before the pipeline runs. Displayed as a pre-run card.

Data source: historical step_results.duration_ms, row_in, row_out.
Confidence: based on how much historical data exists for these step types.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MS_PER_1000_ROWS: dict[str, float] = {
    "load": 80.0,
    "filter": 5.0,
    "aggregate": 15.0,
    "join": 25.0,
    "sort": 20.0,
    "select": 2.0,
    "transform": 10.0,
    "validate": 12.0,
    "save": 100.0,
    "pivot": 30.0,
    "unpivot": 20.0,
    "deduplicate": 10.0,
    "fill_nulls": 5.0,
    "rename": 2.0,
    "sample": 8.0,
    "sql": 20.0,
    "wasm_compute": 50.0,
}

BYTES_PER_CELL = 8.0

DUCKDB_STEP_TYPES = frozenset(
    {
        "filter",
        "select",
        "sort",
        "aggregate",
        "join",
        "deduplicate",
        "fill_nulls",
        "sample",
        "pivot",
        "unpivot",
        "sql",
    }
)


@dataclass
class StepEstimate:
    step_name: str
    step_type: str
    engine: str
    predicted_ms: int
    confidence: float
    row_in_est: int
    row_out_est: int
    note: str = ""


@dataclass
class CostEstimate:
    total_ms: int
    peak_memory_mb: float
    step_estimates: list[StepEstimate] = field(default_factory=list)
    confidence: float = 0.0
    optimization_tip: str = ""
    data_points_used: int = 0


def estimate_pipeline_cost(
    pipeline_yaml: str,
    file_ids: list[str],
    db,
) -> CostEstimate:
    from backend.pipeline.cache import get_parsed_pipeline
    from backend.models.file_profile import FileProfile
    from sqlalchemy import text

    try:
        pipeline = get_parsed_pipeline(pipeline_yaml)
        steps = getattr(pipeline, "steps", []) or pipeline.get("steps", [])
    except Exception as e:
        return CostEstimate(
            total_ms=0,
            peak_memory_mb=0,
            confidence=0.0,
            optimization_tip=f"Pipeline YAML has errors: {e}",
        )

    if not steps:
        return CostEstimate(total_ms=0, peak_memory_mb=0, confidence=0.0)

    estimated_rows = 0
    for file_id in file_ids:
        try:
            profile = (
                db.query(FileProfile)
                .filter(FileProfile.file_id == file_id)
                .first()
            )
            if profile and profile.row_count:
                estimated_rows = max(estimated_rows, profile.row_count)
        except Exception:
            pass

    if estimated_rows == 0:
        estimated_rows = 10_000

    step_types_in_pipeline = list(
        {_get_step_attr(s, "type", "load") for s in steps}
    )

    historical: dict[str, float] = {}
    data_points_used = 0

    if step_types_in_pipeline:
        try:
            for step_type in step_types_in_pipeline:
                sql = text(
                    """
                    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
                           COUNT(*) AS data_points
                    FROM step_results
                    WHERE step_type = :step_type
                      AND duration_ms IS NOT NULL
                      AND duration_ms > 0
                      AND row_in IS NOT NULL
                      AND row_in > 0
                """
                )
                result = db.execute(sql, {"step_type": step_type}).first()
                if result and result.p50_ms is not None:
                    historical[step_type] = float(result.p50_ms)
                    data_points_used += result.data_points or 0
        except Exception as e:
            logger.warning("Could not load historical step data: %s", e)

    step_estimates = []
    total_ms = 0
    peak_rows = estimated_rows

    filter_after_aggregate = False
    aggregate_idx = next(
        (
            i
            for i, s in enumerate(steps)
            if _get_step_attr(s, "type") == "aggregate"
        ),
        -1,
    )
    filter_idx = next(
        (
            i
            for i, s in enumerate(steps)
            if _get_step_attr(s, "type") == "filter"
        ),
        -1,
    )
    if filter_idx > aggregate_idx > -1:
        filter_after_aggregate = True

    for step in steps:
        step_type = _get_step_attr(step, "type", "load")
        step_name = _get_step_attr(step, "name", "step")
        engine = _determine_engine(step_type)

        if step_type in historical:
            predicted_ms = int(historical[step_type] * (estimated_rows / 10_000))
            confidence_for_step = min(0.9, data_points_used / 20)
        else:
            rate = MS_PER_1000_ROWS.get(step_type, 10.0)
            predicted_ms = int(rate * (estimated_rows / 1_000))
            confidence_for_step = 0.4

        row_in = peak_rows
        row_out = _estimate_output_rows(step, step_type, row_in)
        peak_rows = max(row_out, 1)

        step_estimates.append(
            StepEstimate(
                step_name=step_name,
                step_type=step_type,
                engine=engine,
                predicted_ms=max(predicted_ms, 1),
                confidence=confidence_for_step,
                row_in_est=row_in,
                row_out_est=row_out,
            )
        )
        total_ms += max(predicted_ms, 1)

    avg_confidence = (
        sum(s.confidence for s in step_estimates) / len(step_estimates)
        if step_estimates
        else 0.0
    )

    num_cols = 10
    has_join = any(
        _get_step_attr(s, "type") == "join" for s in steps
    )
    multiplier = 3.0 if has_join else 1.5
    peak_memory_mb = (
        estimated_rows * num_cols * BYTES_PER_CELL * multiplier
    ) / 1_048_576

    tip = _generate_optimization_tip(steps, step_estimates, filter_after_aggregate)

    return CostEstimate(
        total_ms=total_ms,
        peak_memory_mb=round(peak_memory_mb, 1),
        step_estimates=step_estimates,
        confidence=round(avg_confidence * 100, 1),
        optimization_tip=tip,
        data_points_used=data_points_used,
    )


def _estimate_output_rows(step, step_type: str, input_rows: int) -> int:
    if step_type == "filter":
        return max(input_rows // 3, 1)
    elif step_type == "aggregate":
        return max(input_rows // 10, 1)
    elif step_type == "deduplicate":
        return max(int(input_rows * 0.9), 1)
    elif step_type == "sample":
        n = _get_step_attr(step, "n", None)
        fraction = _get_step_attr(step, "fraction", None)
        if n:
            return min(int(n), input_rows)
        elif fraction:
            return max(int(input_rows * float(fraction)), 1)
        return min(1000, input_rows)
    elif step_type == "join":
        return max(int(input_rows * 0.8), 1)
    else:
        return input_rows


def _generate_optimization_tip(
    steps, estimates, filter_after_aggregate: bool
) -> str:
    if filter_after_aggregate:
        return (
            "Move your filter step before the aggregate — "
            "filtering first can reduce aggregate computation by up to 70%."
        )

    has_wasm = any(e.step_type == "wasm_compute" for e in estimates)
    if has_wasm:
        return (
            "Wasm UDF steps run row-by-row — "
            "for large datasets, consider pre-filtering with a DuckDB filter step first."
        )

    has_multiple_joins = (
        sum(1 for e in estimates if e.step_type == "join") > 1
    )
    if has_multiple_joins:
        return (
            "Multiple joins detected — "
            "place the most selective join first to reduce subsequent join sizes."
        )

    slowest = max(estimates, key=lambda e: e.predicted_ms, default=None)
    if slowest and slowest.predicted_ms > 5000:
        return (
            f"'{slowest.step_name}' ({slowest.step_type}) is the bottleneck "
            f"(~{slowest.predicted_ms // 1000}s estimated). "
            f"Consider partitioning the input file for parallel processing."
        )

    return "Pipeline structure looks efficient for this data size."


def _get_step_attr(step, attr: str, default=None):
    return (
        getattr(step, attr, None)
        or (step.get(attr) if isinstance(step, dict) else None)
        or default
    )


def _determine_engine(step_type: str) -> str:
    if step_type == "wasm_compute":
        return "wasm"
    elif step_type in DUCKDB_STEP_TYPES:
        return "duckdb"
    elif step_type in ("load", "save"):
        return "io"
    return "pandas"
