"""Tests for the OpenAI /v1/chat/completions adapter in proxy.py.

Covers every capture path the chat/completions endpoint must handle:
  A. tool-call edits (non-streaming, streaming-fragmented, legacy function_call)
  B. text-content edits (Aider SEARCH/REPLACE, unified diff, fenced code blocks)
  C. mixed content+tool_calls, n>1 choices, guaranteed text fallback
  D. tool-result resolution, Azure deployment path, fail-open behaviour
"""
import asyncio
import json
import sys

sys.path.insert(0, ".")
import proxy  # noqa: E402
from adapters.contract import RESULT_CAPTURE_UNAVAILABLE, classify_capture_result  # noqa: E402


# ── path detection ─────────────────────────────────────────────────────────────

def test_is_openai_chat_path():
    assert proxy._is_openai_chat_path("https://api.openai.com/v1/chat/completions")
    assert not proxy._is_openai_chat_path("https://api.openai.com/v1/responses")
    assert not proxy._is_openai_chat_path("https://api.anthropic.com/v1/messages")


def test_azure_deployments_path_recognized():
    url = ("https://my-resource.openai.azure.com/openai/deployments/"
           "gpt-4o/chat/completions?api-version=2024-02-01")
    assert proxy._is_openai_chat_path(url)
    # And inbound provider detection routes it to openai.
    inbound = "/openai/deployments/gpt-4o/chat/completions"
    assert proxy.detect_provider_from_inbound(inbound, {}) == "openai"


# ── A. tool-call edits ─────────────────────────────────────────────────────────

def test_tool_call_write_file_non_streaming():
    body = json.dumps({
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": "app/foo.py", "content": "print(1)\n"}),
                    },
                }],
            },
        }],
    }).encode()
    choices = proxy._extract_openai_choices_from_body(body)
    assert len(choices) == 1
    tc = choices[0]["tool_calls"][0]
    edits = proxy._parse_openai_tool_call_to_edits(tc)
    assert len(edits) == 1
    assert edits[0]["file_path"] == "app/foo.py"
    assert edits[0]["new_string"] == "print(1)\n"
    assert edits[0]["verb"] == "write"
    assert edits[0]["tool_use_id"] == "call_1"


def test_tool_call_write_to_file_cline_variant():
    tc = {"id": "c1", "name": "write_to_file",
          "arguments": json.dumps({"file_path": "x.ts", "new_content": "export {}"})}
    edits = proxy._parse_openai_tool_call_to_edits(tc)
    assert edits[0]["file_path"] == "x.ts"
    assert edits[0]["new_string"] == "export {}"


def test_tool_call_str_replace():
    tc = {"id": "c2", "name": "str_replace",
          "arguments": json.dumps({"path": "a.py", "old_str": "x", "new_str": "y"})}
    edits = proxy._parse_openai_tool_call_to_edits(tc)
    assert len(edits) == 1
    assert edits[0]["verb"] == "replace"
    assert edits[0]["old_string"] == "x"
    assert edits[0]["new_string"] == "y"


def test_tool_call_apply_patch_dsl():
    patch = ("*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n*** End Patch\n")
    tc = {"id": "c3", "name": "apply_patch", "arguments": json.dumps({"input": patch})}
    edits = proxy._parse_openai_tool_call_to_edits(tc)
    assert len(edits) == 1
    assert edits[0]["file_path"] == "a.py"
    assert edits[0]["new_string"] == "y"


def test_tool_call_diff_argument():
    diff = "--- a/a.py\n+++ b/a.py\n@@\n-old\n+new\n"
    tc = {"id": "c4", "name": "edit_file",
          "arguments": json.dumps({"path": "a.py", "diff": diff})}
    edits = proxy._parse_openai_tool_call_to_edits(tc)
    assert len(edits) == 1
    assert "new" in edits[0]["new_string"]
    assert "old" in edits[0]["old_string"]


def test_legacy_function_call_non_streaming():
    body = json.dumps({
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "function_call": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": "legacy.py", "content": "x=1"}),
                },
            },
        }],
    }).encode()
    choices = proxy._extract_openai_choices_from_body(body)
    assert len(choices[0]["tool_calls"]) == 1
    edits = proxy._parse_openai_tool_call_to_edits(choices[0]["tool_calls"][0])
    assert edits[0]["file_path"] == "legacy.py"


def test_streaming_tool_call_fragmented_args():
    args_str = json.dumps({"path": "stream.py", "content": "print('hi')\n"})
    half = len(args_str) // 2
    p1, p2 = args_str[:half], args_str[half:]
    events = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_s", "function": {"name": "write_file", "arguments": ""}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": p1}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": p2}}]}}]},
    ]
    sse = ("\n\n".join(f"data: {json.dumps(e)}" for e in events) + "\n\n").encode()
    choices = proxy._extract_openai_choices_from_sse([sse])
    assert len(choices) == 1
    tc = choices[0]["tool_calls"][0]
    assert tc["id"] == "call_s"
    assert tc["arguments"] == args_str
    edits = proxy._parse_openai_tool_call_to_edits(tc)
    assert edits[0]["file_path"] == "stream.py"


def test_streaming_content_assembled():
    events = [
        {"choices": [{"index": 0, "delta": {"content": "hello "}}]},
        {"choices": [{"index": 0, "delta": {"content": "world"}}]},
    ]
    sse = ("\n\n".join(f"data: {json.dumps(e)}" for e in events) + "\n\n").encode()
    choices = proxy._extract_openai_choices_from_sse([sse])
    assert choices[0]["content"] == "hello world"


# ── B. text-content edits ──────────────────────────────────────────────────────

def test_aider_search_replace_block():
    content = (
        "Here is the change:\n\n"
        "mathweb/flask/app.py\n"
        "```python\n"
        "<<<<<<< SEARCH\n"
        "def hello():\n"
        "    return 1\n"
        "=======\n"
        "def hello():\n"
        "    return 2\n"
        ">>>>>>> REPLACE\n"
        "```\n"
    )
    edits = proxy._parse_aider_search_replace(content)
    assert len(edits) == 1
    assert edits[0]["file_path"] == "mathweb/flask/app.py"
    assert "return 1" in edits[0]["old_string"]
    assert "return 2" in edits[0]["new_string"]
    assert edits[0]["verb"] == "replace"


def test_aider_search_replace_via_text_dispatcher():
    content = (
        "src/x.py\n```\n<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n```\n"
    )
    edits = proxy._parse_text_content_to_edits(content)
    assert len(edits) == 1
    assert edits[0]["file_path"] == "src/x.py"
    assert edits[0]["tool_name"] == "aider_search_replace"


def test_unified_diff_in_content():
    content = (
        "Sure:\n\n"
        "```diff\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1,2 +1,2 @@\n"
        " import os\n"
        "-x = 1\n"
        "+x = 2\n"
        "```\n"
    )
    edits = proxy._parse_text_content_to_edits(content)
    assert len(edits) == 1
    assert edits[0]["file_path"] == "src/foo.py"
    assert "x = 2" in edits[0]["new_string"]
    assert "x = 1" in edits[0]["old_string"]
    assert "import os" in edits[0]["old_string"]


def test_diff_git_header():
    content = (
        "diff --git a/a.py b/a.py\n"
        "index e69de29..4b825dc 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -0,0 +1 @@\n"
        "+print('hi')\n"
    )
    edits = proxy._parse_unified_diff(content)
    assert len(edits) == 1
    assert edits[0]["file_path"] == "a.py"
    assert "print('hi')" in edits[0]["new_string"]


def test_fenced_block_with_file_hint():
    content = (
        "```python\n"
        "# file: app/bar.py\n"
        "def bar():\n"
        "    pass\n"
        "```\n"
    )
    edits = proxy._parse_text_content_to_edits(content)
    assert len(edits) == 1
    assert edits[0]["file_path"] == "app/bar.py"
    assert "def bar()" in edits[0]["new_string"]


def test_fenced_block_with_path_info_string():
    content = "```python path=app/baz.py\nx = 1\n```\n"
    edits = proxy._parse_fenced_code_blocks(content)
    assert len(edits) == 1
    assert edits[0]["file_path"] == "app/baz.py"


def test_fenced_block_no_path_still_captured():
    content = "```python\nprint('orphan code')\n```\n"
    edits = proxy._parse_text_content_to_edits(content)
    assert len(edits) == 1
    assert edits[0]["file_path"] == "proxy-capture"
    assert "orphan code" in edits[0]["new_string"]


# ── C. mixed / multi-choice ────────────────────────────────────────────────────

def test_mixed_content_and_tool_calls():
    body = json.dumps({
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "src/y.py\n```\n<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n```",
                "tool_calls": [{
                    "id": "call_m",
                    "function": {"name": "write_file",
                                 "arguments": json.dumps({"path": "z.py", "content": "z=1"})},
                }],
            },
        }],
    }).encode()
    choices = proxy._extract_openai_choices_from_body(body)
    tool_edits = proxy._parse_openai_tool_call_to_edits(choices[0]["tool_calls"][0])
    text_edits = proxy._parse_text_content_to_edits(choices[0]["content"])
    assert len(tool_edits) == 1 and tool_edits[0]["file_path"] == "z.py"
    assert len(text_edits) == 1 and text_edits[0]["file_path"] == "src/y.py"


def test_multiple_choices_each_captured():
    body = json.dumps({
        "choices": [
            {"index": 0, "message": {"role": "assistant", "tool_calls": [
                {"id": "c0", "function": {"name": "write_file",
                 "arguments": json.dumps({"path": "a.py", "content": "a"})}}]}},
            {"index": 1, "message": {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "write_file",
                 "arguments": json.dumps({"path": "b.py", "content": "b"})}}]}},
        ],
    }).encode()
    choices = proxy._extract_openai_choices_from_body(body)
    assert len(choices) == 2
    paths = []
    for ch in choices:
        for tc in ch["tool_calls"]:
            paths += [e["file_path"] for e in proxy._parse_openai_tool_call_to_edits(tc)]
    assert sorted(paths) == ["a.py", "b.py"]


# ── D. resolution + classification ─────────────────────────────────────────────

def test_extract_tool_results():
    body = json.dumps({
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_x", "function": {"name": "write_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_x", "content": "File written"},
        ],
    }).encode()
    results = proxy._extract_openai_tool_results(body)
    assert len(results) == 1
    assert results[0]["tool_call_id"] == "call_x"


def test_classify_tool_result():
    assert proxy._classify_openai_tool_result({"content": "ok"})[0] == "applied"
    assert proxy._classify_openai_tool_result({"content": "Error: no such file"})[0] == "errored"
    assert proxy._classify_openai_tool_result({"content": "User rejected edit"})[0] == "rejected"


# ── full lifecycle / capture orchestration ─────────────────────────────────────

def test_capture_from_body_tool_call_then_resolve():
    captured = []
    original_ingest = proxy._ingest_edit

    async def fake_ingest(edit, session_key, status, error_message, provider):
        captured.append((edit["file_path"], status, provider))

    proxy._ingest_edit = fake_ingest
    proxy._pending_edits.clear()
    try:
        async def run():
            sk = "oai_sess"
            body = json.dumps({"choices": [{"index": 0, "message": {
                "role": "assistant", "tool_calls": [{"id": "call_z", "function": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": "w.py", "content": "w"})}}]}}]}).encode()
            ok = await proxy._capture_openai_chat_from_body(sk, body, {}, None, "openai")
            assert ok is True
            assert (sk, "call_z") in proxy._pending_edits
            results = [{"tool_call_id": "call_z", "content": "Done"}]
            await proxy._resolve_openai_pending_edits(sk, results, "openai")
            await asyncio.sleep(0.05)
            assert (sk, "call_z") not in proxy._pending_edits
            assert captured == [("w.py", "applied", "openai")]
        asyncio.run(run())
    finally:
        proxy._ingest_edit = original_ingest


def test_capture_text_edit_ingested_directly():
    captured = []
    original_ingest = proxy._ingest_edit

    async def fake_ingest(edit, session_key, status, error_message, provider):
        captured.append((edit["file_path"], status))

    proxy._ingest_edit = fake_ingest
    proxy._pending_edits.clear()
    try:
        async def run():
            body = json.dumps({"choices": [{"index": 0, "message": {
                "role": "assistant",
                "content": "src/q.py\n```\n<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n```",
            }}]}).encode()
            ok = await proxy._capture_openai_chat_from_body("sk2", body, {}, None, "openai")
            assert ok is True
            await asyncio.sleep(0.05)
            assert captured == [("src/q.py", "applied")]
        asyncio.run(run())
    finally:
        proxy._ingest_edit = original_ingest


def test_capture_returns_false_when_no_code():
    async def run():
        body = json.dumps({"choices": [{"index": 0, "message": {
            "role": "assistant", "content": "Just some prose, no code here."}}]}).encode()
        proxy._pending_edits.clear()
        ok = await proxy._capture_openai_chat_from_body("sk3", body, {}, None, "openai")
        # No fenced block / diff / tool call → nothing structured captured,
        # so the generic text fallback (handled in the proxy handler) takes over.
        assert ok is False
    asyncio.run(run())


# ── fail-open ──────────────────────────────────────────────────────────────────

def test_malformed_arguments_json_no_raise():
    tc = {"id": "c", "name": "write_file", "arguments": "{not valid json"}
    assert proxy._parse_openai_tool_call_to_edits(tc) == []


def test_malformed_search_replace_no_raise():
    # Truncated SEARCH/REPLACE (no closing marker) must not raise, just yield [].
    content = "f.py\n```\n<<<<<<< SEARCH\nincomplete\n"
    assert proxy._parse_aider_search_replace(content) == []
    # And the dispatcher falls back to fenced capture without raising.
    assert isinstance(proxy._parse_text_content_to_edits(content), list)


def test_empty_body_no_raise():
    assert proxy._extract_openai_choices_from_body(b"not json") == []
    assert proxy._extract_openai_tool_results(b"not json") == []


def test_capability_is_declared():
    """PART 5 #54 — the adapter must declare its capability/fidelity.

    Unlike the other three adapters, openai_chat has no fixed mutating-tool
    name set (it infers mutation from argument shape, see module docstring
    strategy B), so classify_capture_result's name-based RESULT_UNKNOWN vs
    RESULT_CAPTURE_UNAVAILABLE distinction is exercised generically here
    rather than against a specific tool-name allowlist.
    """
    assert proxy._OPENAI_CHAT_CAPABILITY.provider == "openai_chat"
    assert proxy._OPENAI_CHAT_CAPABILITY.fidelity == "partial"


def test_capture_unavailable_for_malformed_tool_call_arguments():
    status = classify_capture_result(
        tool_name="edit_file",
        mutating_tool_names={"edit_file"},
        edits=proxy._parse_openai_tool_call_to_edits(
            {"id": "t1", "name": "edit_file", "arguments": "{not valid json"}
        ),
        input_was_dict=False,
    )
    assert status == RESULT_CAPTURE_UNAVAILABLE


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except Exception as exc:
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    print(f"\n{passed}/{len(tests)} tests passed")
    sys.exit(0 if not failed else 1)
