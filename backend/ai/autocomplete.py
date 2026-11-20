"""
Column name autocomplete using Jaro-Winkler similarity.
When a user types a column name that doesn't exist in the schema,
suggest the closest match if similarity is above the threshold.

Jaro-Winkler was chosen over simple Levenshtein because:
- It weights prefix matches more heavily (typos usually happen mid-word or at end)
- Better for short strings (column names are typically 5-20 chars)
- Produces a 0.0-1.0 score (easier to threshold than edit distance)
"""

import jellyfish

# Minimum similarity to offer a suggestion
# 0.85 avoids false positives — "name" should NOT suggest "amount" (too different)
# But "reveue" SHOULD suggest "revenue" (transposed letters, high similarity)
SIMILARITY_THRESHOLD = 0.85

# M-016: bounds so attacker-sized inputs cannot stall the event loop.
# suggest_column is O(len(available_columns)) jellyfish comparisons and
# jaro-winkler itself is O(len(a)*len(b)); batch multiplies the two.
MAX_TYPED_LENGTH = 256
MAX_COLUMNS = 2000
MAX_BATCH_ITEMS = 200


def _cap_inputs(typed: str, available_columns: list[str]) -> tuple[str, list[str]]:
    """Truncate oversized inputs to the M-016 caps."""
    if len(typed) > MAX_TYPED_LENGTH:
        typed = typed[:MAX_TYPED_LENGTH]
    if len(available_columns) > MAX_COLUMNS:
        available_columns = available_columns[:MAX_COLUMNS]
    return typed, available_columns


def suggest_column(typed: str, available_columns: list[str]) -> str | None:
    """
    Suggest the closest column name for a likely typo.

    Args:
        typed:             The column name the user typed
        available_columns: All valid column names for this step

    Returns:
        str: The closest column name if similarity >= SIMILARITY_THRESHOLD
        None: If no close match found or typed is already valid

    Examples:
        suggest_column("reveue", ["revenue", "id", "region"]) → "revenue"
        suggest_column("revenue", ["revenue", "id", "region"]) → None  (exact match)
        suggest_column("xyz", ["revenue", "id", "region"])     → None  (no close match)
    """
    if not typed or not available_columns:
        return None

    typed, available_columns = _cap_inputs(typed, available_columns)

    # Exact match: no suggestion needed
    if typed in available_columns:
        return None

    # Case insensitive exact match check
    typed_lower = typed.lower()
    for col in available_columns:
        if col.lower() == typed_lower:
            return col

    # Compute Jaro-Winkler similarity against all candidates
    # Comparison is case-insensitive: "Revenue" and "revenue" should match
    scored = [
        (col, jellyfish.jaro_winkler_similarity(typed_lower, col.lower()))
        for col in available_columns
    ]

    if not scored:
        return None

    # Find the best match
    best_col, best_score = max(scored, key=lambda x: x[1])

    if best_score >= SIMILARITY_THRESHOLD:
        return best_col

    return None


def suggest_columns_batch(
    queries: list[str], available_columns: list[str]
) -> dict[str, str | None]:
    """
    Batch version of suggest_column for checking multiple column references at once.
    Returns a dict mapping each query to its suggestion (or None if no suggestion).

    The query list is capped at MAX_BATCH_ITEMS (M-016) so a single request
    cannot force unbounded O(n*m) comparison work.
    """
    capped_columns = available_columns[:MAX_COLUMNS]
    return {typed: suggest_column(typed, capped_columns) for typed in queries[:MAX_BATCH_ITEMS]}
