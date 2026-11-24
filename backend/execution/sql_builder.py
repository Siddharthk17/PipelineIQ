"""SQL builders for DuckDB-backed step execution.

These builders translate typed step configs into SQL that operates on
ephemeral DuckDB relations:
    - __input__ for single-input steps
    - __left__/__right__ for join steps
"""

from __future__ import annotations

import math
import re
from typing import Any

_FORBIDDEN_SQL_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|attach|detach|copy|export|import|call|pragma|prepare|execute|deallocate|explain|analyze|set|install|load|use|checkpoint|force|show|describe|summarize)\b",
    re.IGNORECASE,
)
_FORBIDDEN_SQL_FUNCTIONS = re.compile(
    r"""(?ix)
    \b(
        read_[a-z_]*                     # read_csv, read_parquet, read_text, ...
        | csv_scan | parquet_scan | delta_scan | iceberg_scan
        | sqlite_scan | sqlite_query
        | postgres_scan | postgres_query
        | glob(_table_function)?
        | query(_table)?                 # generic query() inner-SQL escape hatch
        | pragma_[a-z_]*                # pragma_* table functions
        | sniff_csv | read_xlsx | read_excel
        | [a-z_][a-z0-9_]*_(scan|query)  # any other *_scan() / *_query() reader
    )\s*\(
    """,
)
_FORBIDDEN_WITH_RECURSIVE = re.compile(
    r"\bwith\s+recursive\b",
    re.IGNORECASE,
)
_FORBIDDEN_EXTERNAL_URI = re.compile(
    r"(?i)(?:s3|http|https|file|ftp|gcs|azure)://"
)
_LEADING_COMMENT = re.compile(
    r"^\s*(?:--[^\n]*\n|/\*.*?\*/\s*)*",
    re.IGNORECASE | re.DOTALL,
)
_DOLLAR_QUOTE_START = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")

#: Relations a SQL step may read. Deny-by-default: user SQL can only touch
#: the registered input relation(s), CTEs it defines itself, brace
#: placeholders substituted by the executor, or a few provably safe table
#: functions. Anything else after FROM/JOIN (file paths, reader functions,
#: foreign tables) is rejected. (M-001)
_ALLOWED_TABLE_REFS = frozenset({"__input__", "__left__", "__right__"})
_SAFE_TABLE_FUNCTIONS = frozenset({"range", "generate_series", "unnest"})
_CLAUSE_TERMINATORS = frozenset({
    "where", "group", "order", "limit", "offset", "having", "window",
    "union", "except", "intersect", "fetch", "for",
})
_CTE_DEFINITION = re.compile(
    r"(?:\bwith\b|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(",
    re.IGNORECASE,
)
_QUOTED_CTE_DEFINITION = re.compile(
    r'(?:\bwith\b|,)\s*"((?:[^"]|"")*)"\s+as\s*\(',
    re.IGNORECASE,
)
_FROM_OR_JOIN = re.compile(r"\b(from|join)\b", re.IGNORECASE)
_WORD_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _strip_literals_and_comments(sql: str) -> str:
    """Mask SQL string/comment bodies so keyword scans only inspect executable SQL.

    Double-quoted identifiers are masked as well, *except* when the quoted
    name is immediately followed by ``(``: DuckDB accepts quoted function
    names (``"read_text"('/etc/hostname')``), so those must stay visible to
    the forbidden-function scan (M-001). Quoted non-call identifiers such
    as ``SELECT "delete" FROM ...`` remain masked to avoid false positives.

    Masking is strictly length-preserving, so offsets in the returned scan
    string match offsets in the input (used by table-reference checks).
    """
    result: list[str] = []
    i = 0
    state = "code"
    dollar_delimiter = ""
    quoted_buffer: list[str] = []
    quote_start = 0
    length = len(sql)

    while i < length:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < length else ""

        if state == "code":
            if ch == "$":
                match = _DOLLAR_QUOTE_START.match(sql, i)
                if match:
                    dollar_delimiter = match.group(0)
                    state = "dollar_quote"
                    result.extend(" " * len(dollar_delimiter))
                    i += len(dollar_delimiter) - 1
                else:
                    result.append(ch)
            elif ch == "'" and state == "code":
                state = "single_quote"
                result.append(" ")
            elif ch == '"' and state == "code":
                state = "double_quote"
                quoted_buffer = []
                quote_start = i
            elif ch == "-" and nxt == "-":
                state = "line_comment"
                result.extend((" ", " "))
                i += 1
            elif ch == "/" and nxt == "*":
                state = "block_comment"
                result.extend((" ", " "))
                i += 1
            else:
                result.append(ch)
        elif state == "single_quote":
            if ch == "'" and nxt == "'":
                result.extend((" ", " "))
                i += 1
            elif ch == "'":
                state = "code"
                result.append(" ")
            else:
                result.append(" ")
        elif state == "double_quote":
            if ch == '"' and nxt == '"':
                quoted_buffer.append('"')
                i += 1
            elif ch == '"':
                # A quoted name followed by '(' is a (possibly malicious)
                # quoted function call — keep it visible, padded so scan
                # and raw positions stay aligned. Otherwise mask it.
                content = "".join(quoted_buffer)
                consumed = i - quote_start + 1
                if sql[i + 1:].lstrip().startswith("("):
                    result.append(content + " " * (consumed - len(content)))
                else:
                    result.append(" " * consumed)
                state = "code"
            else:
                quoted_buffer.append(ch)
        elif state == "line_comment":
            if ch == "\n":
                state = "code"
                result.append("\n")
            else:
                result.append(" ")
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                result.extend((" ", " "))
                i += 1
            else:
                result.append(" ")
        elif state == "dollar_quote":
            if dollar_delimiter and sql.startswith(dollar_delimiter, i):
                result.extend(" " * len(dollar_delimiter))
                i += len(dollar_delimiter) - 1
                dollar_delimiter = ""
                state = "code"
            else:
                result.append(" ")

        i += 1

    if state in {"single_quote", "double_quote", "block_comment", "dollar_quote"}:
        raise ValueError("SQL query contains an unterminated literal or comment")

    return "".join(result)


def _collect_cte_names(scan_sql: str, raw_sql: str) -> set[str]:
    """Collect CTE names defined by WITH ... AS (...) / , ... AS (...).

    Bare names come from the masked scan; double-quoted names (``WITH
    "my cte" AS (...)``) come from the raw text, which shares positions
    with the scan. Over-collection is safe: an allowed name that resolves
    to nothing fails closed at execution with a catalog error. (M-001)
    """
    names = {match.group(1).lower() for match in _CTE_DEFINITION.finditer(scan_sql)}
    names.update(
        match.group(1).replace('""', '"').lower()
        for match in _QUOTED_CTE_DEFINITION.finditer(raw_sql)
    )
    return names


def _parse_quoted_name(raw_sql: str, pos: int) -> tuple[str, int] | None:
    """Parse a double-quoted or backtick-quoted name at ``pos``.

    Returns ``(name, end_pos)`` where ``end_pos`` is just past the closing
    quote, or ``None`` if the quotes are unbalanced.
    """
    quote = raw_sql[pos]
    i = pos + 1
    chars: list[str] = []
    while i < len(raw_sql):
        ch = raw_sql[i]
        if ch == quote:
            if quote == '"' and i + 1 < len(raw_sql) and raw_sql[i + 1] == '"':
                chars.append('"')
                i += 2
                continue
            return "".join(chars), i + 1
        chars.append(ch)
        i += 1
    return None


def _check_table_references(scan_sql: str, raw_sql: str) -> None:
    """Enforce deny-by-default table references after FROM/JOIN.

    Each table factor must be a parenthesised subquery, the registered
    input relation (``{{input}}``/``__input__``/``__left__``/``__right__``),
    a brace placeholder substituted by the executor, a CTE defined in the
    query itself, or a provably safe table function. Bare path literals
    (replacement scans), quoted paths, unknown tables and unlisted reader
    functions are rejected. (M-001)

    ``scan_sql`` (masked) and ``raw_sql`` share positions: masking is
    length-preserving, so quote characters are read from ``raw_sql`` at the
    same offset (masked spans appear as spaces in the scan).
    """
    cte_names = _collect_cte_names(scan_sql, raw_sql)
    for match in _FROM_OR_JOIN.finditer(scan_sql):
        pos = match.end()
        factor_checked = False
        while True:
            # Skip real whitespace AND masked comments, but never skip a
            # masked string/quoted literal: those are masked to spaces in
            # the scan, so a naive isspace() skip would jump over a
            # replacement scan (FROM '/etc/passwd') without noticing.
            # Stop when the raw character opens a literal. (M-001)
            while pos < len(scan_sql) and scan_sql[pos].isspace():
                if raw_sql[pos] in ("'", '"', "`"):
                    break
                pos += 1
            if pos >= len(scan_sql):
                break
            raw_ch = raw_sql[pos]
            if raw_ch in ("'", '"', "`"):
                if raw_ch == "'":
                    # A string literal is never a valid table factor.
                    raise ValueError(
                        "SQL query must read from {{input}}; "
                        "file-path table references are not allowed")
                parsed = _parse_quoted_name(raw_sql, pos)
                if parsed is None:
                    raise ValueError(
                        "SQL query contains an unterminated quoted identifier")
                name, end = parsed
                if name.lower() not in _ALLOWED_TABLE_REFS | cte_names:
                    raise ValueError(
                        f"SQL query table reference '{name}' is not allowed; "
                        "read from {{input}}")
                pos = end
                factor_checked = True
                continue
            ch = scan_sql[pos]
            if ch == "(":
                # Parenthesised subquery / VALUES / function args: the inner
                # FROM/JOIN occurrences are checked independently.
                break
            if ch == ",":
                # Previous factor done; a new factor follows.
                pos += 1
                factor_checked = False
                continue
            if ch == "{":
                # {{input}} or {extra} placeholder substituted by executor.
                if scan_sql.startswith("{{", pos):
                    end = scan_sql.find("}}", pos)
                    if end == -1:
                        raise ValueError(
                            "SQL query contains an unterminated placeholder")
                    pos = end + 2
                else:
                    end = scan_sql.find("}", pos)
                    if end == -1:
                        raise ValueError(
                            "SQL query contains an unterminated placeholder")
                    pos = end + 1
                factor_checked = True
                continue
            word_match = _WORD_TOKEN.match(scan_sql, pos)
            if word_match is None:
                # Punctuation (e.g. '*', operator) — nothing table-like.
                break
            word = word_match.group(0)
            lowered = word.lower()
            if not factor_checked:
                if lowered in _ALLOWED_TABLE_REFS or lowered in cte_names:
                    pass
                elif lowered == "lateral":
                    pos = word_match.end()
                    continue
                elif lowered in _SAFE_TABLE_FUNCTIONS:
                    after = scan_sql[word_match.end():].lstrip()
                    if not after.startswith("("):
                        raise ValueError(
                            f"SQL query table reference '{word}' is not allowed; "
                            "read from {{input}}")
                else:
                    raise ValueError(
                        f"SQL query table reference '{word}' is not allowed; "
                        "read from {{input}}")
                factor_checked = True
                pos = word_match.end()
                continue
            # We already have a table factor: expect an alias, comma, or a
            # clause terminator — anything else ends this scan.
            if lowered in _CLAUSE_TERMINATORS or lowered in {
                    "as", "on", "using", "left", "right", "inner", "outer",
                    "full", "cross", "join", "natural", "lateral"}:
                break
            # A second bare word is an alias (possibly followed by more
            # factors after a comma); keep scanning for commas/terminators.
            pos = word_match.end()
            # Skip a single alias then require comma or terminator next
            # (same masked-literal care as above).
            while pos < len(scan_sql) and scan_sql[pos].isspace():
                if raw_sql[pos] in ("'", '"', "`"):
                    break
                pos += 1
            if pos < len(scan_sql) and scan_sql[pos] == ",":
                pos += 1
                factor_checked = False
                continue
            break


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
    return '"' + identifier.replace('"', '""') + '"'


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

    Deny-by-default (M-001): the query must read only from the registered
    input relation. Enforcement is layered:

    1. Single statement starting with SELECT/WITH.
    2. No write/admin keywords (scanned outside literals/comments, with
       quoted function names exposed so ``"read_text"(...)`` cannot hide).
    3. No external file-reader functions (``read_*``, ``*_scan``,
       ``*_query``, ``glob``, ``query_table``, ``pragma_*``, ...).
    4. No ``WITH RECURSIVE`` (unbounded CTEs wedge workers; a statement
       timeout is additionally enforced on every DuckDB connection).
    5. Every FROM/JOIN table factor must be ``{{input}}``, a CTE defined in
       the query, an executor brace placeholder, or a safe table function —
       bare path literals (replacement scans) and foreign tables rejected.
    6. ``{{input}}`` must appear outside strings/comments.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("SQL query must be a non-empty string")

    normalized = query.strip()
    if normalized.endswith(";"):
        normalized = normalized[:-1].strip()
    if ";" in normalized:
        raise ValueError("Only a single SQL statement is allowed")
    if _FORBIDDEN_EXTERNAL_URI.search(normalized):
        raise ValueError("SQL query cannot reference external URLs or object storage paths")

    sql_for_scan = _LEADING_COMMENT.sub("", normalized)
    if not sql_for_scan:
        raise ValueError("SQL query cannot be empty")

    leading_keyword = sql_for_scan.split(None, 1)[0].lower()
    if leading_keyword not in {"select", "with"}:
        raise ValueError("Only SELECT/CTE queries are allowed")

    keyword_scan_sql = _strip_literals_and_comments(sql_for_scan)
    if _FORBIDDEN_SQL_KEYWORDS.search(keyword_scan_sql):
        raise ValueError("SQL query contains disallowed write/admin keywords")
    if _FORBIDDEN_WITH_RECURSIVE.search(keyword_scan_sql):
        raise ValueError(
            "SQL query must not use WITH RECURSIVE (unbounded recursion)")
    if _FORBIDDEN_SQL_FUNCTIONS.search(keyword_scan_sql):
        raise ValueError("SQL query contains disallowed external file access functions")
    _check_table_references(keyword_scan_sql, sql_for_scan)

    if require_input_placeholder and "{{input}}" not in keyword_scan_sql:
        raise ValueError("SQL query must reference the input via {{input}}")

    return normalized.replace("{{input}}", "__input__")


def _escape_like_literal(value: str) -> str:
    """Escape LIKE wildcards so ``contains`` matches literally (M-014/M-161).

    ``%``, ``_`` and the escape character itself are backslash-escaped;
    callers must append ``ESCAPE '\\'`` to the predicate.
    """
    return (
        str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


def _like_predicate(column: str, pattern: str, *, negated: bool) -> str:
    """Build a LIKE predicate with an explicit ESCAPE clause."""
    keyword = "NOT LIKE" if negated else "LIKE"
    return (
        f"CAST({column} AS VARCHAR) {keyword} "
        f"{sql_literal(pattern)} ESCAPE '\\'"
    )


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
        predicate = _like_predicate(column, f"%{_escape_like_literal(value)}%", negated=False)
    elif operator == "not_contains":
        predicate = _like_predicate(column, f"%{_escape_like_literal(value)}%", negated=True)
    elif operator == "starts_with":
        predicate = _like_predicate(column, f"{_escape_like_literal(value)}%", negated=False)
    elif operator == "ends_with":
        predicate = _like_predicate(column, f"%{_escape_like_literal(value)}", negated=False)
    elif operator == "is_null":
        predicate = f"{column} IS NULL"
    elif operator == "is_not_null":
        predicate = f"{column} IS NOT NULL"
    else:
        raise ValueError(
            f"Unsupported filter operator '{operator}' for DuckDB routing")

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
        # M-112(d): strict-bool — bool("false") is True, so string
        # coercion silently flips DESC to ASC. Reject non-bool fail-closed.
        for v in ascending_values:
            if type(v) is not bool:
                raise ValueError(
                    "Sort 'ascending' must be boolean "
                    f"(got {v!r}); use true/false, not strings")
        directions = ["ASC" if v else "DESC" for v in ascending_values]
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
    group_by = [
        str(col) for col in _as_list(
            _step_value(
                step,
                "group_by",
                []))]
    aggregations = _step_value(step, "aggregations", [])
    if isinstance(aggregations, dict):
        aggregations = [{"column": col, "function": func}
                        for col, func in aggregations.items()]
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


def build_join_sql(
    step: Any,
    left_columns: list[str] | None = None,
    right_columns: list[str] | None = None,
) -> str:
    """Build join SQL with pandas-compatible output columns.

    pandas ``merge(..., suffixes=("_left", "_right"))`` keeps a single join
    key, then left columns, then right non-key columns, renaming overlaps to
    ``<col>_left``/``<col>_right``. ``SELECT * ... USING`` instead yields
    duplicate/renamed columns (``val``, ``val_1``), silently changing results
    above the DuckDB routing threshold (M-013). When both schemas are known
    an explicit aliased projection reproducing pandas names, order and key
    semantics (null-safe ``ON`` + per-``how`` key coalescing) is emitted;
    otherwise the legacy ``USING`` form is returned for backward compatibility.
    """
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
    if left_columns is None or right_columns is None:
        return (
            f"SELECT * FROM __left__ {how.upper()} JOIN __right__ "
            f"USING ({quote_identifier(join_key)})"
        )

    join_map = {
        "inner": "INNER",
        "left": "LEFT",
        "right": "RIGHT",
        "outer": "FULL OUTER",
    }
    overlap = (set(left_columns) & set(right_columns)) - {join_key}
    projections: list[str] = []
    if how in ("inner", "left"):
        key_expr = f'__left__.{quote_identifier(join_key)}'
    elif how == "right":
        key_expr = f'__right__.{quote_identifier(join_key)}'
    else:
        key_expr = (
            f'COALESCE(__left__.{quote_identifier(join_key)}, '
            f'__right__.{quote_identifier(join_key)})'
        )
    projections.append(f"{key_expr} AS {quote_identifier(join_key)}")
    for col in left_columns:
        if col == join_key:
            continue
        if col in overlap:
            projections.append(
                f"__left__.{quote_identifier(col)} AS {quote_identifier(col + '_left')}")
        else:
            projections.append(
                f"__left__.{quote_identifier(col)} AS {quote_identifier(col)}")
    for col in right_columns:
        if col == join_key:
            continue
        if col in overlap:
            projections.append(
                f"__right__.{quote_identifier(col)} AS {quote_identifier(col + '_right')}")
        else:
            projections.append(
                f"__right__.{quote_identifier(col)} AS {quote_identifier(col)}")
    # NULL-safe equality: pandas matches NaN/None keys with each other,
    # while SQL `=` never matches NULL. IS NOT DISTINCT FROM reproduces
    # pandas key-matching exactly for all dtypes.
    on_clause = (
        f"__left__.{quote_identifier(join_key)} IS NOT DISTINCT FROM "
        f"__right__.{quote_identifier(join_key)}"
    )
    return (
        f"SELECT {', '.join(projections)} FROM __left__ "
        f"{join_map[how]} JOIN __right__ ON ({on_clause})"
    )


def build_deduplicate_sql(
    step: Any,
    all_columns: list[str] | None = None,
) -> str:
    """Build deterministic deduplicate SQL.

    M-114(f): the old ``ROW_NUMBER() OVER ()`` base had no ORDER BY, so
    which duplicate was kept was nondeterministic. The base window now
    orders explicitly: by all input columns when the caller provides the
    schema (DuckDB path), else by the subset columns (still deterministic,
    though ties on identical subset values fall back to storage order —
    identical subset rows keep identical subset values either way). The
    outer ranked window then orders by the materialized ``__rowid__``,
    giving a repeatable keep. Set semantics: identical full rows are
    interchangeable, so ordering by all columns is total for the outcome.
    """
    subset = [str(col) for col in _as_list(_step_value(step, "subset", []))]
    keep = str(_step_value(step, "keep", "first")).lower()

    if not subset:
        if keep in {"none", "false"}:
            raise ValueError(
                "DuckDB deduplicate with keep='none' requires subset columns")
        # Deterministic output order for the set operation.
        return "SELECT DISTINCT * FROM __input__ ORDER BY ALL"

    partition = ", ".join(quote_identifier(col) for col in subset)
    # Deterministic base order: all columns when known, else subset.
    order_cols = all_columns if all_columns else subset
    base_order = ", ".join(quote_identifier(str(c)) for c in order_cols)
    base_window = f"ROW_NUMBER() OVER (ORDER BY {base_order}) AS __rowid__"
    if keep in {"first", "last"}:
        order = "ASC" if keep == "first" else "DESC"
        return (
            "WITH __base AS ("
            f"SELECT *, {base_window} FROM __input__"
            "), __ranked AS ("
            f"SELECT *, ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY __rowid__ {order}) AS __rn "
            "FROM __base"
            ") "
            "SELECT * EXCLUDE (__rowid__, __rn) FROM __ranked WHERE __rn = 1"
        )
    if keep in {"none", "false"}:
        return (
            "WITH __base AS ("
            f"SELECT *, {base_window} FROM __input__"
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
    # M-112(e): negative seeds fail closed at parse and here (DuckDB SQL
    # rejects negative seeds; pandas must not silently differ).
    if random_state is not None and (
            type(random_state) is bool
            or not isinstance(random_state, int)
            or random_state < 0):
        raise ValueError(
            "Sample step 'random_state' must be a non-negative integer")

    if n is not None:
        rows = max(0, int(n))
        sample_spec = "reservoir"
        if random_state is not None:
            # DuckDB 1.5.x accepts the seed inside the sampling method clause
            # for the compact "USING SAMPLE ... ROWS (...)" syntax.
            sample_spec = f"reservoir, {int(random_state)}"
        return f"SELECT * FROM __input__ USING SAMPLE {rows} ROWS ({sample_spec})"

    frac = float(fraction)
    if frac < 0:
        frac = 0.0
    if frac > 1:
        frac = 1.0
    percent = frac * 100.0
    sample_spec = "bernoulli"
    if random_state is not None:
        sample_spec = f"bernoulli, {int(random_state)}"
    return f"SELECT * FROM __input__ USING SAMPLE {percent:.6f}% ({sample_spec})"


def build_fill_nulls_sql(step: Any) -> str:
    strategy = str(
        _step_value(step, "strategy", _step_value(step, "method", "constant"))
    ).lower()
    columns = [str(col) for col in _as_list(_step_value(step, "columns", []))]
    if not columns:
        raise ValueError(
            "fill_nulls DuckDB routing requires explicit 'columns'")

    strategy_map = {
        "ffill": "forward_fill",
        "bfill": "backward_fill",
    }
    strategy = strategy_map.get(strategy, strategy)

    if strategy == "constant":
        constant_value = _step_value(
            step, "constant_value", _step_value(
                step, "value"))
        expressions = [
            f"COALESCE({quote_identifier(col)}, {sql_literal(constant_value)}) AS {quote_identifier(col)}" for col in columns]
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
        raise ValueError(
            "Pivot aggfunc must be a simple SQL function identifier")

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
    value_vars = [
        str(col) for col in _as_list(
            _step_value(
                step,
                "value_vars",
                []))]
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
        raise ValueError(
            f"No SQL builder registered for step type '{step_type}'")
    return builder(step)
