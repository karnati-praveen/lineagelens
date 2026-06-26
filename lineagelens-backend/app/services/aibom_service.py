from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attestation import get_public_key_hex, sign_detached, verify_detached
from app.db.models import ProvenanceRecord
from app.services.integrity_service import (
    compute_prompt_sha256,
    sign_aibom,
    verify_aibom_signature,
)

# Bumped to 1.1 with the addition of the Ed25519 (asymmetric) signature block.
AIBOM_SCHEMA_VERSION = "1.1"


async def generate_aibom(
    session: AsyncSession,
    workspace_id: str,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    """Generate a signed AI Bill of Materials for the given workspace.

    Returns a dict ready to serialise as JSON.  The ``signature`` block at the
    top level carries an HMAC-SHA256 of all other fields so the recipient can
    detect tampering before trusting the summary numbers.
    """
    filters: list[Any] = [ProvenanceRecord.workspace_id == workspace_id]
    if date_from:
        filters.append(ProvenanceRecord.timestamp_iso >= date_from)
    if date_to:
        filters.append(ProvenanceRecord.timestamp_iso <= date_to)

    stmt = (
        select(ProvenanceRecord)
        .where(and_(*filters))
        .order_by(asc(ProvenanceRecord.id))
    )
    result = await session.execute(stmt)
    records = result.scalars().all()

    entries: list[dict[str, Any]] = []
    model_counts: dict[str, int] = {}
    disclosed_count = 0
    chain_ok = True
    chain_break_uuid: str | None = None
    expected_prev: str | None = None

    for record in records:
        model = record.model_name or "unknown"
        model_counts[model] = model_counts.get(model, 0) + 1

        # Prefer the committed digest so a redacted/deleted record still reports
        # its original prompt commitment instead of a null (PART 2 #10).
        prompt_sha = getattr(record, "prompt_sha256", None) or compute_prompt_sha256(record.prompt_messages)
        if prompt_sha is not None:
            disclosed_count += 1

        # Verify chain linkage for records that have hashes
        if chain_ok and record.record_hash is not None:
            if record.prev_hash != expected_prev:
                chain_ok = False
                chain_break_uuid = str(record.uuid)
            expected_prev = record.record_hash

        risk_reasons: list[str] = []
        pp = record.provenance_payload or {}
        stored_risk = (
            pp.get("metadata", {}).get("riskAssessment")
            if isinstance(pp.get("metadata"), dict)
            else None
        )
        if isinstance(stored_risk, dict):
            risk_reasons = (stored_risk.get("reasons") or [])[:3]

        entries.append(
            {
                "uuid": str(record.uuid),
                "file_path": record.file_path,
                "model_name": record.model_name,
                "prompt_sha256": prompt_sha,
                "risk_score": record.risk_score,
                "risk_reasons": risk_reasons,
                "timestamp_iso": record.timestamp_iso.isoformat(),
                "is_redacted": record.is_redacted,
                "lifecycle_state": getattr(record, "lifecycle_state", "active"),
                "record_hash": record.record_hash,
                "prev_hash": record.prev_hash,
            }
        )

    total = len(records)
    disclosure_pct = round(disclosed_count / total * 100, 1) if total > 0 else 0.0

    summary: dict[str, Any] = {
        "total_records": total,
        "ai_authored_pct": 100.0,
        "by_model": model_counts,
        "disclosure_coverage_pct": disclosure_pct,
        "chain_verified": chain_ok,
        "chain_break_uuid": chain_break_uuid,
    }

    payload: dict[str, Any] = {
        "schema_version": AIBOM_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "workspace_id": workspace_id,
        "filter": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
        },
        "summary": summary,
        "records": entries,
    }

    # Sign everything except the signature block itself. We dual-sign:
    #   * HMAC-SHA256 — preserved for backward compatibility (verifiable only by
    #     a holder of JWT_SECRET_KEY).
    #   * Ed25519 — asymmetric, so a standalone tool holding only the exported
    #     public key can verify the document offline (PART 1 #9).
    canonical = json.dumps(payload, sort_keys=True, default=str)
    ed25519_sig, public_key_id = sign_detached(canonical.encode())
    payload["signature"] = {
        # `algorithm`/`value` kept at top level for backward-compatible HMAC verify.
        "algorithm": "hmac-sha256",
        "value": sign_aibom(canonical),
        "ed25519": {
            "algorithm": "ed25519",
            "value": ed25519_sig,
            "publicKeyId": public_key_id,
            "publicKeyHex": get_public_key_hex(),
        },
    }

    return payload


def verify_aibom(payload: dict[str, Any]) -> dict[str, bool]:
    """Verify both signatures on a generated AI-BOM payload.

    Returns {"hmac": bool, "ed25519": bool}. The Ed25519 check needs only the
    public key embedded in the document, so it works offline / in a standalone
    verifier; the HMAC check needs the original signing secret.
    """
    sig_block = payload.get("signature") or {}
    body = {k: v for k, v in payload.items() if k != "signature"}
    canonical = json.dumps(body, sort_keys=True, default=str)

    hmac_ok = bool(sig_block.get("value")) and verify_aibom_signature(canonical, sig_block["value"])
    ed = sig_block.get("ed25519") or {}
    ed_ok = bool(ed.get("value")) and verify_detached(canonical.encode(), ed["value"])
    return {"hmac": hmac_ok, "ed25519": ed_ok}
