"""Column-level access policy enforcement.

Policies are evaluated at two points:
1. File preview — user sees redacted/masked data
2. Pipeline load step — columns are filtered before execution begins

'redacted' columns are dropped from the DataFrame.
'masked' columns have values partially obscured using deterministic patterns.
Users with a role in 'allowed_roles' see the full unmodified value.
Policies are cached in Redis for 60 seconds to avoid repeated DB lookups.
"""

import logging
import re
from dataclasses import dataclass
from typing import cast

import orjson
import pandas as pd

from backend.utils.uuid_utils import as_uuid

logger = logging.getLogger(__name__)

POLICY_CACHE_TTL = 60

# Server-side bounds for `custom:N` mask patterns (M-072). A negative N
# (`value[:-N]`) or an unbounded N exposes nearly the entire value, so N
# is clamped to [0, MAX_CUSTOM_VISIBLE_CHARS] at enforcement time and
# rejected at creation time (see column_policies router).
MAX_CUSTOM_VISIBLE_CHARS = 4

ALLOWED_MASK_PATTERNS = frozenset(
    {"default", "email", "phone", "credit_card", "ssn", "name"}
)

PII_SEMANTIC_TYPES = frozenset(
    {
        "email",
        "phone",
        "ssn",
        "credit_card",
        "ip_address",
        "person_name",
        "address",
    }
)


@dataclass
class ColumnPolicyRecord:
    column_name: str
    policy: str
    mask_pattern: str | None
    allowed_roles: list[str]


def _serialize_policy_records(records: list[ColumnPolicyRecord]) -> bytes:
    return orjson.dumps([record.__dict__ for record in records])


def _deserialize_policy_records(payload: bytes) -> list[ColumnPolicyRecord]:
    raw_records = orjson.loads(payload)
    if not isinstance(raw_records, list):
        return []
    return [
        ColumnPolicyRecord(
            column_name=str(record.get("column_name", "")),
            policy=str(record.get("policy", "")),
            mask_pattern=record.get("mask_pattern"),
            allowed_roles=list(record.get("allowed_roles") or []),
        )
        for record in raw_records
        if isinstance(record, dict)
    ]


def validate_mask_pattern(mask_pattern: str | None) -> str | None:
    """Validate a mask pattern, raising ValueError when it is not allowed.

    Returns the normalized pattern (``custom:N`` with N clamped into
    range). ``None`` (redacted policies / default masking) passes through.
    """
    if mask_pattern is None:
        return None
    if mask_pattern in ALLOWED_MASK_PATTERNS:
        return mask_pattern
    if mask_pattern.startswith("custom:"):
        try:
            n = int(mask_pattern.split(":", 1)[1])
        except (IndexError, ValueError):
            raise ValueError(
                "mask_pattern 'custom:N' requires an integer N") from None
        if not 0 <= n <= MAX_CUSTOM_VISIBLE_CHARS:
            raise ValueError(
                f"mask_pattern 'custom:N' requires 0 <= N <= "
                f"{MAX_CUSTOM_VISIBLE_CHARS}")
        return f"custom:{n}"
    raise ValueError(
        f"Unknown mask_pattern '{mask_pattern}'. Allowed: "
        f"{sorted(ALLOWED_MASK_PATTERNS)} or 'custom:N' "
        f"(0 <= N <= {MAX_CUSTOM_VISIBLE_CHARS}).")


def normalize_mask_pattern(mask_pattern: str | None) -> str:
    """Clamp a stored mask pattern into the safe range (defense in depth).

    Legacy rows written before validation (e.g. ``custom:-1`` or
    ``custom:999``) are clamped here so enforcement never leaks values,
    even if the creation-time validator was bypassed.
    """
    if not mask_pattern:
        return "default"
    if mask_pattern in ALLOWED_MASK_PATTERNS:
        return mask_pattern
    if mask_pattern.startswith("custom:"):
        try:
            n = int(mask_pattern.split(":", 1)[1])
        except (IndexError, ValueError):
            return "default"
        return f"custom:{max(0, min(n, MAX_CUSTOM_VISIBLE_CHARS))}"
    return "default"


def apply_column_policies(
    df: pd.DataFrame,
    user_role: str,
    policies: list[ColumnPolicyRecord],
) -> pd.DataFrame:
    if not policies:
        return df

    result = df.copy()

    for pol in policies:
        col = pol.column_name

        if col not in result.columns:
            continue

        if user_role in (pol.allowed_roles or []):
            logger.debug(
                "Column '%s' accessible to role '%s' — no policy applied",
                col,
                user_role,
            )
            continue

        if pol.policy == "redacted":
            result = result.drop(columns=[col])
            logger.debug("Column '%s' redacted for role '%s'", col, user_role)

        elif pol.policy == "masked":
            pattern = normalize_mask_pattern(pol.mask_pattern)
            result[col] = result[col].apply(
                lambda v, _pattern=pattern: _apply_mask(str(v), _pattern)
                if pd.notna(v) else v
            )
            logger.debug(
                "Column '%s' masked (%s) for role '%s'", col, pattern, user_role
            )

    return result


def apply_policies_to_records(
    records: list[dict],
    user_role: str,
    policies: list[ColumnPolicyRecord],
) -> list[dict]:
    """Apply column policies to exported row dicts (export/download paths).

    Mirrors :func:`apply_column_policies` for ``DataFrame.to_dict``-style
    records so CSV/JSON exports cannot leak unmasked values (M-072).
    Redacted columns are dropped; masked columns are masked in place.
    """
    if not policies or not records:
        return records

    redacted = {
        pol.column_name for pol in policies
        if pol.policy == "redacted"
        and user_role not in (pol.allowed_roles or [])
    }
    masked = {
        pol.column_name: normalize_mask_pattern(pol.mask_pattern)
        for pol in policies
        if pol.policy == "masked"
        and user_role not in (pol.allowed_roles or [])
    }
    if not redacted and not masked:
        return records

    scrubbed: list[dict] = []
    for row in records:
        new_row = {
            k: v for k, v in row.items() if k not in redacted
        }
        for col, pattern in masked.items():
            if col in new_row and new_row[col] is not None:
                new_row[col] = _apply_mask(str(new_row[col]), pattern)
        scrubbed.append(new_row)
    return scrubbed


def apply_policies_to_profile(
    profile: dict,
    user_role: str,
    policies: list[ColumnPolicyRecord],
) -> dict:
    """Apply column policies to a data profile payload (profile paths).

    Redacted columns are replaced with a ``{"redacted": True}`` marker;
    masked columns keep aggregate stats but have raw ``top_values``
    entries masked, so profile responses never leak unmasked samples
    (M-072).
    """
    if not policies or not profile:
        return profile

    scrubbed = dict(profile)
    for pol in policies:
        col = pol.column_name
        if col not in scrubbed:
            continue
        if user_role in (pol.allowed_roles or []):
            continue
        if pol.policy == "redacted":
            scrubbed[col] = {"name": col, "redacted": True}
        elif pol.policy == "masked":
            pattern = normalize_mask_pattern(pol.mask_pattern)
            entry = dict(scrubbed[col]) if isinstance(
                scrubbed[col], dict) else {}
            top_values = entry.get("top_values")
            if isinstance(top_values, list):
                masked_top = []
                for item in top_values:
                    if isinstance(item, dict) and "value" in item:
                        masked_item = dict(item)
                        masked_item["value"] = _apply_mask(
                            str(item["value"]), pattern)
                        masked_top.append(masked_item)
                    else:
                        masked_top.append(item)
                entry["top_values"] = masked_top
            entry["masked"] = True
            scrubbed[col] = entry
    return scrubbed


def _apply_mask(value: str, pattern: str) -> str:
    if not value:
        return value

    if pattern == "email":
        parts = value.split("@")
        if len(parts) == 2:
            local, domain = parts
            return f"{local[0]}***@{domain}" if local else f"***@{domain}"
        return f"{value[0]}***"

    elif pattern == "phone":
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 4:
            return f"***-***-{digits[-4:]}"
        return "***"

    elif pattern == "credit_card":
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 4:
            return f"****-****-****-{digits[-4:]}"
        return "****"

    elif pattern == "ssn":
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 4:
            return f"***-**-{digits[-4:]}"
        return "***-**-****"

    elif pattern == "name":
        return f"{value[0]}***" if value else "***"

    elif pattern.startswith("custom:"):
        try:
            n = int(pattern.split(":")[1])
        except (IndexError, ValueError):
            pass
        else:
            # Clamp N server-side: negative N (`value[:-N]`) or huge N
            # would expose nearly the entire value (M-072).
            n = max(0, min(n, MAX_CUSTOM_VISIBLE_CHARS))
            return f"{value[:n]}***"
    if len(value) <= 3:
        return "***"
    return f"{value[:2]}***{value[-1]}"


def get_column_policies_for_file(
    file_id: str,
    db,
) -> list[ColumnPolicyRecord]:
    from backend.db.redis_pools import get_cache_redis_binary
    from backend.models.column_policy import ColumnPolicy

    cache_key = f"col_policies:{file_id}"

    try:
        redis = get_cache_redis_binary()
        cached = cast('bytes | None', redis.get(cache_key))
        if cached:
            return _deserialize_policy_records(cached)
    except Exception:
        pass

    policies = db.query(ColumnPolicy).filter(
        ColumnPolicy.file_id == as_uuid(file_id)).all()

    records = [
        ColumnPolicyRecord(
            column_name=p.column_name,
            policy=p.policy,
            mask_pattern=p.mask_pattern,
            allowed_roles=list(p.allowed_roles or []),
        )
        for p in policies
    ]

    try:
        redis = get_cache_redis_binary()
        redis.setex(cache_key, POLICY_CACHE_TTL, _serialize_policy_records(records))
    except Exception:
        pass

    return records


def invalidate_policy_cache(file_id: str) -> None:
    try:
        from backend.db.redis_pools import get_cache_redis

        redis = get_cache_redis()
        redis.delete(f"col_policies:{file_id}")
    except Exception:
        pass


def detect_pii_columns(profile: dict) -> list[str]:
    pii_columns = []
    for col_name, col_data in profile.items():
        semantic_type = (col_data or {}).get("semantic_type", "")
        if semantic_type in PII_SEMANTIC_TYPES:
            pii_columns.append(col_name)
    return pii_columns
