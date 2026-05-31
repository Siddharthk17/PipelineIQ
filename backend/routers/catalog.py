"""Global data catalog API endpoints.

Search, blast radius, upstream lineage, orphan detection.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.config import settings
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import User
from backend.repositories.catalog import (
    search_assets,
    get_blast_radius,
    get_upstream_lineage,
    list_orphan_assets as list_orphan_assets_repo,
)
from backend.utils.rate_limiter import limiter

router = APIRouter(prefix="/api/catalog", tags=["Catalog"])
logger = logging.getLogger(__name__)


@router.get("/search")
@limiter.limit(settings.RATE_LIMIT_READ)
def search_catalog(
    request: Request,
    response: Response,
    q: str = Query(..., min_length=2, description="Search query for asset names"),
    asset_type: Optional[str] = Query(None, description="Filter: file|column|pipeline|topic"),
    limit: int = Query(default=20, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = get_read_db_dependency(),
):
    """Full-text search over the global data asset catalog using trigram similarity."""
    results = search_assets(db, query=q, asset_type=asset_type, limit=limit)
    return {
        "query": q,
        "results": results,
        "count": len(results),
    }


@router.get("/assets/{asset_name}/impact")
@limiter.limit(settings.RATE_LIMIT_READ)
def get_impact_analysis(
    request: Request,
    response: Response,
    asset_name: str,
    asset_type: Optional[str] = Query(None),
    max_depth: int = Query(default=10, le=15),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db_dependency),
):
    """Forward blast radius: what downstream assets break if this asset changes.

    Uses a PostgreSQL recursive CTE with Redis result caching and
    statement timeout protection against disk-filling queries.
    """
    results = get_blast_radius(db, asset_name=asset_name, asset_type=asset_type, max_depth=max_depth)

    response.headers["Cache-Control"] = "private, max-age=300, stale-while-revalidate=60"

    if not results:
        return {
            "asset_name": asset_name,
            "downstream": [],
            "depth_reached": 0,
            "message": (
                f"No downstream dependencies found for '{asset_name}'. "
                f"Either it has no dependents, or no pipeline runs have "
                f"referenced it yet."
            ),
        }

    max_depth_found = max(r["depth"] for r in results)
    pipeline_count = len({r["pipeline_name"] for r in results if r["pipeline_name"]})

    return {
        "asset_name": asset_name,
        "downstream": results,
        "total_assets": len(results),
        "depth_reached": max_depth_found,
        "pipelines_affected": pipeline_count,
    }


@router.get("/assets/{asset_name}/lineage")
@limiter.limit(settings.RATE_LIMIT_READ)
def get_asset_lineage(
    request: Request,
    response: Response,
    asset_name: str,
    max_depth: int = Query(default=10, le=15),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db_dependency),
):
    """Backward lineage: where does this asset come from.

    Uses statement timeout protection against long-running CTE queries.
    """
    results = get_upstream_lineage(db, asset_name=asset_name, max_depth=max_depth)
    return {
        "asset_name": asset_name,
        "upstream": results,
        "total": len(results),
    }


@router.get("/orphans")
@limiter.limit("60/minute")
def list_orphan_data_assets(
    request: Request,
    response: Response,
    days_inactive: int = Query(default=90, description="Assets not seen in N days"),
    current_user: User = Depends(get_current_user),
    db: Session = get_read_db_dependency(),
):
    """Find assets no pipeline has used in the last N days."""
    orphans = list_orphan_assets_repo(db, days_inactive=days_inactive)
    return {
        "days_inactive": days_inactive,
        "orphans": orphans,
        "count": len(orphans),
    }


@router.get("/stats")
@limiter.limit(settings.RATE_LIMIT_READ)
def get_catalog_stats(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = get_read_db_dependency(),
):
    """Overall catalog statistics."""
    asset_counts = db.execute(
        text("SELECT asset_type, COUNT(*) AS cnt FROM data_assets GROUP BY asset_type ORDER BY cnt DESC")
    )
    rel_count = db.execute(text("SELECT COUNT(*) FROM asset_relationships"))

    return {
        "assets_by_type": {row.asset_type: row.cnt for row in asset_counts.fetchall()},
        "total_relationships": rel_count.scalar_one(),
    }
