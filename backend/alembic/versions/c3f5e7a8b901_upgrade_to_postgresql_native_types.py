"""upgrade to postgresql native types

Revision ID: c3f5e7a8b901
Revises: 14a9b359a361
Create Date: 2025-01-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision: str = 'c3f5e7a8b901'
down_revision: Union[str, None] = '14a9b359a361'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# FK constraints that must be dropped before altering PK types
FK_CONSTRAINTS = [
    ("step_results", "step_results_pipeline_run_id_fkey"),
    ("lineage_graphs", "lineage_graphs_pipeline_run_id_fkey"),
    ("schema_snapshots", "schema_snapshots_file_id_fkey"),
    ("schema_snapshots", "schema_snapshots_run_id_fkey"),
    ("pipeline_versions", "pipeline_versions_run_id_fkey"),
]

# Unique constraints that reference UUID columns
UNIQUE_CONSTRAINTS = [
    ("lineage_graphs", "lineage_graphs_pipeline_run_id_key", ["pipeline_run_id"]),
    ("pipeline_versions", "pipeline_versions_pipeline_name_version_number_key",
     ["pipeline_name", "version_number"]),
]

# All UUID columns in dependency order (PKs first, then FKs)
UUID_COLUMNS = [
    ("pipeline_runs", "id"),
    ("uploaded_files", "id"),
    ("step_results", "id"),
    ("step_results", "pipeline_run_id"),
    ("lineage_graphs", "id"),
    ("lineage_graphs", "pipeline_run_id"),
    ("schema_snapshots", "id"),
    ("schema_snapshots", "file_id"),
    ("schema_snapshots", "run_id"),
    ("pipeline_versions", "id"),
    ("pipeline_versions", "run_id"),
]

# All JSON→JSONB columns
JSONB_COLUMNS = [
    ("step_results", "columns_in"),
    ("step_results", "columns_out"),
    ("step_results", "warnings"),
    ("lineage_graphs", "graph_data"),
    ("lineage_graphs", "react_flow_data"),
    ("uploaded_files", "columns"),
    ("uploaded_files", "dtypes"),
    ("schema_snapshots", "columns"),
    ("schema_snapshots", "dtypes"),
]

# FK references to restore after type conversion
FK_REFERENCES = [
    ("step_results", "step_results_pipeline_run_id_fkey",
     "pipeline_run_id", "pipeline_runs", "id"),
    ("lineage_graphs", "lineage_graphs_pipeline_run_id_fkey",
     "pipeline_run_id", "pipeline_runs", "id"),
    ("schema_snapshots", "schema_snapshots_file_id_fkey",
     "file_id", "uploaded_files", "id"),
    ("schema_snapshots", "schema_snapshots_run_id_fkey",
     "run_id", "pipeline_runs", "id"),
    ("pipeline_versions", "pipeline_versions_run_id_fkey",
     "run_id", "pipeline_runs", "id"),
]


def upgrade() -> None:
    # 1. Drop unique constraints on UUID columns
    for table, name, _ in UNIQUE_CONSTRAINTS:
        op.drop_constraint(name, table, type_="unique")

    # 2. Drop FK constraints
    for table, name in FK_CONSTRAINTS:
        op.drop_constraint(name, table, type_="foreignkey")

    # 3. Convert VARCHAR(36) → UUID
    for table, column in UUID_COLUMNS:
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN {column} TYPE uuid USING {column}::uuid'
        )

    # 4. Convert JSON → JSONB
    for table, column in JSONB_COLUMNS:
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN {column} TYPE jsonb USING {column}::jsonb'
        )

    # 5. Restore FK constraints
    for table, name, col, ref_table, ref_col in FK_REFERENCES:
        op.create_foreign_key(name, table, ref_table, [col], [ref_col])

    # 6. Restore unique constraints
    for table, name, columns in UNIQUE_CONSTRAINTS:
        op.create_unique_constraint(name, table, columns)


def downgrade() -> None:
    for table, name, _ in UNIQUE_CONSTRAINTS:
        op.drop_constraint(name, table, type_="unique")

    for table, name in FK_CONSTRAINTS:
        op.drop_constraint(name, table, type_="foreignkey")

    for table, column in UUID_COLUMNS:
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN {column} TYPE varchar(36) USING {column}::text'
        )

    for table, column in JSONB_COLUMNS:
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN {column} TYPE json USING {column}::json'
        )

    for table, name, col, ref_table, ref_col in FK_REFERENCES:
        op.create_foreign_key(name, table, ref_table, [col], [ref_col])

    for table, name, columns in UNIQUE_CONSTRAINTS:
        op.create_unique_constraint(name, table, columns)
