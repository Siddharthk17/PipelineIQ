"""API router aggregation.

Combines all API sub-routers into versioned and legacy compatibility routers.
The v1 router is mounted under /api/v1 and the legacy router under /api.
"""

from fastapi import APIRouter
from backend.api.files import router as files_router, legacy_router as files_legacy_router
from backend.api.lineage import router as lineage_router
from backend.api.pipelines import router as pipelines_router, legacy_runs_router
from backend.api.versions import router as versions_router
from backend.api.schedules import router as schedules_router
from backend.api.templates import router as templates_router
from backend.api.notifications import router as notifications_router
from backend.api.dashboard import router as dashboard_router
from backend.api.permissions import router as permissions_router

api_router = APIRouter()
legacy_api_router = APIRouter()

api_router.include_router(files_router)
api_router.include_router(pipelines_router)
api_router.include_router(lineage_router)
api_router.include_router(versions_router)
api_router.include_router(schedules_router)
api_router.include_router(templates_router)
api_router.include_router(notifications_router)
api_router.include_router(dashboard_router)
api_router.include_router(permissions_router)

# Week-1 compatibility contract from prompt.md
legacy_api_router.include_router(files_legacy_router)
legacy_api_router.include_router(legacy_runs_router)
