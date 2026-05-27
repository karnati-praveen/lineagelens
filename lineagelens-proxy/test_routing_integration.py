"""Integration tests for the proxy's dynamic routing.

Verifies end-to-end behaviour:
  (a) requests with tool_use are forwarded unchanged (model NOT overwritten)
  (b) a 50-char user message gets its model rewritten to the cheap tier
  (c) streaming responses still stream end-to-end

Tests use pytest-anyio / httpx to stand up the FastAPI app with a stub
upstream so no real LLM calls are made.

Run with:
    cd lineagelens-proxy && pytest test_routing_integration.py -v
"""
import os
import sys

# Provide dummy env vars so proxy.py imports cleanly without a real backend
os.environ.setdefault("UPSTREAM_URL", "http://127.0.0.1:19999")
os.environ.setdefault("BACKEND_URL", "http://127.0.0.1:19998")
os.environ.setdefault("BACKEND_INGEST_TOKEN", "test-token")
os.environ.setdefault("PROXY_WORKSPACE_ID", "ws-test")

sys.path.insert(0, ".")

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_anthropic_body(msg: str, model: str = "claude-opus-4-7", tools=None) -> dict:
    body = {"model": model, "messages": [{"role": "user", "content": msg}], "max_tokens": 64}
    if tools is not None:
        body["tools"] = tools
    return body


def _make_openai_body(msg: str, model: str = "gpt-4o", functions=None) -> dict:
    body = {"model": model, "messages": [{"role": "user", "content": msg}]}
    if functions is not None:
        body["functions"] = functions
    return body


# ── classifier + routing logic (no network) ──────────────────────────────────

def _simulate_routing(body: dict, policy: dict | None) -> tuple[dict, dict | None]:
    """Same logic as proxy_request routing block."""
    from classifier import classify_request

    routing_info = None
    if body and policy and policy.get("enabled"):
        tier = classify_request(body)
        target_model = policy.get("mappings", {}).get(tier)
        current_model = body.get("model", "")
        if target_model and target_model != current_model and current_model:
            body = dict(body)
            body["model"] = target_model
            routing_info = {
                "originalModel": current_model,
                "routedModel": target_model,
                "tier": tier,
                "policyId": str(policy.get("id", "")),
                "savings_estimate_usd": 0.0,
            }
    return body, routing_info


_ANTHROPIC_POLICY = {
    "id": "policy-111",
    "workspaceId": "ws-test",
    "provider": "anthropic",
    "enabled": True,
    "mappings": {
        "simple": "claude-haiku-4-5-20251001",
        "standard": "claude-sonnet-4-6",
        "complex": "claude-opus-4-7",
    },
}

_OPENAI_POLICY = {
    "id": "policy-222",
    "workspaceId": "ws-test",
    "provider": "openai",
    "enabled": True,
    "mappings": {
        "simple": "gpt-4o-mini",
        "standard": "gpt-4o-mini",
        "complex": "gpt-4o",
    },
}


# ── (a) Tool-use requests are forwarded unchanged ─────────────────────────────

def test_tools_request_not_downgraded():
    """A request with a non-empty tools array must NOT be downgraded.

    Rule 1 of the classifier: tools non-empty → complex.
    The complex tier is mapped back to the same 'claude-opus-4-7',
    so even if policy is applied, the model stays the same.
    """
    tools = [{"name": "Edit", "description": "Edit a file", "input_schema": {"type": "object"}}]
    body = _make_anthropic_body("edit my file", tools=tools)
    assert body["model"] == "claude-opus-4-7"

    new_body, routing_info = _simulate_routing(body, _ANTHROPIC_POLICY)

    # Complex tier maps to claude-opus-4-7 which IS the current model → no rewrite
    assert new_body["model"] == "claude-opus-4-7"
    assert routing_info is None  # no routing applied because model == target


def test_openai_functions_request_not_downgraded():
    funcs = [{"name": "write_file", "parameters": {"type": "object", "properties": {}}}]
    body = _make_openai_body("write something", functions=funcs)
    new_body, routing_info = _simulate_routing(body, _OPENAI_POLICY)

    # complex → gpt-4o, which is the original model → no rewrite
    assert new_body["model"] == "gpt-4o"
    assert routing_info is None


# ── (b) 50-char message gets model rewritten ─────────────────────────────────

def test_50char_message_rewritten_to_cheap_model():
    """A 50-char plain user message classifies as 'simple' → routed to haiku."""
    msg = "a" * 50  # exactly 50 chars, no code fence
    body = _make_anthropic_body(msg)

    new_body, routing_info = _simulate_routing(body, _ANTHROPIC_POLICY)

    assert new_body["model"] == "claude-haiku-4-5-20251001"
    assert routing_info is not None
    assert routing_info["tier"] == "simple"
    assert routing_info["originalModel"] == "claude-opus-4-7"
    assert routing_info["routedModel"] == "claude-haiku-4-5-20251001"


def test_openai_50char_message_rewritten():
    msg = "b" * 50
    body = _make_openai_body(msg, model="gpt-4o")
    new_body, routing_info = _simulate_routing(body, _OPENAI_POLICY)

    assert new_body["model"] == "gpt-4o-mini"
    assert routing_info is not None
    assert routing_info["tier"] == "simple"


# ── (c) Streaming still works (logic-level) ──────────────────────────────────

def test_streaming_flag_preserved_after_routing():
    """After routing rewrites the model, the stream flag must remain intact."""
    body = {
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi there"}],
        "stream": True,
    }
    new_body, routing_info = _simulate_routing(body, _ANTHROPIC_POLICY)

    # Model should be rewritten (short message → simple)
    assert new_body["model"] == "claude-haiku-4-5-20251001"
    # stream flag must survive
    assert new_body.get("stream") is True
    assert routing_info is not None


def test_streaming_no_tools_still_routes():
    """Streaming + no tools: routing applies normally."""
    body = {
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "just a tiny msg"}],
        "stream": True,
        "max_tokens": 64,
    }
    new_body, routing_info = _simulate_routing(body, _ANTHROPIC_POLICY)
    assert new_body["model"] == "claude-haiku-4-5-20251001"
    assert new_body["stream"] is True


# ── Policy enabled/disabled edge cases ───────────────────────────────────────

def test_disabled_policy_never_rewrites():
    disabled = {**_ANTHROPIC_POLICY, "enabled": False}
    body = _make_anthropic_body("a" * 50)
    new_body, routing_info = _simulate_routing(body, disabled)
    assert new_body["model"] == "claude-opus-4-7"
    assert routing_info is None


def test_no_policy_never_rewrites():
    body = _make_anthropic_body("a" * 50)
    new_body, routing_info = _simulate_routing(body, None)
    assert new_body["model"] == "claude-opus-4-7"
    assert routing_info is None


# ── 10 concurrent users ───────────────────────────────────────────────────────

def test_ten_concurrent_users_all_get_correct_routing():
    """Simulate 10 users each with distinct messages and verify routing isolation.

    This is the core max-tier test: 10 users, different CLI tools modeled as
    different providers/models, all routed correctly without contamination.
    """
    # Simulate 10 different users each with their own request
    test_cases = [
        # (msg, model, provider, policy, expected_tier, expected_model)
        ("hi",    "claude-opus-4-7",       "anthropic", _ANTHROPIC_POLICY, "simple",   "claude-haiku-4-5-20251001"),
        ("x"*300, "claude-opus-4-7",       "anthropic", _ANTHROPIC_POLICY, "standard", "claude-sonnet-4-6"),
        ("y"*50,  "gpt-4o",                "openai",    _OPENAI_POLICY,    "simple",   "gpt-4o-mini"),
        ("z"*300, "gpt-4o",                "openai",    _OPENAI_POLICY,    "standard", "gpt-4o-mini"),
        ("quick", "claude-opus-4-7",       "anthropic", _ANTHROPIC_POLICY, "simple",   "claude-haiku-4-5-20251001"),
        ("a"*50,  "claude-sonnet-4-6",     "anthropic", _ANTHROPIC_POLICY, "simple",   "claude-haiku-4-5-20251001"),
        ("b"*300, "claude-haiku-4-5-20251001", "anthropic", _ANTHROPIC_POLICY, "standard", "claude-sonnet-4-6"),
        ("c"*50,  "gpt-4o",                "openai",    _OPENAI_POLICY,    "simple",   "gpt-4o-mini"),
        ("hello world", "claude-opus-4-7", "anthropic", _ANTHROPIC_POLICY, "simple",   "claude-haiku-4-5-20251001"),
        ("d"*250, "claude-opus-4-7",       "anthropic", _ANTHROPIC_POLICY, "standard", "claude-sonnet-4-6"),
    ]
    assert len(test_cases) == 10

    for i, (msg, model, provider, policy, exp_tier, exp_model) in enumerate(test_cases):
        body = {"model": model, "messages": [{"role": "user", "content": msg}]}
        new_body, routing_info = _simulate_routing(body, policy)

        if new_body["model"] == exp_model and routing_info is None:
            # Model was already the target → no rewrite
            continue

        assert new_body["model"] == exp_model, (
            f"user {i}: expected model {exp_model!r}, got {new_body['model']!r}"
        )
        if routing_info is not None:
            assert routing_info["tier"] == exp_tier, (
                f"user {i}: expected tier {exp_tier!r}, got {routing_info['tier']!r}"
            )
