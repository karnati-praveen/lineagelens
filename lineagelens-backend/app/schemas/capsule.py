from __future__ import annotations

"""Versioned Evidence Capsule shape (PART 5 #51).

A capsule is a signed, content-addressed bundle that turns the individually
verifiable evidence pieces already shipped (Agent Trace docs, typed claims,
frozen policy versions, redaction/deletion tombstones, outcome/recall/review/
action/audit events, the dual-signed AI-BOM, license-corpus digest, and the
key registry) into one exportable, offline-verifiable unit.

Bump CAPSULE_SCHEMA_VERSION whenever a top-level key is added, removed, or
its meaning changes — the standalone verifier (lineagelens-verifier/) checks
this field first and reports `unsupported_schema` rather than guessing at an
unfamiliar shape.
"""

from pydantic import BaseModel

CAPSULE_SCHEMA_VERSION = "1.0"

# Only "full_internal" is built today. The others are a documented follow-up —
# each needs per-variant redaction/filtering rules that require legal review,
# not something a solo developer should invent unilaterally.
SUPPORTED_CAPSULE_VARIANTS = frozenset({"full_internal"})
FOLLOW_UP_CAPSULE_VARIANTS = frozenset(
    {"redacted_legal", "selective_disclosure", "recall", "release_assurance", "vendor_exit"}
)


class CapsuleManifestEntry(BaseModel):
    path: str
    sha256: str
    sizeBytes: int
    contentType: str


class CapsuleManifest(BaseModel):
    capsuleSchemaVersion: str = CAPSULE_SCHEMA_VERSION
    entries: list[CapsuleManifestEntry]


class CapsuleScope(BaseModel):
    dateFrom: str | None = None
    dateTo: str | None = None
    recordUuids: list[str] | None = None
    recordCount: int
    notes: list[str] = []


class CapsuleVersions(BaseModel):
    capsuleSchemaVersion: str = CAPSULE_SCHEMA_VERSION
    aibomSchemaVersion: str
    policyEvaluatorVersion: str
    backendGitCommit: str | None = None
    alembicHead: str | None = None


class CapsuleDocument(BaseModel):
    """Typed mirror of capsule.json's top-level shape.

    Inner sections (agentTraceDocuments, claims, policyVersions, event lists,
    aibom, keyRegistry) stay as loosely-typed dicts/lists here rather than
    fully-nested models — they are each independently typed/versioned by
    their own owning service (agent_trace, evidence, policy_version_service,
    aibom_service, attestation). This model exists to pin the *top-level*
    contract so a schema drift is caught by test_capsule_schema.py without a
    version bump.
    """

    capsuleSchemaVersion: str = CAPSULE_SCHEMA_VERSION
    variant: str
    workspaceId: str
    generatedAt: str
    scope: CapsuleScope
    agentTraceDocuments: list[dict]
    recordChain: list[dict]
    claims: dict[str, list[dict]]
    policyVersions: dict[str, list[dict]]
    lifecycleEvents: list[dict]
    outcomeEvents: list[dict]
    recallEvents: list[dict]
    reviewEvents: list[dict]
    auditEvents: list[dict]
    actionEvents: list[dict]
    aibom: dict
    licenseCorpus: dict
    keyRegistry: list[dict]
    versions: CapsuleVersions
    slsaProvenanceRef: str | None = None
