"""The `sql` step type executes arbitrary DuckDB SQL against the upstream DataFrame.

The input DataFrame is always available as ``{{input}}`` in the query.
Full DuckDB SQL feature set is accessible: window functions, CTEs, PIVOT,
UNPIVOT, LATERAL joins, and any loaded DuckDB extension.

Examples::

    -- Simple aggregation with window function
    SELECT customer_id,
           SUM(amount) AS total,
           RANK() OVER (ORDER BY SUM(amount) DESC) AS rank
    FROM {{input}}
    GROUP BY customer_id

    -- CTE with filter
    WITH ranked AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY region ORDER BY amount DESC) AS rn
        FROM {{input}}
    )
    SELECT * FROM ranked WHERE rn <= 5

Safety:
    Only SELECT and WITH (CTE) queries are allowed. All DML and DDL
    statements (INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE,
    GRANT, REVOKE) are blocked at validation time.
"""

from __future__ import annotations

import re
from typing import ClassVar

_FORBIDDEN_KEYWORDS: ClassVar[set[str]] = {
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE",
    "ALTER", "TRUNCATE", "GRANT", "REVOKE",
}

_ALLOWED_LEADING: ClassVar[set[str]] = {"SELECT", "WITH"}

_INPUT_PLACEHOLDER = re.compile(r"\{\{[ \t]*input[ \t]*\}\}")


class SqlStep:
    """Validation logic for the ``sql`` pipeline step type.

    This class encapsulates the validation rules that are applied when
    a ``sql`` step is defined in a pipeline configuration. The validation
    is invoked at parse/configuration time (before execution) and again
    at execution time by the SQL builder.

    The ``query`` field must:
    1. Be non-empty
    2. Reference ``{{input}}`` to access the upstream DataFrame
    3. Start with SELECT or WITH (CTE)
    4. Not contain any DML/DDL keywords
    5. Be a single SQL statement (no semicolons)
    """

    type: str = "sql"
    query: str = ""

    @classmethod
    def validate_query(cls, query: str) -> str:
        """Validate and normalize a SQL step query.

        Returns the normalized query string on success.
        Raises ValueError with a specific message on any validation failure.
        """
        if not query or not query.strip():
            raise ValueError("sql step query must not be empty")

        normalized = query.strip()

        if _INPUT_PLACEHOLDER.search(normalized) is None:
            raise ValueError(
                "sql step query must reference {{input}} to access upstream data. "
                "Example: SELECT * FROM {{input}} WHERE amount > 100"
            )

        if normalized.endswith(";"):
            normalized = normalized[:-1].strip()

        if ";" in normalized:
            raise ValueError(
                "sql step only allows a single SQL statement. "
                "Multiple statements separated by semicolons are not permitted."
            )

        leading_keyword = normalized.split(None, 1)[0].upper()
        if leading_keyword not in _ALLOWED_LEADING:
            raise ValueError(
                f"sql step only allows SELECT and WITH (CTE) queries. "
                f"Found leading keyword: {leading_keyword}"
            )

        upper_words = normalized.upper().split()
        for word in upper_words:
            for kw in _FORBIDDEN_KEYWORDS:
                if word == kw or word.startswith(kw + " "):
                    raise ValueError(
                        f"sql step does not allow DML/DDL statements. "
                        f"Found forbidden keyword: {kw}"
                    )

        return normalized
