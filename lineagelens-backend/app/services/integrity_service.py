from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any


def compute_prompt_sha256(prompt_messages: Any) -> str | None:
    """Return SHA-256 hex of the canonical prompt JSON, or None if absent.

    Stored in the AI-BOM instead of raw prompt content so the hash chain
    captures a fingerprint without embedding potentially sensitive prompts.
    """
    if prompt_messages is None:
        return None
    try:
        canonical = json.dumps(prompt_messages, sort_keys=True, default=str)
    except Exception:
        canonical = str(prompt_messages)
    return hashlib.sha256(canonical.encode()).hexdigest()


def compute_content_sha256(inserted_code: str | None) -> str | None:
    """Return SHA-256 hex of the inserted code, or None if absent.

    Stored as a content *commitment* at ingest time so the original code can be
    cryptographically attested even after it is scrubbed by a redaction or
    deletion tombstone — the verifier checks the commitment, not the (now
    removed) plaintext, and reports ``validly_redacted`` / ``validly_deleted``
    instead of ``tampered``.
    """
    if inserted_code is None:
        return None
    return hashlib.sha256(inserted_code.encode()).hexdigest()


def compute_record_hash(
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
    """Return the SHA-256 chain hash for one provenance record.

    Canonical fields are serialised as sorted-key JSON so the hash is
    deterministic and reproducible from the stored column values alone.
    Changing any of these fields in the DB will cause verify_chain() to
    report a break at that record — that is the intended tamper signal.
    """
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


def _derive_hmac_key() -> bytes:
    """Derive a 32-byte HMAC key from JWT_SECRET_KEY using a dedicated salt.

    Uses a different salt from encryption.py's Fernet derivation so
    AIBOM signing and field encryption are independent.

    The salt here is a fixed domain-separation constant, not a password salt —
    it derives from an already-high-entropy secret (JWT_SECRET_KEY), and
    sign_aibom()/verify_aibom_signature() must stay deterministic (a tested
    contract: see test_sign_aibom_is_deterministic) so a random per-call salt
    isn't compatible with this derivation the way it is for encrypt_field().
    """
    seed = os.environ.get("JWT_SECRET_KEY", "").strip()
    if not seed:
        raise RuntimeError(
            "JWT_SECRET_KEY must be set to enable AI-BOM signing."
        )
    return hashlib.pbkdf2_hmac(
        "sha256",
        seed.encode(),
        b"lineagelens-aibom-signing-v1",
        iterations=100_000,
        dklen=32,
    )


def sign_aibom(payload_json: str) -> str:
    """Return HMAC-SHA256 hex digest over the canonical AI-BOM JSON string."""
    key = _derive_hmac_key()
    return hmac.new(key, payload_json.encode(), hashlib.sha256).hexdigest()


def verify_aibom_signature(payload_json: str, signature: str) -> bool:
    """Constant-time comparison to verify an AI-BOM signature."""
    key = _derive_hmac_key()
    expected = hmac.new(key, payload_json.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
