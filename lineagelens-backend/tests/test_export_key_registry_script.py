"""Tests for the release key-registry export script (PART 5 #59).

Only the pure formatting function is tested here — it takes duck-typed key
objects (no DB connection needed), matching the extraction described in the
plan so CI can validate the export shape without a live database.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "lineagelens-scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from export_key_registry import format_registry_export  # noqa: E402


@dataclass
class _FakeKey:
    public_key_id: str
    public_key_hex: str
    valid_from: datetime | None
    valid_until: datetime | None
    compromised_at: datetime | None
    status: str
    label: str | None


def test_format_registry_export_empty_list():
    export = format_registry_export([])
    assert export["schemaVersion"] == "1.0"
    assert export["keys"] == []


def test_format_registry_export_serializes_all_fields():
    key = _FakeKey(
        public_key_id="abc123",
        public_key_hex="ab" * 32,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_until=None,
        compromised_at=None,
        status="active",
        label="release-signing",
    )
    export = format_registry_export([key])
    assert len(export["keys"]) == 1
    entry = export["keys"][0]
    assert entry["publicKeyId"] == "abc123"
    assert entry["validFrom"] == "2026-01-01T00:00:00+00:00"
    assert entry["validUntil"] is None
    assert entry["status"] == "active"
    assert entry["label"] == "release-signing"


def test_format_registry_export_handles_compromised_key():
    key = _FakeKey(
        public_key_id="deadbeef",
        public_key_hex="cd" * 32,
        valid_from=None,
        valid_until=None,
        compromised_at=datetime(2026, 6, 1, tzinfo=UTC),
        status="compromised",
        label=None,
    )
    export = format_registry_export([key])
    assert export["keys"][0]["compromisedAt"] == "2026-06-01T00:00:00+00:00"
    assert export["keys"][0]["status"] == "compromised"
