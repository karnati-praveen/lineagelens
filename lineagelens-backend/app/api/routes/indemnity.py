from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attestation import SignedAttestation, get_public_key_hex, verify_attestation
from app.core.mode_guard import require_non_solo
from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context, require_admin
from app.db.models import Attestation, IndemnityCertificate, IndemnityPolicy
from app.db.session import get_db_session
from app.services.indemnity_service import issue_certificate

router = APIRouter(prefix="/indemnity", tags=["indemnity"])
logger = logging.getLogger(__name__)

_VALID_SCOPES = {"record", "pr", "release"}

# PART 1 #1 — this artifact is a *Policy Eligibility Attestation*, not insurance.
# It only attests that a scope met the configured policy (max risk, license
# state, review state) and was signed. There is no insurer, warranty, payout,
# or claims process. Every response carries this disclaimer to prevent the
# "indemnity" name from implying coverage that does not exist.
ATTESTATION_TYPE = "policy_eligibility_attestation"
ATTESTATION_DISCLAIMER = (
    "This is a Policy Eligibility Attestation: a signed statement that the scope "
    "met the configured policy at issuance time. It is NOT insurance, indemnity, a "
    "warranty, or a guarantee, and carries no payout or claims process."
)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class PolicyRules(BaseModel):
    max_risk_score: int = Field(default=70, ge=0, le=100, alias="maxRiskScore")
    require_license_clean: bool = Field(default=False, alias="requireLicenseClean")
    require_human_review: bool = Field(default=False, alias="requireHumanReview")
    allowed_models: list[str] = Field(default_factory=list, alias="allowedModels")
    unknown_review_pass: bool = Field(default=False, alias="unknownReviewPass")
    cert_ttl_days: int = Field(default=90, ge=1, le=3650, alias="certTtlDays")

    model_config = ConfigDict(populate_by_name=True)


class PolicyUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    rules: PolicyRules = Field(default_factory=PolicyRules)
    active: bool = True

    model_config = ConfigDict(populate_by_name=True)


class CertificateRequest(BaseModel):
    scope: str = Field(..., description="record | pr | release")
    scope_ref: str = Field(..., min_length=1, max_length=256, alias="scopeRef")

    model_config = ConfigDict(populate_by_name=True)


# ── Serialisers ───────────────────────────────────────────────────────────────

def _serialize_policy(p: IndemnityPolicy) -> dict:
    return {
        "id": p.id,
        "workspaceId": p.workspace_id,
        "name": p.name,
        "rules": p.rules_json,
        "active": p.active,
        "createdAt": p.created_at.isoformat(),
        "updatedAt": p.updated_at.isoformat(),
    }


def _serialize_cert(cert: IndemnityCertificate) -> dict:
    return {
        "id": str(cert.id),
        "workspaceId": cert.workspace_id,
        "attestationType": ATTESTATION_TYPE,
        "disclaimer": ATTESTATION_DISCLAIMER,
        "scope": cert.scope,
        "scopeRef": cert.scope_ref,
        "eligibility": cert.eligibility,
        "reasons": cert.reasons_json,
        "attestationId": cert.attestation_id,
        "createdAt": cert.created_at.isoformat(),
        "expiresAt": cert.expires_at.isoformat() if cert.expires_at else None,
    }


# ── Policy endpoints ──────────────────────────────────────────────────────────

@router.put("/policy", dependencies=[Depends(require_non_solo)])
async def upsert_policy(
    body: PolicyUpsert,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Create or replace the indemnity policy for the caller's workspace. Admin only."""
    workspace_id = auth.workspace_id

    result = await session.execute(
        select(IndemnityPolicy).where(IndemnityPolicy.workspace_id == workspace_id)
    )
    policy = result.scalar_one_or_none()

    rules_dict = body.rules.model_dump(by_alias=False)

    if policy is None:
        policy = IndemnityPolicy(
            workspace_id=workspace_id,
            name=body.name,
            rules_json=rules_dict,
            active=body.active,
        )
        session.add(policy)
    else:
        policy.name = body.name
        policy.rules_json = rules_dict
        policy.active = body.active

    await session.commit()
    await session.refresh(policy)
    return _serialize_policy(policy)


@router.get("/policy", dependencies=[Depends(require_non_solo)])
async def get_policy(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return the indemnity policy for the caller's workspace."""
    result = await session.execute(
        select(IndemnityPolicy).where(IndemnityPolicy.workspace_id == auth.workspace_id)
    )
    policy = result.scalar_one_or_none()
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No indemnity policy configured for this workspace.",
        )
    return _serialize_policy(policy)


# ── Certificate endpoints ─────────────────────────────────────────────────────

@router.post("/certificate", dependencies=[Depends(require_non_solo)])
async def create_certificate(
    body: CertificateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Evaluate eligibility and issue a Policy Eligibility Attestation (PART 1 #1).

    This is not insurance/indemnity — see ATTESTATION_DISCLAIMER in the response.
    """
    if body.scope not in _VALID_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"scope must be one of: {', '.join(sorted(_VALID_SCOPES))}.",
        )

    result = await session.execute(
        select(IndemnityPolicy).where(
            IndemnityPolicy.workspace_id == auth.workspace_id,
            IndemnityPolicy.active.is_(True),
        )
    )
    policy = result.scalar_one_or_none()
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active indemnity policy found. Create one via PUT /indemnity/policy.",
        )

    cert, _ = await issue_certificate(
        session,
        workspace_id=auth.workspace_id,
        scope=body.scope,
        scope_ref=body.scope_ref,
        policy=policy,
        issued_by=auth.subject,
    )
    await session.commit()
    return _serialize_cert(cert)


@router.get("/certificate/{cert_id}", dependencies=[Depends(require_non_solo)])
async def get_certificate(
    cert_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return a certificate. Workspace-scoped."""
    import uuid as _uuid
    try:
        cert_uuid = _uuid.UUID(cert_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid certificate id.")

    result = await session.execute(
        select(IndemnityCertificate).where(IndemnityCertificate.id == cert_uuid)
    )
    cert = result.scalar_one_or_none()
    if cert is None or cert.workspace_id != auth.workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Certificate not found.")

    if cert.expires_at and cert.expires_at < datetime.now(tz=UTC):
        return {**_serialize_cert(cert), "expired": True}
    return {**_serialize_cert(cert), "expired": False}


@router.get("/certificate/{cert_id}/verify")
async def verify_certificate(
    cert_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    """Public endpoint: verify the attestation signature on a certificate."""
    import uuid as _uuid
    try:
        cert_uuid = _uuid.UUID(cert_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid certificate id.")

    result = await session.execute(
        select(IndemnityCertificate).where(IndemnityCertificate.id == cert_uuid)
    )
    cert = result.scalar_one_or_none()
    if cert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Certificate not found.")

    if cert.attestation_id is None:
        return {
            "id": cert_id,
            "valid": False,
            "eligibility": cert.eligibility,
            "reason": "Certificate is ineligible — no attestation was issued.",
        }

    att_result = await session.execute(
        select(Attestation).where(Attestation.id == cert.attestation_id)
    )
    att = att_result.scalar_one_or_none()
    if att is None:
        return {"id": cert_id, "valid": False, "reason": "Attestation record missing."}

    statement = json.loads(att.statement_json)
    signed = SignedAttestation(
        statement=statement,
        signature=att.signature,
        public_key_id=att.public_key_id,
    )
    valid = verify_attestation(signed)
    expired = bool(cert.expires_at and cert.expires_at < datetime.now(tz=UTC))

    return {
        "id": cert_id,
        "attestationType": ATTESTATION_TYPE,
        "disclaimer": ATTESTATION_DISCLAIMER,
        "valid": valid and not expired,
        "signatureValid": valid,
        "expired": expired,
        "eligibility": cert.eligibility,
        "scope": cert.scope,
        "scopeRef": cert.scope_ref,
        "attestationId": str(att.public_ref),
        "publicKeyId": att.public_key_id,
        "publicKeyHex": get_public_key_hex(),
        "expiresAt": cert.expires_at.isoformat() if cert.expires_at else None,
        "createdAt": cert.created_at.isoformat(),
    }
