"""Easy-connect pairing codes (roadmap: easy connection).

A workspace admin generates a short-lived, single-use code from the dashboard
(/connect page) and hands it to a developer. The developer pastes it into the
VS Code extension's "Connect to Server" command; the extension redeems it for
a scoped API key and is fully configured — no manual URL/token/workspace
settings.

Security model:
  - Codes are Crockford-base32, format LL-XXXXX-XXXXX (10 chars of entropy,
    ~2^50 keyspace), hashed with SHA-256 at rest — a DB dump does not leak
    redeemable codes. Unmatched guesses attribute to no row; brute force is
    contained by the keyspace plus the global per-IP rate limiter on
    /connect/redeem. `attempts` counts failed redemptions of a matched row
    (revoked/expired/exhausted retries) as a diagnostic signal.
  - The /connect/redeem endpoint is unauthenticated but strictly rate-limited
    (wired in main.py like /auth/login).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.db.models import ApiKey, PairingCode

CODE_PREFIX = "LL-"
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32 (no I, L, O, U)
_CODE_BODY_LEN = 10
MAX_TTL_MINUTES = 120
MAX_USES = 25

ALLOWED_CLIENTS = {"vscode-power", "vscode-base", "mcp", "cli"}
CLIENT_SCOPE_PRESETS: dict[str, list[str]] = {
    "vscode-power": ["read", "write", "provenance:read", "provenance:write"],
    "vscode-base": ["provenance:write"],
    "mcp": ["provenance:read"],
    "cli": ["read", "write", "provenance:read", "provenance:write"],
}


class PairingError(Exception):
    """Raised for invalid/expired/exhausted/revoked codes."""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _as_utc(dt: datetime) -> datetime:
    """SQLite-backed DateTime columns round-trip naive; treat stored values as UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def generate_pairing_code() -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_BODY_LEN))
    return f"{CODE_PREFIX}{body[:5]}-{body[5:]}"


def normalize_code(raw: str) -> str:
    """Normalize user input: uppercase, strip separators, map confusables."""
    cleaned = (raw or "").strip().upper().replace("-", "").replace(" ", "")
    if cleaned.startswith("LL"):
        cleaned = cleaned[2:]
    elif cleaned.startswith(CODE_PREFIX):
        cleaned = cleaned[len(CODE_PREFIX):]
    cleaned = cleaned.replace("I", "1").replace("L", "1").replace("O", "0").replace("U", "V")
    if len(cleaned) != _CODE_BODY_LEN or any(c not in _ALPHABET for c in cleaned):
        raise PairingError("Invalid pairing code format.")
    return f"{CODE_PREFIX}{cleaned[:5]}-{cleaned[5:]}"


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def scopes_for_client(client: str) -> list[str]:
    return list(CLIENT_SCOPE_PRESETS.get(client, ["read"]))


async def create_pairing_code(
    session: AsyncSession,
    *,
    workspace_id: str,
    created_by: str,
    client: str,
    label: str | None = None,
    ttl_minutes: int = 10,
    max_uses: int = 1,
) -> tuple[PairingCode, str]:
    """Create a pairing code. Returns (row, plaintext_code); plaintext shown once."""
    if client not in ALLOWED_CLIENTS:
        raise PairingError(f"Unknown client type. Allowed: {sorted(ALLOWED_CLIENTS)}")
    if not 1 <= ttl_minutes <= MAX_TTL_MINUTES:
        raise PairingError(f"ttlMinutes must be 1..{MAX_TTL_MINUTES}.")
    if not 1 <= max_uses <= MAX_USES:
        raise PairingError(f"maxUses must be 1..{MAX_USES}.")

    code = generate_pairing_code()
    row = PairingCode(
        workspace_id=workspace_id,
        created_by=created_by,
        client=client,
        code_hash=hash_code(code),
        label=label,
        scopes=scopes_for_client(client),
        max_uses=max_uses,
        use_count=0,
        attempts=0,
        expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=created_by,
        action="pairing.code_created",
        target_uuid=str(row.id),
        details={"client": client, "label": label, "ttlMinutes": ttl_minutes, "maxUses": max_uses},
    )
    return row, code


async def redeem_pairing_code(
    session: AsyncSession,
    *,
    raw_code: str,
    client: str,
    label: str | None = None,
) -> tuple[ApiKey, PairingCode, str]:
    """Validate a code and mint a scoped API key. Returns (api_key, pairing_row, plaintext_key)."""
    if client not in ALLOWED_CLIENTS:
        raise PairingError(f"Unknown client type. Allowed: {sorted(ALLOWED_CLIENTS)}")

    try:
        code = normalize_code(raw_code)
    except PairingError:
        raise PairingError("Invalid pairing code format.") from None

    result = await session.execute(select(PairingCode).where(PairingCode.code_hash == hash_code(code)))
    pairing = result.scalar_one_or_none()

    if pairing is None:
        raise PairingError("Invalid pairing code.", status_code=404)

    async def _record_failed_attempt(message: str, *, status_code: int = 410) -> None:
        pairing.attempts += 1
        if pairing.attempts >= 5 and pairing.revoked_at is None:
            pairing.revoked_at = datetime.now(UTC)
            await log_audit_event(
                session,
                workspace_id=pairing.workspace_id,
                user_id=None,
                action="pairing.code_revoked",
                target_uuid=str(pairing.id),
                details={"reason": "max_attempts_exceeded", "attempts": pairing.attempts},
            )
            await session.commit()
            raise PairingError("This pairing code has been locked out after too many attempts.", status_code=410)
        await session.commit()
        raise PairingError(message, status_code=status_code)

    if pairing.revoked_at is not None:
        await _record_failed_attempt("This pairing code has been revoked.", status_code=410)
    now = datetime.now(UTC)
    if _as_utc(pairing.expires_at) <= now:
        await _record_failed_attempt("This pairing code has expired.", status_code=410)
    if pairing.use_count >= pairing.max_uses:
        await _record_failed_attempt("This pairing code has already been used.", status_code=410)

    scopes = scopes_for_client(client)
    key_name = f"pairing:{pairing.client}:{label or pairing.label or pairing.id}"
    raw_key = secrets.token_urlsafe(32)
    full_key = "llk_" + raw_key
    ak = ApiKey(
        workspace_id=pairing.workspace_id,
        user_id=pairing.created_by,
        name=key_name[:256],
        key_prefix=full_key[:12],
        key_hash=hashlib.sha256(full_key.encode()).hexdigest(),
        scopes=scopes,
        is_active=True,
    )
    session.add(ak)

    pairing.use_count += 1
    pairing.last_redeemed_label = label

    await log_audit_event(
        session,
        workspace_id=pairing.workspace_id,
        user_id=None,
        action="pairing.code_redeemed",
        target_uuid=str(pairing.id),
        details={"client": client, "label": label, "apiKeyId": str(ak.id)},
    )
    await session.commit()
    await session.refresh(ak)
    return ak, pairing, full_key


async def revoke_pairing_code(
    session: AsyncSession,
    *,
    pairing_id: str,
    workspace_id: str,
    revoked_by: str,
) -> PairingCode:
    import uuid as uuid_pkg

    try:
        pid = uuid_pkg.UUID(pairing_id)
    except ValueError as exc:
        raise PairingError("Invalid pairing code ID.") from exc

    result = await session.execute(
        select(PairingCode).where(PairingCode.id == pid, PairingCode.workspace_id == workspace_id)
    )
    pairing = result.scalar_one_or_none()
    if pairing is None:
        raise PairingError("Pairing code not found.", status_code=404)
    if pairing.revoked_at is None:
        pairing.revoked_at = datetime.now(UTC)
        await log_audit_event(
            session,
            workspace_id=workspace_id,
            user_id=revoked_by,
            action="pairing.code_revoked",
            target_uuid=str(pairing.id),
            details=None,
        )
    await session.commit()
    return pairing
