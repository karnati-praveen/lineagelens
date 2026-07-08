"""Golden backward-compat test for the capsule top-level shape (PART 5 #51).

Mirrors the "golden backward-compat fixtures" pattern called out for Agent
Trace in PART 4 #32: if the top-level capsule.json shape changes without a
CAPSULE_SCHEMA_VERSION bump, this test fails loudly instead of silently
drifting a format that lineagelens-verifier independently re-implements.
"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET_KEY", "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789")

from app.schemas.capsule import (
    CAPSULE_SCHEMA_VERSION,
    SUPPORTED_CAPSULE_VARIANTS,
    CapsuleDocument,
)

# The exact set of top-level keys capsule.json must have at schema version 1.0.
# Adding/removing/renaming a key here without bumping CAPSULE_SCHEMA_VERSION is
# the regression this test exists to catch.
_EXPECTED_TOP_LEVEL_KEYS = {
    "capsuleSchemaVersion",
    "variant",
    "workspaceId",
    "generatedAt",
    "scope",
    "agentTraceDocuments",
    "recordChain",
    "claims",
    "policyVersions",
    "lifecycleEvents",
    "outcomeEvents",
    "recallEvents",
    "reviewEvents",
    "auditEvents",
    "actionEvents",
    "aibom",
    "licenseCorpus",
    "keyRegistry",
    "versions",
    "slsaProvenanceRef",
}


def test_schema_version_is_1_0():
    assert CAPSULE_SCHEMA_VERSION == "1.0"


def test_only_full_internal_variant_is_supported_today():
    assert SUPPORTED_CAPSULE_VARIANTS == {"full_internal"}


def test_capsule_document_top_level_keys_match_golden_fixture():
    fixture = {
        "capsuleSchemaVersion": CAPSULE_SCHEMA_VERSION,
        "variant": "full_internal",
        "workspaceId": "ws-golden",
        "generatedAt": "2026-06-26T00:00:00+00:00",
        "scope": {"recordCount": 0, "notes": []},
        "agentTraceDocuments": [],
        "recordChain": [],
        "claims": {},
        "policyVersions": {},
        "lifecycleEvents": [],
        "outcomeEvents": [],
        "recallEvents": [],
        "reviewEvents": [],
        "auditEvents": [],
        "actionEvents": [],
        "aibom": {"schema_version": "1.1", "summary": {}, "records": []},
        "licenseCorpus": {"configured": False},
        "keyRegistry": [],
        "versions": {
            "capsuleSchemaVersion": CAPSULE_SCHEMA_VERSION,
            "aibomSchemaVersion": "1.1",
            "policyEvaluatorVersion": "policy-eval-1",
        },
        "slsaProvenanceRef": None,
    }

    doc = CapsuleDocument.model_validate(fixture)
    dumped_keys = set(doc.model_dump(by_alias=True).keys())

    assert dumped_keys == _EXPECTED_TOP_LEVEL_KEYS
    assert set(fixture.keys()) == _EXPECTED_TOP_LEVEL_KEYS
