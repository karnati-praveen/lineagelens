"""Proxy adapter tests for F4 agent-action extraction.

Verifies that:
  - Shell tool_use blocks are extracted and classified correctly
  - Package install commands are classified as dependency_install
  - File-mutating tools are captured as file_write (for the full ledger)
  - Read-only tools (Read, Glob, Grep) are silently dropped
  - Argument values are secret-redacted before transmission
  - Large argument values are truncated within the size cap
  - _compute_prompt_context_id is stable for the same input
"""
import importlib
import os
import sys
import pytest

# Ensure the proxy directory is on the path (tests are run from repo root or
# from the proxy directory — handle both).
_proxy_dir = os.path.dirname(__file__)
if _proxy_dir not in sys.path:
    sys.path.insert(0, _proxy_dir)

# Set required environment variables before importing proxy modules.
os.environ.setdefault("BACKEND_INGEST_TOKEN", "test-token")
os.environ.setdefault("BACKEND_URL", "http://localhost:8787")
os.environ.setdefault("PROXY_WORKSPACE_ID", "proxy-test")

from adapters.anthropic import (
    _classify_agent_action_type,
    _compute_prompt_context_id,
    _extract_anthropic_agent_actions,
    _sanitise_args,
)


# ── _classify_agent_action_type ───────────────────────────────────────────────

class TestClassifyAgentActionType:
    def test_bash_is_shell(self):
        assert _classify_agent_action_type("Bash", {"command": "ls"}) == "shell"

    def test_bash_npm_install_is_dependency_install(self):
        result = _classify_agent_action_type("Bash", {"command": "npm install lodash"})
        assert result == "dependency_install"

    def test_bash_pip_install_is_dependency_install(self):
        result = _classify_agent_action_type("Bash", {"command": "pip install requests"})
        assert result == "dependency_install"

    def test_bash_yarn_add_is_dependency_install(self):
        result = _classify_agent_action_type("Bash", {"command": "yarn add react"})
        assert result == "dependency_install"

    def test_write_is_file_write(self):
        assert _classify_agent_action_type("Write", {"file_path": "x.py", "content": "..."}) == "file_write"

    def test_edit_is_file_write(self):
        assert _classify_agent_action_type("Edit", {}) == "file_write"

    def test_multiedit_is_file_write(self):
        assert _classify_agent_action_type("MultiEdit", {}) == "file_write"

    def test_read_is_skipped(self):
        assert _classify_agent_action_type("Read", {}) is None

    def test_glob_is_skipped(self):
        assert _classify_agent_action_type("Glob", {}) is None

    def test_grep_is_skipped(self):
        assert _classify_agent_action_type("Grep", {}) is None

    def test_webfetch_is_network(self):
        assert _classify_agent_action_type("WebFetch", {"url": "https://example.com"}) == "network"

    def test_delete_is_file_delete(self):
        assert _classify_agent_action_type("Delete", {}) == "file_delete"

    def test_unknown_tool_is_other(self):
        assert _classify_agent_action_type("SomeCustomTool", {}) == "other"


# ── _extract_anthropic_agent_actions ─────────────────────────────────────────

class TestExtractAnthropicAgentActions:
    def _make_tool_use(self, name: str, inp: dict, tool_id: str = "tu_001") -> dict:
        return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}

    def _prompt_ctx(self, model: str = "claude-sonnet-4-6") -> dict:
        return {"model": model, "system": "You are a helpful agent.", "messages": []}

    def test_bash_shell_extracted(self):
        tool_uses = [self._make_tool_use("Bash", {"command": "ls /tmp"})]
        results = _extract_anthropic_agent_actions(tool_uses, self._prompt_ctx(), "sess-abc")
        assert len(results) == 1
        assert results[0]["actionType"] == "shell"
        assert results[0]["toolName"] == "Bash"
        assert results[0]["argumentsJson"]["command"] == "ls /tmp"

    def test_bash_install_classified_as_dependency_install(self):
        tool_uses = [self._make_tool_use("Bash", {"command": "pip install numpy"})]
        results = _extract_anthropic_agent_actions(tool_uses, self._prompt_ctx(), "sess-x")
        assert results[0]["actionType"] == "dependency_install"

    def test_read_only_tools_dropped(self):
        tool_uses = [
            self._make_tool_use("Read", {"file_path": "/src/app.py"}, "tu_1"),
            self._make_tool_use("Glob", {"pattern": "**/*.py"}, "tu_2"),
            self._make_tool_use("Grep", {"pattern": "def main"}, "tu_3"),
        ]
        results = _extract_anthropic_agent_actions(tool_uses, self._prompt_ctx(), "sess-y")
        assert results == []

    def test_mixed_tools_read_only_dropped(self):
        tool_uses = [
            self._make_tool_use("Read", {"file_path": "x.py"}, "tu_r"),
            self._make_tool_use("Bash", {"command": "echo hello"}, "tu_b"),
            self._make_tool_use("WebFetch", {"url": "https://example.com"}, "tu_w"),
        ]
        results = _extract_anthropic_agent_actions(tool_uses, self._prompt_ctx(), "sess-m")
        assert len(results) == 2
        types = {r["actionType"] for r in results}
        assert types == {"shell", "network"}

    def test_occurred_at_is_iso_string(self):
        tool_uses = [self._make_tool_use("Bash", {"command": "date"})]
        results = _extract_anthropic_agent_actions(tool_uses, self._prompt_ctx(), "sess-t")
        from datetime import datetime
        dt = datetime.fromisoformat(results[0]["occurredAt"].replace("Z", "+00:00"))
        assert dt is not None

    def test_empty_tool_uses_returns_empty(self):
        results = _extract_anthropic_agent_actions([], self._prompt_ctx(), "sess-e")
        assert results == []

    def test_tool_with_no_name_skipped(self):
        tool_uses = [{"type": "tool_use", "id": "tu_x", "name": "", "input": {}}]
        results = _extract_anthropic_agent_actions(tool_uses, self._prompt_ctx(), "sess-n")
        assert results == []


# ── Secret redaction in arguments ─────────────────────────────────────────────

class TestSecretRedaction:
    def test_api_key_redacted_in_command(self):
        """Anthropic API keys in command arguments must be redacted."""
        inp = {"command": "curl -H 'x-api-key: sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234' https://api.anthropic.com"}
        tool_uses = [{"type": "tool_use", "id": "tu_r1", "name": "Bash", "input": inp}]
        results = _extract_anthropic_agent_actions(tool_uses, {}, "sess-redact")
        cmd = results[0]["argumentsJson"]["command"]
        assert "sk-ant-api03" not in cmd
        assert "[REDACTED]" in cmd

    def test_openai_key_redacted(self):
        inp = {"command": "export OPENAI_KEY=sk-abcdefghijklmnop1234567890abcd && python script.py"}
        safe = _sanitise_args(inp)
        assert "sk-" not in safe.get("command", "")

    def test_jwt_redacted(self):
        jwt_token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMTIzIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        inp = {"command": f"curl -H 'Authorization: Bearer {jwt_token}' https://api.example.com"}
        safe = _sanitise_args(inp)
        assert jwt_token not in safe.get("command", "")

    def test_non_secret_value_preserved(self):
        inp = {"command": "ls -la /tmp", "timeout": 30}
        safe = _sanitise_args(inp)
        assert safe["command"] == "ls -la /tmp"
        assert safe["timeout"] == 30


# ── Argument size bounding ────────────────────────────────────────────────────

class TestArgumentSizeBounding:
    def test_large_string_truncated(self):
        big_value = "x" * 2000
        inp = {"content": big_value}
        safe = _sanitise_args(inp)
        assert len(safe["content"]) <= 1024

    def test_nested_large_string_truncated(self):
        inp = {"nested": {"key": "y" * 2000}}
        safe = _sanitise_args(inp)
        assert len(safe["nested"]["key"]) <= 1024

    def test_small_string_preserved(self):
        inp = {"command": "echo hello"}
        safe = _sanitise_args(inp)
        assert safe["command"] == "echo hello"


# ── _compute_prompt_context_id ────────────────────────────────────────────────

class TestComputePromptContextId:
    def test_same_context_same_id(self):
        ctx = {"model": "claude-sonnet-4-6", "system": "You are an agent."}
        assert _compute_prompt_context_id(ctx) == _compute_prompt_context_id(ctx)

    def test_different_model_different_id(self):
        ctx1 = {"model": "claude-sonnet-4-6", "system": "sys"}
        ctx2 = {"model": "claude-opus-4-8", "system": "sys"}
        assert _compute_prompt_context_id(ctx1) != _compute_prompt_context_id(ctx2)

    def test_returns_32_char_hex(self):
        pid = _compute_prompt_context_id({"model": "m", "system": "s"})
        assert len(pid) == 32
        assert all(c in "0123456789abcdef" for c in pid)

    def test_empty_context_stable(self):
        pid = _compute_prompt_context_id({})
        assert len(pid) == 32
