"""Tests for the Google Gemini CLI adapter in proxy.py."""
import json
import sys

sys.path.insert(0, ".")
import proxy  # noqa: E402


# ── functionCall → edits ───────────────────────────────────────────────────────

def test_write_file_tool_parse():
    fc = {
        "name": "write_file",
        "args": {"file_path": "src/new.py", "content": "print('hello')"},
    }
    edits = proxy._parse_gemini_function_call_to_edits(fc)
    assert len(edits) == 1
    assert edits[0]["tool_name"] == "write_file"
    assert edits[0]["file_path"] == "src/new.py"
    assert edits[0]["new_string"] == "print('hello')"
    assert edits[0]["old_string"] == ""
    assert edits[0]["verb"] == "write"


def test_pascalcase_write_file():
    fc = {
        "name": "WriteFile",
        "args": {"file_path": "a.py", "content": "x = 1"},
    }
    edits = proxy._parse_gemini_function_call_to_edits(fc)
    assert len(edits) == 1
    assert edits[0]["new_string"] == "x = 1"


def test_replace_tool_parse():
    fc = {
        "name": "replace",
        "args": {
            "file_path": "src/foo.py",
            "old_string": "return 1",
            "new_string": "return 2",
        },
    }
    edits = proxy._parse_gemini_function_call_to_edits(fc)
    assert len(edits) == 1
    assert edits[0]["tool_name"] == "replace"
    assert edits[0]["old_string"] == "return 1"
    assert edits[0]["new_string"] == "return 2"
    assert edits[0]["verb"] == "replace"


def test_replace_camelcase_arg_names():
    """Some Gemini versions use camelCase arg names."""
    fc = {
        "name": "replace",
        "args": {
            "filePath": "src/foo.py",
            "oldString": "x",
            "newString": "y",
        },
    }
    edits = proxy._parse_gemini_function_call_to_edits(fc)
    assert len(edits) == 1
    assert edits[0]["file_path"] == "src/foo.py"
    assert edits[0]["old_string"] == "x"
    assert edits[0]["new_string"] == "y"


def test_create_file_tool_parse():
    fc = {
        "name": "create_file",
        "args": {"file_path": "new.py", "content": "x"},
    }
    edits = proxy._parse_gemini_function_call_to_edits(fc)
    assert len(edits) == 1
    assert edits[0]["verb"] == "create"


def test_read_file_skipped():
    fc = {"name": "read_file", "args": {"file_path": "foo.py"}}
    assert proxy._parse_gemini_function_call_to_edits(fc) == []


def test_run_shell_command_skipped():
    fc = {"name": "run_shell_command", "args": {"command": "ls"}}
    assert proxy._parse_gemini_function_call_to_edits(fc) == []


def test_function_call_missing_file_path():
    fc = {"name": "write_file", "args": {"content": "x"}}
    assert proxy._parse_gemini_function_call_to_edits(fc) == []


def test_function_call_uses_explicit_id_when_present():
    fc = {
        "id": "fc_explicit_123",
        "name": "write_file",
        "args": {"file_path": "a.py", "content": "x"},
    }
    edits = proxy._parse_gemini_function_call_to_edits(fc)
    assert edits[0]["tool_use_id"] == "fc_explicit_123"


def test_function_call_synthesizes_id_when_absent():
    fc = {"name": "write_file", "args": {"file_path": "a.py", "content": "x"}}
    edits = proxy._parse_gemini_function_call_to_edits(fc)
    assert edits[0]["tool_use_id"].startswith("gemini_")
    assert len(edits[0]["tool_use_id"]) == len("gemini_") + 16


# ── extraction from response body / SSE ────────────────────────────────────────

def test_extract_function_calls_from_body():
    body = json.dumps({
        "candidates": [{
            "content": {
                "role": "model",
                "parts": [
                    {"text": "I'll write the file."},
                    {"functionCall": {"name": "write_file",
                                      "args": {"file_path": "a.py", "content": "x"}}},
                ],
            },
        }],
    }).encode()
    fcs = proxy._extract_gemini_function_calls_from_body(body)
    assert len(fcs) == 1
    assert fcs[0]["name"] == "write_file"


def test_extract_function_calls_from_body_parallel_calls():
    body = json.dumps({
        "candidates": [{
            "content": {
                "role": "model",
                "parts": [
                    {"functionCall": {"name": "write_file",
                                      "args": {"file_path": "a.py", "content": "a"}}},
                    {"functionCall": {"name": "write_file",
                                      "args": {"file_path": "b.py", "content": "b"}}},
                ],
            },
        }],
    }).encode()
    fcs = proxy._extract_gemini_function_calls_from_body(body)
    assert len(fcs) == 2
    assert fcs[0]["args"]["file_path"] == "a.py"
    assert fcs[1]["args"]["file_path"] == "b.py"


def test_extract_function_calls_from_sse_single_chunk():
    chunk_data = json.dumps({
        "candidates": [{
            "content": {
                "parts": [{"functionCall": {"name": "replace",
                                            "args": {"file_path": "x.py", "old_string": "a", "new_string": "b"}}}],
            },
        }],
    })
    sse = (f"data: {chunk_data}\n\n").encode("utf-8")
    fcs = proxy._extract_gemini_function_calls_from_sse([sse])
    assert len(fcs) == 1
    assert fcs[0]["name"] == "replace"


def test_extract_function_calls_from_sse_multi_chunk():
    """Two SSE chunks, each carrying one functionCall."""
    c1 = json.dumps({"candidates": [{"content": {"parts": [
        {"functionCall": {"name": "write_file", "args": {"file_path": "a.py", "content": "a"}}}
    ]}}]})
    c2 = json.dumps({"candidates": [{"content": {"parts": [
        {"functionCall": {"name": "write_file", "args": {"file_path": "b.py", "content": "b"}}}
    ]}}]})
    sse = (f"data: {c1}\n\ndata: {c2}\n\n").encode("utf-8")
    fcs = proxy._extract_gemini_function_calls_from_sse([sse])
    assert len(fcs) == 2
    paths = [fc["args"]["file_path"] for fc in fcs]
    assert paths == ["a.py", "b.py"]


def test_extract_function_responses_simple():
    body = json.dumps({
        "contents": [
            {"role": "user", "parts": [{"text": "do x"}]},
            {"role": "model", "parts": [{"functionCall": {"name": "write_file",
                                                          "args": {"file_path": "a.py", "content": "x"}}}]},
            {"role": "user", "parts": [{"functionResponse": {"name": "write_file",
                                                             "response": {"output": "Wrote a.py"}}}]},
        ],
    }).encode()
    frs = proxy._extract_gemini_function_responses(body)
    assert len(frs) == 1
    assert frs[0]["name"] == "write_file"


def test_extract_function_responses_takes_only_most_recent_message():
    body = json.dumps({
        "contents": [
            {"role": "user", "parts": [{"text": "do x"}]},
            {"role": "model", "parts": [{"functionCall": {"name": "write_file",
                                                          "args": {"file_path": "a.py", "content": "x"}}}]},
            {"role": "user", "parts": [{"functionResponse": {"name": "write_file",
                                                             "response": {"output": "Done old"}}}]},
            {"role": "model", "parts": [{"functionCall": {"name": "replace",
                                                          "args": {"file_path": "a.py", "old_string": "x", "new_string": "y"}}}]},
            {"role": "user", "parts": [{"functionResponse": {"name": "replace",
                                                             "response": {"output": "Done new"}}}]},
        ],
    }).encode()
    frs = proxy._extract_gemini_function_responses(body)
    # Should only return the most recent batch (the replace response)
    assert len(frs) == 1
    assert frs[0]["name"] == "replace"


def test_extract_function_responses_function_role():
    """Newer Gemini API uses role='function' for responses."""
    body = json.dumps({
        "contents": [
            {"role": "user", "parts": [{"text": "do x"}]},
            {"role": "model", "parts": [{"functionCall": {"name": "write_file",
                                                          "args": {"file_path": "a.py", "content": "x"}}}]},
            {"role": "function", "parts": [{"functionResponse": {"name": "write_file",
                                                                  "response": {"output": "ok"}}}]},
        ],
    }).encode()
    frs = proxy._extract_gemini_function_responses(body)
    assert len(frs) == 1


def test_extract_function_responses_parallel():
    body = json.dumps({
        "contents": [
            {"role": "user", "parts": [{"text": "do x"}]},
            {"role": "model", "parts": [
                {"functionCall": {"name": "write_file", "args": {"file_path": "a.py", "content": "a"}}},
                {"functionCall": {"name": "write_file", "args": {"file_path": "b.py", "content": "b"}}},
            ]},
            {"role": "user", "parts": [
                {"functionResponse": {"name": "write_file", "response": {"output": "Wrote a"}}},
                {"functionResponse": {"name": "write_file", "response": {"output": "Wrote b"}}},
            ]},
        ],
    }).encode()
    frs = proxy._extract_gemini_function_responses(body)
    assert len(frs) == 2


# ── classifier ─────────────────────────────────────────────────────────────────

def test_classify_applied_dict():
    fr = {"name": "write_file", "response": {"output": "Wrote file successfully"}}
    status, _ = proxy._classify_gemini_function_response(fr)
    assert status == "applied"


def test_classify_errored_with_error_key():
    fr = {"name": "write_file", "response": {"error": {"message": "Permission denied"}}}
    status, msg = proxy._classify_gemini_function_response(fr)
    assert status == "errored"
    assert "Permission" in msg


def test_classify_errored_text_keyword():
    fr = {"name": "write_file", "response": {"output": "Error: file not writable"}}
    status, _ = proxy._classify_gemini_function_response(fr)
    assert status == "errored"


def test_classify_rejected():
    fr = {"name": "write_file", "response": {"output": "User rejected the change"}}
    status, _ = proxy._classify_gemini_function_response(fr)
    assert status == "rejected"


def test_classify_string_response():
    fr = {"name": "write_file", "response": "Done"}
    status, _ = proxy._classify_gemini_function_response(fr)
    assert status == "applied"


# ── session key ────────────────────────────────────────────────────────────────

def test_session_key_from_systeminstruction_parts():
    sk1 = proxy._gemini_session_key(
        {"systemInstruction": {"parts": [{"text": "You are Gemini CLI"}]}},
        {"x-goog-api-key": "AIza-abc"},
    )
    sk2 = proxy._gemini_session_key(
        {"systemInstruction": {"parts": [{"text": "You are Gemini CLI"}]}},
        {"x-goog-api-key": "AIza-abc"},
    )
    sk3 = proxy._gemini_session_key(
        {"systemInstruction": {"parts": [{"text": "Different"}]}},
        {"x-goog-api-key": "AIza-abc"},
    )
    assert sk1 == sk2
    assert sk1 != sk3


def test_session_key_snake_case_field():
    sk = proxy._gemini_session_key(
        {"system_instruction": {"parts": [{"text": "You are Gemini"}]}},
        {"authorization": "Bearer x"},
    )
    assert isinstance(sk, str) and len(sk) == 16


def test_session_keys_three_adapters_distinct():
    """Same content across adapters must produce different session keys."""
    same_prompt = "You are an AI"
    same_auth = {"authorization": "Bearer same"}
    sk_anth = proxy._session_key({"system": same_prompt}, same_auth)
    sk_codex = proxy._codex_session_key({"instructions": same_prompt}, same_auth)
    sk_gemini = proxy._gemini_session_key(
        {"systemInstruction": {"parts": [{"text": same_prompt}]}}, same_auth)
    # All three must be distinct so dict keys never collide across adapters.
    assert sk_anth != sk_codex
    assert sk_anth != sk_gemini
    assert sk_codex != sk_gemini


# ── full lifecycle ─────────────────────────────────────────────────────────────

def test_full_lifecycle_applied():
    import asyncio

    captured = []
    original_ingest = proxy._ingest_edit

    async def fake_ingest(edit, session_key, status, error_message, provider):
        captured.append((edit, status, provider))

    proxy._ingest_edit = fake_ingest
    proxy._pending_edits.clear()

    try:
        async def run():
            session_key = "gemini_session_abc"
            fc = {
                "name": "replace",
                "args": {"file_path": "a.py", "old_string": "x", "new_string": "y"},
            }
            await proxy._store_gemini_pending_edits(session_key, [fc])
            assert len([k for k in proxy._pending_edits if k[0] == session_key]) == 1

            fr = {"name": "replace", "response": {"output": "Replaced"}}
            await proxy._resolve_gemini_pending_edits(session_key, [fr], "gemini")
            await asyncio.sleep(0.05)

            assert len([k for k in proxy._pending_edits if k[0] == session_key]) == 0
            assert len(captured) == 1
            edit, status, provider = captured[0]
            assert status == "applied"
            assert provider == "gemini"
            assert edit["file_path"] == "a.py"
            assert edit["new_string"] == "y"

        asyncio.run(run())
    finally:
        proxy._ingest_edit = original_ingest


def test_full_lifecycle_fifo_correlation_two_calls_two_responses():
    """Two write_file calls; two write_file responses; FIFO matches them in order."""
    import asyncio

    captured = []
    original_ingest = proxy._ingest_edit

    async def fake_ingest(edit, session_key, status, error_message, provider):
        captured.append(edit["file_path"])

    proxy._ingest_edit = fake_ingest
    proxy._pending_edits.clear()

    try:
        async def run():
            session_key = "sk"
            fc1 = {"name": "write_file", "args": {"file_path": "a.py", "content": "a"}}
            fc2 = {"name": "write_file", "args": {"file_path": "b.py", "content": "b"}}
            await proxy._store_gemini_pending_edits(session_key, [fc1, fc2])
            # Both pending entries should be in dict in insertion order
            assert len([k for k in proxy._pending_edits if k[0] == session_key]) == 2

            # Two responses, both with same tool name; FIFO matches them in order
            frs = [
                {"name": "write_file", "response": {"output": "wrote a"}},
                {"name": "write_file", "response": {"output": "wrote b"}},
            ]
            await proxy._resolve_gemini_pending_edits(session_key, frs, "gemini")
            await asyncio.sleep(0.05)

            assert len(captured) == 2
            # First response resolves first call (a.py), second resolves second (b.py)
            assert captured == ["a.py", "b.py"]
            assert len([k for k in proxy._pending_edits if k[0] == session_key]) == 0

        asyncio.run(run())
    finally:
        proxy._ingest_edit = original_ingest


def test_full_lifecycle_explicit_id_correlation():
    """When functionCall carries an `id`, response with same `id` resolves it exactly."""
    import asyncio

    captured = []
    original_ingest = proxy._ingest_edit

    async def fake_ingest(edit, session_key, status, error_message, provider):
        captured.append(edit)

    proxy._ingest_edit = fake_ingest
    proxy._pending_edits.clear()

    try:
        async def run():
            session_key = "sk_id"
            # Two calls with explicit (different) ids
            fc1 = {"id": "call_aaa", "name": "write_file",
                   "args": {"file_path": "a.py", "content": "a"}}
            fc2 = {"id": "call_bbb", "name": "write_file",
                   "args": {"file_path": "b.py", "content": "b"}}
            await proxy._store_gemini_pending_edits(session_key, [fc1, fc2])

            # Resolve only the SECOND call by id (out of FIFO order)
            fr = {"id": "call_bbb", "name": "write_file",
                  "response": {"output": "wrote b"}}
            await proxy._resolve_gemini_pending_edits(session_key, [fr], "gemini")
            await asyncio.sleep(0.05)

            assert len(captured) == 1
            assert captured[0]["file_path"] == "b.py"  # not the FIFO first
            assert (session_key, "call_aaa") in proxy._pending_edits

        asyncio.run(run())
    finally:
        proxy._ingest_edit = original_ingest


def test_full_lifecycle_errored():
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
            fc = {"name": "replace",
                  "args": {"file_path": "x.py", "old_string": "a", "new_string": "b"}}
            await proxy._store_gemini_pending_edits(session_key, [fc])
            fr = {"name": "replace",
                  "response": {"error": {"message": "Could not find old_string"}}}
            await proxy._resolve_gemini_pending_edits(session_key, [fr], "gemini")
            await asyncio.sleep(0.05)
            assert len(captured) == 1
            assert captured[0][0] == "errored"
            assert "old_string" in captured[0][1]

        asyncio.run(run())
    finally:
        proxy._ingest_edit = original_ingest


def test_unresolved_response_silently_skipped():
    """A functionResponse with no matching pending edit is a no-op."""
    import asyncio

    captured = []
    original_ingest = proxy._ingest_edit

    async def fake_ingest(edit, session_key, status, error_message, provider):
        captured.append(edit)

    proxy._ingest_edit = fake_ingest
    proxy._pending_edits.clear()

    try:
        async def run():
            session_key = "sk_orphan"
            fr = {"name": "read_file", "response": {"content": "..."}}
            await proxy._resolve_gemini_pending_edits(session_key, [fr], "gemini")
            await asyncio.sleep(0.05)
            assert captured == []

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
