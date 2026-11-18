"""Regression tests for M-001: SQL step sandbox escape.

Each bypass listed in the finding must be rejected by the validator
(deny-by-default) and blocked at the DuckDB connection level:

- quoted identifiers:      SELECT * FROM "read_text"('/etc/hostname')
- generic query() fn:      query('SELECT ... read_text ...')
- replacement scans:       FROM '/etc/passwd'
- unlisted file readers:   glob, parquet_scan, read_ndjson, read_xlsx, ...
- unbounded recursion:     WITH RECURSIVE ... (no statement timeout)
- placeholder smuggling:   {{input}} hidden in a comment or string literal
"""

import pandas as pd
import pyarrow as pa
import pytest

from backend.execution.duckdb_executor import DuckDBExecutor
from backend.execution.sql_builder import validate_sql_step_query
from backend.pipeline.parser import SqlStepConfig, StepType

EVASIONS = [
    # Quoted identifier defeats the bare-token function regex.
    'SELECT * FROM "read_text"(\'/etc/hostname\') -- {{input}}',
    'SELECT "read_csv"(\'/etc/passwd\') FROM {{input}}',
    # Generic query() table function runs an inner SQL string.
    "SELECT *, (SELECT content FROM query('SELECT 1 AS content')) AS q "
    "FROM {{input}}",
    # Replacement scans: bare string literal as a table reference.
    "SELECT * FROM '/etc/passwd' -- {{input}}",
    'SELECT * FROM "/etc/hostname" -- {{input}}',
    # Unlisted file-reader table functions.
    "SELECT * FROM glob('/etc/*') WHERE '{{input}}' = 'x'",
    "SELECT * FROM parquet_scan('/tmp/x.parquet') -- {{input}}",
    "SELECT * FROM read_ndjson('/tmp/a.json') -- {{input}}",
    "SELECT * FROM read_json_objects('/tmp/a.json') -- {{input}}",
    "SELECT * FROM read_xlsx('/tmp/a.xlsx') -- {{input}}",
    "SELECT * FROM csv_scan('/tmp/a.csv') -- {{input}}",
    "SELECT * FROM delta_scan('/tmp/delta') -- {{input}}",
    "SELECT * FROM sqlite_scan('/tmp/x.db', 't') -- {{input}}",
    "SELECT * FROM query_table('t', 'SELECT 1') -- {{input}}",
    "SELECT * FROM pragma_database_list() -- {{input}}",
    # Unbounded recursive CTE wedges workers (no statement timeout).
    "WITH RECURSIVE t(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM t) "
    "SELECT * FROM t, {{input}}",
    # {{input}} smuggled inside a comment or string literal.
    "SELECT * FROM read_csv('/etc/passwd') -- {{input}}",
    "SELECT * FROM read_csv('/etc/passwd') WHERE 'x' = '{{input}}'",
]


@pytest.mark.parametrize("query", EVASIONS)
def test_m001_validator_rejects_file_access_evasions(query: str) -> None:
    """Every documented bypass must be rejected at validation time."""
    with pytest.raises(ValueError):
        validate_sql_step_query(query)


def test_m001_validator_accepts_legit_select_from_input() -> None:
    """Deny-by-default must not break legitimate projections."""
    normalized = validate_sql_step_query(
        "SELECT customer_id, amount * 2 AS amount_x2 "
        "FROM {{input}} WHERE amount > 100"
    )
    assert "__input__" in normalized
    assert "{{input}}" not in normalized


def test_m001_validator_accepts_cte_over_input() -> None:
    """Named CTEs over {{input}} remain usable."""
    normalized = validate_sql_step_query(
        "WITH big AS (SELECT * FROM {{input}} WHERE amount > 1) "
        "SELECT * FROM big"
    )
    assert "__input__" in normalized


def test_m001_executor_blocks_direct_file_read() -> None:
    """Connection-level hardening blocks file access even past validation."""
    executor = DuckDBExecutor()
    table = pa.Table.from_pandas(pd.DataFrame({"x": [1]}), preserve_index=False)
    step = SqlStepConfig(
        name="evil", step_type=StepType.SQL, input="load",
        query="SELECT * FROM read_csv('/etc/hostname') -- {{input}}",
    )
    # Validation fires first (defense in depth: both layers must hold).
    with pytest.raises(ValueError):
        executor.execute_step(step, table)


def test_m001_connection_disables_external_access() -> None:
    """Worker/local connections must deny file-system access by default."""
    executor = DuckDBExecutor()
    conn = executor._get_connection()
    rows = dict(
        conn.execute(
            "SELECT name, value FROM duckdb_settings() "
            "WHERE name IN ('enable_external_access', 'max_execution_time', "
            "'autoinstall_known_extensions', 'autoload_known_extensions')"
        ).fetchall()
    )
    assert rows["enable_external_access"] == "false"
    assert int(rows["max_execution_time"]) > 0
    assert rows["autoinstall_known_extensions"] == "false"
    assert rows["autoload_known_extensions"] == "false"
    executor.close()
