from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, status

from app.core.config import Settings, get_settings
from app.core.license import PLAN_RANK


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


def require_plan(min_plan: str) -> Callable[[Settings], None]:
    """Build a route dependency that enforces a minimum paid plan.

    Unlike :func:`require_non_solo` (which trusts the BACKEND_MODE honor flag), this
    checks ``settings.effective_plan`` — derived from the offline, vendor-signed license
    and bounded by the deployed infrastructure — so it cannot be unlocked by editing
    ``.env``. Use ``require_plan("plus")`` or ``require_plan("max")``.
    """
    if min_plan not in PLAN_RANK:
        raise ValueError(f"min_plan must be one of {sorted(PLAN_RANK)}, got {min_plan!r}")

    def _dependency(settings: Settings = Depends(get_settings)) -> None:
        current = settings.effective_plan
        if PLAN_RANK.get(current, -1) < PLAN_RANK[min_plan]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This feature requires the {min_plan.title()} plan "
                    f"(current plan: {current.title()}). See https://lineagelens.dev/pricing."
                ),
            )

    return _dependency
