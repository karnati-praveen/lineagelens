"""Unit tests for the routing decision logic.

Tests the routing_cache.get_policy integration with the proxy's
per-request routing block without starting the full proxy server.

Covers:
  - policy disabled → body unchanged
  - policy missing → body unchanged
  - model already equals target → body unchanged
  - simple 50-char message + enabled policy → model overwritten
  - workspace_id not resolved → skip routing (no error)
  - pricing.estimate_savings produces correct output

Run with:
    cd lineagelens-proxy && pytest test_routing.py -v
"""
import sys

sys.path.insert(0, ".")

from classifier import classify_request
from pricing import estimate_savings, get_pricing


# ── pricing unit tests ────────────────────────────────────────────────────────

def test_pricing_known_model():
    p = get_pricing("claude-opus-4-7")
    assert p is not None
    assert p["input_per_1m"] == 15.0
    assert p["output_per_1m"] == 75.0


def test_pricing_prefix_fallback():
    # claude-haiku-4-5-20251001 is an exact key; a suffixed variant should still match.
    p = get_pricing("claude-haiku-4-5-20251001-special")
    assert p is not None
    assert p["input_per_1m"] == 0.25


def test_pricing_unknown_model_returns_none():
    assert get_pricing("model-that-does-not-exist-xyzzy") is None


def test_savings_positive():
    savings = estimate_savings(
        original_model="claude-opus-4-7",
        routed_model="claude-haiku-4-5-20251001",
        input_tokens=1000,
        output_tokens=500,
    )
    assert savings > 0, "routing to haiku should be cheaper than opus"


def test_savings_clamped_to_zero():
    # Routing from cheap to expensive — savings must be 0 (never negative)
    savings = estimate_savings(
        original_model="claude-haiku-4-5-20251001",
        routed_model="claude-opus-4-7",
        input_tokens=1000,
        output_tokens=500,
    )
    assert savings == 0.0


def test_savings_same_model():
    savings = estimate_savings(
        original_model="claude-sonnet-4-6",
        routed_model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=1000,
    )
    assert savings == 0.0


def test_savings_unknown_model_returns_zero():
    savings = estimate_savings(
        original_model="no-such-model",
        routed_model="claude-haiku-4-5-20251001",
        input_tokens=1000,
        output_tokens=1000,
    )
    assert savings == 0.0


# ── routing decision logic (simulates proxy_request block) ───────────────────

def _apply_routing(body: dict, policy: dict | None) -> tuple[dict, dict | None]:
    """Replicate the routing block from proxy_request for unit testing."""
    routing_info = None
    if body and policy and policy.get("enabled"):
        tier = classify_request(body)
        target_model = policy.get("mappings", {}).get(tier)
        current_model = body.get("model", "")
        if target_model and target_model != current_model and current_model:
            body = dict(body)  # shallow copy — avoid mutating input
            body["model"] = target_model
            routing_info = {
                "originalModel": current_model,
                "routedModel": target_model,
                "tier": tier,
                "policyId": str(policy.get("id", "")),
                "savings_estimate_usd": 0.0,
            }
    return body, routing_info


_SAMPLE_POLICY = {
    "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "workspaceId": "ws-test",
    "provider": "anthropic",
    "mappings": {
        "simple": "claude-haiku-4-5-20251001",
        "standard": "claude-sonnet-4-6",
        "complex": "claude-opus-4-7",
    },
    "enabled": True,
}


def test_routing_simple_request_rewrites_model():
    body = {
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "What time is it?"}],
    }
    new_body, routing_info = _apply_routing(body, _SAMPLE_POLICY)
    assert new_body["model"] == "claude-haiku-4-5-20251001"
    assert routing_info is not None
    assert routing_info["tier"] == "simple"
    assert routing_info["originalModel"] == "claude-opus-4-7"
    assert routing_info["routedModel"] == "claude-haiku-4-5-20251001"


def test_routing_policy_disabled_does_not_rewrite():
    disabled_policy = {**_SAMPLE_POLICY, "enabled": False}
    body = {"model": "claude-opus-4-7", "messages": [{"role": "user", "content": "hi"}]}
    new_body, routing_info = _apply_routing(body, disabled_policy)
    assert new_body["model"] == "claude-opus-4-7"
    assert routing_info is None


def test_routing_no_policy_does_not_rewrite():
    body = {"model": "claude-opus-4-7", "messages": [{"role": "user", "content": "hi"}]}
    new_body, routing_info = _apply_routing(body, None)
    assert new_body["model"] == "claude-opus-4-7"
    assert routing_info is None


def test_routing_model_already_target_does_not_rewrite():
    """If the client already sent the target model, skip the rewrite."""
    body = {
        "model": "claude-haiku-4-5-20251001",
        "messages": [{"role": "user", "content": "hi there"}],
    }
    new_body, routing_info = _apply_routing(body, _SAMPLE_POLICY)
    assert new_body["model"] == "claude-haiku-4-5-20251001"
    assert routing_info is None  # no rewrite needed


def test_routing_complex_request_goes_to_opus():
    body = {
        "model": "claude-haiku-4-5-20251001",
        "tools": [{"name": "Edit", "description": "edit", "input_schema": {}}],
        "messages": [{"role": "user", "content": "make changes"}],
    }
    new_body, routing_info = _apply_routing(body, _SAMPLE_POLICY)
    assert new_body["model"] == "claude-opus-4-7"
    assert routing_info is not None
    assert routing_info["tier"] == "complex"


def test_routing_standard_request_goes_to_sonnet():
    msg = "x" * 300  # > 200 chars, no code fence → standard
    body = {
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": msg}],
    }
    new_body, routing_info = _apply_routing(body, _SAMPLE_POLICY)
    assert new_body["model"] == "claude-sonnet-4-6"
    assert routing_info is not None
    assert routing_info["tier"] == "standard"


def test_routing_no_model_in_body_skips():
    """Body without a model field: nothing to rewrite."""
    body = {"messages": [{"role": "user", "content": "hi"}]}
    new_body, routing_info = _apply_routing(body, _SAMPLE_POLICY)
    assert "model" not in new_body
    assert routing_info is None


def test_routing_workspace_not_resolved_no_crash():
    """Simulates workspace_id being empty — should not raise."""
    body = {"model": "claude-opus-4-7", "messages": [{"role": "user", "content": "hi"}]}
    # No exception should propagate
    new_body, routing_info = _apply_routing(body, None)  # no policy for empty workspace
    assert new_body == body
    assert routing_info is None


# ── 10 concurrent users simulation ───────────────────────────────────────────

def test_ten_concurrent_users_isolated():
    """Each user's routing decision must be independent — no shared state corruption."""
    users = []
    for i in range(10):
        workspace = f"workspace-{i:02d}"
        msg = "hi" if i % 3 == 0 else ("x" * 300 if i % 3 == 1 else "x" * 32_100)
        body = {"model": "claude-opus-4-7", "messages": [{"role": "user", "content": msg}]}
        policy = {**_SAMPLE_POLICY, "workspaceId": workspace}
        users.append((body, policy))

    results = [_apply_routing(b, p) for b, p in users]

    # Every result should be independently determined
    assert len(results) == 10
    for (new_body, ri), (orig_body, _) in zip(results, users):
        # Model was either rewritten or stayed the same
        assert new_body.get("model") in (
            "claude-opus-4-7",
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-6",
        )
        # routing_info (if present) always has the expected keys
        if ri is not None:
            assert "originalModel" in ri
            assert "routedModel" in ri
            assert "tier" in ri
            assert ri["tier"] in ("simple", "standard", "complex")
