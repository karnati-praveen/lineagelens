"""Tests for integrity_service: hash chain correctness, tamper detection, HMAC signing."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789")

from app.services.integrity_service import (
    compute_prompt_sha256,
    compute_record_hash,
    sign_aibom,
    verify_aibom_signature,
)


# ── compute_prompt_sha256 ─────────────────────────────────────────────────────

def test_prompt_sha256_none_returns_none():
    assert compute_prompt_sha256(None) is None


def test_prompt_sha256_is_deterministic():
    msgs = [{"role": "user", "content": "add rate limiting"}]
    assert compute_prompt_sha256(msgs) == compute_prompt_sha256(msgs)


def test_prompt_sha256_different_prompts_differ():
    a = compute_prompt_sha256([{"role": "user", "content": "hello"}])
    b = compute_prompt_sha256([{"role": "user", "content": "world"}])
    assert a != b


def test_prompt_sha256_is_64_hex_chars():
    result = compute_prompt_sha256("simple string prompt")
    assert result is not None
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


# ── compute_record_hash ───────────────────────────────────────────────────────

_BASE_KWARGS = dict(
    record_uuid="11111111-1111-4111-8111-111111111111",
    workspace_id="ws-alpha",
    file_path="src/auth/handler.py",
    inserted_code="def login(): pass",
    model_name="claude-opus-4-5",
    prompt_sha256="abc123",
    timestamp_iso="2026-06-01T10:00:00+00:00",
    prev_hash=None,
)


def test_record_hash_is_deterministic():
    h1 = compute_record_hash(**_BASE_KWARGS)
    h2 = compute_record_hash(**_BASE_KWARGS)
    assert h1 == h2


def test_record_hash_is_64_hex_chars():
    h = compute_record_hash(**_BASE_KWARGS)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_record_hash_changes_when_inserted_code_changes():
    original = compute_record_hash(**_BASE_KWARGS)
    tampered = compute_record_hash(**{**_BASE_KWARGS, "inserted_code": "def login(): return True"})
    assert original != tampered


def test_record_hash_changes_when_file_path_changes():
    original = compute_record_hash(**_BASE_KWARGS)
    moved = compute_record_hash(**{**_BASE_KWARGS, "file_path": "src/api/handler.py"})
    assert original != moved


def test_record_hash_changes_when_prev_hash_changes():
    first = compute_record_hash(**_BASE_KWARGS)
    chained = compute_record_hash(**{**_BASE_KWARGS, "prev_hash": first})
    assert first != chained


def test_record_hash_none_fields_treated_as_empty_string():
    with_none = compute_record_hash(**{**_BASE_KWARGS, "inserted_code": None, "model_name": None})
    with_empty = compute_record_hash(**{**_BASE_KWARGS, "inserted_code": "", "model_name": ""})
    assert with_none == with_empty


# ── Chain linkage verification (unit, no DB) ──────────────────────────────────

def test_chain_links_correctly():
    """Simulate a 3-record chain and assert prev_hash propagation is correct."""
    hash_r1 = compute_record_hash(
        record_uuid="r1", workspace_id="ws", file_path="a.py",
        inserted_code="x=1", model_name="m", prompt_sha256=None,
        timestamp_iso="2026-06-01T10:00:00+00:00", prev_hash=None,
    )
    hash_r2 = compute_record_hash(
        record_uuid="r2", workspace_id="ws", file_path="b.py",
        inserted_code="y=2", model_name="m", prompt_sha256=None,
        timestamp_iso="2026-06-01T10:01:00+00:00", prev_hash=hash_r1,
    )
    hash_r3 = compute_record_hash(
        record_uuid="r3", workspace_id="ws", file_path="c.py",
        inserted_code="z=3", model_name="m", prompt_sha256=None,
        timestamp_iso="2026-06-01T10:02:00+00:00", prev_hash=hash_r2,
    )

    # Verify by recomputing
    assert compute_record_hash(
        record_uuid="r1", workspace_id="ws", file_path="a.py",
        inserted_code="x=1", model_name="m", prompt_sha256=None,
        timestamp_iso="2026-06-01T10:00:00+00:00", prev_hash=None,
    ) == hash_r1

    assert compute_record_hash(
        record_uuid="r2", workspace_id="ws", file_path="b.py",
        inserted_code="y=2", model_name="m", prompt_sha256=None,
        timestamp_iso="2026-06-01T10:01:00+00:00", prev_hash=hash_r1,
    ) == hash_r2

    assert compute_record_hash(
        record_uuid="r3", workspace_id="ws", file_path="c.py",
        inserted_code="z=3", model_name="m", prompt_sha256=None,
        timestamp_iso="2026-06-01T10:02:00+00:00", prev_hash=hash_r2,
    ) == hash_r3


def test_tampered_middle_record_breaks_chain():
    """Mutating r2's inserted_code means r2's stored hash no longer matches r3's prev_hash."""
    hash_r1 = compute_record_hash(
        record_uuid="r1", workspace_id="ws", file_path="a.py",
        inserted_code="x=1", model_name="m", prompt_sha256=None,
        timestamp_iso="2026-06-01T10:00:00+00:00", prev_hash=None,
    )
    # r2 as originally written
    hash_r2_original = compute_record_hash(
        record_uuid="r2", workspace_id="ws", file_path="b.py",
        inserted_code="y=2", model_name="m", prompt_sha256=None,
        timestamp_iso="2026-06-01T10:01:00+00:00", prev_hash=hash_r1,
    )
    # r3 was chained to original r2
    hash_r3 = compute_record_hash(
        record_uuid="r3", workspace_id="ws", file_path="c.py",
        inserted_code="z=3", model_name="m", prompt_sha256=None,
        timestamp_iso="2026-06-01T10:02:00+00:00", prev_hash=hash_r2_original,
    )

    # Simulate tamper: attacker changes r2's inserted_code in the DB
    hash_r2_tampered = compute_record_hash(
        record_uuid="r2", workspace_id="ws", file_path="b.py",
        inserted_code="INJECTED MALICIOUS CODE",
        model_name="m", prompt_sha256=None,
        timestamp_iso="2026-06-01T10:01:00+00:00", prev_hash=hash_r1,
    )
    assert hash_r2_tampered != hash_r2_original

    # r3's prev_hash no longer matches the tampered r2 hash — break detected
    r3_prev_would_be_tampered = hash_r2_tampered
    assert r3_prev_would_be_tampered != hash_r3  # r3 still references original


# ── HMAC signing ──────────────────────────────────────────────────────────────

def test_sign_aibom_returns_64_hex_chars():
    sig = sign_aibom('{"schema_version":"1.0"}')
    assert len(sig) == 64
    assert all(c in "0123456789abcdef" for c in sig)


def test_sign_aibom_is_deterministic():
    payload = '{"schema_version":"1.0","workspace_id":"ws"}'
    assert sign_aibom(payload) == sign_aibom(payload)


def test_verify_aibom_signature_valid():
    payload = '{"schema_version":"1.0","workspace_id":"ws-beta"}'
    sig = sign_aibom(payload)
    assert verify_aibom_signature(payload, sig) is True


def test_verify_aibom_signature_detects_mutation():
    payload = '{"schema_version":"1.0","workspace_id":"ws-beta"}'
    sig = sign_aibom(payload)
    mutated = '{"schema_version":"1.0","workspace_id":"ws-EVIL"}'
    assert verify_aibom_signature(mutated, sig) is False


def test_verify_aibom_signature_detects_wrong_sig():
    payload = '{"schema_version":"1.0"}'
    assert verify_aibom_signature(payload, "a" * 64) is False


# ── Solo-mode ingest skips hash chain ─────────────────────────────────────────

def test_solo_mode_skips_hash_chain(monkeypatch) -> None:
    """ingest_provenance_event must not call _attach_hash_chain when is_solo_mode=True."""
    import asyncio
    from typing import cast
    import app.services.provenance_service as ps
    import app.services.insights_service as insights_svc
    from app.core.config import Settings
    from app.core.security import AuthContext
    from app.services.ingest_normalizer import normalize_ingest_payload
    from sqlalchemy.ext.asyncio import AsyncSession

    class _Scalar:
        def all(self): return []

    class _Exec:
        def scalars(self): return _Scalar()
        def scalar_one_or_none(self): return None

    class _Session:
        """Fake AsyncSession double — only `add`/`execute` need real behavior for this test."""
        def __init__(self): self.added = []
        async def execute(self, _s): return _Exec()
        def add(self, r): self.added.append(r)
        async def flush(self): pass  # no-op: fake session has nothing to flush
        async def commit(self): pass  # no-op: fake session has nothing to commit
        async def rollback(self): pass  # no-op: fake session has nothing to roll back
        async def refresh(self, r): pass  # no-op: fake session has nothing to refresh from

    attach_calls = []

    async def _spy_attach(session, record):
        attach_calls.append(True)

    async def _fake_embedding(*a, **kw):
        return [0.0] * 256

    monkeypatch.setattr(ps, "_attach_hash_chain", _spy_attach)
    monkeypatch.setattr(ps, "generate_embedding", _fake_embedding)
    monkeypatch.setattr(insights_svc, "invalidate_insights_cache", lambda ws: None)

    solo_settings = Settings.model_validate({
        "APP_ENV": "test",
        "JWT_SECRET_KEY": "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789",
        "BACKEND_CORS_ORIGINS": "http://localhost:3000",
        "BACKEND_MODE": "solo",
    })

    payload = normalize_ingest_payload(
        {
            "id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            "timestampIso": "2026-06-01T10:00:00.000Z",
            "workspaceId": "ws-solo",
            "filePath": "src/demo.py",
            "insertedText": "x = 1",
        },
        workspace_id="ws-solo",
    )

    asyncio.run(
        ps.ingest_provenance_event(
            session=cast(AsyncSession, _Session()),
            payload=payload,
            auth=AuthContext(
                subject="proxy",
                workspace_id="ws-solo",
                scopes=set(),
                token_type="api_key",
                token_payload={},
            ),
            settings=solo_settings,
            neo4j_service=None,
        )
    )

    assert attach_calls == [], "hash chain must not be written in solo/Lite mode"
