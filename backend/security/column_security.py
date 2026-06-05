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
from typing import Optional

import orjson
import pandas as pd

logger = logging.getLogger(__name__)

POLICY_CACHE_TTL = 60

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
    mask_pattern: Optional[str]
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
            pattern = pol.mask_pattern or "default"
            result[col] = result[col].apply(
                lambda v: _apply_mask(str(v), pattern) if pd.notna(v) else v
            )
            logger.debug(
                "Column '%s' masked (%s) for role '%s'", col, pattern, user_role
            )

    return result


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
            visible = value[:n]
            return f"{visible}***"
        except (IndexError, ValueError):
            pass

    if len(value) <= 3:
        return "***"
    return f"{value[:2]}***{value[-1]}"


def get_column_policies_for_file(
    file_id: str,
    db,
) -> list[ColumnPolicyRecord]:
    from backend.db.redis_pools import get_cache_redis_binary, get_cache_redis
    from backend.models.column_policy import ColumnPolicy

    cache_key = f"col_policies:{file_id}"

    try:
        redis = get_cache_redis_binary()
        cached = redis.get(cache_key)
        if cached:
            return _deserialize_policy_records(cached)
    except Exception:
        pass

    policies = db.query(ColumnPolicy).filter(ColumnPolicy.file_id == file_id).all()

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
