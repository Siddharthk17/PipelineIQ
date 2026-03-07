"""API router aggregation.

Combines all API sub-routers (files, pipelines, lineage) into a
single router that is mounted under the API prefix in main.py.
"""

from fastapi import APIRouter

from backend.api.files import router as files_router
from backend.api.lineage import router as lineage_router
from backend.api.pipelines import router as pipelines_router
from backend.api.versions import router as versions_router

api_router = APIRouter()

api_router.include_router(files_router)
api_router.include_router(pipelines_router)
api_router.include_router(lineage_router)
api_router.include_router(versions_router)
