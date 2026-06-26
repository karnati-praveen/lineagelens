from __future__ import annotations

import json
import logging
import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attestation import build_attestation, sign_attestation, get_public_key_hex
from app.core.audit import log_audit_event
from app.core.security import AuthContext, get_current_auth_context, require_admin
from app.db.models import Attestation, ProvenanceRecord
from app.db.session import get_db_session
from app.services.indemnity_service import _get_chain_tip
from app.services.license_match_service import scan_and_record

router = APIRouter(prefix="/license", tags=["license"])
logger = logging.getLogger(__name__)


@router.get("/record/{record_uuid}")
async def get_license_status(
    record_uuid: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return the license scan status and best match for a provenance record."""
    try:
        uuid_val = uuid_pkg.UUID(record_uuid)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid UUID.")

    result = await session.execute(
        select(ProvenanceRecord).where(
            ProvenanceRecord.uuid == uuid_val,
            ProvenanceRecord.workspace_id == auth.workspace_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found.")

    return {
        "uuid": record_uuid,
        "licenseStatus": record.license_status,
        "bestMatchLicense": record.license_match_license,
        "similarity": record.license_similarity,
        "scanned": record.license_status is not None,
    }


@router.post("/rescan/{record_uuid}", dependencies=[Depends(require_admin)])
async def rescan_record(
    record_uuid: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Trigger a fresh license scan for a record. Admin only."""
    try:
        uuid_val = uuid_pkg.UUID(record_uuid)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid UUID.")

    result = await session.execute(
        select(ProvenanceRecord).where(
            ProvenanceRecord.uuid == uuid_val,
            ProvenanceRecord.workspace_id == auth.workspace_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found.")

    match_result = await scan_and_record(session, record)
    await session.commit()

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="license_rescan",
        target_uuid=record_uuid,
        details={
            "licenseStatus": match_result.match_status,
            "bestMatchLicense": match_result.best_match_license,
            "similarity": match_result.similarity,
        },
    )

    return {
        "uuid": record_uuid,
        "licenseStatus": match_result.match_status,
        "bestMatchLicense": match_result.best_match_license,
        "similarity": match_result.similarity,
    }


@router.get("/certificate/{record_uuid}")
async def get_clean_room_certificate(
    record_uuid: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return a signed clean-room certificate for a record with 'clean' license status.

    The certificate is created on demand and anchored into the hash chain.
    Returns 409 if the record's license status is not 'clean'.
    """
    try:
        uuid_val = uuid_pkg.UUID(record_uuid)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid UUID.")

    result = await session.execute(
        select(ProvenanceRecord).where(
            ProvenanceRecord.uuid == uuid_val,
            ProvenanceRecord.workspace_id == auth.workspace_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found.")

    from app.services.license_match_service import CLEAN_STATES

    if record.license_status is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Record has not been license-scanned yet. Call POST /license/rescan/{uuid} first.",
        )
    if record.license_status in ("not_configured", "insufficient_corpus"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot issue clean-room certificate: license status is "
                   f"'{record.license_status}' — no corpus was checked, so cleanliness "
                   "cannot be certified. Configure LICENSE_FINGERPRINT_PATH and rescan.",
        )
    if record.license_status not in CLEAN_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot issue clean-room certificate: license status is '{record.license_status}' "
                   f"(matched: {record.license_match_license}).",
        )

    # Carry the scan provenance into the cert so it states exactly what was checked.
    scan_meta = {}
    if isinstance(record.provenance_payload, dict):
        scan_meta = record.provenance_payload.get("licenseScanResult", {}) or {}

    prev_hash = await _get_chain_tip(session, auth.workspace_id)
    statement = build_attestation(
        subject_type="record",
        subject_id=record_uuid,
        claims={
            "license_status": record.license_status,
            "similarity": record.license_similarity,
            "corpus_digest": scan_meta.get("corpusDigest"),
            "scanner_version": scan_meta.get("scannerVersion"),
            "match_threshold": scan_meta.get("matchThreshold"),
            "coverage": scan_meta.get("coverage"),
            "scanned_at": datetime.now(tz=UTC).isoformat(),
        },
        workspace_id=auth.workspace_id,
        prev_hash=prev_hash,
    )
    signed = sign_attestation(statement)

    att = Attestation(
        workspace_id=auth.workspace_id,
        subject_type="record",
        subject_id=record_uuid,
        statement_json=json.dumps(signed.statement, sort_keys=True, default=str),
        signature=signed.signature,
        public_key_id=signed.public_key_id,
        prev_hash=prev_hash,
    )
    session.add(att)
    await session.flush()
    await session.commit()

    return {
        "uuid": record_uuid,
        "attestationId": att.id,
        "licenseStatus": record.license_status,
        "similarity": record.license_similarity,
        "signatureValid": True,
        "publicKeyId": signed.public_key_id,
        "publicKeyHex": get_public_key_hex(),
        "issuedAt": signed.statement["issued_at"],
    }
