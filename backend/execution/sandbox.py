"""Ephemeral DuckDB sandbox used to validate healing patches safely."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
from sqlalchemy.orm import Session

from backend.execution.duckdb_executor import DuckDBExecutor
from backend.models import UploadedFile
from backend.pipeline.cache import get_parsed_pipeline
from backend.services.storage_service import storage_service
from backend.utils.uuid_utils import as_uuid

SANDBOX_SAMPLE_ROWS = 100


@dataclass
class SandboxResult:
    """Outcome of validating a candidate healing patch in DuckDB."""

    success: bool
    output_rows: int = 0
    output_columns: list[str] = field(default_factory=list)
    error: str | None = None
    duration_ms: float = 0.0


def test_patch_in_sandbox(
    *,
    patched_yaml: str,
    file_ids: list[str],
    db: Session,
    sample_rows: int = SANDBOX_SAMPLE_ROWS,
) -> SandboxResult:
    """Run a patched pipeline on sampled data inside a fresh DuckDB connection."""
    start = time.perf_counter()
    connection: duckdb.DuckDBPyConnection | None = None

    try:
        config = get_parsed_pipeline(patched_yaml)
        if not getattr(config, "steps", None):
            return SandboxResult(success=False, error="Patched pipeline has no steps")

        sampled_tables = _load_sample_tables(file_ids=file_ids, db=db, sample_rows=sample_rows)
        connection = duckdb.connect(database=":memory:")
        connection.execute("PRAGMA threads=2")
        connection.execute("SET memory_limit='500MB'")

        executor = DuckDBExecutor(
            connection_getter=lambda: connection,
            local_fallback=False,
        )
        result_table = _run_pipeline_in_sandbox(
            config=config,
            executor=executor,
            sampled_tables=sampled_tables,
        )
        return SandboxResult(
            success=True,
            output_rows=result_table.num_rows,
            output_columns=result_table.column_names,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        )
    except Exception as exc:
        return SandboxResult(
            success=False,
            error=str(exc),
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        )
    finally:
        if connection is not None:
            connection.close()


def _run_pipeline_in_sandbox(*, config, executor: DuckDBExecutor, sampled_tables: dict[str, pa.Table]) -> pa.Table:
    table_registry: dict[str, pa.Table] = {}
    last_result: pa.Table | None = None

    for step in config.steps:
        step_type = getattr(step.step_type, "value", step.step_type)

        if step_type == "load":
            sample_table = sampled_tables.get(step.file_id)
            if sample_table is None:
                raise ValueError(
                    f"Load step '{step.name}' references file_id '{step.file_id}' with no sampled table"
                )
            table_registry[step.name] = sample_table
            last_result = sample_table
            continue

        if step_type == "save":
            last_result = table_registry.get(step.input, last_result)
            continue

        if step_type == "rename":
            input_table = _require_input_table(step.name, getattr(step, "input", ""), table_registry)
            input_df = input_table.to_pandas()
            missing_columns = [column for column in step.mapping.keys() if column not in input_df.columns]
            if missing_columns:
                raise ValueError(
                    f"Rename step '{step.name}' references missing columns: {missing_columns}"
                )
            renamed_table = pa.Table.from_pandas(
                input_df.rename(columns=step.mapping),
                preserve_index=False,
            )
            table_registry[step.name] = renamed_table
            last_result = renamed_table
            continue

        if step_type == "validate":
            validated_table = _require_input_table(step.name, getattr(step, "input", ""), table_registry)
            table_registry[step.name] = validated_table
            last_result = validated_table
            continue

        if step_type == "join":
            left_table = _require_input_table(step.name, step.left, table_registry)
            right_table = _require_input_table(step.name, step.right, table_registry)
            result_table = executor.execute_step(
                step,
                left_table,
                extra_tables={"__left__": left_table, "__right__": right_table},
            )
            table_registry[step.name] = result_table
            last_result = result_table
            continue

        input_name = getattr(step, "input", "")
        input_table = _require_input_table(step.name, input_name, table_registry)
        result_table = executor.execute_step(step, input_table)
        table_registry[step.name] = result_table
        last_result = result_table

    if last_result is None:
        raise ValueError("Sandbox pipeline produced no output")

    return last_result


def _require_input_table(step_name: str, input_name: str, table_registry: dict[str, pa.Table]) -> pa.Table:
    table = table_registry.get(input_name)
    if table is None:
        raise ValueError(
            f"Step '{step_name}' input '{input_name}' is not available in the sandbox"
        )
    return table


def _load_sample_tables(*, file_ids: list[str], db: Session, sample_rows: int) -> dict[str, pa.Table]:
    sampled_tables: dict[str, pa.Table] = {}
    for file_id in file_ids:
        file_record = db.query(UploadedFile).filter(UploadedFile.id == as_uuid(file_id)).first()
        if file_record is None:
            raise ValueError(f"File '{file_id}' not found")

        sampled_frame = _load_sample_frame(file_record=file_record, sample_rows=sample_rows)
        sampled_tables[str(file_record.id)] = pa.Table.from_pandas(
            sampled_frame,
            preserve_index=False,
        )
    return sampled_tables


def _load_sample_frame(*, file_record: UploadedFile, sample_rows: int) -> pd.DataFrame:
    extension = Path(file_record.stored_path).suffix.lower()
    with storage_service.download(file_record.stored_path) as handle:
        if extension == ".csv":
            return pd.read_csv(handle, nrows=sample_rows)
        if extension == ".json":
            return pd.read_json(handle).head(sample_rows)
    raise ValueError(
        f"Sandbox only supports CSV and JSON inputs, got '{extension}' for '{file_record.original_filename}'"
    )
