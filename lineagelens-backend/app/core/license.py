"""Offline license verification for LineageLens paid tiers.

A license key is an Ed25519-signed statement minted by the *vendor* (the LineageLens
maintainer) at purchase time and verified *offline* by the customer's backend. No
license server, no phone-home — which is exactly what an air-gapped enterprise needs.

    license key = base64url(canonical_payload) "." base64url(signature)

The signing PRIVATE key is held only by the vendor and never ships. The backend ships
with the vendor PUBLIC key embedded in ``LICENSE_PUBLIC_KEY_HEX`` (overridable in dev via
``LINEAGELENS_LICENSE_PUBLIC_KEY``). Because verification uses a *dedicated vendor key* —
not the per-customer attestation key derived from ``JWT_SECRET_KEY`` — a customer cannot
mint their own license.

Verification never trusts ``BACKEND_MODE`` for entitlement. ``BACKEND_MODE`` describes the
deployed *infrastructure* (sqlite / postgres / +neo4j); the *plan* a customer is entitled
to comes from the signed license. The effective plan is the lesser of the two
(:func:`effective_plan`): a Max license on a SQLite box still cannot do vector search.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

logger = logging.getLogger(__name__)

# Vendor license-signing PUBLIC key: hex of the raw 32-byte Ed25519 public key.
# The matching PRIVATE key is held offline by the vendor and MUST NEVER be committed
# or shipped. Generate a real keypair with:
#     python lineagelens-scripts/mint_license.py keygen
# then paste the public key below (or set LINEAGELENS_LICENSE_PUBLIC_KEY at runtime).
# While this is the placeholder, all licenses are rejected and the backend runs free
# (Lite) — that is the safe default before any keypair has been provisioned.
LICENSE_PUBLIC_KEY_HEX = "0" * 64

_PLACEHOLDER_PUBLIC_KEY = "0" * 64
_ISSUER = "lineagelens-license"

# Plan ordering. Higher number = more capability.
PLAN_RANK: dict[str, int] = {"lite": 0, "plus": 1, "max": 2}
FREE_PLAN = "lite"

# What each BACKEND_MODE can physically support. A license can never grant more than
# the deployed infrastructure can actually run.
_MODE_CEILING: dict[str, str] = {"solo": "lite", "team": "plus", "enterprise": "max"}


@dataclass(frozen=True, slots=True)
class Entitlement:
    """The verified result of a license check (or the free default)."""

    plan: str  # lite | plus | max
    seats: int  # 0 == unlimited
    customer: str  # display name / id; "" for the free tier
    expires: str | None  # ISO date (YYYY-MM-DD) or None for perpetual
    licensed: bool  # True only when a valid signed license produced this
    reason: str = ""  # diagnostic note when unlicensed/invalid

    def allows(self, min_plan: str) -> bool:
        """True if this entitlement's plan meets or exceeds *min_plan* (ignores topology)."""
        return PLAN_RANK.get(self.plan, -1) >= PLAN_RANK.get(min_plan, 99)


FREE_ENTITLEMENT = Entitlement(
    plan=FREE_PLAN,
    seats=1,
    customer="",
    expires=None,
    licensed=False,
    reason="no license configured",
)


# ── base64url helpers (no padding, URL-safe) ──────────────────────────────────


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _canonical(statement: dict) -> bytes:
    """Deterministic bytes for signing/verification (sorted keys, compact separators)."""
    return json.dumps(statement, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ── minting (vendor side — useless without the private key) ────────────────────


def issue_license(
    *,
    plan: str,
    private_key_b64: str,
    seats: int = 0,
    customer: str = "",
    expires: str | None = None,
    issued: date | None = None,
) -> str:
    """Mint a signed license key. Requires the vendor's private key seed (base64, 32 bytes).

    *expires* is an ISO date string (``YYYY-MM-DD``) or None for a perpetual license.
    """
    if plan not in PLAN_RANK:
        raise ValueError(f"plan must be one of {sorted(PLAN_RANK)}, got {plan!r}")
    if expires is not None:
        # Fail fast on a malformed date rather than minting an unverifiable key.
        date.fromisoformat(expires)

    seed = base64.b64decode(private_key_b64)
    if len(seed) != 32:
        raise ValueError(f"private key seed must be 32 bytes, got {len(seed)}")
    private_key = Ed25519PrivateKey.from_private_bytes(seed)

    statement = {
        "customer": customer,
        "exp": expires,
        "iat": (issued or datetime.now(tz=UTC).date()).isoformat(),
        "iss": _ISSUER,
        "plan": plan,
        "seats": int(seats),
    }
    payload = _canonical(statement)
    signature = private_key.sign(payload)
    return f"{_b64url_encode(payload)}.{_b64url_encode(signature)}"


def public_key_hex_for(private_key_b64: str) -> str:
    """Return the hex public key for a private seed — used by the keygen tool."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    seed = base64.b64decode(private_key_b64)
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    return private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


# ── verification (customer side — ships in the backend) ────────────────────────


def _active_public_key_hex() -> str:
    return (os.environ.get("LINEAGELENS_LICENSE_PUBLIC_KEY") or LICENSE_PUBLIC_KEY_HEX).strip()


def _free(reason: str) -> Entitlement:
    return Entitlement(
        plan=FREE_PLAN, seats=1, customer="", expires=None, licensed=False, reason=reason
    )


def verify_license(
    key: str,
    *,
    public_key_hex: str | None = None,
    today: date | None = None,
) -> Entitlement:
    """Verify *key* against the vendor public key. Never raises — returns the free tier
    with a diagnostic ``reason`` on any failure (bad format, bad signature, expired)."""
    pub_hex = (public_key_hex or _active_public_key_hex()).strip()
    if not pub_hex or pub_hex == _PLACEHOLDER_PUBLIC_KEY:
        return _free("licensing not configured (public key is a placeholder)")

    raw = (key or "").strip()
    if raw.count(".") != 1:
        return _free("malformed license key")
    payload_b64, sig_b64 = raw.split(".", 1)

    try:
        payload_bytes = _b64url_decode(payload_b64)
        signature = _b64url_decode(sig_b64)
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        public_key.verify(signature, payload_bytes)  # raises on mismatch
        statement = json.loads(payload_bytes)
    except Exception:
        return _free("invalid license signature")

    if statement.get("iss") != _ISSUER:
        return _free("unknown license issuer")

    plan = statement.get("plan")
    if plan not in PLAN_RANK:
        return _free(f"unknown plan {plan!r}")

    expires = statement.get("exp")
    if expires:
        try:
            exp_date = date.fromisoformat(expires)
        except (TypeError, ValueError):
            return _free("malformed expiry date")
        if (today or datetime.now(tz=UTC).date()) > exp_date:
            # Expired licenses gracefully degrade to the free tier (renewal restores it).
            return _free(f"license expired on {expires}")

    return Entitlement(
        plan=plan,
        seats=int(statement.get("seats", 0)),
        customer=str(statement.get("customer", "")),
        expires=expires,
        licensed=True,
        reason="",
    )


@lru_cache(maxsize=1)
def load_entitlement() -> Entitlement:
    """Load and verify the active license from the environment (cached).

    Reads ``LINEAGELENS_LICENSE`` (the key itself) or ``LINEAGELENS_LICENSE_FILE``
    (a path to a file containing the key). Call ``load_entitlement.cache_clear()``
    after changing the environment (tests do this).
    """
    key = os.environ.get("LINEAGELENS_LICENSE", "").strip()
    if not key:
        path = os.environ.get("LINEAGELENS_LICENSE_FILE", "").strip()
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    key = handle.read().strip()
            except OSError as exc:
                logger.warning("Could not read LINEAGELENS_LICENSE_FILE %s: %s", path, exc)
    if not key:
        return FREE_ENTITLEMENT

    entitlement = verify_license(key)
    if not entitlement.licensed:
        logger.warning("LineageLens license present but not valid: %s", entitlement.reason)
    else:
        logger.info(
            "LineageLens %s license active (customer=%s, expires=%s)",
            entitlement.plan,
            entitlement.customer or "n/a",
            entitlement.expires or "perpetual",
        )
    return entitlement


def effective_plan(entitlement: Entitlement, backend_mode: str) -> str:
    """The lesser of the licensed plan and what the deployed infrastructure can run."""
    ceiling = _MODE_CEILING.get(backend_mode, "plus")
    if PLAN_RANK.get(entitlement.plan, 0) <= PLAN_RANK[ceiling]:
        return entitlement.plan
    return ceiling
