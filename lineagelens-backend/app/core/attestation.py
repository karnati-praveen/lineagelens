from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

logger = logging.getLogger(__name__)

_DERIVE_SALT = b"lineagelens-attestation-signing-v1"
_ISSUER = "lineagelens-attestation"


@dataclass(frozen=True, slots=True)
class SignedAttestation:
    statement: dict
    signature: str  # hex-encoded 64-byte Ed25519 signature
    public_key_id: str  # short hex fingerprint of the 32-byte public key


@lru_cache(maxsize=1)
def _load_private_key() -> Ed25519PrivateKey:
    """Load the Ed25519 private key from ATTESTATION_SIGNING_KEY or derive from JWT_SECRET_KEY.

    In production ATTESTATION_SIGNING_KEY must be set explicitly (enforced by the
    Settings validator).  In development/test a deterministic key is derived from
    JWT_SECRET_KEY using a dedicated PBKDF2 salt so it never collides with other
    derived secrets.
    """
    raw_b64 = os.environ.get("ATTESTATION_SIGNING_KEY", "").strip()
    if raw_b64:
        try:
            seed = base64.b64decode(raw_b64)
            if len(seed) != 32:
                raise ValueError(f"Expected 32 raw bytes after base64 decode, got {len(seed)}")
            return Ed25519PrivateKey.from_private_bytes(seed)
        except Exception as exc:
            raise RuntimeError(f"ATTESTATION_SIGNING_KEY is invalid: {exc}") from exc

    jwt_secret = os.environ.get("JWT_SECRET_KEY", "").strip()
    if not jwt_secret:
        raise RuntimeError(
            "ATTESTATION_SIGNING_KEY (or JWT_SECRET_KEY as fallback) must be set "
            "to enable attestation signing."
        )
    logger.warning(
        "ATTESTATION_SIGNING_KEY is not set — deriving a deterministic dev key from "
        "JWT_SECRET_KEY. Set ATTESTATION_SIGNING_KEY explicitly in production."
    )
    seed = hashlib.pbkdf2_hmac(
        "sha256",
        jwt_secret.encode(),
        _DERIVE_SALT,
        iterations=100_000,
        dklen=32,
    )
    return Ed25519PrivateKey.from_private_bytes(seed)


def _get_public_key_id(private_key: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return hashlib.sha256(pub_bytes).hexdigest()[:16]


def get_public_key_hex() -> str:
    """Return the hex-encoded raw (32-byte) public key for external verification."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    pk = _load_private_key()
    return pk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def sign_detached(data: bytes) -> tuple[str, str]:
    """Sign arbitrary bytes with the Ed25519 attestation key.

    Returns (signature_hex, public_key_id). Used to add an asymmetric signature
    to artifacts (e.g. the AI-BOM) so a standalone tool holding only the public
    key can verify them — unlike an HMAC, which needs the shared secret
    (PART 1 #9).
    """
    private_key = _load_private_key()
    sig = private_key.sign(data)
    return sig.hex(), _get_public_key_id(private_key)


def verify_detached(data: bytes, signature_hex: str) -> bool:
    """Verify a detached Ed25519 signature over *data*. Returns False, never raises."""
    try:
        pub_key = _load_private_key().public_key()
        pub_key.verify(bytes.fromhex(signature_hex), data)
        return True
    except Exception:
        return False


def build_attestation(
    subject_type: str,
    subject_id: str,
    claims: dict,
    *,
    workspace_id: str,
    prev_hash: str | None = None,
) -> dict:
    """Build a canonical (unsigned) attestation statement.

    Keys are in alphabetical order so json.dumps(sort_keys=True) produces
    identical bytes regardless of call-site insertion order.
    """
    return {
        "claims": claims,
        "issued_at": datetime.now(tz=UTC).isoformat(),
        "issuer": _ISSUER,
        "prev_hash": prev_hash or "",
        "subject": {"id": subject_id, "type": subject_type},
        "workspace_id": workspace_id,
    }


def sign_attestation(statement: dict) -> SignedAttestation:
    """Sign *statement* with the loaded Ed25519 private key and return a SignedAttestation."""
    private_key = _load_private_key()
    canonical = json.dumps(statement, sort_keys=True, default=str).encode()
    sig_bytes = private_key.sign(canonical)
    return SignedAttestation(
        statement=statement,
        signature=sig_bytes.hex(),
        public_key_id=_get_public_key_id(private_key),
    )


def verify_attestation(signed: SignedAttestation) -> bool:
    """Verify the Ed25519 signature on *signed*. Returns False (never raises) on failure."""
    try:
        private_key = _load_private_key()
        pub_key = private_key.public_key()
        canonical = json.dumps(signed.statement, sort_keys=True, default=str).encode()
        sig_bytes = bytes.fromhex(signed.signature)
        pub_key.verify(sig_bytes, canonical)
        return True
    except Exception:
        return False
