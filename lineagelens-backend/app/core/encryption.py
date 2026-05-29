from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

_DERIVE_SALT = b"lineagelens-field-encryption-v1"
_ENCRYPTED_PREFIX = "enc:"


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """Build a Fernet instance from FIELD_ENCRYPTION_KEY or derive one from JWT_SECRET_KEY.

    The derivation is deterministic so existing ciphertext remains readable as long
    as the source secret does not change.  Set FIELD_ENCRYPTION_KEY explicitly in
    production to decouple field encryption from JWT rotation.
    """
    seed_str = os.environ.get("FIELD_ENCRYPTION_KEY", "").strip()
    if not seed_str:
        seed_str = os.environ.get("JWT_SECRET_KEY", "").strip()
    if not seed_str:
        raise RuntimeError(
            "FIELD_ENCRYPTION_KEY (or JWT_SECRET_KEY as fallback) must be set "
            "to enable encrypted field storage."
        )
    seed = seed_str.encode("utf-8")
    dk = hashlib.pbkdf2_hmac("sha256", seed, _DERIVE_SALT, iterations=100_000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(dk))


def encrypt_field(plaintext: str) -> str:
    """Encrypt *plaintext* and return a prefixed ciphertext string.

    Empty strings are returned as-is (no encryption overhead for absent fields).
    """
    if not plaintext:
        return plaintext
    ciphertext = _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _ENCRYPTED_PREFIX + ciphertext


def decrypt_field(value: str) -> str:
    """Decrypt a value produced by :func:`encrypt_field`.

    Values without the ``enc:`` prefix are returned unchanged for backward
    compatibility with legacy plaintext rows written before encryption was enabled.
    """
    if not value or not value.startswith(_ENCRYPTED_PREFIX):
        return value
    try:
        return _get_fernet().decrypt(value[len(_ENCRYPTED_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, Exception) as exc:
        raise ValueError(
            "Failed to decrypt field — the FIELD_ENCRYPTION_KEY may have changed."
        ) from exc
