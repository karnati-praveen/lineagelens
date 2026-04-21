from fastapi import Depends, HTTPException, status

from app.core.config import Settings, get_settings


def require_non_solo(settings: Settings = Depends(get_settings)) -> None:
    """Raise 403 when the backend is running in Solo mode.

    Attach as a route dependency on any endpoint that is not available in
    Solo plan: team management, semantic search, and insights dashboard.
    """
    if settings.is_solo_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This feature is not available in Solo mode. Upgrade to Team plan.",
        )
