"""Unit tests for SQL builder helpers."""

from types import SimpleNamespace

import pytest

from backend.execution.sql_builder import (
    build_aggregate_sql,
    build_filter_sql,
    build_sort_sql,
    build_sql_step_sql,
    validate_sql_step_query,
)


def test_build_filter_sql_generates_where_clause() -> None:
    step = SimpleNamespace(column="status", operator="equals", value="delivered")
    sql = build_filter_sql(step)
    assert "FROM __input__" in sql
    assert "WHERE" in sql
    assert '"status" = \'delivered\'' in sql


def test_build_aggregate_sql_creates_expected_alias() -> None:
    step = SimpleNamespace(
        group_by=["region"],
        aggregations=[{"column": "amount", "function": "sum"}],
    )
    sql = build_aggregate_sql(step)
    assert 'SUM("amount") AS "amount_sum"' in sql
    assert 'GROUP BY "region"' in sql


def test_build_sort_sql_supports_multiple_columns() -> None:
    step = SimpleNamespace(by=["region", "amount"], ascending=[True, False])
    sql = build_sort_sql(step)
    assert 'ORDER BY "region" ASC, "amount" DESC' in sql


def test_validate_sql_step_query_requires_input_placeholder() -> None:
    with pytest.raises(ValueError, match=r"\{\{input\}\}"):
        validate_sql_step_query("SELECT * FROM some_table")


def test_validate_sql_step_query_rejects_write_keywords() -> None:
    with pytest.raises(ValueError, match="disallowed"):
        validate_sql_step_query("SELECT * FROM {{input}} WHERE action = 'drop'")


def test_validate_sql_step_query_rejects_multiple_statements() -> None:
    with pytest.raises(ValueError, match="single SQL statement"):
        validate_sql_step_query("SELECT * FROM {{input}}; SELECT 1;")


def test_build_sql_step_sql_replaces_input_placeholder() -> None:
    step = SimpleNamespace(query="SELECT customer_id FROM {{input}} WHERE amount > 5")
    sql = build_sql_step_sql(step)
    assert "{{input}}" not in sql
    assert "__input__" in sql
