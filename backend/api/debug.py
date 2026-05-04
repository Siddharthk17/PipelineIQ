"""Debug endpoints for development and testing.

Only included when ENVIRONMENT != 'production'.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/debug", tags=["Debug"])


@router.get("/sentry-test")
async def trigger_sentry_error():
    """Test endpoint to verify Sentry is capturing errors."""
    raise ValueError(
        "Sentry test error from PipelineIQ — if you see this in Sentry, it works!"
    )
