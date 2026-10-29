"""Debug endpoints for development and testing.

Only included when ENVIRONMENT != 'production'.
"""

from fastapi import APIRouter, Depends

from backend.auth import get_current_admin
from backend.models import User

router = APIRouter(prefix="/debug", tags=["Debug"])


@router.get("/sentry-test")
async def trigger_sentry_error(
    current_user: User = Depends(get_current_admin),
):
    """Test endpoint to verify Sentry is capturing errors."""
    """Test endpoint to verify Sentry is capturing errors."""
    raise ValueError(
        "Sentry test error from PipelineIQ — if you see this in Sentry, it works!"
    )
