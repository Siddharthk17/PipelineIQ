"""Schema drift detection for uploaded files.

Detects changes in CSV column structure between uploads:
- Added columns (INFO severity)
- Removed columns (BREAKING severity)
- Type changes (WARNING severity)
"""

from dataclasses import dataclass


@dataclass
class ColumnDrift:
    """A single schema drift item."""

    column: str
    drift_type: str  # "added", "removed", "type_changed"
    old_type: str | None
    new_type: str | None
    severity: str  # "breaking", "warning", "info"


@dataclass
class SchemaDriftReport:
    """Complete schema drift report between two snapshots."""

    has_drift: bool
    columns_added: list[str]
    columns_removed: list[str]
    type_changes: list[ColumnDrift]
    summary: str


def detect_schema_drift(
    old_columns: list[str],
    old_dtypes: dict[str, str],
    new_columns: list[str],
    new_dtypes: dict[str, str],
) -> SchemaDriftReport:
    """Compare two sets of columns/dtypes for schema drift."""
    old_set = set(old_columns)
    new_set = set(new_columns)

    columns_added = sorted(new_set - old_set)
    columns_removed = sorted(old_set - new_set)
    type_changes: list[ColumnDrift] = []

    for col in sorted(old_set & new_set):
        old_type = old_dtypes.get(col, "")
        new_type = new_dtypes.get(col, "")
        if old_type != new_type:
            type_changes.append(ColumnDrift(
                column=col,
                drift_type="type_changed",
                old_type=old_type,
                new_type=new_type,
                severity="warning",
            ))

    has_drift = bool(columns_added or columns_removed or type_changes)

    parts = []
    if columns_added:
        parts.append(f"{len(columns_added)} columns added")
    if columns_removed:
        parts.append(f"{len(columns_removed)} columns removed")
    if type_changes:
        parts.append(f"{len(type_changes)} type changes")
    summary = ", ".join(parts) if parts else "No drift detected"

    return SchemaDriftReport(
        has_drift=has_drift,
        columns_added=columns_added,
        columns_removed=columns_removed,
        type_changes=type_changes,
        summary=summary,
    )
