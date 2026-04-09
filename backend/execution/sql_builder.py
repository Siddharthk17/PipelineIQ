"""SQL builders for DuckDB-backed step execution.

These builders translate typed step configs into SQL that operates on
ephemeral DuckDB relations:
    - __input__ for single-input steps
    - __left__/__right__ for join steps
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Sequence

_FORBIDDEN_SQL_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|attach|detach|copy|export|import|call|pragma)\b",
    re.IGNORECASE,
)
_LEADING_COMMENT = re.compile(
    r"^\s*(?:--[^\n]*\n|/\*.*?\*/\s*)*",
    re.IGNORECASE | re.DOTALL,
)


def _step_value(step: Any, key: str, default: Any = None) -> Any:
    if isinstance(step, dict):
        return step.get(key, default)
    return getattr(step, key, default)


def _step_type(step: Any) -> str:
    step_type = _step_value(step, "step_type", _step_value(step, "type", ""))
    if hasattr(step_type, "value"):
        return str(step_type.value)
    return str(step_type)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def quote_identifier(identifier: str) -> str:
    """Quote SQL identifiers safely for DuckDB."""
    if not isinstance(identifier, str) or identifier == "":
        raise ValueError("Identifier must be a non-empty string")
    return f"\"{identifier.replace('\"', '\"\"')}\""


def sql_literal(value: Any) -> str:
    """Convert a Python value into a SQL literal string."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("NaN/Inf values are not valid SQL literals")
        return repr(value)
    if isinstance(value, (list, tuple, set)):
        return "(" + ", ".join(sql_literal(v) for v in value) + ")"
    text = str(value).replace("'", "''")
    return f"'{text}'"


def validate_sql_step_query(
    query: str,
    *,
    require_input_placeholder: bool = True,
) -> str:
    """Validate and normalize SQL-step query text.

    Rules:
    1. Must be a single statement.
    2. Must start with SELECT or WITH (after comments/whitespace).
    3. Must not contain write/admin keywords.
    4. Must include {{input}} placeholder when required.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("SQL query must be a non-empty string")

    normalized = query.strip()
    if normalized.endswith(";"):
        normalized = normalized[:-1].strip()
    if ";" in normalized:
        raise ValueError("Only a single SQL statement is allowed")

    if require_input_placeholder and "{{input}}" not in normalized:
        raise ValueError("SQL query must reference the input via {{input}}")

    sql_for_scan = _LEADING_COMMENT.sub("", normalized)
    if not sql_for_scan:
        raise ValueError("SQL query cannot be empty")

    leading_keyword = sql_for_scan.split(None, 1)[0].lower()
    if leading_keyword not in {"select", "with"}:
        raise ValueError("Only SELECT/CTE queries are allowed")

    if _FORBIDDEN_SQL_KEYWORDS.search(sql_for_scan):
        raise ValueError("SQL query contains disallowed write/admin keywords")

    return normalized.replace("{{input}}", "__input__")


def build_filter_sql(step: Any) -> str:
    column = quote_identifier(str(_step_value(step, "column", "")))
    operator = _step_value(step, "operator", "equals")
    if hasattr(operator, "value"):
        operator = operator.value
    operator = str(operator)
    operator = operator.lower()
    value = _step_value(step, "value")

    compare_ops = {
        "equals": "=",
        "eq": "=",
        "not_equals": "!=",
        "neq": "!=",
        "greater_than": ">",
        "gt": ">",
        "less_than": "<",
        "lt": "<",
        "gte": ">=",
        "lte": "<=",
    }

    if operator in compare_ops:
        predicate = f"{column} {compare_ops[operator]} {sql_literal(value)}"
    elif operator == "contains":
        predicate = f"CAST({column} AS VARCHAR) LIKE {sql_literal(f'%{value}%')}"
    elif operator == "not_contains":
        predicate = f"CAST({column} AS VARCHAR) NOT LIKE {sql_literal(f'%{value}%')}"
    elif operator == "starts_with":
        predicate = f"CAST({column} AS VARCHAR) LIKE {sql_literal(f'{value}%')}"
    elif operator == "ends_with":
        predicate = f"CAST({column} AS VARCHAR) LIKE {sql_literal(f'%{value}')}"
    elif operator == "is_null":
        predicate = f"{column} IS NULL"
    elif operator == "is_not_null":
        predicate = f"{column} IS NOT NULL"
    else:
        raise ValueError(f"Unsupported filter operator '{operator}' for DuckDB routing")

    return f"SELECT * FROM __input__ WHERE {predicate}"


def build_select_sql(step: Any) -> str:
    columns = _as_list(_step_value(step, "columns", []))
    mode = str(_step_value(step, "mode", "include")).lower()
    if not columns:
        return "SELECT * FROM __input__"

    quoted = ", ".join(quote_identifier(str(col)) for col in columns)
    if mode in {"drop", "exclude"}:
        return f"SELECT * EXCLUDE ({quoted}) FROM __input__"
    return f"SELECT {quoted} FROM __input__"


def build_sort_sql(step: Any) -> str:
    by_value = _step_value(step, "by", "")
    by_columns = [str(c) for c in _as_list(by_value) if str(c)]
    if not by_columns:
        raise ValueError("Sort step requires at least one 'by' column")

    ascending = _step_value(step, "ascending", None)
    if ascending is not None:
        ascending_values = _as_list(ascending)
        if len(ascending_values) == 1 and len(by_columns) > 1:
            ascending_values = ascending_values * len(by_columns)
        if len(ascending_values) != len(by_columns):
            raise ValueError("Sort 'ascending' length must match 'by' columns")
        directions = ["ASC" if bool(v) else "DESC" for v in ascending_values]
    else:
        order = _step_value(step, "order", "asc")
        if hasattr(order, "value"):
            order = order.value
        direction = "DESC" if str(order).lower() == "desc" else "ASC"
        directions = [direction] * len(by_columns)

    order_by = ", ".join(
        f"{quote_identifier(col)} {directions[idx]}"
        for idx, col in enumerate(by_columns)
    )
    return f"SELECT * FROM __input__ ORDER BY {order_by}"


def build_aggregate_sql(step: Any) -> str:
    group_by = [str(col) for col in _as_list(_step_value(step, "group_by", []))]
    aggregations = _step_value(step, "aggregations", [])
    if isinstance(aggregations, dict):
        aggregations = [
            {"column": col, "function": func} for col, func in aggregations.items()
        ]
    if not aggregations:
        raise ValueError("Aggregate step requires at least one aggregation")

    select_parts: list[str] = [quote_identifier(col) for col in group_by]
    simple_fn_map = {
        "sum": "SUM",
        "mean": "AVG",
        "avg": "AVG",
        "min": "MIN",
        "max": "MAX",
        "count": "COUNT",
        "median": "MEDIAN",
        "std": "STDDEV_SAMP",
        "var": "VAR_SAMP",
        "first": "FIRST",
        "last": "LAST",
        "mode": "MODE",
    }

    for agg in aggregations:
        column = str(agg.get("column", ""))
        function = str(agg.get("function", "")).lower()
        alias = str(agg.get("alias", f"{column}_{function}"))
        q_col = quote_identifier(column)
        q_alias = quote_identifier(alias)

        if function == "count_distinct":
            expr = f"COUNT(DISTINCT {q_col})"
        elif function in {"p50", "p95"}:
            quantile = "0.5" if function == "p50" else "0.95"
            expr = f"QUANTILE_CONT({q_col}, {quantile})"
        elif function in simple_fn_map:
            expr = f"{simple_fn_map[function]}({q_col})"
        else:
            raise ValueError(f"Unsupported aggregate function '{function}'")

        select_parts.append(f"{expr} AS {q_alias}")

    sql = f"SELECT {', '.join(select_parts)} FROM __input__"
    if group_by:
        group_clause = ", ".join(quote_identifier(col) for col in group_by)
        sql += f" GROUP BY {group_clause}"
    return sql


def build_join_sql(step: Any) -> str:
    join_key = str(_step_value(step, "on", ""))
    if not join_key:
        raise ValueError("Join step requires 'on' key")
    how = _step_value(step, "how", "inner")
    if hasattr(how, "value"):
        how = how.value
    how = str(how).lower()
    valid = {"inner", "left", "right", "outer"}
    if how not in valid:
        raise ValueError(f"Unsupported join type '{how}'")
    return (
        f"SELECT * FROM __left__ {how.upper()} JOIN __right__ "
        f"USING ({quote_identifier(join_key)})"
    )


def build_deduplicate_sql(step: Any) -> str:
    subset = [str(col) for col in _as_list(_step_value(step, "subset", []))]
    keep = str(_step_value(step, "keep", "first")).lower()

    if not subset:
        if keep in {"none", "false"}:
            raise ValueError("DuckDB deduplicate with keep='none' requires subset columns")
        return "SELECT DISTINCT * FROM __input__"

    partition = ", ".join(quote_identifier(col) for col in subset)
    if keep in {"first", "last"}:
        order = "ASC" if keep == "first" else "DESC"
        return (
            "WITH __base AS ("
            "SELECT *, ROW_NUMBER() OVER () AS __rowid__ FROM __input__"
            "), __ranked AS ("
            f"SELECT *, ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY __rowid__ {order}) AS __rn "
            "FROM __base"
            ") "
            "SELECT * EXCLUDE (__rowid__, __rn) FROM __ranked WHERE __rn = 1"
        )
    if keep in {"none", "false"}:
        return (
            "WITH __base AS ("
            "SELECT *, ROW_NUMBER() OVER () AS __rowid__ FROM __input__"
            "), __ranked AS ("
            f"SELECT *, COUNT(*) OVER (PARTITION BY {partition}) AS __cnt FROM __base"
            ") "
            "SELECT * EXCLUDE (__rowid__, __cnt) FROM __ranked WHERE __cnt = 1"
        )
    raise ValueError(f"Unsupported keep strategy '{keep}'")


def build_sample_sql(step: Any) -> str:
    n = _step_value(step, "n")
    fraction = _step_value(step, "fraction", _step_value(step, "frac"))
    random_state = _step_value(step, "random_state", None)

    if n is None and fraction is None:
        raise ValueError("Sample step requires either 'n' or 'fraction'")
    if n is not None and fraction is not None:
        raise ValueError("Sample step cannot define both 'n' and 'fraction'")

    repeat_clause = (
        f" REPEATABLE ({int(random_state)})" if random_state is not None else ""
    )
    if n is not None:
        rows = max(0, int(n))
        return (
            f"SELECT * FROM __input__ USING SAMPLE {rows} ROWS (reservoir)"
            f"{repeat_clause}"
        )

    frac = float(fraction)
    if frac < 0:
        frac = 0.0
    if frac > 1:
        frac = 1.0
    percent = frac * 100.0
    return (
        f"SELECT * FROM __input__ USING SAMPLE {percent:.6f}% (bernoulli)"
        f"{repeat_clause}"
    )


def build_fill_nulls_sql(step: Any) -> str:
    strategy = str(
        _step_value(step, "strategy", _step_value(step, "method", "constant"))
    ).lower()
    columns = [str(col) for col in _as_list(_step_value(step, "columns", []))]
    if not columns:
        raise ValueError("fill_nulls DuckDB routing requires explicit 'columns'")

    strategy_map = {
        "ffill": "forward_fill",
        "bfill": "backward_fill",
    }
    strategy = strategy_map.get(strategy, strategy)

    if strategy == "constant":
        constant_value = _step_value(step, "constant_value", _step_value(step, "value"))
        expressions = [
            f"COALESCE({quote_identifier(col)}, {sql_literal(constant_value)}) AS {quote_identifier(col)}"
            for col in columns
        ]
        return f"SELECT * REPLACE ({', '.join(expressions)}) FROM __input__"

    if strategy in {"mean", "median", "mode"}:
        fn = {"mean": "AVG", "median": "MEDIAN", "mode": "MODE"}[strategy]
        expressions = [
            f"COALESCE({quote_identifier(col)}, {fn}({quote_identifier(col)}) OVER ()) AS {quote_identifier(col)}"
            for col in columns
        ]
        return f"SELECT * REPLACE ({', '.join(expressions)}) FROM __input__"

    if strategy in {"forward_fill", "backward_fill"}:
        if strategy == "forward_fill":
            fill_expr = (
                "LAST_VALUE({col} IGNORE NULLS) OVER ("
                "ORDER BY __rowid__ "
                "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
            )
        else:
            fill_expr = (
                "FIRST_VALUE({col} IGNORE NULLS) OVER ("
                "ORDER BY __rowid__ "
                "ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)"
            )

        expressions = []
        for col in columns:
            q_col = quote_identifier(col)
            expressions.append(
                f"COALESCE({q_col}, {fill_expr.format(col=q_col)}) AS {q_col}"
            )

        return (
            "WITH __base AS ("
            "SELECT *, ROW_NUMBER() OVER () AS __rowid__ FROM __input__"
            ") "
            "SELECT * EXCLUDE (__rowid__) "
            f"REPLACE ({', '.join(expressions)}) "
            "FROM __base"
        )

    raise ValueError(f"Unsupported fill_nulls strategy '{strategy}'")


def build_pivot_sql(step: Any) -> str:
    index_cols = [str(col) for col in _as_list(_step_value(step, "index", []))]
    columns_col = str(_step_value(step, "columns", ""))
    values_col = str(_step_value(step, "values", ""))
    aggfunc = str(_step_value(step, "aggfunc", "sum"))
    if not columns_col or not values_col:
        raise ValueError("Pivot step requires 'columns' and 'values'")
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", aggfunc):
        raise ValueError("Pivot aggfunc must be a simple SQL function identifier")

    group_by_clause = ""
    if index_cols:
        group_by_clause = " GROUP BY " + ", ".join(
            quote_identifier(col) for col in index_cols
        )
    return (
        "SELECT * FROM ("
        f"PIVOT __input__ ON {quote_identifier(columns_col)} "
        f"USING {aggfunc}({quote_identifier(values_col)})"
        f"{group_by_clause}"
        ")"
    )


def build_unpivot_sql(step: Any) -> str:
    id_vars = [str(col) for col in _as_list(_step_value(step, "id_vars", []))]
    value_vars = [str(col) for col in _as_list(_step_value(step, "value_vars", []))]
    var_name = str(_step_value(step, "var_name", "variable"))
    value_name = str(_step_value(step, "value_name", "value"))
    if not value_vars:
        raise ValueError("Unpivot step requires non-empty value_vars")

    source_projection = ", ".join(
        quote_identifier(col) for col in [*id_vars, *value_vars]
    )
    unpivot_columns = ", ".join(quote_identifier(col) for col in value_vars)

    return (
        "SELECT * FROM ("
        f"UNPIVOT (SELECT {source_projection} FROM __input__) "
        f"ON {unpivot_columns} "
        f"INTO NAME {quote_identifier(var_name)} VALUE {quote_identifier(value_name)}"
        ")"
    )


def build_sql_step_sql(step: Any) -> str:
    query = str(_step_value(step, "query", ""))
    return validate_sql_step_query(query, require_input_placeholder=True)


def build_sql_for_step(step: Any) -> str:
    """Dispatch to the correct SQL builder using step type."""
    step_type = _step_type(step)
    builders = {
        "filter": build_filter_sql,
        "select": build_select_sql,
        "sort": build_sort_sql,
        "aggregate": build_aggregate_sql,
        "join": build_join_sql,
        "deduplicate": build_deduplicate_sql,
        "sample": build_sample_sql,
        "fill_nulls": build_fill_nulls_sql,
        "pivot": build_pivot_sql,
        "unpivot": build_unpivot_sql,
        "sql": build_sql_step_sql,
    }
    builder = builders.get(step_type)
    if builder is None:
        raise ValueError(f"No SQL builder registered for step type '{step_type}'")
    return builder(step)
