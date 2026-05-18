"""Tests for the OpenAI Codex CLI (Responses API) adapter in proxy.py."""
import json
import sys

sys.path.insert(0, ".")
import proxy  # noqa: E402


# ── apply_patch DSL parser ─────────────────────────────────────────────────────

def test_dsl_add_file():
    patch = (
        "*** Begin Patch\n"
        "*** Add File: src/new.py\n"
        "+def hello():\n"
        "+    return \"world\"\n"
        "*** End Patch\n"
    )
    edits = proxy._parse_apply_patch_dsl(patch)
    assert len(edits) == 1
    assert edits[0]["verb"] == "add"
    assert edits[0]["file_path"] == "src/new.py"
    assert edits[0]["old_string"] == ""
    assert edits[0]["new_string"] == "def hello():\n    return \"world\""
    assert edits[0]["moved_to"] == ""


def test_dsl_update_file_with_hunk():
    patch = (
        "*** Begin Patch\n"
        "*** Update File: src/foo.py\n"
        "@@ def foo():\n"
        "-    return 1\n"
        "+    return 2\n"
        "*** End Patch\n"
    )
    edits = proxy._parse_apply_patch_dsl(patch)
    assert len(edits) == 1
    assert edits[0]["verb"] == "update"
    assert edits[0]["file_path"] == "src/foo.py"
    assert edits[0]["old_string"] == "    return 1"
    assert edits[0]["new_string"] == "    return 2"


def test_dsl_update_with_context_lines():
    patch = (
        "*** Begin Patch\n"
        "*** Update File: src/foo.py\n"
        "@@ def foo():\n"
        " def foo():\n"
        "-    return 1\n"
        "+    return 2\n"
        "     # trailing comment\n"
        "*** End Patch\n"
    )
    edits = proxy._parse_apply_patch_dsl(patch)
    assert len(edits) == 1
    # context lines (leading space) appear in BOTH old and new
    assert "def foo():" in edits[0]["old_string"]
    assert "def foo():" in edits[0]["new_string"]
    assert "return 1" in edits[0]["old_string"]
    assert "return 2" in edits[0]["new_string"]
    assert "    # trailing comment" in edits[0]["old_string"]
    assert "    # trailing comment" in edits[0]["new_string"]


def test_dsl_delete_file():
    patch = (
        "*** Begin Patch\n"
        "*** Delete File: src/old.py\n"
        "*** End Patch\n"
    )
    edits = proxy._parse_apply_patch_dsl(patch)
    assert len(edits) == 1
    assert edits[0]["verb"] == "delete"
    assert edits[0]["file_path"] == "src/old.py"
    assert edits[0]["new_string"] == ""


def test_dsl_move_to():
    patch = (
        "*** Begin Patch\n"
        "*** Update File: src/foo.py\n"
        "*** Move to: src/bar.py\n"
        "@@ x\n"
        "-old\n"
        "+new\n"
        "*** End Patch\n"
    )
    edits = proxy._parse_apply_patch_dsl(patch)
    assert len(edits) == 1
    assert edits[0]["file_path"] == "src/foo.py"
    assert edits[0]["moved_to"] == "src/bar.py"


def test_dsl_multi_file():
    patch = (
        "*** Begin Patch\n"
        "*** Add File: a.py\n"
        "+a content\n"
        "*** Update File: b.py\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** Delete File: c.py\n"
        "*** End Patch\n"
    )
    edits = proxy._parse_apply_patch_dsl(patch)
    assert len(edits) == 3
    assert [e["verb"] for e in edits] == ["add", "update", "delete"]
    assert [e["file_path"] for e in edits] == ["a.py", "b.py", "c.py"]


def test_dsl_empty_returns_empty():
    assert proxy._parse_apply_patch_dsl("") == []
    assert proxy._parse_apply_patch_dsl("   \n  \n") == []


# ── function_call → edits ──────────────────────────────────────────────────────

def test_function_call_apply_patch_parsed():
    patch = (
        "*** Begin Patch\n"
        "*** Update File: a.py\n"
        "@@\n"
        "-x\n"
        "+y\n"
        "*** End Patch\n"
    )
    fc = {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_xyz",
        "name": "apply_patch",
        "arguments": json.dumps({"input": patch}),
    }
    edits = proxy._parse_codex_function_call_to_edits(fc)
    assert len(edits) == 1
    assert edits[0]["tool_use_id"] == "call_xyz"
    assert edits[0]["tool_name"] == "apply_patch"
    assert edits[0]["file_path"] == "a.py"
    assert edits[0]["verb"] == "update"
    assert edits[0]["old_string"] == "x"
    assert edits[0]["new_string"] == "y"


def test_function_call_apply_patch_multi_file_flattens():
    patch = (
        "*** Begin Patch\n"
        "*** Add File: a.py\n"
        "+a\n"
        "*** Add File: b.py\n"
        "+b\n"
        "*** End Patch\n"
    )
    fc = {
        "type": "function_call",
        "call_id": "call_xyz",
        "name": "apply_patch",
        "arguments": json.dumps({"input": patch}),
    }
    edits = proxy._parse_codex_function_call_to_edits(fc)
    assert len(edits) == 2
    assert edits[0]["edit_index"] == 0
    assert edits[1]["edit_index"] == 1
    assert edits[0]["tool_use_id"] == edits[1]["tool_use_id"] == "call_xyz"


def test_function_call_shell_skipped():
    fc = {
        "type": "function_call",
        "call_id": "call_1",
        "name": "shell",
        "arguments": json.dumps({"command": ["ls", "-la"]}),
    }
    assert proxy._parse_codex_function_call_to_edits(fc) == []


def test_function_call_unknown_tool_skipped():
    fc = {
        "type": "function_call",
        "call_id": "call_1",
        "name": "read_file",
        "arguments": json.dumps({"path": "foo.py"}),
    }
    assert proxy._parse_codex_function_call_to_edits(fc) == []


def test_function_call_malformed_arguments_returns_empty():
    fc = {
        "type": "function_call",
        "call_id": "call_1",
        "name": "apply_patch",
        "arguments": "{not valid json",
    }
    assert proxy._parse_codex_function_call_to_edits(fc) == []


# ── extraction from response body / SSE ────────────────────────────────────────

def test_extract_function_calls_from_body():
    body = json.dumps({
        "output": [
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "I'll patch it."}]},
            {"type": "function_call", "id": "fc_1", "call_id": "call_xyz",
             "name": "apply_patch", "arguments": "{}"},
            {"type": "function_call", "id": "fc_2", "call_id": "call_xyz2",
             "name": "shell", "arguments": "{}"},
        ]
    }).encode()
    fcs = proxy._extract_codex_function_calls_from_body(body)
    assert len(fcs) == 2
    assert fcs[0]["call_id"] == "call_xyz"
    assert fcs[1]["name"] == "shell"


def test_extract_function_calls_from_sse_delta_assembly():
    """Streaming case: output_item.added + two deltas + output_item.done."""
    args_str = json.dumps({"input": "*** Begin Patch\n*** Add File: a.py\n+x\n*** End Patch\n"})
    # split into two halves to simulate streaming
    half = len(args_str) // 2
    part1, part2 = args_str[:half], args_str[half:]

    events = [
        json.dumps({
            "type": "response.output_item.added", "output_index": 0,
            "item": {"id": "fc_1", "type": "function_call",
                     "call_id": "call_xyz", "name": "apply_patch", "arguments": ""},
        }),
        json.dumps({
            "type": "response.function_call_arguments.delta",
            "output_index": 0, "delta": part1,
        }),
        json.dumps({
            "type": "response.function_call_arguments.delta",
            "output_index": 0, "delta": part2,
        }),
        json.dumps({"type": "response.output_item.done", "output_index": 0,
                    "item": {"id": "fc_1", "type": "function_call",
                             "call_id": "call_xyz", "name": "apply_patch",
                             "arguments": args_str}}),
    ]
    sse_bytes = ("\n\n".join(f"data: {e}" for e in events) + "\n\n").encode("utf-8")
    fcs = proxy._extract_codex_function_calls_from_sse([sse_bytes])
    assert len(fcs) == 1
    assert fcs[0]["call_id"] == "call_xyz"
    assert fcs[0]["name"] == "apply_patch"
    assert fcs[0]["arguments"] == args_str


def test_extract_function_calls_from_sse_no_output_item_done():
    """If output_item.done never arrives, fall back to accumulated deltas."""
    args_str = '{"input":"*** Begin Patch\\n*** Delete File: x.py\\n*** End Patch\\n"}'
    events = [
        json.dumps({
            "type": "response.output_item.added", "output_index": 0,
            "item": {"id": "fc_1", "type": "function_call",
                     "call_id": "call_aaa", "name": "apply_patch", "arguments": ""},
        }),
        json.dumps({
            "type": "response.function_call_arguments.delta",
            "output_index": 0, "delta": args_str,
        }),
    ]
    sse_bytes = ("\n\n".join(f"data: {e}" for e in events) + "\n\n").encode("utf-8")
    fcs = proxy._extract_codex_function_calls_from_sse([sse_bytes])
    assert len(fcs) == 1
    assert fcs[0]["arguments"] == args_str


def test_extract_function_call_outputs():
    body = json.dumps({
        "model": "gpt-5-codex",
        "input": [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "function_call_output", "call_id": "call_xyz",
             "output": "Patch applied successfully"},
            {"type": "function_call_output", "call_id": "call_xyz2",
             "output": "Error: file not found"},
        ],
    }).encode()
    outputs = proxy._extract_codex_function_call_outputs(body)
    assert len(outputs) == 2
    assert outputs[0]["call_id"] == "call_xyz"
    assert outputs[1]["output"].startswith("Error")


# ── classifier ─────────────────────────────────────────────────────────────────

def test_classify_applied_empty():
    status, _ = proxy._classify_codex_function_call_output({"output": ""})
    assert status == "applied"


def test_classify_applied_done():
    status, _ = proxy._classify_codex_function_call_output({"output": "Done!"})
    assert status == "applied"


def test_classify_errored():
    status, _ = proxy._classify_codex_function_call_output(
        {"output": "Error: could not find old string"}
    )
    assert status == "errored"


def test_classify_errored_not_found():
    status, _ = proxy._classify_codex_function_call_output(
        {"output": "No such file or directory"}
    )
    assert status == "errored"


def test_classify_rejected():
    status, _ = proxy._classify_codex_function_call_output(
        {"output": "User rejected the patch"}
    )
    assert status == "rejected"


def test_classify_dict_output_with_error():
    status, msg = proxy._classify_codex_function_call_output(
        {"output": {"error": "permission denied"}}
    )
    assert status == "errored"
    assert "permission" in msg


# ── session key ────────────────────────────────────────────────────────────────

def test_session_key_from_instructions():
    sk1 = proxy._codex_session_key(
        {"instructions": "You are Codex"},
        {"authorization": "Bearer sk-abc"},
    )
    sk2 = proxy._codex_session_key(
        {"instructions": "You are Codex"},
        {"authorization": "Bearer sk-abc"},
    )
    sk3 = proxy._codex_session_key(
        {"instructions": "Different"},
        {"authorization": "Bearer sk-abc"},
    )
    assert sk1 == sk2
    assert sk1 != sk3


def test_session_key_from_input_system():
    """When instructions is missing, fall back to a system/developer input item."""
    sk = proxy._codex_session_key(
        {"input": [{"role": "developer", "content": "You are Codex CLI"}]},
        {"authorization": "Bearer sk-abc"},
    )
    assert isinstance(sk, str) and len(sk) == 16


def test_session_key_anthropic_codex_distinct():
    """Anthropic and Codex session keys should never collide for same content."""
    sk_anth = proxy._session_key(
        {"system": "You are Claude"},
        {"authorization": "Bearer sk-abc"},
    )
    sk_codex = proxy._codex_session_key(
        {"instructions": "You are Claude"},
        {"authorization": "Bearer sk-abc"},
    )
    assert sk_anth != sk_codex  # codex prefix in hash input keeps them distinct


def test_is_codex_responses_path():
    assert proxy._is_codex_responses_path("https://api.openai.com/v1/responses")
    assert proxy._is_codex_responses_path("https://api.openai.com/v1/responses/stream")
    assert not proxy._is_codex_responses_path("https://api.openai.com/v1/chat/completions")


# ── full lifecycle ─────────────────────────────────────────────────────────────

def test_full_lifecycle_pending_to_resolved():
    import asyncio

    captured = []
    original_ingest = proxy._ingest_edit

    async def fake_ingest(edit, session_key, status, error_message, provider):
        captured.append((edit, status, provider))

    proxy._ingest_edit = fake_ingest
    proxy._pending_edits.clear()

    try:
        async def run():
            session_key = "codex_session_abc"
            patch = (
                "*** Begin Patch\n"
                "*** Update File: a.py\n"
                "@@\n"
                "-x\n"
                "+y\n"
                "*** End Patch\n"
            )
            fc = {
                "type": "function_call",
                "call_id": "call_42",
                "name": "apply_patch",
                "arguments": json.dumps({"input": patch}),
            }
            await proxy._store_codex_pending_edits(session_key, [fc])
            assert (session_key, "call_42") in proxy._pending_edits

            outputs = [{"type": "function_call_output", "call_id": "call_42",
                        "output": "Patch applied"}]
            await proxy._resolve_codex_pending_edits(session_key, outputs, "openai")
            await asyncio.sleep(0.05)

            assert (session_key, "call_42") not in proxy._pending_edits
            assert len(captured) == 1
            edit, status, provider = captured[0]
            assert status == "applied"
            assert provider == "openai"
            assert edit["file_path"] == "a.py"
            assert edit["verb"] == "update"
            assert edit["new_string"] == "y"

        asyncio.run(run())
    finally:
        proxy._ingest_edit = original_ingest


def test_full_lifecycle_multi_file_patch_one_call_id():
    """A multi-file apply_patch makes N pending records sharing one call_id;
    one function_call_output resolves all of them at once."""
    import asyncio

    captured = []
    original_ingest = proxy._ingest_edit

    async def fake_ingest(edit, session_key, status, error_message, provider):
        captured.append((edit, status))

    proxy._ingest_edit = fake_ingest
    proxy._pending_edits.clear()

    try:
        async def run():
            session_key = "sk"
            patch = (
                "*** Begin Patch\n"
                "*** Add File: a.py\n"
                "+a\n"
                "*** Add File: b.py\n"
                "+b\n"
                "*** End Patch\n"
            )
            fc = {
                "type": "function_call",
                "call_id": "call_multi",
                "name": "apply_patch",
                "arguments": json.dumps({"input": patch}),
            }
            await proxy._store_codex_pending_edits(session_key, [fc])
            outputs = [{"type": "function_call_output", "call_id": "call_multi",
                        "output": "Done!"}]
            await proxy._resolve_codex_pending_edits(session_key, outputs, "openai")
            await asyncio.sleep(0.05)

            assert len(captured) == 2
            assert all(s == "applied" for _, s in captured)
            paths = sorted(e["file_path"] for e, _ in captured)
            assert paths == ["a.py", "b.py"]

        asyncio.run(run())
    finally:
        proxy._ingest_edit = original_ingest


def test_full_lifecycle_errored_classification():
    import asyncio

    captured = []
    original_ingest = proxy._ingest_edit

    async def fake_ingest(edit, session_key, status, error_message, provider):
        captured.append((status, error_message))

    proxy._ingest_edit = fake_ingest
    proxy._pending_edits.clear()

    try:
        async def run():
            session_key = "sk_err"
            patch = "*** Begin Patch\n*** Update File: x.py\n@@\n-a\n+b\n*** End Patch\n"
            fc = {"type": "function_call", "call_id": "call_e", "name": "apply_patch",
                  "arguments": json.dumps({"input": patch})}
            await proxy._store_codex_pending_edits(session_key, [fc])
            outputs = [{"type": "function_call_output", "call_id": "call_e",
                        "output": "Error: did not match expected context"}]
            await proxy._resolve_codex_pending_edits(session_key, outputs, "openai")
            await asyncio.sleep(0.05)
            assert len(captured) == 1
            assert captured[0][0] == "errored"
            assert "context" in captured[0][1]

        asyncio.run(run())
    finally:
        proxy._ingest_edit = original_ingest


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
