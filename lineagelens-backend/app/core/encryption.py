from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

_LEGACY_SALT = b"lineagelens-field-encryption-v1"
_LEGACY_PREFIX = "enc:"
_ENCRYPTED_PREFIX = "enc2:"
_SALT_LEN = 16


def _seed() -> bytes:
    seed_str = os.environ.get("FIELD_ENCRYPTION_KEY", "").strip()
    if not seed_str:
        seed_str = os.environ.get("JWT_SECRET_KEY", "").strip()
    if not seed_str:
        raise RuntimeError(
            "FIELD_ENCRYPTION_KEY (or JWT_SECRET_KEY as fallback) must be set "
            "to enable encrypted field storage."
        )
    return seed_str.encode("utf-8")


def _derive_fernet(salt: bytes) -> Fernet:
    dk = hashlib.pbkdf2_hmac("sha256", _seed(), salt, iterations=100_000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(dk))


@lru_cache(maxsize=1)
def _get_legacy_fernet() -> Fernet:
    """Fixed-salt Fernet kept only to decrypt ciphertext written before per-value salts."""
    return _derive_fernet(_LEGACY_SALT)


def encrypt_field(plaintext: str) -> str:
    """Encrypt *plaintext* and return a prefixed ciphertext string.

    Each call derives the Fernet key with a fresh random salt so identical
    plaintexts never produce identical ciphertext and the key-derivation salt
    isn't a fixed, predictable value. The salt travels with the ciphertext
    since it isn't secret — only the seed (FIELD_ENCRYPTION_KEY/JWT_SECRET_KEY)
    needs to stay secret.

    Empty strings are returned as-is (no encryption overhead for absent fields).
    """
    if not plaintext:
        return plaintext
    salt = os.urandom(_SALT_LEN)
    ciphertext = _derive_fernet(salt).encrypt(plaintext.encode("utf-8")).decode("ascii")
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii")
    return f"{_ENCRYPTED_PREFIX}{salt_b64}:{ciphertext}"


def decrypt_field(value: str) -> str:
    """Decrypt a value produced by :func:`encrypt_field`.

    Handles both the current per-value-salt format (``enc2:``) and the legacy
    fixed-salt format (``enc:``) so previously encrypted rows keep decrypting
    without a data migration. Values without either prefix are returned
    unchanged for backward compatibility with legacy plaintext rows written
    before encryption was enabled.
    """
    if not value:
        return value
    try:
        if value.startswith(_ENCRYPTED_PREFIX):
            salt_b64, _, ciphertext = value[len(_ENCRYPTED_PREFIX):].partition(":")
            salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
            return _derive_fernet(salt).decrypt(ciphertext.encode("ascii")).decode("utf-8")
        if value.startswith(_LEGACY_PREFIX):
            return _get_legacy_fernet().decrypt(value[len(_LEGACY_PREFIX):].encode("ascii")).decode("utf-8")
        return value
    except (InvalidToken, Exception) as exc:
        raise ValueError(
            "Failed to decrypt field — the FIELD_ENCRYPTION_KEY may have changed."
        ) from exc
