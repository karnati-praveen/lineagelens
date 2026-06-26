from __future__ import annotations

import json
import logging
import uuid as uuid_pkg
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attestation import build_attestation, sign_attestation
from app.core.audit import log_audit_event

logger = logging.getLogger(__name__)

_DEFAULT_CERT_TTL_DAYS = 90


# ── Chain-tip helper ──────────────────────────────────────────────────────────

async def _get_chain_tip(session: AsyncSession, workspace_id: str) -> str | None:
    """Return the record_hash of the latest hash-chained record in the workspace."""
    from app.db.models import ProvenanceRecord

    result = await session.execute(
        select(ProvenanceRecord.record_hash)
        .where(
            ProvenanceRecord.workspace_id == workspace_id,
            ProvenanceRecord.record_hash.is_not(None),
        )
        .order_by(ProvenanceRecord.id.desc())
        .limit(1)
    )
    row = result.one_or_none()
    return row[0] if row else None


# ── Record / review-status helpers ───────────────────────────────────────────

async def _fetch_records_for_scope(
    session: AsyncSession,
    workspace_id: str,
    scope: str,
    scope_ref: str,
) -> list:
    """Fetch provenance records covered by *scope* + *scope_ref*.

    Supported scopes:
      record  — single record identified by UUID
      pr      — all records whose provenance_payload->provider_ref matches
      release — same lookup strategy as pr
    """
    from app.db.models import ProvenanceRecord

    if scope == "record":
        try:
            uuid_val = uuid_pkg.UUID(scope_ref)
        except ValueError:
            return []
        result = await session.execute(
            select(ProvenanceRecord).where(
                ProvenanceRecord.workspace_id == workspace_id,
                ProvenanceRecord.uuid == uuid_val,
            )
        )
        row = result.scalar_one_or_none()
        return [row] if row else []

    # pr / release: look up records tagged with the ref
    from sqlalchemy import cast, Text
    from app.db.models import ProvenanceTag
    tagged = await session.execute(
        select(ProvenanceRecord)
        .join(
            ProvenanceTag,
            (
                cast(ProvenanceRecord.uuid, Text) == ProvenanceTag.record_uuid
            ),
        )
        .where(
            ProvenanceRecord.workspace_id == workspace_id,
            ProvenanceTag.workspace_id == workspace_id,
            ProvenanceTag.tag == f"{scope}:{scope_ref}",
        )
    )
    return list(tagged.scalars().all())


async def _get_review_status(
    session: AsyncSession,
    workspace_id: str,
    record_uuid: str,
) -> str:
    """Return the human-review status for a single record.

    Values: "approved" | "rejected" | "pending" | "unknown"
    "unknown" means no ReviewQueue entry exists.
    """
    from app.db.models import ReviewQueue

    result = await session.execute(
        select(ReviewQueue.status).where(
            ReviewQueue.workspace_id == workspace_id,
            ReviewQueue.record_uuid == record_uuid,
        )
        .order_by(ReviewQueue.updated_at.desc())
        .limit(1)
    )
    row = result.one_or_none()
    if row is None:
        return "unknown"
    # ReviewQueue uses: pending | approved | rejected
    status = (row[0] or "unknown").lower()
    return status if status in {"approved", "rejected", "pending"} else "unknown"


# ── Eligibility evaluation ────────────────────────────────────────────────────

async def evaluate_eligibility(
    session: AsyncSession,
    workspace_id: str,
    scope: str,
    scope_ref: str,
    policy,  # IndemnityPolicy ORM object
) -> dict[str, Any]:
    """Evaluate whether *scope_ref* meets the coverage policy eligibility rules.

    Returns a dict with keys:
      eligible (bool), reasons (list[str]), records_evaluated (int)
    """
    from app.core.config import get_settings
    settings = get_settings()

    records = await _fetch_records_for_scope(session, workspace_id, scope, scope_ref)
    if not records:
        return {
            "eligible": False,
            "reasons": [f"No provenance records found for {scope}:{scope_ref}."],
            "records_evaluated": 0,
        }

    rules: dict = policy.rules_json or {}
    max_risk: int = int(rules.get("max_risk_score", 100))
    require_license_clean: bool = bool(rules.get("require_license_clean", False))
    require_human_review: bool = bool(rules.get("require_human_review", False))
    allowed_models: list[str] = rules.get("allowed_models", [])
    # Per-policy override takes precedence over the global setting.
    unknown_review_pass: bool = bool(
        rules.get("unknown_review_pass", settings.indemnity_unknown_review_pass)
    )

    reasons: list[str] = []
    eligible = True

    for record in records:
        uid = str(record.uuid)

        # Risk check
        if record.risk_score is not None and record.risk_score > max_risk:
            eligible = False
            reasons.append(
                f"Record {uid}: risk score {record.risk_score} exceeds policy maximum {max_risk}."
            )

        # License check (F5). "nothing checked" (not scanned / no corpus) must
        # never satisfy a require_license_clean gate — PART 1 #2.
        if require_license_clean:
            from app.services.license_match_service import CLEAN_STATES

            ls = record.license_status
            if ls is None:
                eligible = False
                reasons.append(
                    f"Record {uid}: license not yet scanned (not_scanned) — cannot confirm clean."
                )
            elif ls in ("not_configured", "insufficient_corpus"):
                eligible = False
                reasons.append(
                    f"Record {uid}: license status '{ls}' — no corpus was checked, "
                    "absence of a match is not evidence of cleanliness."
                )
            elif ls not in CLEAN_STATES:
                eligible = False
                reasons.append(
                    f"Record {uid}: license status '{ls}' "
                    f"(matched: {record.license_match_license}) — policy requires a clean scan."
                )

        # Human-review check (Prompt 3 will populate ReviewQueue; until then → "unknown")
        review_status = await _get_review_status(session, workspace_id, uid)
        if require_human_review:
            if review_status == "unknown":
                if not unknown_review_pass:
                    eligible = False
                    reasons.append(
                        f"Record {uid}: human-review status is 'unknown' — "
                        "policy requires reviewed code (set INDEMNITY_UNKNOWN_REVIEW_PASS=true to allow)."
                    )
            elif review_status not in {"approved"}:
                eligible = False
                reasons.append(
                    f"Record {uid}: human-review status is '{review_status}' — policy requires 'approved'."
                )

        # Model allowlist check
        if allowed_models and record.model_name:
            if record.model_name not in allowed_models:
                eligible = False
                reasons.append(
                    f"Record {uid}: model '{record.model_name}' is not in the policy allowed-models list."
                )

    return {
        "eligible": eligible,
        "reasons": reasons,
        "records_evaluated": len(records),
    }


# ── Certificate issuance ──────────────────────────────────────────────────────

async def issue_certificate(
    session: AsyncSession,
    *,
    workspace_id: str,
    scope: str,
    scope_ref: str,
    policy,  # IndemnityPolicy ORM object
    issued_by: str,
) -> tuple:
    """Evaluate eligibility and issue an IndemnityCertificate.

    On eligible:  creates an Attestation + IndemnityCertificate, returns (cert, attestation).
    On ineligible: creates an unsigned certificate (no Attestation), returns (cert, None).
    Always writes an audit log entry.
    """
    from app.db.models import Attestation, IndemnityCertificate

    eval_result = await evaluate_eligibility(session, workspace_id, scope, scope_ref, policy)
    eligible: bool = eval_result["eligible"]
    reasons: list[str] = eval_result["reasons"]

    rules: dict = policy.rules_json or {}
    ttl_days: int = int(rules.get("cert_ttl_days", _DEFAULT_CERT_TTL_DAYS))
    expires_at = datetime.now(tz=UTC) + timedelta(days=ttl_days)

    attestation_row: Attestation | None = None

    if eligible:
        prev_hash = await _get_chain_tip(session, workspace_id)
        statement = build_attestation(
            subject_type="certificate",
            subject_id=scope_ref,
            claims={
                "scope": scope,
                "scope_ref": scope_ref,
                "policy_id": policy.id,
                "policy_name": policy.name,
                "records_evaluated": eval_result["records_evaluated"],
                "expires_at": expires_at.isoformat(),
            },
            workspace_id=workspace_id,
            prev_hash=prev_hash,
        )
        signed = sign_attestation(statement)
        attestation_row = Attestation(
            workspace_id=workspace_id,
            subject_type="certificate",
            subject_id=scope_ref,
            statement_json=json.dumps(signed.statement, sort_keys=True, default=str),
            signature=signed.signature,
            public_key_id=signed.public_key_id,
            prev_hash=prev_hash,
        )
        session.add(attestation_row)
        await session.flush()  # populate attestation_row.id

    cert = IndemnityCertificate(
        workspace_id=workspace_id,
        scope=scope,
        scope_ref=scope_ref,
        eligibility="eligible" if eligible else "ineligible",
        reasons_json=reasons,
        attestation_id=attestation_row.id if attestation_row else None,
        expires_at=expires_at if eligible else None,
    )
    session.add(cert)
    await session.flush()

    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=issued_by,
        action="indemnity_certificate_issued",
        target_uuid=str(cert.id),
        details={
            "scope": scope,
            "scope_ref": scope_ref,
            "eligibility": cert.eligibility,
            "policy_id": policy.id,
            "attestation_id": attestation_row.id if attestation_row else None,
        },
    )

    return cert, attestation_row
