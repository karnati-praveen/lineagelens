"""Tests for multi-provider routing in the LineageLens proxy.

Verifies that a single proxy correctly routes requests from different CLI tools
to the appropriate upstream provider:
  - Claude Code CLI  → Anthropic  (/v1/messages, anthropic-version header)
  - Codex CLI        → OpenAI     (/v1/chat/completions, /v1/responses)
  - Gemini CLI       → Google     (/v1beta/models/…:generateContent)

These tests are logic-level (no network), covering:
  1. `detect_provider_from_inbound` path/header detection
  2. `_get_provider_upstream_base` provider → URL resolution
  3. `_build_upstream_url_for_provider` URL construction
  4. 10-user concurrent simulation with three providers mixed

Run with:
    cd lineagelens-proxy && pytest test_provider_routing.py -v
"""
from __future__ import annotations

import os
import sys

# Provide dummy env vars so proxy.py imports cleanly without a real backend
os.environ.setdefault("UPSTREAM_URL",             "https://api.anthropic.com")
os.environ.setdefault("ANTHROPIC_UPSTREAM_URL",   "https://api.anthropic.com")
os.environ.setdefault("OPENAI_UPSTREAM_URL",      "https://api.openai.com")
os.environ.setdefault("GEMINI_UPSTREAM_URL",      "https://generativelanguage.googleapis.com")
os.environ.setdefault("BACKEND_URL",              "http://127.0.0.1:19998")
os.environ.setdefault("BACKEND_INGEST_TOKEN",     "test-token")
os.environ.setdefault("PROXY_WORKSPACE_ID",       "ws-test")

sys.path.insert(0, ".")

import importlib
import proxy as _proxy_mod

# Always re-import after env is set
importlib.reload(_proxy_mod)

from proxy import (  # noqa: E402 — import after env setup
    detect_provider_from_inbound,
    _get_provider_upstream_base,
    _build_upstream_url_for_provider,
    _ANTHROPIC_UPSTREAM_URL,
    _OPENAI_UPSTREAM_URL,
    _GEMINI_UPSTREAM_URL,
)


# ── 1. detect_provider_from_inbound — Anthropic ──────────────────────────────

def test_anthropic_messages_path():
    """Claude Code CLI uses /v1/messages path."""
    assert detect_provider_from_inbound("/v1/messages", {}) == "anthropic"


def test_anthropic_version_header():
    """Any path with anthropic-version header → anthropic."""
    assert detect_provider_from_inbound("/some/path", {"anthropic-version": "2023-06-01"}) == "anthropic"


def test_anthropic_explicit_header():
    """x-api-key-provider: anthropic header → anthropic."""
    assert detect_provider_from_inbound("/v1/something", {"x-api-key-provider": "anthropic"}) == "anthropic"


# ── 2. detect_provider_from_inbound — OpenAI ─────────────────────────────────

def test_openai_chat_completions_path():
    """Codex CLI and most OpenAI SDKs use /v1/chat/completions."""
    assert detect_provider_from_inbound("/v1/chat/completions", {}) == "openai"


def test_openai_responses_path():
    """Codex CLI 'responses' API endpoint."""
    assert detect_provider_from_inbound("/v1/responses", {}) == "openai"


def test_openai_embeddings_path():
    """Embedding requests go to /v1/embeddings."""
    assert detect_provider_from_inbound("/v1/embeddings", {}) == "openai"


def test_openai_completions_path():
    """Legacy completions endpoint."""
    assert detect_provider_from_inbound("/v1/completions", {}) == "openai"


# ── 3. detect_provider_from_inbound — Gemini ─────────────────────────────────

def test_gemini_v1beta_path():
    """Gemini CLI uses /v1beta/models/…:generateContent."""
    assert detect_provider_from_inbound(
        "/v1beta/models/gemini-2.5-pro:generateContent", {}
    ) == "gemini"


def test_gemini_generate_content_path():
    """Older Gemini path pattern."""
    assert detect_provider_from_inbound(
        "/v1/models/gemini-pro:generateContent", {}
    ) == "gemini"


def test_gemini_stream_generate_content_path():
    """Streaming Gemini path."""
    assert detect_provider_from_inbound(
        "/v1beta/models/gemini-2.5-flash:streamGenerateContent", {}
    ) == "gemini"


def test_gemini_ya29_bearer_token():
    """Google OAuth tokens start with ya29."""
    assert detect_provider_from_inbound(
        "/some/api/endpoint",
        {"authorization": "Bearer ya29.a0ARrdaM-xyz"},
    ) == "gemini"


# ── 4. _get_provider_upstream_base ───────────────────────────────────────────

def test_anthropic_upstream_url():
    assert _get_provider_upstream_base("anthropic") == _ANTHROPIC_UPSTREAM_URL


def test_openai_upstream_url():
    assert _get_provider_upstream_base("openai") == _OPENAI_UPSTREAM_URL


def test_gemini_upstream_url():
    assert _get_provider_upstream_base("gemini") == _GEMINI_UPSTREAM_URL


def test_unknown_falls_back_to_upstream_url():
    """An unknown provider falls back to the generic UPSTREAM_URL."""
    from proxy import UPSTREAM_URL
    assert _get_provider_upstream_base("unknown") == UPSTREAM_URL


# ── 5. _build_upstream_url_for_provider ──────────────────────────────────────

def test_anthropic_url_construction():
    url = _build_upstream_url_for_provider("anthropic", "v1/messages", "")
    assert url.startswith("https://api.anthropic.com")
    assert "v1/messages" in url


def test_openai_url_construction():
    url = _build_upstream_url_for_provider("openai", "v1/chat/completions", "")
    assert url.startswith("https://api.openai.com")
    assert "v1/chat/completions" in url


def test_gemini_url_construction():
    url = _build_upstream_url_for_provider(
        "gemini", "v1beta/models/gemini-2.5-pro:generateContent", ""
    )
    assert url.startswith("https://generativelanguage.googleapis.com")
    assert "v1beta/models/gemini-2.5-pro" in url


def test_query_string_preserved():
    url = _build_upstream_url_for_provider("openai", "v1/chat/completions", "stream=true")
    assert "stream=true" in url


# ── 6. 10-user concurrent simulation (provider-level) ─────────────────────────

def test_ten_users_different_providers_correct_routing():
    """10 users, each using a different CLI tool, all pointing at the same proxy.

    Verifies that each request is identified for the correct provider and would
    be forwarded to the correct upstream URL, without any cross-contamination.
    """
    users = [
        # (username, path, headers, expected_provider, expected_upstream_contains)
        ("user1_claude_code",  "/v1/messages",                          {"anthropic-version": "2023-06-01"}, "anthropic", "anthropic.com"),
        ("user2_codex_cli",    "/v1/chat/completions",                  {},                                  "openai",    "openai.com"),
        ("user3_gemini_cli",   "/v1beta/models/gemini-2.5-pro:generateContent", {},                         "gemini",    "googleapis.com"),
        ("user4_claude_code",  "/v1/messages",                          {"anthropic-version": "2023-06-01"}, "anthropic", "anthropic.com"),
        ("user5_openai_sdk",   "/v1/chat/completions",                  {},                                  "openai",    "openai.com"),
        ("user6_gemini_cli",   "/v1beta/models/gemini-2.5-flash:streamGenerateContent", {},                 "gemini",    "googleapis.com"),
        ("user7_codex_resp",   "/v1/responses",                         {},                                  "openai",    "openai.com"),
        ("user8_claude_code",  "/v1/messages",                          {"anthropic-version": "2023-06-01"}, "anthropic", "anthropic.com"),
        ("user9_openai_embed", "/v1/embeddings",                        {},                                  "openai",    "openai.com"),
        ("user10_gemini_v1",   "/v1/models/gemini-pro:generateContent", {},                                  "gemini",    "googleapis.com"),
    ]

    assert len(users) == 10, "must have exactly 10 users"

    for username, path, headers, exp_provider, exp_upstream_substr in users:
        provider = detect_provider_from_inbound(path, headers)
        assert provider == exp_provider, (
            f"{username}: expected provider {exp_provider!r}, got {provider!r} "
            f"(path={path!r})"
        )

        safe_path = path.lstrip("/")
        url = _build_upstream_url_for_provider(provider, safe_path, "")
        assert exp_upstream_substr in url, (
            f"{username}: expected URL containing {exp_upstream_substr!r}, got {url!r}"
        )


# ── 7. Backward-compat: unknown provider uses UPSTREAM_URL ───────────────────

def test_empty_per_provider_vars_fall_back_gracefully():
    """When per-provider vars are not set, UPSTREAM_URL is used for all."""
    import proxy as p
    orig_anthropic = p._ANTHROPIC_UPSTREAM_URL
    orig_openai    = p._OPENAI_UPSTREAM_URL
    orig_gemini    = p._GEMINI_UPSTREAM_URL
    try:
        # Simulate unset env vars
        p._ANTHROPIC_UPSTREAM_URL = ""
        p._OPENAI_UPSTREAM_URL    = ""
        p._GEMINI_UPSTREAM_URL    = ""
        for prov in ("anthropic", "openai", "gemini", "unknown"):
            base = p._get_provider_upstream_base(prov)
            assert base == p.UPSTREAM_URL, f"fallback failed for provider={prov!r}, got {base!r}"
    finally:
        p._ANTHROPIC_UPSTREAM_URL = orig_anthropic
        p._OPENAI_UPSTREAM_URL    = orig_openai
        p._GEMINI_UPSTREAM_URL    = orig_gemini


# ── 8. No cross-contamination between providers ───────────────────────────────

def test_no_cross_contamination_anthropic_openai():
    """An Anthropic request must NEVER route to the OpenAI upstream."""
    provider = detect_provider_from_inbound("/v1/messages", {"anthropic-version": "2023-06-01"})
    url = _build_upstream_url_for_provider(provider, "v1/messages", "")
    assert "openai.com" not in url
    assert "googleapis.com" not in url
    assert "anthropic.com" in url


def test_no_cross_contamination_gemini_openai():
    """A Gemini request must NEVER route to the OpenAI upstream."""
    provider = detect_provider_from_inbound("/v1beta/models/gemini-2.5-pro:generateContent", {})
    url = _build_upstream_url_for_provider(provider, "v1beta/models/gemini-2.5-pro:generateContent", "")
    assert "openai.com" not in url
    assert "anthropic.com" not in url
    assert "googleapis.com" in url


def test_no_cross_contamination_openai_anthropic():
    """An OpenAI request must NEVER route to the Anthropic upstream."""
    provider = detect_provider_from_inbound("/v1/chat/completions", {})
    url = _build_upstream_url_for_provider(provider, "v1/chat/completions", "")
    assert "anthropic.com" not in url
    assert "googleapis.com" not in url
    assert "openai.com" in url
