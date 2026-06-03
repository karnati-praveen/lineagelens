from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.core.config import Settings, get_settings


def require_non_solo(settings: Settings = Depends(get_settings)) -> None:
    """Raise 403 when the backend is running in Lite (solo) mode.

    Attach as a route dependency on any endpoint that is not available in
    Lite plan: team management, keyword search, and insights dashboard.
    """
    if settings.is_solo_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This feature is not available in Lite mode. Upgrade to Plus plan.",
        )
