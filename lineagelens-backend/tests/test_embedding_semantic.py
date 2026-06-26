"""Tests for semantic-embedding honesty (PART 3 #18).

The default "hash" provider produces non-semantic vectors. The system must never
present hash-vector cosine search as semantic search; it reports
semantic_search_unavailable and falls back to keyword.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.embedding_service import (
    SEMANTIC_UNAVAILABLE_WARNING,
    generate_embedding,
    semantic_provider_active,
)


def _settings(**kw):
    base = dict(embedding_provider="hash", embedding_api_key=None, embedding_model_name="m")
    base.update(kw)
    return SimpleNamespace(**base)


def test_hash_provider_is_not_semantic():
    assert semantic_provider_active(_settings(embedding_provider="hash")) is False


def test_openai_without_key_is_not_semantic():
    assert semantic_provider_active(_settings(embedding_provider="openai", embedding_api_key="")) is False


def test_openai_with_key_is_semantic():
    assert semantic_provider_active(_settings(embedding_provider="openai", embedding_api_key="sk-x")) is True


def test_unknown_provider_is_not_semantic():
    assert semantic_provider_active(_settings(embedding_provider="madeup")) is False


def test_none_settings_is_not_semantic():
    assert semantic_provider_active(None) is False


def test_warning_names_the_state():
    assert "semantic_search_unavailable" in SEMANTIC_UNAVAILABLE_WARNING


def test_hash_embedding_is_deterministic_and_sized():
    v1 = asyncio.run(generate_embedding("hello world", 16, _settings()))
    v2 = asyncio.run(generate_embedding("hello world", 16, _settings()))
    assert v1 == v2
    assert len(v1) == 16
    # Different text → different vector (non-degenerate).
    v3 = asyncio.run(generate_embedding("totally different", 16, _settings()))
    assert v3 != v1
