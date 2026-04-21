"""Schema drift detection helpers for autonomous healing."""

from __future__ import annotations

import jellyfish


RENAME_SIMILARITY_THRESHOLD = 0.75


def find_removed_columns(old_schema: dict, new_schema: dict) -> list[str]:
    """Return columns that existed in the old schema but not the new schema."""
    return sorted(set(old_schema.keys()) - set(new_schema.keys()))


def find_added_columns(old_schema: dict, new_schema: dict) -> list[str]:
    """Return columns that exist in the new schema but not the old schema."""
    return sorted(set(new_schema.keys()) - set(old_schema.keys()))


def find_rename_candidates(old_schema: dict, new_schema: dict) -> list[dict]:
    """Return likely rename pairs ranked by string similarity and type match."""
    removed_columns = find_removed_columns(old_schema, new_schema)
    added_columns = find_added_columns(old_schema, new_schema)
    if not removed_columns or not added_columns:
        return []

    candidates: list[dict] = []
    for old_name in removed_columns:
        for new_name in added_columns:
            similarity = jellyfish.jaro_winkler_similarity(
                str(old_name).lower(),
                str(new_name).lower(),
            )
            if similarity < RENAME_SIMILARITY_THRESHOLD:
                continue

            old_type = (old_schema.get(old_name) or {}).get("semantic_type")
            new_type = (new_schema.get(new_name) or {}).get("semantic_type")
            type_match = bool(old_type and new_type and old_type == new_type)
            confidence = similarity * (1.2 if type_match else 1.0)

            candidates.append(
                {
                    "old_name": old_name,
                    "new_name": new_name,
                    "similarity": round(similarity, 4),
                    "type_match": type_match,
                    "confidence": round(min(confidence, 1.0), 4),
                }
            )

    return sorted(candidates, key=lambda item: item["confidence"], reverse=True)


def compute_schema_diff(old_schema: dict, new_schema: dict) -> dict:
    """Return the full schema diff payload used by the healing agent."""
    removed_columns = find_removed_columns(old_schema, new_schema)
    added_columns = find_added_columns(old_schema, new_schema)
    renamed_candidates = find_rename_candidates(old_schema, new_schema)

    return {
        "removed_columns": removed_columns,
        "added_columns": added_columns,
        "renamed_candidates": renamed_candidates,
        "has_changes": bool(removed_columns or added_columns or renamed_candidates),
        "summary": _build_summary(
            removed_columns=removed_columns,
            added_columns=added_columns,
            renamed_candidates=renamed_candidates,
        ),
    }


def _build_summary(
    *,
    removed_columns: list[str],
    added_columns: list[str],
    renamed_candidates: list[dict],
) -> str:
    """Build a short readable summary of schema drift for logs and UI."""
    parts: list[str] = []

    for rename in renamed_candidates[:3]:
        parts.append(f"{rename['old_name']} -> {rename['new_name']}")

    renamed_old = {rename["old_name"] for rename in renamed_candidates}
    renamed_new = {rename["new_name"] for rename in renamed_candidates}

    for column in removed_columns[:2]:
        if column not in renamed_old:
            parts.append(f"{column} removed")

    for column in added_columns[:2]:
        if column not in renamed_new:
            parts.append(f"{column} added")

    return "; ".join(parts) if parts else "No schema changes detected"
