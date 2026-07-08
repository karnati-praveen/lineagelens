from __future__ import annotations

"""Standalone offline verifier for LineageLens Evidence Capsules (PART 5 #52).

Deliberately has exactly one third-party dependency (cryptography, for
Ed25519 verify) and zero coupling to the LineageLens backend — no database,
no network call, no license, no shared secret. Everything it needs to verify
a capsule travels *inside* the capsule: the signed document, its manifest,
and the key registry snapshot at generation time.

The Ed25519 verify and hash-chain recomputation below are intentionally
re-implemented here rather than imported from the backend (small, ~10-20
lines each) — that duplication is what lets this tool keep working after
every other LineageLens service, and the company itself, is gone. A
round-trip test (tests/test_capsule_verifier_roundtrip.py, backend-side)
guards the two independent implementations from drifting apart.
"""

import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

VERIFIER_VERSION = "1.0.0"

# Bump alongside app.schemas.capsule.CAPSULE_SCHEMA_VERSION on the backend.
# A capsule whose capsuleSchemaVersion isn't in this set is reported
# `unsupported_schema`, never guessed at.
SUPPORTED_CAPSULE_SCHEMA_VERSIONS = frozenset({"1.0"})

# Mirrors the doc's Part 4 #41 states for an offline verifier.
STATUS_VALID = "valid"
STATUS_VALID_WITH_REDACTIONS = "valid_with_redactions"
STATUS_VALID_BUT_INCOMPLETE = "valid_but_incomplete"
STATUS_UNVERIFIABLE = "unverifiable"
STATUS_TAMPERED = "tampered"
STATUS_UNSUPPORTED_SCHEMA = "unsupported_schema"
STATUS_EXPIRED_OR_COMPROMISED_KEY = "expired_or_compromised_key"


@dataclass
class VerificationResult:
    status: str
    manifest_ok: bool = False
    signature_ok: bool = False
    chain_ok: bool = False
    key_trust_ok: bool = False
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "manifestOk": self.manifest_ok,
            "signatureOk": self.signature_ok,
            "chainOk": self.chain_ok,
            "keyTrustOk": self.key_trust_ok,
            "details": self.details,
        }


# ── Loading ────────────────────────────────────────────────────────────────

def load_capsule(path: str) -> tuple[dict, dict, str, str, dict[str, bytes]]:
    """Open a capsule zip. Returns (capsule_json, manifest_json, signature_hex,
    public_key_id, files) where files maps every zip member (except
    manifest.json itself) to its raw bytes for manifest verification."""
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(path, "r") as zf:
        for name in zf.namelist():
            files[name] = zf.read(name)

    if "capsule.json" not in files:
        raise ValueError("capsule.json missing from archive")
    if "capsule.json.sig" not in files:
        raise ValueError("capsule.json.sig missing from archive")
    if "manifest.json" not in files:
        raise ValueError("manifest.json missing from archive")

    capsule_json = json.loads(files["capsule.json"])
    sig_doc = json.loads(files["capsule.json.sig"])
    manifest_json = json.loads(files["manifest.json"])

    manifest_files = {k: v for k, v in files.items() if k != "manifest.json"}
    return capsule_json, manifest_json, sig_doc["signature"], sig_doc["publicKeyId"], manifest_files


# ── Manifest (content-addressed integrity) ──────────────────────────────────

def verify_manifest(files: dict[str, bytes], manifest: dict) -> tuple[bool, list[str]]:
    """Recompute SHA-256 of every manifest-listed file and compare."""
    problems: list[str] = []
    entries = manifest.get("entries", [])
    entry_paths = {e["path"] for e in entries}

    for entry in entries:
        content = files.get(entry["path"])
        if content is None:
            problems.append(f"manifest lists {entry['path']!r} but it is missing from the archive")
            continue
        actual_sha = hashlib.sha256(content).hexdigest()
        if actual_sha != entry["sha256"]:
            problems.append(
                f"{entry['path']!r} sha256 mismatch: manifest says {entry['sha256']}, actual {actual_sha}"
            )
        if len(content) != entry.get("sizeBytes"):
            problems.append(f"{entry['path']!r} size mismatch")

    for path in files:
        if path not in entry_paths:
            problems.append(f"{path!r} present in archive but not listed in manifest")

    return (len(problems) == 0, problems)


# ── Signature (Ed25519, re-implemented — see module docstring) ─────────────

def _canonical_json(obj: object) -> bytes:
    """Must match the backend's canonicalisation exactly: sort_keys, default=str,
    stdlib json.dumps defaults (same separators). See app.core.attestation and
    app.services.integrity_service on the backend side."""
    return json.dumps(obj, sort_keys=True, default=str).encode()


def verify_signature(capsule_json: dict, signature_hex: str, public_key_hex: str) -> bool:
    """Verify a detached Ed25519 signature over the canonical capsule JSON.
    Never raises — returns False on any malformed input."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pub.verify(bytes.fromhex(signature_hex), _canonical_json(capsule_json))
        return True
    except Exception:
        return False


# ── Key trust (uses the capsule's own embedded key registry — fully offline) ─

def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def verify_key_trust(capsule_json: dict, public_key_id: str) -> str:
    """Return the signing key's trust status *at the capsule's own generatedAt
    time*, using only the keyRegistry snapshot embedded in the capsule —
    mirrors app.core.attestation.key_status_at without any live registry call.

    Returns one of: valid | not_yet_valid | expired | compromised | retired |
    unknown_key.
    """
    registry = capsule_json.get("keyRegistry") or []
    record = next((r for r in registry if r.get("publicKeyId") == public_key_id), None)
    if record is None:
        return "unknown_key"

    if record.get("status") == "retired":
        return "retired"

    moment = _parse_ts(capsule_json.get("generatedAt")) or datetime.now(tz=UTC)
    compromised = _parse_ts(record.get("compromisedAt"))
    if compromised is not None and moment >= compromised:
        return "compromised"

    valid_from = _parse_ts(record.get("validFrom"))
    valid_until = _parse_ts(record.get("validUntil"))
    if valid_from is not None and moment < valid_from:
        return "not_yet_valid"
    if valid_until is not None and moment > valid_until:
        return "expired"
    return "valid"


# ── Hash-chain recomputation ─────────────────────────────────────────────────

def _compute_record_hash(
    *,
    record_uuid: str,
    workspace_id: str,
    file_path: str,
    inserted_code: str | None,
    model_name: str | None,
    prompt_sha256: str | None,
    timestamp_iso: str,
    prev_hash: str | None,
) -> str:
    """Re-implementation of app.services.integrity_service.compute_record_hash.
    Field set and canonicalisation must stay byte-identical to the backend."""
    canonical = json.dumps(
        {
            "uuid": record_uuid,
            "workspace_id": workspace_id,
            "file_path": file_path,
            "inserted_code": inserted_code or "",
            "model_name": model_name or "",
            "prompt_sha256": prompt_sha256 or "",
            "timestamp_iso": timestamp_iso,
            "prev_hash": prev_hash or "",
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def verify_chain(capsule_json: dict) -> tuple[bool, bool, list[str]]:
    """Recompute the recordChain hash linkage.

    Returns (chain_ok, has_redactions, problems). Mirrors the backend's
    /integrity/verify semantics: active records are recomputed directly;
    redacted/deleted records are trusted only if a validly-signed lifecycle
    event's content commitment matches the record's stored digests (the
    backend never re-derives a hash from scrubbed content — see PART 2 #10/#11).
    """
    records = capsule_json.get("recordChain") or []
    lifecycle_events = capsule_json.get("lifecycleEvents") or []
    problems: list[str] = []
    has_redactions = False
    expected_prev: str | None = None
    ok = True

    for rec in records:
        lifecycle_state = rec.get("lifecycleState", "active")

        if lifecycle_state in ("redacted", "deleted"):
            has_redactions = True
            expected_type = "deletion" if lifecycle_state == "deleted" else "redaction"
            matching = [
                e for e in lifecycle_events
                if e.get("recordUuid") == rec["uuid"] and e.get("eventType") == expected_type
            ]
            latest = matching[-1] if matching else None
            commitment = (latest or {}).get("contentCommitment") or {}
            valid = (
                latest is not None
                and latest.get("signatureValid") is True
                and commitment.get("prompt_sha256") == rec.get("promptSha256")
                and commitment.get("content_sha256") == rec.get("contentSha256")
            )
            if not valid:
                ok = False
                problems.append(
                    f"record {rec['uuid']} is {lifecycle_state} but has no valid matching "
                    f"lifecycle event — possible tampering"
                )
        else:
            expected_hash = _compute_record_hash(
                record_uuid=rec["uuid"],
                workspace_id=rec["workspaceId"],
                file_path=rec["filePath"],
                inserted_code=rec.get("insertedCode"),
                model_name=rec.get("modelName"),
                prompt_sha256=rec.get("promptSha256"),
                timestamp_iso=rec["timestampIso"],
                prev_hash=rec.get("prevHash"),
            )
            if expected_hash != rec.get("recordHash"):
                ok = False
                problems.append(f"record {rec['uuid']} hash mismatch — possible tampering")

        if rec.get("prevHash") != expected_prev:
            ok = False
            problems.append(f"record {rec['uuid']} chain break — prevHash does not link")

        expected_prev = rec.get("recordHash")

    return ok, has_redactions, problems


# ── Top-level orchestration ──────────────────────────────────────────────────

def verify_capsule(path: str) -> VerificationResult:
    """Verify a capsule zip end-to-end, fully offline. Never raises."""
    try:
        capsule_json, manifest_json, signature_hex, public_key_id, files = load_capsule(path)
    except Exception as exc:
        return VerificationResult(status=STATUS_UNVERIFIABLE, details=[f"failed to open capsule: {exc}"])

    schema_version = capsule_json.get("capsuleSchemaVersion")
    if schema_version not in SUPPORTED_CAPSULE_SCHEMA_VERSIONS:
        return VerificationResult(
            status=STATUS_UNSUPPORTED_SCHEMA,
            details=[f"capsuleSchemaVersion {schema_version!r} is not supported by verifier {VERIFIER_VERSION}"],
        )

    manifest_ok, manifest_problems = verify_manifest(files, manifest_json)

    public_key_hex = None
    for rec in capsule_json.get("keyRegistry") or []:
        if rec.get("publicKeyId") == public_key_id:
            public_key_hex = rec.get("publicKeyHex")
            break

    if public_key_hex is None:
        return VerificationResult(
            status=STATUS_UNVERIFIABLE,
            manifest_ok=manifest_ok,
            details=[*manifest_problems, f"signing key {public_key_id!r} not found in capsule's keyRegistry"],
        )

    signature_ok = verify_signature(capsule_json, signature_hex, public_key_hex)
    if not signature_ok:
        return VerificationResult(
            status=STATUS_TAMPERED,
            manifest_ok=manifest_ok,
            signature_ok=False,
            details=[*manifest_problems, "signature does not verify against capsule.json content"],
        )

    key_status = verify_key_trust(capsule_json, public_key_id)
    key_trust_ok = key_status == "valid"

    chain_ok, has_redactions, chain_problems = verify_chain(capsule_json)

    details = [*manifest_problems, *chain_problems]
    if not key_trust_ok:
        details.append(f"signing key trust status at generation time: {key_status}")

    if not manifest_ok or not chain_ok:
        status = STATUS_TAMPERED
    elif not key_trust_ok:
        status = STATUS_EXPIRED_OR_COMPROMISED_KEY
    elif has_redactions:
        status = STATUS_VALID_WITH_REDACTIONS
    else:
        status = STATUS_VALID

    return VerificationResult(
        status=status,
        manifest_ok=manifest_ok,
        signature_ok=signature_ok,
        chain_ok=chain_ok,
        key_trust_ok=key_trust_ok,
        details=details,
    )
