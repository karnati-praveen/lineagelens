from __future__ import annotations

"""Provenance continuity drill (PART 5 #55).

Exercises, end-to-end, the claims made by parts 1-3 and by capsule/verifier/
key-registry slices of part 5: capsule export, offline verification, runtime
key rotation/revocation, embedding-provider fallback, and vendor-key fallback
— plus (when configured) Neo4j graph reconstruction from Postgres.

No silent green: each step reports `passed` / `failed` / `skipped_not_configured`
independently, and the drill's overall rollup is `green` only when every step
that *doesn't* require optional infra passed. A step being unavailable
(Neo4j not configured, the standalone verifier not installed) is reported
honestly as `skipped_not_configured`, never folded into a false "all green".

Depends on capsule_service (PART 5 #51) and lineagelens-verifier (PART 5 #52)
per the doc's explicit ordering — both must exist before a drill is meaningful.
"""

import json
import tempfile
import uuid as uuid_pkg
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attestation import (
    build_attestation,
    register_key,
    revoke_key,
    sign_attestation,
    verify_attestation_detailed,
)
from app.core.config import Settings
from app.services.capsule_service import CapsuleBuildOptions, build_capsule
from app.services.embedding_service import (
    SEMANTIC_UNAVAILABLE_WARNING,
    semantic_provider_active,
)

DRILL_SCHEMA_VERSION = "1.0"

STEP_EXPORT_CAPSULE = "export_capsule"
STEP_VERIFY_OFFLINE = "verify_capsule_offline"
STEP_REBUILD_GRAPH = "rebuild_graph_projection"
STEP_KEY_ROTATION = "key_rotation_revocation"
STEP_EMBEDDING_FALLBACK = "embedding_provider_fallback"
STEP_VENDOR_FALLBACK = "vendor_key_fallback"

PASSED = "passed"
FAILED = "failed"
SKIPPED_NOT_CONFIGURED = "skipped_not_configured"

# Steps that require no optional infra — these must ALL pass for a green rollup.
_MANDATORY_STEPS = frozenset(
    {STEP_EXPORT_CAPSULE, STEP_KEY_ROTATION, STEP_EMBEDDING_FALLBACK, STEP_VENDOR_FALLBACK}
)


@dataclass
class DrillStepResult:
    step: str
    status: str
    details: str = ""

    def to_dict(self) -> dict:
        return {"step": self.step, "status": self.status, "details": self.details}


@dataclass
class DrillResult:
    steps: list[DrillStepResult] = field(default_factory=list)
    overall_status: str = "red"
    signature: str | None = None
    public_key_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "schemaVersion": DRILL_SCHEMA_VERSION,
            "overallStatus": self.overall_status,
            "steps": [s.to_dict() for s in self.steps],
            "signature": self.signature,
            "publicKeyId": self.public_key_id,
        }


async def _step_export_capsule(session: AsyncSession, workspace_id: str) -> tuple[DrillStepResult, bytes | None]:
    try:
        result = await build_capsule(session, workspace_id, CapsuleBuildOptions())
        return (
            DrillStepResult(
                STEP_EXPORT_CAPSULE, PASSED, f"built capsule with {result.record_count} record(s)"
            ),
            result.capsule_bytes,
        )
    except Exception as exc:
        return DrillStepResult(STEP_EXPORT_CAPSULE, FAILED, f"capsule build raised: {exc}"), None


def _step_verify_offline(capsule_bytes: bytes | None) -> DrillStepResult:
    if capsule_bytes is None:
        return DrillStepResult(STEP_VERIFY_OFFLINE, FAILED, "no capsule to verify (export step failed)")
    try:
        from lineagelens_verifier.verify import (
            STATUS_VALID,
            STATUS_VALID_WITH_REDACTIONS,
            verify_capsule,
        )
    except ImportError:
        return DrillStepResult(
            STEP_VERIFY_OFFLINE,
            SKIPPED_NOT_CONFIGURED,
            "lineagelens-verifier is not installed (pip install -e lineagelens-verifier/)",
        )

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as fh:
        fh.write(capsule_bytes)
        path = fh.name
    try:
        result = verify_capsule(path)
        if result.status in (STATUS_VALID, STATUS_VALID_WITH_REDACTIONS):
            return DrillStepResult(STEP_VERIFY_OFFLINE, PASSED, f"verifier reported: {result.status}")
        return DrillStepResult(
            STEP_VERIFY_OFFLINE, FAILED, f"verifier reported: {result.status} — {result.details}"
        )
    finally:
        Path(path).unlink(missing_ok=True)


async def _step_rebuild_graph(neo4j_service: Any | None, session: AsyncSession, workspace_id: str) -> DrillStepResult:
    if neo4j_service is None:
        return DrillStepResult(
            STEP_REBUILD_GRAPH,
            SKIPPED_NOT_CONFIGURED,
            "neo4j_disabled — graph rebuild was not attempted",
        )
    try:
        from app.db.models import ProvenanceRecord
        from sqlalchemy import select

        result = await session.execute(
            select(ProvenanceRecord).where(ProvenanceRecord.workspace_id == workspace_id)
        )
        records = list(result.scalars().all())
        rebuilt = await neo4j_service.rebuild_projection(records)
        return DrillStepResult(
            STEP_REBUILD_GRAPH,
            PASSED,
            f"rebuilt {rebuilt.get('nodesRebuilt', 0)} node(s) from Postgres; "
            f"{rebuilt.get('limitation', '')}",
        )
    except Exception as exc:
        return DrillStepResult(STEP_REBUILD_GRAPH, FAILED, f"graph rebuild raised: {exc}")


async def _step_key_rotation(session: AsyncSession) -> DrillStepResult:
    drill_key_id = f"drill-{uuid_pkg.uuid4().hex[:16]}"
    try:
        await register_key(
            session, public_key_id=drill_key_id, public_key_hex="ab" * 32, label="continuity-drill"
        )
        await session.commit()

        statement = build_attestation(
            "continuity_drill_probe", drill_key_id, {"probe": True}, workspace_id="_drill"
        )
        # We don't hold the private key for this synthetic drill key, so we
        # exercise the trust-status branch directly rather than a real signature.
        await revoke_key(session, drill_key_id, reason="drill", revoked_by="continuity_drill")
        await session.commit()

        from app.core.attestation import load_registry_from_db

        registry = await load_registry_from_db(session)
        record = registry.get(drill_key_id)
        if record is None or record.status != "compromised":
            return DrillStepResult(
                STEP_KEY_ROTATION, FAILED, "revoked key did not appear as compromised in the registry"
            )
        return DrillStepResult(
            STEP_KEY_ROTATION, PASSED, "registered, then revoked, a drill key; registry reflects compromised status"
        )
    except Exception as exc:
        return DrillStepResult(STEP_KEY_ROTATION, FAILED, f"key rotation drill raised: {exc}")


def _step_embedding_fallback(settings: Settings) -> DrillStepResult:
    try:
        forced_hash_settings = settings.model_copy(update={"embedding_provider": "hash"})
        if semantic_provider_active(forced_hash_settings):
            return DrillStepResult(
                STEP_EMBEDDING_FALLBACK, FAILED, "hash provider reported as semantic — false green"
            )
        return DrillStepResult(
            STEP_EMBEDDING_FALLBACK,
            PASSED,
            f"hash provider correctly reports unavailable: {SEMANTIC_UNAVAILABLE_WARNING[:60]}...",
        )
    except Exception as exc:
        return DrillStepResult(STEP_EMBEDDING_FALLBACK, FAILED, f"embedding fallback drill raised: {exc}")


def _step_vendor_fallback() -> DrillStepResult:
    """Confirm a disabled/placeholder vendor license key degrades safely to
    Free rather than silently granting entitlement (PART 3 #20 covenant)."""
    try:
        from app.core.license import _PLACEHOLDER_PUBLIC_KEY, verify_license

        entitlement = verify_license("fake.key", public_key_hex=_PLACEHOLDER_PUBLIC_KEY)
        if entitlement.licensed:
            return DrillStepResult(
                STEP_VENDOR_FALLBACK, FAILED, "placeholder vendor key was treated as licensed — false green"
            )
        return DrillStepResult(
            STEP_VENDOR_FALLBACK, PASSED, f"placeholder vendor key correctly falls back: {entitlement.reason}"
        )
    except Exception as exc:
        return DrillStepResult(STEP_VENDOR_FALLBACK, FAILED, f"vendor fallback drill raised: {exc}")


def _rollup(steps: list[DrillStepResult]) -> str:
    by_step = {s.step: s.status for s in steps}
    if any(by_step.get(s) == FAILED for s in _MANDATORY_STEPS):
        return "red"
    if any(s.status == FAILED for s in steps):
        return "red"
    if any(s.status == SKIPPED_NOT_CONFIGURED for s in steps):
        return "amber"
    return "green"


async def run_continuity_drill(
    session: AsyncSession,
    workspace_id: str,
    *,
    neo4j_service: Any | None = None,
    settings: Settings | None = None,
) -> DrillResult:
    """Run every drill step, collecting all results even if some fail."""
    from app.core.config import get_settings

    effective_settings = settings or get_settings()

    export_step, capsule_bytes = await _step_export_capsule(session, workspace_id)
    steps = [
        export_step,
        _step_verify_offline(capsule_bytes),
        await _step_rebuild_graph(neo4j_service, session, workspace_id),
        await _step_key_rotation(session),
        _step_embedding_fallback(effective_settings),
        _step_vendor_fallback(),
    ]

    overall_status = _rollup(steps)

    statement = build_attestation(
        "continuity_drill",
        workspace_id,
        {"overallStatus": overall_status, "steps": [s.to_dict() for s in steps]},
        workspace_id=workspace_id,
    )
    signed = sign_attestation(statement)

    return DrillResult(
        steps=steps,
        overall_status=overall_status,
        signature=signed.signature,
        public_key_id=signed.public_key_id,
    )
