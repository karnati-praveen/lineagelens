"""Self-contained tests for the standalone verifier (PART 5 #52).

Builds tiny fixture capsules by hand (own Ed25519 keypair, own zip) — no
LineageLens backend import anywhere in this file, proving the verifier is
genuinely independent.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from lineagelens_verifier.verify import (
    STATUS_EXPIRED_OR_COMPROMISED_KEY,
    STATUS_TAMPERED,
    STATUS_UNSUPPORTED_SCHEMA,
    STATUS_UNVERIFIABLE,
    STATUS_VALID,
    STATUS_VALID_WITH_REDACTIONS,
    _canonical_json,
    verify_capsule,
)


def _keypair():
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return priv, pub_hex


_KEY_ID = "testkey0000000a"


def _base_capsule(**overrides) -> dict:
    doc = {
        "capsuleSchemaVersion": "1.0",
        "variant": "full_internal",
        "workspaceId": "ws-test",
        "generatedAt": datetime.now(UTC).isoformat(),
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
        "versions": {"capsuleSchemaVersion": "1.0", "aibomSchemaVersion": "1.1", "policyEvaluatorVersion": "policy-eval-1"},
        "slsaProvenanceRef": None,
    }
    doc.update(overrides)
    return doc


def _build_zip(capsule_doc: dict, priv, public_key_id: str = _KEY_ID, *, mangle_manifest=False, mangle_signature=False) -> bytes:
    signature_hex = priv.sign(_canonical_json(capsule_doc)).hex()
    if mangle_signature:
        signature_hex = ("0" if signature_hex[0] != "0" else "1") + signature_hex[1:]

    capsule_bytes = json.dumps(capsule_doc, sort_keys=True, indent=2).encode()
    sig_bytes = json.dumps(
        {"algorithm": "ed25519", "signature": signature_hex, "publicKeyId": public_key_id}, sort_keys=True
    ).encode()

    entries = [
        {"path": "capsule.json", "sha256": __import__("hashlib").sha256(capsule_bytes).hexdigest(), "sizeBytes": len(capsule_bytes), "contentType": "application/json"},
        {"path": "capsule.json.sig", "sha256": __import__("hashlib").sha256(sig_bytes).hexdigest(), "sizeBytes": len(sig_bytes), "contentType": "application/json"},
    ]
    if mangle_manifest:
        entries[0]["sha256"] = "0" * 64

    manifest_bytes = json.dumps({"capsuleSchemaVersion": "1.0", "entries": entries}, sort_keys=True).encode()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("capsule.json", capsule_bytes)
        zf.writestr("capsule.json.sig", sig_bytes)
        zf.writestr("manifest.json", manifest_bytes)
    return buf.getvalue()


def _write_temp_zip(tmp_path, data: bytes, name="capsule.zip"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_valid_capsule_verifies(tmp_path):
    priv, pub_hex = _keypair()
    doc = _base_capsule(
        keyRegistry=[{"publicKeyId": _KEY_ID, "publicKeyHex": pub_hex, "status": "active"}]
    )
    zip_bytes = _build_zip(doc, priv)
    path = _write_temp_zip(tmp_path, zip_bytes)

    result = verify_capsule(path)
    assert result.status == STATUS_VALID
    assert result.manifest_ok is True
    assert result.signature_ok is True
    assert result.chain_ok is True
    assert result.key_trust_ok is True


def test_tampered_capsule_content_detected(tmp_path):
    priv, pub_hex = _keypair()
    doc = _base_capsule(
        keyRegistry=[{"publicKeyId": _KEY_ID, "publicKeyHex": pub_hex, "status": "active"}]
    )
    zip_bytes = _build_zip(doc, priv, mangle_manifest=True)
    path = _write_temp_zip(tmp_path, zip_bytes)

    result = verify_capsule(path)
    assert result.status == STATUS_TAMPERED
    assert result.manifest_ok is False


def test_wrong_signature_detected(tmp_path):
    priv, pub_hex = _keypair()
    doc = _base_capsule(
        keyRegistry=[{"publicKeyId": _KEY_ID, "publicKeyHex": pub_hex, "status": "active"}]
    )
    zip_bytes = _build_zip(doc, priv, mangle_signature=True)
    path = _write_temp_zip(tmp_path, zip_bytes)

    result = verify_capsule(path)
    assert result.status == STATUS_TAMPERED
    assert result.signature_ok is False


def test_compromised_key_at_generation_time(tmp_path):
    priv, pub_hex = _keypair()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    doc = _base_capsule(
        keyRegistry=[
            {"publicKeyId": _KEY_ID, "publicKeyHex": pub_hex, "status": "compromised", "compromisedAt": past}
        ]
    )
    zip_bytes = _build_zip(doc, priv)
    path = _write_temp_zip(tmp_path, zip_bytes)

    result = verify_capsule(path)
    assert result.status == STATUS_EXPIRED_OR_COMPROMISED_KEY
    assert result.key_trust_ok is False


def test_unsupported_schema_version(tmp_path):
    priv, pub_hex = _keypair()
    doc = _base_capsule(
        capsuleSchemaVersion="99.9",
        keyRegistry=[{"publicKeyId": _KEY_ID, "publicKeyHex": pub_hex, "status": "active"}],
    )
    zip_bytes = _build_zip(doc, priv)
    path = _write_temp_zip(tmp_path, zip_bytes)

    result = verify_capsule(path)
    assert result.status == STATUS_UNSUPPORTED_SCHEMA


def test_missing_signing_key_in_registry_is_unverifiable(tmp_path):
    priv, _pub_hex = _keypair()
    doc = _base_capsule(keyRegistry=[])  # signing key not embedded at all
    zip_bytes = _build_zip(doc, priv)
    path = _write_temp_zip(tmp_path, zip_bytes)

    result = verify_capsule(path)
    assert result.status == STATUS_UNVERIFIABLE


def test_corrupt_file_is_unverifiable(tmp_path):
    path = tmp_path / "not-a-zip.zip"
    path.write_bytes(b"this is not a zip file")
    result = verify_capsule(str(path))
    assert result.status == STATUS_UNVERIFIABLE


def test_valid_chain_with_redaction_reports_valid_with_redactions(tmp_path):
    priv, pub_hex = _keypair()
    record = {
        "uuid": "r1",
        "workspaceId": "ws-test",
        "filePath": "f.py",
        "insertedCode": "",
        "modelName": "gpt-4o",
        "promptSha256": "committed-prompt-digest",
        "contentSha256": "committed-content-digest",
        "timestampIso": "2026-06-26T00:00:00+00:00",
        "prevHash": None,
        "recordHash": "some-frozen-hash-from-before-redaction",
        "lifecycleState": "redacted",
    }
    lifecycle_event = {
        "recordUuid": "r1",
        "eventType": "redaction",
        "signature": "irrelevant-for-this-check",
        "signatureValid": True,
        "contentCommitment": {
            "prompt_sha256": "committed-prompt-digest",
            "content_sha256": "committed-content-digest",
        },
    }
    doc = _base_capsule(
        recordChain=[record],
        lifecycleEvents=[lifecycle_event],
        keyRegistry=[{"publicKeyId": _KEY_ID, "publicKeyHex": pub_hex, "status": "active"}],
    )
    zip_bytes = _build_zip(doc, priv)
    path = _write_temp_zip(tmp_path, zip_bytes)

    result = verify_capsule(path)
    assert result.status == STATUS_VALID_WITH_REDACTIONS
    assert result.chain_ok is True


def test_redacted_record_without_valid_event_is_tampered(tmp_path):
    priv, pub_hex = _keypair()
    record = {
        "uuid": "r1",
        "workspaceId": "ws-test",
        "filePath": "f.py",
        "insertedCode": "",
        "modelName": "gpt-4o",
        "promptSha256": "committed-prompt-digest",
        "contentSha256": "committed-content-digest",
        "timestampIso": "2026-06-26T00:00:00+00:00",
        "prevHash": None,
        "recordHash": "some-frozen-hash",
        "lifecycleState": "redacted",
    }
    doc = _base_capsule(
        recordChain=[record],
        lifecycleEvents=[],  # no matching lifecycle event — the tamper signal
        keyRegistry=[{"publicKeyId": _KEY_ID, "publicKeyHex": pub_hex, "status": "active"}],
    )
    zip_bytes = _build_zip(doc, priv)
    path = _write_temp_zip(tmp_path, zip_bytes)

    result = verify_capsule(path)
    assert result.status == STATUS_TAMPERED
    assert result.chain_ok is False
