"""Global data catalog API endpoints.

Search, blast radius, upstream lineage, orphan detection.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from backend.database import get_read_db
from backend.auth import get_current_user
from backend.models import User
from backend.repositories.catalog import (
    search_assets,
    get_blast_radius,
    get_upstream_lineage,
    list_orphan_assets,
)
from backend.utils.rate_limiter import limiter

router = APIRouter(prefix="/api/catalog", tags=["Catalog"])
logger = logging.getLogger(__name__)


@router.get("/search")
@limiter.limit("120/minute")
async def search_catalog(
    request,
    q: str = Query(..., min_length=2, description="Search query for asset names"),
    asset_type: Optional[str] = Query(None, description="Filter: file|column|pipeline|topic"),
    limit: int = Query(default=20, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db),
):
    """Full-text search over the global data asset catalog using trigram similarity."""
    results = await search_assets(db, query=q, asset_type=asset_type, limit=limit)
    return {
        "query": q,
        "results": results,
        "count": len(results),
    }


@router.get("/assets/{asset_name}/impact")
@limiter.limit("120/minute")
async def get_impact_analysis(
    request,
    asset_name: str,
    asset_type: Optional[str] = Query(None),
    max_depth: int = Query(default=10, le=15),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db),
):
    """Forward blast radius: what downstream assets break if this asset changes.

    Uses a PostgreSQL recursive CTE -- returns instantly regardless of catalog size.
    """
    results = get_blast_radius(db, asset_name=asset_name, asset_type=asset_type, max_depth=max_depth)

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
@limiter.limit("120/minute")
async def get_asset_lineage(
    request,
    asset_name: str,
    max_depth: int = Query(default=10, le=15),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db),
):
    """Backward lineage: where does this asset come from."""
    results = get_upstream_lineage(db, asset_name=asset_name, max_depth=max_depth)
    return {
        "asset_name": asset_name,
        "upstream": results,
        "total": len(results),
    }


@router.get("/orphans")
@limiter.limit("60/minute")
async def list_orphan_data_assets(
    request,
    days_inactive: int = Query(default=90, description="Assets not seen in N days"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db),
):
    """Find assets no pipeline has used in the last N days."""
    orphans = list_orphan_assets(db, days_inactive=days_inactive)
    return {
        "days_inactive": days_inactive,
        "orphans": orphans,
        "count": len(orphans),
    }


@router.get("/stats")
@limiter.limit("120/minute")
async def get_catalog_stats(
    request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db),
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
