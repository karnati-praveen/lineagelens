"""Unit tests for the request complexity classifier.

Covers all six rules in priority order.  No external dependencies —
only the stdlib and classifier.py itself.

Run with:
    cd lineagelens-proxy && pytest test_classifier.py -v
"""
import sys

sys.path.insert(0, ".")
from classifier import classify_request  # noqa: E402


# ── Rule 1: tools / functions non-empty → complex ────────────────────────────

def test_rule1_anthropic_tools_is_complex():
    body = {
        "model": "claude-haiku-4-5-20251001",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "Edit", "description": "edit a file", "input_schema": {}}],
    }
    assert classify_request(body) == "complex"


def test_rule1_openai_functions_is_complex():
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "functions": [{"name": "write_file", "parameters": {}}],
    }
    assert classify_request(body) == "complex"


def test_rule1_empty_tools_does_not_trigger():
    """An explicit empty tools array should NOT trigger the complex rule."""
    body = {
        "model": "claude-haiku-4-5-20251001",
        "messages": [{"role": "user", "content": "x" * 50}],
        "tools": [],
    }
    # Should fall through to rule 5 → simple (short message, no code fence)
    assert classify_request(body) == "simple"


# ── Rule 2: approximate token count > 8000 → complex ────────────────────────

def test_rule2_long_prompt_is_complex():
    """32 001 chars ≈ 8 000+ tokens."""
    body = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "a" * 32_001}],
    }
    assert classify_request(body) == "complex"


def test_rule2_short_prompt_not_complex():
    body = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hello"}],
    }
    assert classify_request(body) != "complex"


# ── Rule 3: complex keyword in system → complex ──────────────────────────────

def test_rule3_refactor_in_system_is_complex():
    body = {
        "model": "claude-haiku-4-5-20251001",
        "system": "You are a senior developer helping to refactor legacy code.",
        "messages": [{"role": "user", "content": "short"}],
    }
    assert classify_request(body) == "complex"


def test_rule3_security_in_system_is_complex():
    body = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You perform security audits of APIs."},
            {"role": "user", "content": "check this"},
        ],
    }
    assert classify_request(body) == "complex"


def test_rule3_architect_in_system_is_complex():
    body = {
        "model": "claude-opus-4-7",
        "system": "Help architect a microservices platform.",
        "messages": [{"role": "user", "content": "go"}],
    }
    assert classify_request(body) == "complex"


def test_rule3_neutral_system_not_complex():
    body = {
        "model": "claude-haiku-4-5-20251001",
        "system": "You are a helpful coding assistant.",
        "messages": [{"role": "user", "content": "short message here"}],
    }
    # Should fall through to rule 5 → simple
    assert classify_request(body) == "simple"


# ── Rule 4: code fence > 100 lines → standard ────────────────────────────────

def test_rule4_long_code_fence_is_standard():
    code = "\n".join(f"line {i}" for i in range(105))
    body = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": f"Review this:\n```python\n{code}\n```"}],
    }
    assert classify_request(body) == "standard"


def test_rule4_short_code_fence_not_standard():
    code = "\n".join(f"line {i}" for i in range(10))
    body = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": f"Fix:\n```python\n{code}\n```"}],
    }
    # Short code fence → shouldn't trigger rule 4; short message → simple? No —
    # message contains ``` so rule 5 won't fire → standard
    assert classify_request(body) == "standard"


# ── Rule 5: last user message < 200 chars and no fence → simple ──────────────

def test_rule5_short_plain_message_is_simple():
    body = {
        "model": "claude-haiku-4-5-20251001",
        "messages": [{"role": "user", "content": "What is 2 + 2?"}],
    }
    assert classify_request(body) == "simple"


def test_rule5_short_but_has_fence_is_not_simple():
    body = {
        "model": "claude-haiku-4-5-20251001",
        "messages": [{"role": "user", "content": "Fix: ```x = 1```"}],
    }
    assert classify_request(body) == "standard"


def test_rule5_last_user_message_used_not_first():
    """Only the LAST user message is checked for rule 5."""
    long_msg = "b" * 500
    short_msg = "Quick question"
    body = {
        "model": "claude-haiku-4-5-20251001",
        "messages": [
            {"role": "user", "content": long_msg},
            {"role": "assistant", "content": "OK"},
            {"role": "user", "content": short_msg},
        ],
    }
    assert classify_request(body) == "simple"


# ── Rule 6: default → standard ───────────────────────────────────────────────

def test_rule6_default_is_standard():
    body = {
        "model": "claude-sonnet-4-6",
        "messages": [
            {"role": "user", "content": "x" * 250}  # > 200 chars, no code fence
        ],
    }
    assert classify_request(body) == "standard"


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_non_dict_body_is_standard():
    assert classify_request("not a dict") == "standard"
    assert classify_request(None) == "standard"
    assert classify_request([]) == "standard"


def test_gemini_body_simple():
    body = {
        "contents": [{"role": "user", "parts": [{"text": "hi there"}]}],
    }
    assert classify_request(body) == "simple"


def test_gemini_body_with_system_instruction_complex():
    body = {
        "systemInstruction": {"parts": [{"text": "You help audit code vulnerabilities."}]},
        "contents": [{"role": "user", "parts": [{"text": "check"}]}],
    }
    assert classify_request(body) == "complex"


# ── 10-user scenario: concurrent isolation (logic-level) ─────────────────────

def test_ten_users_different_tiers():
    """Simulate 10 users sending requests that should each get distinct tiers."""
    _nl = "\n"
    cases = [
        # (body, expected_tier)
        ({"messages": [{"role": "user", "content": "hi"}]}, "simple"),
        ({"messages": [{"role": "user", "content": "x" * 250}]}, "standard"),
        ({"tools": [{"name": "Edit"}], "messages": [{"role": "user", "content": "x"}]}, "complex"),
        ({"messages": [{"role": "user", "content": "a" * 32_100}]}, "complex"),
        ({"system": "refactor legacy code", "messages": [{"role": "user", "content": "go"}]}, "complex"),
        # Rule 4: code fence with >100 lines → "standard" (NOT complex — rule 4 is for standard)
        ({"messages": [{"role": "user", "content": f"```py{_nl}{('line' + _nl) * 110}```"}]}, "standard"),
        ({"messages": [{"role": "user", "content": "short"}]}, "simple"),
        ({"messages": [{"role": "user", "content": "another 250 char " + "x" * 240}]}, "standard"),
        ({"messages": [{"role": "user", "content": "quick q"}]}, "simple"),
        # Empty tools array must NOT trigger rule 1 → short message → simple
        ({"tools": [], "messages": [{"role": "user", "content": "tiny"}]}, "simple"),
    ]
    assert len(cases) == 10, "should have exactly 10 user cases"
    for body, expected in cases:
        result = classify_request(body)
        assert result == expected, f"body snippet={str(body)[:60]!r}, got {result!r}, want {expected!r}"
