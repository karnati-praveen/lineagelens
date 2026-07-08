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


def get_current_public_key_id() -> str:
    """Return the public_key_id of the currently active signing key."""
    return _get_public_key_id(_load_private_key())


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


# ── Key registry (PART 3 #19) ─────────────────────────────────────────────────
#
# verify_attestation used to validate only with the single currently-loaded key,
# so historical attestations signed by a rotated key could not be verified and a
# compromised key was still trusted. The registry adds:
#   * multi-key lookup by public_key_id (historical verification),
#   * validity windows (valid_from / valid_until),
#   * compromise/revocation timestamps so a signature made AFTER compromise (or
#     after a key was retired) is rejected even though the math checks out.
#
# Sourced from ATTESTATION_KEY_REGISTRY (JSON list); the current active key is
# always included automatically.


@dataclass(frozen=True, slots=True)
class KeyRecord:
    public_key_id: str
    public_key_hex: str
    valid_from: str | None = None
    valid_until: str | None = None
    compromised_at: str | None = None
    status: str = "active"  # active | retired | compromised


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@lru_cache(maxsize=1)
def _registry() -> dict[str, KeyRecord]:
    """Public-key registry keyed by public_key_id (env + current active key)."""
    records: dict[str, KeyRecord] = {}
    raw = os.environ.get("ATTESTATION_KEY_REGISTRY", "").strip()
    if raw:
        try:
            for entry in json.loads(raw):
                rec = KeyRecord(
                    public_key_id=str(entry["publicKeyId"]),
                    public_key_hex=str(entry["publicKeyHex"]),
                    valid_from=entry.get("validFrom"),
                    valid_until=entry.get("validUntil"),
                    compromised_at=entry.get("compromisedAt"),
                    status=entry.get("status", "active"),
                )
                records[rec.public_key_id] = rec
        except Exception as exc:
            logger.error("Failed to parse ATTESTATION_KEY_REGISTRY: %s", exc)
    # Always include the currently-loaded key as active (unless overridden above).
    try:
        pk = _load_private_key()
        cur_id = _get_public_key_id(pk)
        records.setdefault(
            cur_id,
            KeyRecord(public_key_id=cur_id, public_key_hex=get_public_key_hex(), status="active"),
        )
    except Exception:
        pass
    return records


def _registry_clear_cache() -> None:
    _registry.cache_clear()


def key_status_at(record: KeyRecord, at: datetime | None) -> str:
    """Return the key's trust status at time *at*: valid | not_yet_valid | expired | compromised | retired."""
    if record.status == "retired":
        return "retired"
    compromised = _parse_ts(record.compromised_at)
    moment = at or datetime.now(tz=UTC)
    if compromised is not None and moment >= compromised:
        return "compromised"
    vf = _parse_ts(record.valid_from)
    vu = _parse_ts(record.valid_until)
    if vf is not None and moment < vf:
        return "not_yet_valid"
    if vu is not None and moment > vu:
        return "expired"
    return "valid"


def verify_attestation_detailed(
    signed: SignedAttestation,
    *,
    at: datetime | None = None,
    registry_override: dict[str, KeyRecord] | None = None,
) -> dict:
    """Verify signature + key trust state. Returns a dict; never raises.

    keys: valid (bool), signatureValid (bool), keyStatus, publicKeyId, reason.
    *at* defaults to the statement's issued_at (so historical attestations are
    judged against the key state at signing time), else now.
    *registry_override*, if given, is used instead of the env-based registry —
    callers that maintain a DB-backed registry (PART 5 #57) load it themselves
    (async) and pass the dict in, keeping this function sync/pure/testable.
    """
    canonical = json.dumps(signed.statement, sort_keys=True, default=str).encode()
    moment = at or _parse_ts((signed.statement or {}).get("issued_at"))

    registry = registry_override if registry_override is not None else _registry()
    record = registry.get(signed.public_key_id)

    # Resolve the public key: registry entry, else the currently-loaded key
    # (back-compat for attestations made before a registry was configured).
    if record is not None:
        pub_hex = record.public_key_hex
    else:
        try:
            pub_hex = get_public_key_hex()
        except Exception:
            return {"valid": False, "signatureValid": False, "keyStatus": "unknown_key",
                    "publicKeyId": signed.public_key_id, "reason": "no key available"}

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        pub.verify(bytes.fromhex(signed.signature), canonical)
        signature_valid = True
    except Exception:
        signature_valid = False

    if not signature_valid:
        return {"valid": False, "signatureValid": False, "keyStatus": "n/a",
                "publicKeyId": signed.public_key_id, "reason": "signature mismatch"}

    status = key_status_at(record, moment) if record is not None else "valid"
    valid = status == "valid"
    return {
        "valid": valid,
        "signatureValid": True,
        "keyStatus": status,
        "publicKeyId": signed.public_key_id,
        "reason": "" if valid else f"key status: {status}",
    }


def verify_attestation(signed: SignedAttestation, *, at: datetime | None = None) -> bool:
    """Verify the Ed25519 signature *and* key trust state. False (never raises) on failure.

    Backward compatible: with no registry configured, this validates against the
    current key exactly as before.
    """
    return verify_attestation_detailed(signed, at=at)["valid"]


# ── DB-backed key registry (PART 5 #57) ───────────────────────────────────────
#
# The env-based registry above requires a redeploy to revoke a compromised key.
# These functions add a mutable, DB-backed registry so an admin can revoke or
# register a key at runtime. The env registry is still consulted (as a
# fallback / for air-gapped deployments with no admin API access) — DB entries
# take precedence when both exist.

def _key_record_from_row(row: object) -> KeyRecord:
    return KeyRecord(
        public_key_id=row.public_key_id,
        public_key_hex=row.public_key_hex,
        valid_from=row.valid_from.isoformat() if row.valid_from else None,
        valid_until=row.valid_until.isoformat() if row.valid_until else None,
        compromised_at=row.compromised_at.isoformat() if row.compromised_at else None,
        status=row.status,
    )


async def load_registry_from_db(session) -> dict[str, KeyRecord]:
    """Return the merged (DB + env + current key) registry.

    DB rows take precedence over an env entry with the same public_key_id
    since the DB is the mutable source of truth for runtime revocation.
    """
    from sqlalchemy import select

    from app.db.models import AttestationKey

    merged = dict(_registry())
    result = await session.execute(select(AttestationKey))
    for row in result.scalars().all():
        merged[row.public_key_id] = _key_record_from_row(row)
    return merged


async def register_key(
    session,
    *,
    public_key_id: str,
    public_key_hex: str,
    valid_from: datetime | None = None,
    label: str | None = None,
) -> "AttestationKey":  # noqa: F821 - forward ref, imported lazily below
    """Register a new signing key for rotation. Returns the created row."""
    from app.db.models import AttestationKey

    row = AttestationKey(
        public_key_id=public_key_id,
        public_key_hex=public_key_hex,
        valid_from=valid_from,
        label=label,
        status="active",
    )
    session.add(row)
    await session.flush()
    return row


async def revoke_key(
    session,
    public_key_id: str,
    *,
    reason: str,
    revoked_by: str,
) -> "AttestationKey":  # noqa: F821
    """Mark a key compromised at the current time. Raises ValueError if unknown.

    Any signature made after this timestamp will be rejected by
    verify_attestation_detailed even though the cryptographic math checks out —
    that is the point of a runtime-revocable registry.
    """
    from sqlalchemy import select

    from app.db.models import AttestationKey

    result = await session.execute(
        select(AttestationKey).where(AttestationKey.public_key_id == public_key_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Unknown public_key_id: {public_key_id!r}")

    row.status = "compromised"
    row.compromised_at = datetime.now(tz=UTC)
    row.revocation_reason = reason
    row.revoked_by = revoked_by
    await session.flush()
    return row


# ── Customer-controlled countersignature (PART 5 #57, partial) ───────────────
#
# A customer can hold their own Ed25519 keypair out of band and countersign a
# LineageLens statement to add an independent trust root. This is a generic
# verify helper only — it does not manage, store, or rotate customer keys
# (that would require KMS/HSM integration, scoped as a follow-up; see
# SECURITY_NOTES.md).

def verify_customer_countersignature(
    payload_canonical: bytes,
    customer_public_key_hex: str,
    customer_signature_hex: str,
) -> bool:
    """Verify a customer-supplied Ed25519 countersignature. False, never raises."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(customer_public_key_hex))
        pub.verify(bytes.fromhex(customer_signature_hex), payload_canonical)
        return True
    except Exception:
        return False
