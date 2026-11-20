"""Shared pipeline authorization helpers (M-064, M-093).

Single source of truth for two things that must never diverge:

1. Canonical pipeline-name resolution (M-064). The permission check, the
   stored run, and the saved version must all use the same identity.
   Historically the permission check ran against the request name while
   versions were saved under the YAML ``pipeline.name``, so a request
   whose ``name`` differed from the YAML name split one pipeline into
   two identities: the grant lived on one name, the versions on another.
   :func:`canonical_pipeline_name` is the one helper both sides use.

2. Ownership checks (M-093). Every surface (pipelines, versions,
   permissions, contracts, lineage, schedules) must answer "may this
   user act on this pipeline" the same way: admins bypass; owning the
   contract is ownership; otherwise an explicit permission grant at a
   sufficient level is required. No grant at all yields 404 (anti-oracle,
   M-150) so strangers cannot probe other tenants' pipelines; an
   insufficient grant yields 403.
"""

import logging
import re
from collections.abc import Collection

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.models import PipelineContract, PipelinePermission, User

logger = logging.getLogger(__name__)

# Same alphabet as the run-creation validator: letters/digits, space,
# underscore, dot, dash; must start alphanumeric; max 255 chars.
# Anchored with fullmatch so trailing garbage ("name!!!") is rejected —
# ``re.match`` would accept a valid prefix and ignore the tail.
_PIPELINE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\- ]{0,254}")


def canonical_pipeline_name(
    request_name: str | None,
    yaml_name: str | None = None,
) -> str:
    """Resolve the single canonical identity for a pipeline (M-064).

    The explicit request name wins when present, otherwise the YAML
    ``pipeline.name`` is used. Surrounding whitespace is stripped so
    ``"demo"`` and ``"  demo "`` cannot become two identities. The result
    is validated; a :class:`ValueError` is raised for empty/illegal
    names and callers map it to 422.
    """
    candidate = request_name if request_name is not None else yaml_name
    name = (candidate or "").strip()
    if not name:
        raise ValueError("Pipeline name must be a non-empty string")
    if len(name) > 255 or _PIPELINE_NAME_RE.fullmatch(name) is None:
        raise ValueError(
            "Pipeline name contains invalid characters: only letters, "
            "digits, spaces, underscores, dots and dashes are allowed "
            "(max 255 characters)"
        )
    return name


def normalize_pipeline_name(name: str | None) -> str:
    """Canonicalize a pipeline name taken from a path parameter (M-064).

    Raises :class:`ValueError` when the name is missing or illegal;
    API routes translate that to 422.
    """
    return canonical_pipeline_name(name)


def _normalize_levels(required_levels: Collection[str]) -> set[str]:
    """Coerce required levels (str or PermissionLevel) to a plain set."""
    return {
        lvl.value if hasattr(lvl, "value") else str(lvl).strip().lower()
        for lvl in required_levels
    }


def check_pipeline_access(
    db: Session,
    user: User,
    pipeline_name: str,
    required_levels: Collection[str],
) -> str:
    """Verify the user may act on the pipeline; return canonical name.

    Unified ownership check (M-093): admin bypass, own-contract
    ownership, or an explicit grant at one of ``required_levels``.
    Raises 404 when no grant exists at all (anti-oracle) and 403 when
    the grant level is insufficient.
    """
    canonical = normalize_pipeline_name(pipeline_name)
    if user.role == "admin":
        return canonical

    own_contract = (
        db.query(PipelineContract.id)
        .filter(
            PipelineContract.pipeline_name == canonical,
            PipelineContract.user_id == user.id,
        )
        .first()
    )
    if own_contract:
        return canonical

    permission = (
        db.query(PipelinePermission)
        .filter(
            PipelinePermission.pipeline_name == canonical,
            PipelinePermission.user_id == user.id,
        )
        .first()
    )
    if permission is None:
        # No grant at all: 404 (not 403) so the existence of another
        # user's pipeline/run cannot be probed via the status code.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline run or pipeline not found, or access denied.",
        )
    # NOTE: PermissionLevel is a str-enum: compare with ==/in, never
    # str(...) (which yields 'PermissionLevel.OWNER', not 'owner').
    if permission.permission_level not in _normalize_levels(required_levels):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User lacks the required permission for this pipeline.",
        )
    return canonical


def require_pipeline_owner(db: Session, user: User, pipeline_name: str) -> str:
    """Verify the user may administer a pipeline's grants (M-093/M-150).

    Owners (explicit OWNER grant or own-contract ownership) and admins
    may manage permissions. Raises 403 otherwise; the response is the
    same whether or not the pipeline exists, so grants cannot be used
    as an existence oracle.
    """
    canonical = normalize_pipeline_name(pipeline_name)
    if user.role == "admin":
        return canonical

    owner_grant = (
        db.query(PipelinePermission.permission_level)
        .filter(
            PipelinePermission.pipeline_name == canonical,
            PipelinePermission.user_id == user.id,
        )
        .scalar()
    )
    # NOTE: PermissionLevel is a str-enum: compare with ==, never
    # str(...) (which yields 'PermissionLevel.OWNER', not 'owner').
    if owner_grant is not None and owner_grant == "owner":
        return canonical
    own_contract = (
        db.query(PipelineContract.id)
        .filter(
            PipelineContract.pipeline_name == canonical,
            PipelineContract.user_id == user.id,
        )
        .first()
    )
    if own_contract:
        return canonical
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only pipeline owners or admins can manage permissions",
    )
