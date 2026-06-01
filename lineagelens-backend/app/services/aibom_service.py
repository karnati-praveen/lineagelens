from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProvenanceRecord
from app.services.integrity_service import compute_prompt_sha256, sign_aibom

AIBOM_SCHEMA_VERSION = "1.0"


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

        prompt_sha = compute_prompt_sha256(record.prompt_messages)
        if record.prompt_messages is not None:
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

    # Sign everything except the signature block itself
    canonical = json.dumps(payload, sort_keys=True, default=str)
    payload["signature"] = {
        "algorithm": "hmac-sha256",
        "value": sign_aibom(canonical),
    }

    return payload
