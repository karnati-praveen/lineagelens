"""Tests for the Anthropic tool_use/tool_result adapter in proxy.py."""
import json
import sys

sys.path.insert(0, ".")
import proxy  # noqa: E402


def test_edit_tool_parse():
    tool = {
        "type": "tool_use",
        "id": "toolu_01ABC",
        "name": "Edit",
        "input": {
            "file_path": "src/foo.py",
            "old_string": "def foo():\n    return 1",
            "new_string": "def foo():\n    return 2",
        },
    }
    edits = proxy._parse_anthropic_tool_use_to_edits(tool)
    assert len(edits) == 1
    assert edits[0]["tool_name"] == "Edit"
    assert edits[0]["file_path"] == "src/foo.py"
    assert edits[0]["new_string"].endswith("return 2")


def test_write_tool_parse():
    tool = {
        "type": "tool_use",
        "id": "toolu_02",
        "name": "Write",
        "input": {"file_path": "new.py", "content": "print('hello')"},
    }
    edits = proxy._parse_anthropic_tool_use_to_edits(tool)
    assert len(edits) == 1
    assert edits[0]["tool_name"] == "Write"
    assert edits[0]["new_string"] == "print('hello')"


def test_multi_edit_flattens():
    tool = {
        "type": "tool_use",
        "id": "toolu_03",
        "name": "MultiEdit",
        "input": {
            "file_path": "a.py",
            "edits": [
                {"old_string": "x", "new_string": "y"},
                {"old_string": "p", "new_string": "q"},
            ],
        },
    }
    edits = proxy._parse_anthropic_tool_use_to_edits(tool)
    assert len(edits) == 2
    assert edits[0]["edit_index"] == 0
    assert edits[1]["edit_index"] == 1
    assert edits[0]["tool_use_id"] == edits[1]["tool_use_id"] == "toolu_03"


def test_bash_tool_produces_no_edits():
    tool = {"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "ls"}}
    assert proxy._parse_anthropic_tool_use_to_edits(tool) == []


def test_extract_tool_uses_from_body():
    body = json.dumps({
        "content": [
            {"type": "text", "text": "I'll edit it."},
            {"type": "tool_use", "id": "toolu_99", "name": "Edit",
             "input": {"file_path": "a.py", "old_string": "x", "new_string": "y"}},
        ]
    }).encode()
    tool_uses = proxy._extract_anthropic_tool_uses_from_body(body)
    assert len(tool_uses) == 1
    assert tool_uses[0]["id"] == "toolu_99"


def test_extract_tool_results():
    body = json.dumps({
        "messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_01", "content": "ok", "is_error": False}
            ]}
        ]
    }).encode()
    results = proxy._extract_anthropic_tool_results(body)
    assert len(results) == 1
    assert results[0]["tool_use_id"] == "toolu_01"


def test_classify_applied():
    status, _ = proxy._classify_tool_result(
        {"type": "tool_result", "tool_use_id": "x", "content": "Edited", "is_error": False}
    )
    assert status == "applied"


def test_classify_errored():
    status, _ = proxy._classify_tool_result(
        {"type": "tool_result", "tool_use_id": "x", "content": "File not found", "is_error": True}
    )
    assert status == "errored"


def test_classify_rejected():
    status, _ = proxy._classify_tool_result(
        {"type": "tool_result", "tool_use_id": "x", "content": "User rejected this edit", "is_error": True}
    )
    assert status == "rejected"


def test_sse_assembly():
    # Build a realistic Anthropic SSE stream for one Edit tool_use,
    # with input split across two partial_json deltas.
    events = []
    events.append(json.dumps({
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": "toolu_99", "name": "Edit", "input": {}},
    }))
    events.append(json.dumps({
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": '{"file_path":"a.py","old_string":'},
    }))
    events.append(json.dumps({
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": '"x","new_string":"y"}'},
    }))
    events.append(json.dumps({"type": "content_block_stop", "index": 0}))

    sse_bytes = ("\n\n".join(f"data: {e}" for e in events) + "\n\n").encode("utf-8")
    tool_uses = proxy._extract_anthropic_tool_uses_from_sse([sse_bytes])
    assert len(tool_uses) == 1, f"expected 1 tool_use, got {tool_uses}"
    assert tool_uses[0]["id"] == "toolu_99"
    assert tool_uses[0]["input"]["file_path"] == "a.py"
    assert tool_uses[0]["input"]["new_string"] == "y"


def test_session_key_stable_and_differentiating():
    sk1 = proxy._session_key({"system": "You are Claude"}, {"authorization": "Bearer sk-ant-abc"})
    sk2 = proxy._session_key({"system": "You are Claude"}, {"authorization": "Bearer sk-ant-abc"})
    sk3 = proxy._session_key({"system": "Different"}, {"authorization": "Bearer sk-ant-abc"})
    sk4 = proxy._session_key({"system": "You are Claude"}, {"authorization": "Bearer sk-ant-xyz"})
    assert sk1 == sk2
    assert sk1 != sk3
    assert sk1 != sk4


def test_session_key_handles_list_system():
    sk = proxy._session_key(
        {"system": [{"type": "text", "text": "You are Claude"}]},
        {"x-api-key": "sk-ant-abc"},
    )
    assert isinstance(sk, str) and len(sk) == 16


def test_full_lifecycle_pending_to_resolved():
    """End-to-end: tool_use stored as pending, then tool_result resolves it."""
    import asyncio

    captured = []
    original_ingest = proxy._ingest_edit

    async def fake_ingest(edit, session_key, status, error_message, provider):
        captured.append((edit, status, provider))

    proxy._ingest_edit = fake_ingest
    proxy._pending_edits.clear()

    try:
        async def run():
            # Turn 1: assistant sends tool_use
            session_key = "session_abc"
            tool_use = {
                "type": "tool_use", "id": "toolu_42", "name": "Edit",
                "input": {"file_path": "a.py", "old_string": "x", "new_string": "y"},
            }
            await proxy._store_pending_edits(session_key, [tool_use])
            assert ("session_abc", "toolu_42") in proxy._pending_edits

            # Turn 2: client sends tool_result with success
            results = [{"type": "tool_result", "tool_use_id": "toolu_42",
                        "content": "Edited successfully", "is_error": False}]
            await proxy._resolve_pending_edits(session_key, results, "anthropic")

            # Wait for the spawned ingest task to run
            await asyncio.sleep(0.05)

            assert ("session_abc", "toolu_42") not in proxy._pending_edits
            assert len(captured) == 1
            edit, status, provider = captured[0]
            assert status == "applied"
            assert provider == "anthropic"
            assert edit["file_path"] == "a.py"
            assert edit["new_string"] == "y"

        asyncio.run(run())
    finally:
        proxy._ingest_edit = original_ingest


def test_cross_session_isolation():
    """Two sessions with the same tool_use_id must not collide."""
    import asyncio

    async def run():
        proxy._pending_edits.clear()
        tu = {"type": "tool_use", "id": "toolu_X", "name": "Edit",
              "input": {"file_path": "a.py", "old_string": "1", "new_string": "2"}}
        await proxy._store_pending_edits("session_A", [tu])
        await proxy._store_pending_edits("session_B", [tu])
        # Both must coexist in pending
        assert ("session_A", "toolu_X") in proxy._pending_edits
        assert ("session_B", "toolu_X") in proxy._pending_edits
        assert len(proxy._pending_edits) == 2

    asyncio.run(run())


def test_prompt_context_extracted_and_attached_to_ingest():
    """End-to-end: prompt + model from request body must reach _ingest_edit."""
    import asyncio

    captured = []
    original = proxy._ingest_edit

    async def fake(edit, session_key, status, error_message, provider):
        captured.append(edit)

    proxy._ingest_edit = fake
    proxy._pending_edits.clear()

    try:
        async def run():
            # Simulate the prompt context we'd extract from a Claude Code
            # request body: model + system + user message.
            ctx = proxy._extract_anthropic_prompt_context({
                "model": "claude-sonnet-4.6",
                "system": "You are Claude Code, Anthropic's CLI.",
                "messages": [
                    {"role": "user", "content": "Fix the off-by-one bug in foo.py"},
                ],
            })
            assert ctx["model"] == "claude-sonnet-4.6"
            assert ctx["system"].startswith("You are Claude Code")
            assert len(ctx["messages"]) == 1

            tool_use = {
                "type": "tool_use", "id": "toolu_x", "name": "Edit",
                "input": {"file_path": "foo.py", "old_string": "<=", "new_string": "<"},
            }
            await proxy._store_pending_edits("sess_x", [tool_use], prompt_context=ctx)
            results = [{"type": "tool_result", "tool_use_id": "toolu_x",
                        "content": "Edited", "is_error": False}]
            await proxy._resolve_pending_edits("sess_x", results, "anthropic")
            await asyncio.sleep(0.05)

            assert len(captured) == 1
            edit = captured[0]
            assert edit["_model"] == "claude-sonnet-4.6"
            assert "Claude Code" in edit["_system"]
            assert edit["_messages"][0]["content"] == "Fix the off-by-one bug in foo.py"

        asyncio.run(run())
    finally:
        proxy._ingest_edit = original


if __name__ == "__main__":
    import inspect
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except Exception as exc:
            print(f"FAIL {name}: {exc}")
    print(f"\n{passed}/{len(tests)} tests passed")
    sys.exit(0 if passed == len(tests) else 1)
