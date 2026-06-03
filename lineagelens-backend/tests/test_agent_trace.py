"""Tests for Agent Trace import/export: spec conformance, schema shape, conversion,
round-trip, fixture-import, JSON schema validation, and hash chain.

All tests exercise the cursor/agent-trace 0.1.0 format.
"""
from __future__ import annotations

import json
import os
import pathlib
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault(
    "JWT_SECRET_KEY",
    "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789",
)

from app.schemas.agent_trace import (
    AgentTraceDocument,
    AgentTraceRecord,  # backward-compat alias — must equal AgentTraceDocument
    SPEC_VERSION,
    SCHEMA_VERSION,
)
from app.services.agent_trace_service import (
    agent_trace_to_provenance_payload,
    compute_import_hash,
    record_to_agent_trace,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


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


# ── backward-compat alias ─────────────────────────────────────────────────────

def test_agent_trace_record_is_alias_for_document():
    assert AgentTraceRecord is AgentTraceDocument


# ── spec version ──────────────────────────────────────────────────────────────

def test_spec_version_is_semver():
    assert SPEC_VERSION == "0.1.0"


def test_export_version_field_is_semver():
    doc = record_to_agent_trace(_rec("r1"))
    assert doc.version == SPEC_VERSION


# ── top-level spec fields ─────────────────────────────────────────────────────

def test_agent_trace_top_level_keys():
    doc = record_to_agent_trace(_rec("r1"))
    d = doc.model_dump()
    for k in ("version", "id", "timestamp", "files", "metadata"):
        assert k in d, f"missing top-level key: {k!r}"


def test_agent_trace_id_preserved():
    doc = record_to_agent_trace(_rec("abc-123"))
    assert doc.id == "abc-123"


def test_agent_trace_timestamp_preserved():
    doc = record_to_agent_trace(_rec("r1"))
    assert doc.timestamp.startswith("2026-06-01T")


# ── files structure ───────────────────────────────────────────────────────────

def test_agent_trace_file_path_in_files():
    doc = record_to_agent_trace(_rec("r1", file_path="src/auth.py"))
    assert len(doc.files) == 1
    assert doc.files[0].path == "src/auth.py"


def test_agent_trace_files_has_one_conversation():
    doc = record_to_agent_trace(_rec("r1"))
    assert len(doc.files[0].conversations) == 1


def test_agent_trace_conversation_has_one_range():
    doc = record_to_agent_trace(_rec("r1"))
    assert len(doc.files[0].conversations[0].ranges) == 1


# ── contributor (model + type) ────────────────────────────────────────────────

def test_agent_trace_model_in_contributor_model_id():
    doc = record_to_agent_trace(_rec("r1", model="gpt-4o"))
    contrib = doc.files[0].conversations[0].contributor
    assert contrib is not None
    assert contrib.model_id == "gpt-4o"


def test_agent_trace_contributor_type_ai_lowercase():
    """contributor.type must be lowercase 'ai', NOT uppercase 'AI'."""
    rec = _rec("r1", payload={
        "normalizedEvent": {"confidence": {"value": 0.9, "level": "high"}}
    })
    doc = record_to_agent_trace(rec)
    assert doc.files[0].conversations[0].contributor.type == "ai"


def test_agent_trace_contributor_type_mixed():
    rec = _rec("r1", payload={
        "normalizedEvent": {"confidence": {"value": 0.5, "level": "medium"}}
    })
    doc = record_to_agent_trace(rec)
    assert doc.files[0].conversations[0].contributor.type == "mixed"


def test_agent_trace_contributor_type_unknown_low_confidence():
    rec = _rec("r1", payload={
        "normalizedEvent": {"confidence": {"value": 0.1, "level": "low"}}
    })
    doc = record_to_agent_trace(rec)
    assert doc.files[0].conversations[0].contributor.type == "unknown"


def test_agent_trace_contributor_type_unknown_no_model():
    doc = record_to_agent_trace(_rec("r1", model=None))
    assert doc.files[0].conversations[0].contributor.type == "unknown"


# ── line ranges (1-indexed, snake_case) ──────────────────────────────────────

def test_agent_trace_start_line_from_cursor_line_1indexed():
    """cursor_line=42 (0-based) → start_line=43 (1-indexed per spec)."""
    doc = record_to_agent_trace(_rec("r1", cursor_line=42))
    assert doc.files[0].conversations[0].ranges[0].start_line == 43


def test_agent_trace_start_line_defaults_to_1_when_no_cursor():
    doc = record_to_agent_trace(_rec("r1", cursor_line=None))
    assert doc.files[0].conversations[0].ranges[0].start_line == 1


def test_agent_trace_start_line_minimum_is_1():
    """cursor_line=0 (0-based) → start_line=1, not 0."""
    doc = record_to_agent_trace(_rec("r1", cursor_line=0))
    assert doc.files[0].conversations[0].ranges[0].start_line == 1


def test_agent_trace_end_line_gte_start_line():
    doc = record_to_agent_trace(_rec("r1", cursor_line=10))
    r = doc.files[0].conversations[0].ranges[0]
    assert r.end_line >= r.start_line


# ── metadata carries LineageLens-specific fields ──────────────────────────────

def test_agent_trace_workspace_id_in_metadata():
    doc = record_to_agent_trace(_rec("r1", workspace_id="ws-acme"))
    assert doc.metadata is not None
    assert doc.metadata["lineagelens.workspaceId"] == "ws-acme"


def test_agent_trace_schema_version_tag_in_metadata():
    doc = record_to_agent_trace(_rec("r1"))
    assert doc.metadata["lineagelens.schemaVersion"] == SCHEMA_VERSION


def test_agent_trace_inserted_code_preview_in_metadata_truncated():
    long_code = "x = 1\n" * 100
    doc = record_to_agent_trace(_rec("r1", inserted_code=long_code))
    preview = doc.metadata.get("lineagelens.insertedCodePreview")
    assert preview is not None
    assert len(preview) <= 120


def test_agent_trace_no_inserted_code_omits_preview_key():
    doc = record_to_agent_trace(_rec("r1", inserted_code=None))
    assert "lineagelens.insertedCodePreview" not in (doc.metadata or {})


def test_agent_trace_newlines_replaced_in_preview():
    doc = record_to_agent_trace(_rec("r1", inserted_code="a = 1\nb = 2\n"))
    preview = (doc.metadata or {}).get("lineagelens.insertedCodePreview", "")
    assert "\n" not in preview


def test_agent_trace_confidence_in_metadata():
    rec = _rec("r1", payload={
        "normalizedEvent": {"confidence": {"value": 0.85, "level": "high"}}
    })
    doc = record_to_agent_trace(rec)
    conf = (doc.metadata or {}).get("lineagelens.confidence")
    assert conf is not None
    assert conf["score"] == 0.85
    assert conf["level"] == "high"


def test_agent_trace_vcs_is_none():
    """VCS is not captured at insertion time; must be null."""
    doc = record_to_agent_trace(_rec("r1"))
    assert doc.vcs is None


# ── tool extraction ───────────────────────────────────────────────────────────

def test_agent_trace_tool_name_in_top_level_tool():
    rec = _rec("r1", payload={
        "normalizedEvent": {"source": {"toolName": "claude-code"}}
    })
    doc = record_to_agent_trace(rec)
    assert doc.tool is not None
    assert doc.tool.name == "claude-code"


def test_agent_trace_rich_tool_in_metadata():
    rec = _rec("r1", payload={
        "normalizedEvent": {
            "source": {
                "toolName": "claude-code",
                "adapterName": "anthropic-proxy",
                "sessionId": "sess-001",
                "conversationId": "conv-abc",
                "operationType": "edit",
            }
        }
    })
    doc = record_to_agent_trace(rec)
    rich = (doc.metadata or {}).get("lineagelens.tool", {})
    assert rich.get("adapter") == "anthropic-proxy"
    assert rich.get("sessionId") == "sess-001"
    assert rich.get("conversationId") == "conv-abc"
    assert rich.get("operationType") == "edit"


def test_agent_trace_tool_none_when_no_tool_name():
    doc = record_to_agent_trace(_rec("r1", payload={}))
    assert doc.tool is None


# ── evidence in metadata ──────────────────────────────────────────────────────

def test_agent_trace_evidence_in_metadata():
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
    doc = record_to_agent_trace(rec)
    evidence = (doc.metadata or {}).get("lineagelens.evidence", [])
    assert len(evidence) == 2
    assert evidence[0]["source"] == "proxy"
    assert evidence[0]["weight"] == 0.9


def test_agent_trace_empty_evidence_omits_evidence_key():
    doc = record_to_agent_trace(_rec("r1"))
    assert "lineagelens.evidence" not in (doc.metadata or {})


# ── JSON serialisation ────────────────────────────────────────────────────────

def test_agent_trace_jsonl_serializable():
    doc = record_to_agent_trace(_rec("r1"))
    line = json.dumps(doc.model_dump(exclude_none=True), separators=(",", ":"))
    parsed = json.loads(line)
    assert parsed["id"] == "r1"
    assert parsed["version"] == SPEC_VERSION


def test_agent_trace_snake_case_keys_in_dump():
    """Spec uses snake_case throughout (start_line, model_id, etc.)."""
    doc = record_to_agent_trace(_rec("r1", cursor_line=5))
    d = doc.model_dump()
    r = d["files"][0]["conversations"][0]["ranges"][0]
    assert "start_line" in r
    assert "end_line" in r
    assert "startLine" not in r   # old camelCase must be gone


# ── JSON schema validation against the real spec ──────────────────────────────

def test_export_validates_against_spec_json_schema():
    """Exported AgentTraceDocument must validate against the official JSON schema."""
    pytest.importorskip("jsonschema", reason="jsonschema not installed")
    from jsonschema import validate, FormatChecker

    schema_path = FIXTURES / "agent_trace_schema.json"
    schema = json.loads(schema_path.read_text())

    # Use a proper UUID so the schema's format:"uuid" check passes.
    rec = _rec(
        "550e8400-e29b-41d4-a716-446655440001",
        model="claude-opus-4-5",
        file_path="src/auth.py",
        cursor_line=10,
        inserted_code="def foo():\n    pass\n",
        payload={
            "normalizedEvent": {
                "source": {"toolName": "claude-code"},
                "confidence": {"value": 0.85, "level": "high"},
            }
        },
    )
    doc = record_to_agent_trace(rec)
    # exclude_none so optional fields absent from our data (vcs, tool.version, etc.)
    # are omitted — the spec schema requires them to be objects when present.
    data = doc.model_dump(exclude_none=True)
    # Validate; raises jsonschema.ValidationError on failure.
    validate(instance=data, schema=schema, format_checker=FormatChecker())


def test_spec_sample_validates_against_schema():
    """The official sample fixture must pass the spec's JSON schema."""
    pytest.importorskip("jsonschema", reason="jsonschema not installed")
    from jsonschema import validate, FormatChecker

    schema = json.loads((FIXTURES / "agent_trace_schema.json").read_text())
    sample = json.loads((FIXTURES / "agent_trace_sample.json").read_text())
    validate(instance=sample, schema=schema, format_checker=FormatChecker())


# ── import from real sample fixture ──────────────────────────────────────────

def test_import_sample_parses_as_agent_trace_document():
    """The official sample must parse without validation errors."""
    sample = json.loads((FIXTURES / "agent_trace_sample.json").read_text())
    doc = AgentTraceDocument.model_validate(sample)
    assert doc.version == "0.1.0"
    assert doc.id == "550e8400-e29b-41d4-a716-446655440000"
    assert len(doc.files) == 2


def test_import_sample_file_paths():
    sample = json.loads((FIXTURES / "agent_trace_sample.json").read_text())
    doc = AgentTraceDocument.model_validate(sample)
    paths = [f.path for f in doc.files]
    assert "src/utils/parser.ts" in paths
    assert "src/utils/helpers.ts" in paths


def test_import_sample_model_ids():
    sample = json.loads((FIXTURES / "agent_trace_sample.json").read_text())
    doc = AgentTraceDocument.model_validate(sample)
    # File 0 (parser.ts) — claude model
    contrib0 = doc.files[0].conversations[0].contributor
    assert contrib0 is not None
    assert contrib0.model_id == "anthropic/claude-opus-4-5-20251101"
    # File 1 (helpers.ts) — gpt-4o
    contrib1 = doc.files[1].conversations[0].contributor
    assert contrib1 is not None
    assert contrib1.model_id == "openai/gpt-4o"


def test_import_sample_contributor_types():
    sample = json.loads((FIXTURES / "agent_trace_sample.json").read_text())
    doc = AgentTraceDocument.model_validate(sample)
    for afile in doc.files:
        for conv in afile.conversations:
            assert conv.contributor is not None
            assert conv.contributor.type == "ai"


def test_import_sample_line_ranges():
    sample = json.loads((FIXTURES / "agent_trace_sample.json").read_text())
    doc = AgentTraceDocument.model_validate(sample)
    # parser.ts → lines 42–67
    r0 = doc.files[0].conversations[0].ranges[0]
    assert r0.start_line == 42
    assert r0.end_line == 67
    # helpers.ts → lines 10–25
    r1 = doc.files[1].conversations[0].ranges[0]
    assert r1.start_line == 10
    assert r1.end_line == 25


def test_import_sample_vcs_preserved():
    sample = json.loads((FIXTURES / "agent_trace_sample.json").read_text())
    doc = AgentTraceDocument.model_validate(sample)
    assert doc.vcs is not None
    assert doc.vcs.type == "git"
    assert doc.vcs.revision == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"


def test_import_sample_tool_preserved():
    sample = json.loads((FIXTURES / "agent_trace_sample.json").read_text())
    doc = AgentTraceDocument.model_validate(sample)
    assert doc.tool is not None
    assert doc.tool.name == "cursor"
    assert doc.tool.version == "2.4.0"


def test_import_sample_provenance_payload_for_first_file():
    """agent_trace_to_provenance_payload must produce the right model/file for file[0]."""
    sample = json.loads((FIXTURES / "agent_trace_sample.json").read_text())
    doc = AgentTraceDocument.model_validate(sample)

    # Narrow to first file for the payload builder (mirrors import route logic).
    from app.schemas.agent_trace import AgentTraceFile as _ATF
    narrow = AgentTraceDocument(
        version=doc.version,
        id=doc.id,
        timestamp=doc.timestamp,
        vcs=doc.vcs,
        tool=doc.tool,
        files=[_ATF(path=doc.files[0].path, conversations=[doc.files[0].conversations[0]])],
        metadata=doc.metadata,
    )
    payload = agent_trace_to_provenance_payload(narrow, "ws-imported", "2026-06-03T00:00:00Z")

    assert payload["filePath"] == "src/utils/parser.ts"
    ne = payload["normalizedEvent"]
    assert ne["model"]["name"] == "anthropic/claude-opus-4-5-20251101"
    assert ne["source"]["toolName"] == "cursor"
    assert payload["importSource"] == "agent-trace-import"


def test_import_sample_provenance_payload_for_second_file():
    """agent_trace_to_provenance_payload must produce the right model/file for file[1]."""
    sample = json.loads((FIXTURES / "agent_trace_sample.json").read_text())
    doc = AgentTraceDocument.model_validate(sample)

    from app.schemas.agent_trace import AgentTraceFile as _ATF
    narrow = AgentTraceDocument(
        version=doc.version,
        id=doc.id,
        timestamp=doc.timestamp,
        vcs=doc.vcs,
        tool=doc.tool,
        files=[_ATF(path=doc.files[1].path, conversations=[doc.files[1].conversations[0]])],
        metadata=doc.metadata,
    )
    payload = agent_trace_to_provenance_payload(narrow, "ws-imported", "2026-06-03T00:00:00Z")

    assert payload["filePath"] == "src/utils/helpers.ts"
    ne = payload["normalizedEvent"]
    assert ne["model"]["name"] == "openai/gpt-4o"


# ── round-trip ────────────────────────────────────────────────────────────────

def test_agent_trace_round_trip_payload_structure():
    rec = _rec("r1", model="claude-opus-4-5", cursor_line=10, payload={
        "normalizedEvent": {
            "source": {"toolName": "claude-code"},
            "confidence": {"value": 0.85, "level": "high"},
        }
    })
    doc = record_to_agent_trace(rec)
    payload = agent_trace_to_provenance_payload(
        doc, workspace_id="ws-test", imported_at="2026-06-01T00:00:00+00:00"
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
    doc = record_to_agent_trace(rec)
    payload = agent_trace_to_provenance_payload(doc, "ws", "2026-06-01T00:00:00+00:00")
    evidence = payload["normalizedEvent"]["source"]["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["source"] == "proxy"


# ── schema validation (minimal parse, reject bad input) ──────────────────────

def test_agent_trace_document_validates_minimal():
    obj = {
        "version": SPEC_VERSION,
        "id": "00000000-0000-4000-8000-000000000001",
        "timestamp": "2026-06-01T10:00:00+00:00",
        "files": [
            {"path": "src/foo.py", "conversations": [{"ranges": [{"start_line": 1, "end_line": 5}]}]}
        ],
    }
    doc = AgentTraceDocument.model_validate(obj)
    assert doc.id == "00000000-0000-4000-8000-000000000001"
    assert doc.files[0].path == "src/foo.py"


def test_agent_trace_document_optional_fields_absent():
    obj = {
        "version": SPEC_VERSION,
        "id": "00000000-0000-4000-8000-000000000002",
        "timestamp": "2026-06-01T10:00:00+00:00",
        "files": [],
    }
    doc = AgentTraceDocument.model_validate(obj)
    assert doc.vcs is None
    assert doc.tool is None
    assert doc.metadata is None


# ── hash chain ────────────────────────────────────────────────────────────────

def _minimal_doc(doc_id: str, workspace_id: str = "ws-test") -> AgentTraceDocument:
    return AgentTraceDocument.model_validate({
        "version": SPEC_VERSION,
        "id": doc_id,
        "timestamp": "2026-06-01T10:00:00+00:00",
        "files": [
            {
                "path": f"src/{doc_id}.py",
                "conversations": [{"ranges": [{"start_line": 1, "end_line": 1}]}],
            }
        ],
        "metadata": {"lineagelens.workspaceId": workspace_id},
    })


def test_compute_import_hash_returns_nonempty_strings():
    doc = _minimal_doc("r1")
    prompt_sha, rec_hash = compute_import_hash(doc, prev_hash=None)
    assert isinstance(prompt_sha, str) and len(prompt_sha) == 64
    assert isinstance(rec_hash, str) and len(rec_hash) == 64


def test_compute_import_hash_is_hex():
    doc = _minimal_doc("r1")
    _, rec_hash = compute_import_hash(doc, prev_hash=None)
    assert all(c in "0123456789abcdef" for c in rec_hash)


def test_compute_import_hash_chain_links():
    """Second record's prev_hash must equal first record's record_hash."""
    d1 = _minimal_doc("r1")
    d2 = _minimal_doc("r2")
    _, hash1 = compute_import_hash(d1, prev_hash=None)
    _, hash2 = compute_import_hash(d2, prev_hash=hash1)
    _, hash2_no_chain = compute_import_hash(d2, prev_hash=None)
    assert hash2 != hash2_no_chain


def test_compute_import_hash_different_records_differ():
    d1 = _minimal_doc("r1")
    d2 = _minimal_doc("r2")
    _, h1 = compute_import_hash(d1, prev_hash=None)
    _, h2 = compute_import_hash(d2, prev_hash=None)
    assert h1 != h2


def test_compute_import_hash_deterministic():
    doc = _minimal_doc("r1")
    _, h1 = compute_import_hash(doc, prev_hash="abc")
    _, h2 = compute_import_hash(doc, prev_hash="abc")
    assert h1 == h2
