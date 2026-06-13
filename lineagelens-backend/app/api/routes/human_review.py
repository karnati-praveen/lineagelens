from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import AuthContext, get_current_auth_context
from app.db.models import ApiKey
from app.db.session import get_db_session
from app.services.human_review_service import compute_depth_signal, get_review_status, record_review

router = APIRouter(prefix="/review", tags=["human-review"])
logger = logging.getLogger(__name__)

_VALID_VERDICTS = {"approved", "changes_requested"}

# Depth ranking used by the merge gate; higher index = stricter.
_DEPTH_ORDER: dict[str, int] = {"shallow": 0, "adequate": 1, "deep": 2}
_DEFAULT_MIN_DEPTH = "adequate"


class AttestRequest(BaseModel):
    scope_ref: str = Field(..., alias="scopeRef")
    lines_reviewed: int = Field(..., alias="linesReviewed", ge=0)
    seconds_on_diff: int = Field(..., alias="secondsOnDiff", ge=0)
    comment_count: int = Field(default=0, alias="commentCount", ge=0)
    verdict: str = Field(...)

    model_config = ConfigDict(populate_by_name=True)


async def _authenticate_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    session: AsyncSession = Depends(get_db_session),
) -> tuple[str, str]:
    """Authenticate via X-API-Key for CI callers (mirrors github.py pattern)."""
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header required for CI/CD authentication.",
        )
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    result = await session.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active.is_(True))
    )
    ak = result.scalar_one_or_none()
    if ak is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or revoked API key.")
    if ak.expires_at and ak.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key has expired.")
    ak.last_used_at = datetime.now(UTC)
    await session.commit()
    return ak.workspace_id, ak.user_id


@router.post("/attest", status_code=status.HTTP_201_CREATED)
async def attest_review(
    payload: AttestRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Record a signed, tamper-evident human review attestation (JWT auth required).

    Computes depth_signal from time-on-diff, comment count, and lines reviewed.
    Signs and persists an Attestation row plus a HumanReviewAttestation row.
    Returns the depth badge so the extension can surface it immediately.
    """
    if payload.verdict not in _VALID_VERDICTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"verdict must be one of {sorted(_VALID_VERDICTS)}.",
        )

    hra = await record_review(
        session,
        workspace_id=auth.workspace_id,
        scope_ref=payload.scope_ref,
        reviewer_user_id=auth.subject,
        lines_reviewed=payload.lines_reviewed,
        seconds_on_diff=payload.seconds_on_diff,
        comment_count=payload.comment_count,
        verdict=payload.verdict,
    )
    await session.commit()
    await session.refresh(hra)

    return {
        "id": hra.id,
        "scopeRef": hra.scope_ref,
        "depthSignal": hra.depth_signal,
        "verdict": hra.verdict,
        "attestationId": hra.attestation_id,
        "createdAt": hra.created_at.isoformat() if hra.created_at else None,
    }


@router.get("/status/{ref:path}")
async def get_review_status_endpoint(
    ref: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return the latest human review status for a scope ref (JWT auth required)."""
    status_dict = await get_review_status(
        session,
        workspace_id=auth.workspace_id,
        scope_ref=ref,
    )
    return {"scopeRef": ref, **status_dict}


@router.post("/gate/{pr_ref:path}")
async def review_gate(
    pr_ref: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    api_key_ctx: Annotated[tuple[str, str], Depends(_authenticate_api_key)],
    min_depth: str = _DEFAULT_MIN_DEPTH,
) -> dict:
    """CI merge gate: pass/fail for a PR ref (X-API-Key auth).

    Configurable minimum depth via ?min_depth=adequate (default) or ?min_depth=deep.
    Gate passes only when depth_signal ≥ min_depth AND verdict == 'approved'.
    """
    workspace_id, _ = api_key_ctx

    if min_depth not in _DEPTH_ORDER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"min_depth must be one of {sorted(_DEPTH_ORDER)}.",
        )

    status_dict = await get_review_status(
        session,
        workspace_id=workspace_id,
        scope_ref=pr_ref,
    )

    if not status_dict["has_review"]:
        return {
            "prRef": pr_ref,
            "passed": False,
            "reason": "No human review attestation found for this ref.",
            "depthSignal": None,
            "verdict": None,
            "minDepthRequired": min_depth,
        }

    signal: str = status_dict["depth_signal"]
    verdict: str = status_dict["verdict"]
    depth_ok = _DEPTH_ORDER.get(signal, 0) >= _DEPTH_ORDER[min_depth]
    verdict_ok = verdict == "approved"
    passed = depth_ok and verdict_ok

    if not verdict_ok:
        reason = f"Verdict is '{verdict}'; 'approved' required."
    elif not depth_ok:
        reason = f"Review depth '{signal}' is below minimum required '{min_depth}'."
    else:
        reason = "passed"

    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=None,
        action="human_review.gate",
        target_uuid=pr_ref,
        details={
            "passed": passed,
            "depth_signal": signal,
            "verdict": verdict,
            "min_depth": min_depth,
        },
    )
    await session.commit()

    return {
        "prRef": pr_ref,
        "passed": passed,
        "reason": reason,
        "depthSignal": signal,
        "verdict": verdict,
        "minDepthRequired": min_depth,
        "attestationId": status_dict["attestation_id"],
    }
