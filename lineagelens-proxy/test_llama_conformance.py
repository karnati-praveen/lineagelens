"""Llama conformance tests — LineageLens proxy + Llama out of the box.

Meta's Llama models are served through OpenAI-compatible endpoints (Llama API,
vLLM, llama.cpp server, Ollama, Together, Groq). The proxy's /v1/chat/completions
adapter therefore captures Llama traffic with zero configuration; these tests
pin that contract with Llama-shaped fixtures so a proxy regression cannot
silently break the Llama story:

  1. vLLM-style tool_calls (write / str_replace) are extracted as edits.
  2. The Llama model string flows into the prompt context (records say
     "model: meta-llama/…" instead of an anonymous capture).
  3. Plain-text Llama responses with fenced code are still captured (some
     Llama serving stacks emit edits as text, not tool_calls).
  4. Llama API-style responses (identical wire shape, Llama model) round-trip.
"""
import json
import sys

sys.path.insert(0, ".")
import proxy  # noqa: E402

LLAMA_MODELS = [
    "meta-llama/Llama-3.3-70B-Instruct",
    "Llama-3.1-8B-Instruct",
    "llama3.2:3b",  # Ollama naming
]


def _chat_response(model: str, message: dict) -> bytes:
    return json.dumps({
        "id": "chatcmpl-llama-1",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 40},
    }).encode()


def _request_body(model: str) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a precise coding agent."},
            {"role": "user", "content": "Add a health endpoint to app.py."},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "write_file",
                "parameters": {"type": "object", "properties": {"path": {}, "content": {}}},
            },
        }],
    }


# ── 1. vLLM-style Llama tool_calls are captured as edits ──────────────────────

def test_llama_tool_call_write_captured():
    body = _chat_response(LLAMA_MODELS[0], {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_llama_1",
            "type": "function",
            "function": {
                "name": "write_file",
                "arguments": json.dumps({
                    "path": "app.py",
                    "content": "def health():\n    return {'status': 'ok'}\n",
                }),
            },
        }],
    })
    choices = proxy._extract_openai_choices_from_body(body)
    assert len(choices) == 1
    edits = proxy._parse_openai_tool_call_to_edits(choices[0]["tool_calls"][0])
    assert len(edits) == 1
    assert edits[0]["file_path"] == "app.py"
    assert edits[0]["verb"] == "write"
    assert "def health():" in edits[0]["new_string"]
    assert edits[0]["tool_use_id"] == "call_llama_1"


def test_llama_tool_call_replace_captured_for_all_model_spellings():
    for model in LLAMA_MODELS:
        body = _chat_response(model, {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_llama_2",
                "type": "function",
                "function": {
                    "name": "str_replace",
                    "arguments": json.dumps({
                        "path": "app.py", "old_str": "pass", "new_str": "return 42",
                    }),
                },
            }],
        })
        choices = proxy._extract_openai_choices_from_body(body)
        edits = proxy._parse_openai_tool_call_to_edits(choices[0]["tool_calls"][0])
        assert edits and edits[0]["verb"] == "replace", model
        assert edits[0]["file_path"] == "app.py", model


# ── 2. The Llama model string reaches the captured prompt context ─────────────

def test_llama_model_flows_into_prompt_context():
    ctx = proxy._extract_openai_chat_prompt_context(_request_body(LLAMA_MODELS[0]))
    assert ctx["model"] == LLAMA_MODELS[0]
    assert ctx["system"] == "You are a precise coding agent."
    roles = [m["role"] for m in ctx["messages"]]
    assert roles == ["user"]  # system extracted separately, tool echoes excluded


def test_llama_tool_result_echoes_excluded_from_prompt_context():
    body = _request_body(LLAMA_MODELS[1])
    body["messages"].append({"role": "tool", "tool_call_id": "x", "content": "file written"})
    ctx = proxy._extract_openai_chat_prompt_context(body)
    assert all(m["role"] != "tool" for m in ctx["messages"])


# ── 3. Text-only Llama responses with fenced code still captured ──────────────

def test_llama_text_response_fenced_code_captured():
    body = _chat_response(LLAMA_MODELS[0], {
        "role": "assistant",
        "content": (
            "Here's the health endpoint for app.py:\n\n"
            "```python app.py\n"
            "def health():\n    return {'status': 'ok'}\n"
            "```\n"
        ),
    })
    choices = proxy._extract_openai_choices_from_body(body)
    edits = proxy._parse_text_content_to_edits(choices[0]["content"])
    assert edits, "Llama fenced-code response must still produce a captured edit"
    assert edits[0]["file_path"] == "app.py"
    assert "def health():" in edits[0]["new_string"]


# ── 4. Llama tool results resolve pending edits (accept/reject tracking) ──────

def test_llama_tool_results_recognized():
    follow_up = json.dumps({
        "model": LLAMA_MODELS[0],
        "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "tool_calls": [{
                "id": "call_llama_1", "type": "function",
                "function": {"name": "write_file", "arguments": "{}"},
            }]},
            {"role": "tool", "tool_call_id": "call_llama_1", "content": "File created successfully at: app.py"},
        ],
    }).encode()
    results = proxy._extract_openai_tool_results(follow_up)
    assert results, "Llama tool_result must be extracted for accepted/rejected tracking"
    status, _detail = proxy._classify_openai_tool_result(results[0])
    assert status == "applied"
