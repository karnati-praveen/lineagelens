"""Tests for Agent Trace import/export: schema shape, conversion, round-trip, and hash chain."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault(
    "JWT_SECRET_KEY",
    "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789",
)

from app.schemas.agent_trace import AgentTraceRecord, SCHEMA_VERSION
from app.services.agent_trace_service import (
    agent_trace_to_provenance_payload,
    compute_import_hash,
    record_to_agent_trace,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _ts(s: str = "2026-06-01T10:00:00+00:00") -> datetime:
    return datetime.fromisoformat(s)


def _rec(
    uuid,
    *,
    model="claude-opus-4-5",
    file_path=None,
    workspace_id="ws-test",
    cursor_line=None,
    inserted_code=None,
    payload=None,
):
    return SimpleNamespace(
        uuid=uuid,
        file_path=file_path or f"src/{uuid}.py",
        model_name=model,
        workspace_id=workspace_id,
        timestamp_iso=_ts(),
        inserted_code=inserted_code,
        cursor_line=cursor_line,
        provenance_payload=payload or {},
    )


# ── schema shape ──────────────────────────────────────────────────────────────

def test_agent_trace_top_level_keys():
    trace = record_to_agent_trace(_rec("r1"))
    d = trace.model_dump(by_alias=False)
    for k in ("schema_version", "uuid", "timestamp", "file_path", "workspace_id",
              "tool", "model", "confidence", "evidence", "exported_at"):
        assert k in d, f"missing top-level key: {k!r}"


def test_agent_trace_schema_version():
    trace = record_to_agent_trace(_rec("r1"))
    assert trace.schema_version == SCHEMA_VERSION


def test_agent_trace_uuid_preserved():
    trace = record_to_agent_trace(_rec("abc-123"))
    assert trace.uuid == "abc-123"


def test_agent_trace_file_path_preserved():
    trace = record_to_agent_trace(_rec("r1", file_path="src/auth.py"))
    assert trace.file_path == "src/auth.py"


def test_agent_trace_model_preserved():
    trace = record_to_agent_trace(_rec("r1", model="gpt-4o"))
    assert trace.model.name == "gpt-4o"


def test_agent_trace_workspace_id_preserved():
    trace = record_to_agent_trace(_rec("r1", workspace_id="ws-acme"))
    assert trace.workspace_id == "ws-acme"


def test_agent_trace_inserted_code_preview_truncated():
    long_code = "x = 1\n" * 100
    trace = record_to_agent_trace(_rec("r1", inserted_code=long_code))
    assert trace.inserted_code_preview is not None
    assert len(trace.inserted_code_preview) <= 120


def test_agent_trace_no_inserted_code_gives_none_preview():
    trace = record_to_agent_trace(_rec("r1", inserted_code=None))
    assert trace.inserted_code_preview is None


def test_agent_trace_newlines_replaced_in_preview():
    trace = record_to_agent_trace(_rec("r1", inserted_code="a = 1\nb = 2\n"))
    assert "\n" not in (trace.inserted_code_preview or "")


# ── new spec fields ───────────────────────────────────────────────────────────

def test_agent_trace_line_start_from_cursor_line():
    trace = record_to_agent_trace(_rec("r1", cursor_line=42))
    assert trace.line_start == 42


def test_agent_trace_line_start_none_when_no_cursor():
    trace = record_to_agent_trace(_rec("r1", cursor_line=None))
    assert trace.line_start is None


def test_agent_trace_contributor_type_ai_for_high_confidence():
    rec = _rec("r1", payload={
        "normalizedEvent": {"confidence": {"value": 0.9, "level": "high"}}
    })
    trace = record_to_agent_trace(rec)
    assert trace.contributor_type == "AI"


def test_agent_trace_contributor_type_mixed_for_mid_confidence():
    rec = _rec("r1", payload={
        "normalizedEvent": {"confidence": {"value": 0.5, "level": "medium"}}
    })
    trace = record_to_agent_trace(rec)
    assert trace.contributor_type == "mixed"


def test_agent_trace_contributor_type_unknown_for_low_confidence():
    rec = _rec("r1", payload={
        "normalizedEvent": {"confidence": {"value": 0.1, "level": "low"}}
    })
    trace = record_to_agent_trace(rec)
    assert trace.contributor_type == "unknown"


def test_agent_trace_contributor_type_unknown_for_no_model():
    trace = record_to_agent_trace(_rec("r1", model=None))
    assert trace.contributor_type == "unknown"


def test_agent_trace_contributor_type_in_dump():
    trace = record_to_agent_trace(_rec("r1", payload={
        "normalizedEvent": {"confidence": {"value": 0.85, "level": "high"}}
    }))
    d = trace.model_dump(by_alias=False)
    assert "contributor_type" in d


# ── tool extraction ───────────────────────────────────────────────────────────

def test_agent_trace_tool_extracted_from_payload():
    rec = _rec("r1", payload={
        "normalizedEvent": {
            "source": {
                "toolName": "claude-code",
                "adapterName": "anthropic-proxy",
                "sessionId": "sess-001",
                "conversationId": "conv-abc",
                "runId": "run-xyz",
                "operationType": "edit",
                "sessionKind": "interactive",
            }
        }
    })
    trace = record_to_agent_trace(rec)
    assert trace.tool.name == "claude-code"
    assert trace.tool.adapter == "anthropic-proxy"
    assert trace.tool.session_id == "sess-001"
    assert trace.tool.conversation_id == "conv-abc"
    assert trace.tool.run_id == "run-xyz"
    assert trace.tool.operation_type == "edit"
    assert trace.tool.session_kind == "interactive"


def test_agent_trace_tool_empty_when_no_payload():
    trace = record_to_agent_trace(_rec("r1", payload={}))
    assert trace.tool.name is None
    assert trace.tool.adapter is None


# ── evidence extraction ───────────────────────────────────────────────────────

def test_agent_trace_evidence_items_extracted():
    rec = _rec("r1", payload={
        "normalizedEvent": {
            "source": {
                "evidence": [
                    {"source": "proxy", "field": "model", "value": "claude-opus-4-5", "weight": 0.9},
                    {"source": "extension", "field": "file_path", "value": "auth.py", "weight": 0.5},
                ]
            }
        }
    })
    trace = record_to_agent_trace(rec)
    assert len(trace.evidence) == 2
    assert trace.evidence[0].source == "proxy"
    assert trace.evidence[0].weight == 0.9
    assert trace.evidence[1].source == "extension"


def test_agent_trace_empty_evidence_gives_empty_list():
    trace = record_to_agent_trace(_rec("r1"))
    assert trace.evidence == []


# ── round-trip ────────────────────────────────────────────────────────────────

def test_agent_trace_round_trip_payload_structure():
    """agent_trace_to_provenance_payload must produce the structure the import route expects."""
    rec = _rec("r1", model="claude-opus-4-5", cursor_line=10, payload={
        "normalizedEvent": {
            "source": {"toolName": "claude-code"},
            "confidence": {"value": 0.85, "level": "high"},
        }
    })
    trace = record_to_agent_trace(rec)
    payload = agent_trace_to_provenance_payload(
        trace, workspace_id="ws-test", imported_at="2026-06-01T00:00:00+00:00"
    )

    assert payload["workspaceId"] == "ws-test"
    assert payload["filePath"] == rec.file_path
    assert payload["importSource"] == "agent-trace-import"
    ne = payload["normalizedEvent"]
    assert ne["source"]["toolName"] == "claude-code"
    assert ne["model"]["name"] == "claude-opus-4-5"
    assert ne["confidence"]["value"] == 0.85


def test_agent_trace_round_trip_evidence_preserved():
    rec = _rec("r1", payload={
        "normalizedEvent": {
            "source": {
                "evidence": [{"source": "proxy", "weight": 0.9}]
            }
        }
    })
    trace = record_to_agent_trace(rec)
    payload = agent_trace_to_provenance_payload(trace, "ws", "2026-06-01T00:00:00+00:00")
    evidence = payload["normalizedEvent"]["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["source"] == "proxy"


def test_agent_trace_jsonl_serializable():
    """The exported record must serialize to valid JSON without encoding errors."""
    trace = record_to_agent_trace(_rec("r1"))
    line = json.dumps(trace.model_dump(by_alias=False), separators=(",", ":"))
    parsed = json.loads(line)
    assert parsed["uuid"] == "r1"


def test_agent_trace_camel_case_aliases_in_dump():
    trace = record_to_agent_trace(_rec("r1"))
    d = trace.model_dump(by_alias=True)
    assert "schemaVersion" in d
    assert "filePath" in d
    assert "workspaceId" in d
    assert "exportedAt" in d


# ── schema validation ─────────────────────────────────────────────────────────

def test_agent_trace_record_validates_minimal():
    obj = {
        "schemaVersion": SCHEMA_VERSION,
        "uuid": "00000000-0000-4000-8000-000000000001",
        "timestamp": "2026-06-01T10:00:00+00:00",
        "filePath": "src/foo.py",
        "workspaceId": "ws-test",
    }
    trace = AgentTraceRecord.model_validate(obj, by_alias=True)
    assert trace.uuid == "00000000-0000-4000-8000-000000000001"


def test_agent_trace_record_rejects_wrong_schema_version():
    from pydantic import ValidationError
    obj = {
        "schemaVersion": "bogus-schema/99",
        "uuid": "00000000-0000-4000-8000-000000000001",
        "timestamp": "2026-06-01T10:00:00+00:00",
        "filePath": "src/foo.py",
        "workspaceId": "ws-test",
    }
    with pytest.raises(ValidationError):
        AgentTraceRecord.model_validate(obj, by_alias=True)


def test_agent_trace_record_accepts_snake_case():
    # populate_by_name=True means model_validate() accepts both aliases and field names.
    obj = {
        "schema_version": SCHEMA_VERSION,
        "uuid": "00000000-0000-4000-8000-000000000002",
        "timestamp": "2026-06-01T10:00:00+00:00",
        "file_path": "src/bar.py",
        "workspace_id": "ws-test",
    }
    trace = AgentTraceRecord.model_validate(obj)
    assert trace.uuid == "00000000-0000-4000-8000-000000000002"


def test_agent_trace_record_new_fields_optional():
    obj = {
        "schemaVersion": SCHEMA_VERSION,
        "uuid": "00000000-0000-4000-8000-000000000003",
        "timestamp": "2026-06-01T10:00:00+00:00",
        "filePath": "src/baz.py",
        "workspaceId": "ws-test",
        "lineStart": 10,
        "lineEnd": 25,
        "contributorType": "AI",
    }
    trace = AgentTraceRecord.model_validate(obj, by_alias=True)
    assert trace.line_start == 10
    assert trace.line_end == 25
    assert trace.contributor_type == "AI"


# ── hash chain ────────────────────────────────────────────────────────────────

def _minimal_trace(uuid: str, workspace_id: str = "ws-test") -> AgentTraceRecord:
    return AgentTraceRecord.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "uuid": uuid,
        "timestamp": "2026-06-01T10:00:00+00:00",
        "filePath": f"src/{uuid}.py",
        "workspaceId": workspace_id,
    }, by_alias=True)


def test_compute_import_hash_returns_nonempty_strings():
    trace = _minimal_trace("r1")
    prompt_sha, rec_hash = compute_import_hash(trace, prev_hash=None)
    assert isinstance(prompt_sha, str) and len(prompt_sha) == 64
    assert isinstance(rec_hash, str) and len(rec_hash) == 64


def test_compute_import_hash_is_hex():
    trace = _minimal_trace("r1")
    _, rec_hash = compute_import_hash(trace, prev_hash=None)
    assert all(c in "0123456789abcdef" for c in rec_hash)


def test_compute_import_hash_chain_links():
    """Second record's prev_hash must equal first record's record_hash."""
    t1 = _minimal_trace("r1")
    t2 = _minimal_trace("r2")
    _, hash1 = compute_import_hash(t1, prev_hash=None)
    _, hash2 = compute_import_hash(t2, prev_hash=hash1)
    # Verify that hash2 changes when prev_hash changes (tamper detection).
    _, hash2_no_chain = compute_import_hash(t2, prev_hash=None)
    assert hash2 != hash2_no_chain


def test_compute_import_hash_different_records_differ():
    t1 = _minimal_trace("r1")
    t2 = _minimal_trace("r2")
    _, h1 = compute_import_hash(t1, prev_hash=None)
    _, h2 = compute_import_hash(t2, prev_hash=None)
    assert h1 != h2


def test_compute_import_hash_deterministic():
    trace = _minimal_trace("r1")
    _, h1 = compute_import_hash(trace, prev_hash="abc")
    _, h2 = compute_import_hash(trace, prev_hash="abc")
    assert h1 == h2
