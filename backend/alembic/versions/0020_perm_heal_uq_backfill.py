"""Backfill dropped unique constraints: permissions + healing (M-080).

``pipeline_permissions`` must be unique per (pipeline_name, user_id) and
``healing_attempts`` per (pipeline_run_id, attempt_number). Databases
created or migrated while the ORM models lacked these constraints can
hold duplicates, so this migration:

* skips tables/constraints that already enforce the same column set
  (e.g. healing_attempts on fully-migrated databases, or the
  auto-named ``pipeline_permissions_pipeline_name_user_id_key``),
* aborts fail-closed with duplicate counts when duplicates exist, so
  the operator dedupes instead of silently picking winners,
* is a no-op off PostgreSQL: SQLite/test databases are built via
  ``Base.metadata.create_all``, which already carries the constraints
  from the models.

Revision ID: 0020_perm_heal_uq_backfill
Revises: 0019_wasm_user_scoped_names
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_perm_heal_uq_backfill"
down_revision: Union[str, None] = "0019_wasm_user_scoped_names"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINTS: tuple[tuple[str, str, list[str]], ...] = (
    (
        "pipeline_permissions",
        "uq_pipeline_permissions_name_user",
        ["pipeline_name", "user_id"],
    ),
    (
        "healing_attempts",
        "uq_healing_attempts_run_attempt",
        ["pipeline_run_id", "attempt_number"],
    ),
)


def _unique_column_sets(bind, table: str) -> set[tuple[str, ...]]:
    """Column sets already under a UNIQUE constraint or unique index."""
    inspector = sa.inspect(bind)
    sets: set[tuple[str, ...]] = set()
    for const in inspector.get_unique_constraints(table):
        sets.add(tuple(const["column_names"]))
    for index in inspector.get_indexes(table):
        if index.get("unique"):
            sets.add(tuple(index["column_names"]))
    return sets


def _duplicate_groups(bind, table: str, columns: list[str]) -> int:
    """Count (column-set) groups occurring more than once."""
    cols = ", ".join(f'"{col}"' for col in columns)
    return int(
        bind.execute(
            sa.text(
                f'SELECT COUNT(*) FROM (SELECT {cols} FROM "{table}" '
                f"GROUP BY {cols} HAVING COUNT(*) > 1) AS dup"
            )
        ).scalar()
        or 0
    )


def upgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        # SQLite and other backends are created via Base.metadata.create_all,
        # which already carries both constraints from the models.
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, name, columns in _CONSTRAINTS:
        if table not in inspector.get_table_names():
            continue
        if tuple(columns) in _unique_column_sets(bind, table):
            continue
        dupes = _duplicate_groups(bind, table, columns)
        if dupes:
            raise RuntimeError(
                f"Cannot create unique constraint {name} on {table}: "
                f"{dupes} duplicate ({', '.join(columns)}) group(s) exist. "
                "Dedupe the rows manually, then re-run the upgrade."
            )
        op.create_unique_constraint(name, table, columns)


def downgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, name, _columns in _CONSTRAINTS:
        if table not in inspector.get_table_names():
            continue
        existing = {
            const["name"] for const in inspector.get_unique_constraints(table)
        }
        if name in existing:
            op.drop_constraint(name, table, type_="unique")
