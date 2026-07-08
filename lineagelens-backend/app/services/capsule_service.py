from __future__ import annotations

"""Evidence Capsule assembly (PART 5 #51).

Bundles the evidence pieces already shipped by earlier parts into one
signed, content-addressed, offline-verifiable archive:

  - Agent Trace documents (agent_trace_service, PART 2 #15)
  - typed per-record claims (core.evidence, PART 1 #7 / PART 5 #56)
  - frozen policy versions (policy_version_service, PART 2 #12)
  - redaction/deletion lifecycle events (record_lifecycle_service, PART 2 #10/#11)
  - outcome / recall / review / agent-action / audit events
  - the dual-signed AI-BOM (aibom_service, PART 1 #9)
  - the license-corpus digest/coverage (license_match_service, PART 1 #2)
  - the key registry (attestation.load_registry_from_db, PART 5 #57)
  - the standalone offline verifier's own source, vendored in under verifier/
    (lineagelens-verifier/, PART 5 #52)

Only the "full_internal" variant is built. The doc's other variants
(redacted_legal, selective_disclosure, recall, release_assurance, vendor_exit)
each need per-variant redaction/filtering rules that require legal review —
not something to invent unilaterally. See app.schemas.capsule for the list.

Real in-toto/SLSA provenance generation happens at *release*-build time
(PART 5 #59), not per-capsule build. A capsule only carries a pass-through
pointer (LINEAGELENS_RELEASE_ATTESTATION_URL) when the running deployment was
itself built from an attested release — never a fabricated reference.
"""

import hashlib
import json
import os
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
import uuid as uuid_pkg

from sqlalchemy import and_, asc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attestation import load_registry_from_db, sign_attestation
from app.core.evidence import classify_record_claims
from app.db.models import Policy, ProvenanceRecord
from app.schemas.capsule import CAPSULE_SCHEMA_VERSION, SUPPORTED_CAPSULE_VARIANTS
from app.services import (
    agent_action_service,
    agent_trace_service,
    aibom_service,
    human_review_service,
    license_match_service,
    outcome_service,
    policy_version_service,
    recall_service,
    record_lifecycle_service,
)
from app.core.audit import list_audit_events

# lineagelens-backend/app/services/capsule_service.py -> repo root
VERIFIER_SOURCE_DIR = Path(__file__).resolve().parents[3] / "lineagelens-verifier"
_VERIFIER_SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", "tests", "dist", "build"}
_VERIFIER_SKIP_SUFFIXES = {".pyc"}


@dataclass(frozen=True)
class CapsuleManifestEntry:
    path: str
    sha256: str
    size_bytes: int
    content_type: str

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "sizeBytes": self.size_bytes,
            "contentType": self.content_type,
        }


@dataclass
class CapsuleBuildOptions:
    variant: str = "full_internal"
    date_from: datetime | None = None
    date_to: datetime | None = None
    record_uuids: list[str] | None = None


@dataclass(frozen=True)
class CapsuleBuildResult:
    capsule_bytes: bytes
    capsule_digest: str
    manifest: dict
    signature: str
    public_key_id: str
    record_count: int


def capsule_digest(capsule_bytes: bytes) -> str:
    """Top-level content address of the whole capsule zip."""
    return hashlib.sha256(capsule_bytes).hexdigest()


async def _resolve_records(
    session: AsyncSession, workspace_id: str, options: CapsuleBuildOptions
) -> list[ProvenanceRecord]:
    filters: list[Any] = [ProvenanceRecord.workspace_id == workspace_id]
    if options.record_uuids:
        parsed: list[uuid_pkg.UUID] = []
        for u in options.record_uuids:
            try:
                parsed.append(uuid_pkg.UUID(u))
            except ValueError:
                continue
        if not parsed:
            return []
        filters.append(ProvenanceRecord.uuid.in_(parsed))
    else:
        if options.date_from is not None:
            filters.append(ProvenanceRecord.timestamp_iso >= options.date_from)
        if options.date_to is not None:
            filters.append(ProvenanceRecord.timestamp_iso <= options.date_to)

    result = await session.execute(
        select(ProvenanceRecord).where(and_(*filters)).order_by(asc(ProvenanceRecord.id))
    )
    return list(result.scalars().all())


def _git_commit() -> str | None:
    """Best-effort git HEAD of the running backend checkout. None if unavailable."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        commit = proc.stdout.strip()
        return commit or None
    except Exception:
        return None


async def _alembic_head(session: AsyncSession) -> str | None:
    """Best-effort current Alembic revision, straight from the DB (not the code constant)."""
    try:
        result = await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
        row = result.first()
        return row[0] if row else None
    except Exception:
        return None


def _lifecycle_event_to_dict(event) -> dict:
    return {
        "recordUuid": event.record_uuid,
        "eventType": event.event_type,
        "reason": event.reason,
        "policyRef": event.policy_ref,
        "authorizedBy": event.authorized_by,
        "contentCommitment": event.content_commitment,
        "signature": event.signature,
        "publicKeyId": event.public_key_id,
        "signatureValid": record_lifecycle_service.verify_event_signature(event),
        "createdAt": event.created_at.isoformat() if event.created_at else None,
    }


def _record_chain_entry(record: ProvenanceRecord) -> dict:
    """Raw fields needed to independently recompute compute_record_hash.

    agentTraceDocuments (Agent Trace spec shape) doesn't carry enough fields
    to recompute the chain hash. This section is what makes the capsule
    actually chain-verifiable offline, not just descriptively bundled.
    """
    return {
        "uuid": str(record.uuid),
        "workspaceId": record.workspace_id,
        "filePath": record.file_path,
        "insertedCode": record.inserted_code,
        "modelName": record.model_name,
        "promptSha256": record.prompt_sha256,
        "contentSha256": getattr(record, "content_sha256", None),
        "timestampIso": record.timestamp_iso.isoformat(),
        "prevHash": record.prev_hash,
        "recordHash": record.record_hash,
        "lifecycleState": getattr(record, "lifecycle_state", "active"),
    }


def _action_to_dict(action) -> dict:
    return {
        "sessionKey": action.session_key,
        "actionType": action.action_type,
        "toolName": action.tool_name,
        "agentIdentity": action.agent_identity,
        "humanPrincipal": action.human_principal,
        "mandateRef": action.mandate_ref,
        "capability": action.capability,
        "authorityState": action.authority_state,
        "recordHash": action.record_hash,
        "prevHash": action.prev_hash,
        "occurredAt": action.occurred_at.isoformat() if action.occurred_at else None,
    }


def _vendor_verifier(zf: zipfile.ZipFile, manifest_entries: list[CapsuleManifestEntry]) -> None:
    """Bundle the standalone offline verifier's source under verifier/ (PART 5 #52).

    Best-effort: if lineagelens-verifier/ isn't present in this checkout, the
    capsule still builds, but callers must check for a 'verifier/' prefix in
    the manifest before claiming a verifier is actually included — never
    silently claim inclusion that didn't happen.
    """
    if not VERIFIER_SOURCE_DIR.is_dir():
        return
    for path in sorted(VERIFIER_SOURCE_DIR.rglob("*")):
        if path.is_dir():
            continue
        if any(part in _VERIFIER_SKIP_DIR_NAMES for part in path.relative_to(VERIFIER_SOURCE_DIR).parts):
            continue
        if path.suffix in _VERIFIER_SKIP_SUFFIXES:
            continue
        rel = path.relative_to(VERIFIER_SOURCE_DIR)
        arcname = f"verifier/{rel.as_posix()}"
        content = path.read_bytes()
        zf.writestr(arcname, content)
        manifest_entries.append(
            CapsuleManifestEntry(
                path=arcname,
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                content_type="text/plain",
            )
        )


async def build_capsule(
    session: AsyncSession,
    workspace_id: str,
    options: CapsuleBuildOptions,
) -> CapsuleBuildResult:
    if options.variant not in SUPPORTED_CAPSULE_VARIANTS:
        raise ValueError(
            f"Unsupported capsule variant {options.variant!r}. Only "
            f"{sorted(SUPPORTED_CAPSULE_VARIANTS)} are built today — other variants "
            "are a documented follow-up (see app.schemas.capsule)."
        )

    records = await _resolve_records(session, workspace_id, options)

    agent_trace_documents = [
        agent_trace_service.record_to_agent_trace(r).model_dump(exclude_none=True) for r in records
    ]
    claims = {str(r.uuid): classify_record_claims(r) for r in records}

    policies_result = await session.execute(select(Policy).where(Policy.workspace_id == workspace_id))
    policy_versions: dict[str, list[dict]] = {}
    for policy in policies_result.scalars().all():
        versions = await policy_version_service.list_versions(session, policy.id, workspace_id)
        policy_versions[str(policy.id)] = [
            {
                "version": v.version,
                "changeType": v.change_type,
                "name": v.name,
                "digest": v.digest,
                "evaluatorVersion": v.evaluator_version,
                "createdAt": v.created_at.isoformat() if v.created_at else None,
                "supersededAt": v.superseded_at.isoformat() if v.superseded_at else None,
            }
            for v in versions
        ]

    lifecycle_events = await record_lifecycle_service.list_events_for_workspace(
        session, workspace_id, date_from=options.date_from, date_to=options.date_to
    )
    outcome_events = await outcome_service.list_outcomes_for_workspace(
        session, workspace_id, date_from=options.date_from, date_to=options.date_to
    )
    recall_events = await recall_service.list_campaigns_for_workspace(session, workspace_id)
    review_events = await human_review_service.list_reviews_for_workspace(
        session, workspace_id, date_from=options.date_from, date_to=options.date_to
    )
    audit_events = await list_audit_events(
        session, workspace_id, date_from=options.date_from, date_to=options.date_to
    )
    action_rows = await agent_action_service.list_actions(
        session,
        workspace_id=workspace_id,
        from_dt=options.date_from,
        to_dt=options.date_to,
        limit=10_000,
    )

    aibom = await aibom_service.generate_aibom(
        session, workspace_id, date_from=options.date_from, date_to=options.date_to
    )

    key_registry = await load_registry_from_db(session)
    key_registry_out = [
        {
            "publicKeyId": rec.public_key_id,
            "publicKeyHex": rec.public_key_hex,
            "validFrom": rec.valid_from,
            "validUntil": rec.valid_until,
            "compromisedAt": rec.compromised_at,
            "status": rec.status,
        }
        for rec in key_registry.values()
    ]

    scope_notes: list[str] = []
    if options.record_uuids and options.date_from is None and options.date_to is None:
        scope_notes.append(
            "aibom/outcomeEvents/recallEvents/reviewEvents/auditEvents/actionEvents cover the "
            "whole workspace (not filtered to recordUuids) since no date range was given to "
            "bound them; agentTraceDocuments and claims ARE scoped to recordUuids."
        )

    capsule_doc: dict[str, Any] = {
        "capsuleSchemaVersion": CAPSULE_SCHEMA_VERSION,
        "variant": options.variant,
        "workspaceId": workspace_id,
        "generatedAt": datetime.now(UTC).isoformat(),
        "scope": {
            "dateFrom": options.date_from.isoformat() if options.date_from else None,
            "dateTo": options.date_to.isoformat() if options.date_to else None,
            "recordUuids": options.record_uuids,
            "recordCount": len(records),
            "notes": scope_notes,
        },
        "agentTraceDocuments": agent_trace_documents,
        "recordChain": [_record_chain_entry(r) for r in records],
        "claims": claims,
        "policyVersions": policy_versions,
        "lifecycleEvents": [_lifecycle_event_to_dict(e) for e in lifecycle_events],
        "outcomeEvents": outcome_events,
        "recallEvents": recall_events,
        "reviewEvents": review_events,
        "auditEvents": audit_events,
        "actionEvents": [_action_to_dict(a) for a in action_rows],
        "aibom": aibom,
        "licenseCorpus": license_match_service.corpus_summary(),
        "keyRegistry": key_registry_out,
        "versions": {
            "capsuleSchemaVersion": CAPSULE_SCHEMA_VERSION,
            "aibomSchemaVersion": aibom_service.AIBOM_SCHEMA_VERSION,
            "policyEvaluatorVersion": policy_version_service.EVALUATOR_VERSION,
            "backendGitCommit": _git_commit(),
            "alembicHead": await _alembic_head(session),
        },
        "slsaProvenanceRef": os.environ.get("LINEAGELENS_RELEASE_ATTESTATION_URL"),
    }

    signed = sign_attestation(capsule_doc)

    manifest_entries: list[CapsuleManifestEntry] = []
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        capsule_bytes_inner = json.dumps(capsule_doc, sort_keys=True, default=str, indent=2).encode()
        zf.writestr("capsule.json", capsule_bytes_inner)
        manifest_entries.append(
            CapsuleManifestEntry(
                path="capsule.json",
                sha256=hashlib.sha256(capsule_bytes_inner).hexdigest(),
                size_bytes=len(capsule_bytes_inner),
                content_type="application/json",
            )
        )

        sig_bytes = json.dumps(
            {
                "algorithm": "ed25519",
                "signature": signed.signature,
                "publicKeyId": signed.public_key_id,
            },
            sort_keys=True,
            indent=2,
        ).encode()
        zf.writestr("capsule.json.sig", sig_bytes)
        manifest_entries.append(
            CapsuleManifestEntry(
                path="capsule.json.sig",
                sha256=hashlib.sha256(sig_bytes).hexdigest(),
                size_bytes=len(sig_bytes),
                content_type="application/json",
            )
        )

        _vendor_verifier(zf, manifest_entries)

        manifest_doc = {
            "capsuleSchemaVersion": CAPSULE_SCHEMA_VERSION,
            "entries": [e.to_dict() for e in manifest_entries],
        }
        manifest_bytes = json.dumps(manifest_doc, sort_keys=True, indent=2).encode()
        zf.writestr("manifest.json", manifest_bytes)

    capsule_zip_bytes = buf.getvalue()

    return CapsuleBuildResult(
        capsule_bytes=capsule_zip_bytes,
        capsule_digest=capsule_digest(capsule_zip_bytes),
        manifest=manifest_doc,
        signature=signed.signature,
        public_key_id=signed.public_key_id,
        record_count=len(records),
    )
