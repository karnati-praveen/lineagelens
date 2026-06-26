"""Tests for app.services.license_match_service — offline k-gram fingerprint matcher.

All tests are pure unit tests (no DB).  The corpus is injected via LocalMatcher()
constructor so LICENSE_FINGERPRINT_PATH does not need to be set.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789")

from app.services.license_match_service import (
    LocalMatcher,
    MatchResult,
    _MATCH_THRESHOLD,
    compute_shingles,
    scan_and_record,
)

# Keep the env-driven corpus path out of these unit tests so default-matcher
# behaviour (not_configured) is deterministic regardless of the host machine.
os.environ.pop("LICENSE_FINGERPRINT_PATH", None)


# ── Corpus fixture ────────────────────────────────────────────────────────────

# A small corpus entry whose shingles are derived from the GPL preamble snippet.
_GPL_SNIPPET = """
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.
"""

_GPL_SHINGLES = list(compute_shingles(_GPL_SNIPPET))

_TEST_CORPUS = [
    {"id": "GPL-3.0", "name": "GNU General Public License v3.0", "shingles": _GPL_SHINGLES},
]


def _matcher(corpus=None) -> LocalMatcher:
    return LocalMatcher(corpus=corpus if corpus is not None else _TEST_CORPUS)


# ── Tests: local matching ─────────────────────────────────────────────────────

def test_identical_to_gpl_is_match():
    """Code identical to the GPL corpus entry must produce 'match' status."""
    result = _matcher().scan(_GPL_SNIPPET)
    assert result.match_status == "match"
    assert result.best_match_license == "GPL-3.0"
    assert result.similarity >= _MATCH_THRESHOLD


def test_unrelated_code_is_clean_within_corpus():
    """Unrelated code scanned against a real corpus is 'clean_within_corpus'."""
    unrelated = "def add(a, b): return a + b"
    result = _matcher().scan(unrelated)
    assert result.match_status == "clean_within_corpus"
    assert result.best_match_license is None
    assert result.corpus_digest is not None  # cert can state what was checked


def test_empty_injected_corpus_is_insufficient_corpus():
    """A configured-but-empty corpus must be 'insufficient_corpus', never 'clean' (PART 1 #2)."""
    result = _matcher(corpus=[]).scan(_GPL_SNIPPET)
    assert result.match_status == "insufficient_corpus"
    assert result.similarity == 0.0


def test_no_corpus_configured_is_not_configured():
    """Default matcher with no corpus + no env path must report not_configured."""
    result = LocalMatcher().scan(_GPL_SNIPPET)
    assert result.match_status == "not_configured"
    assert result.coverage == "no_corpus"


def test_empty_code_is_clean_within_corpus():
    """Empty/whitespace code against a real corpus is clean_within_corpus (nothing matched)."""
    result = _matcher().scan("")
    assert result.match_status == "clean_within_corpus"
    result2 = _matcher().scan("   \n\t  ")
    assert result2.match_status == "clean_within_corpus"


def test_deterministic_across_runs():
    """Two calls with the same code and corpus must produce identical results."""
    r1 = _matcher().scan(_GPL_SNIPPET)
    r2 = _matcher().scan(_GPL_SNIPPET)
    assert r1.match_status == r2.match_status
    assert r1.similarity == r2.similarity
    assert r1.best_match_license == r2.best_match_license


def test_shingle_computation_is_stable():
    """compute_shingles must return the same set for repeated identical inputs."""
    s1 = compute_shingles("def foo(): pass")
    s2 = compute_shingles("def foo(): pass")
    assert s1 == s2


def test_comment_stripped_before_matching():
    """Inline comments should not inflate or deflate similarity."""
    code_with_comments = _GPL_SNIPPET + "\n# a comment\n// another comment\n"
    result = _matcher().scan(code_with_comments)
    # Should still match GPL since the bulk is identical
    assert result.match_status == "match"


# ── Tests: scan_and_record ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_and_record_writes_clean_within_corpus_status():
    """scan_and_record sets license_status='clean_within_corpus' on unrelated code."""

    class FakeRecord:
        inserted_code = "x = 1 + 2"
        license_status = None
        license_match_license = None
        license_similarity = None
        provenance_payload = {}

    class FakeSession:
        def add(self, obj): pass

    record = FakeRecord()
    result = await scan_and_record(FakeSession(), record, matcher=_matcher())
    assert result.match_status == "clean_within_corpus"
    assert record.license_status == "clean_within_corpus"
    assert record.license_match_license is None
    # Scan provenance is persisted for certificate transparency.
    assert record.provenance_payload["licenseScanResult"]["scannerVersion"] == "kgram-shingle-1"


@pytest.mark.asyncio
async def test_scan_and_record_not_configured_when_no_corpus():
    """With no corpus configured, scan_and_record records not_configured (never clean)."""

    class FakeRecord:
        inserted_code = "x = 1 + 2"
        license_status = None
        license_match_license = None
        license_similarity = None
        provenance_payload = {}

    class FakeSession:
        def add(self, obj): pass

    record = FakeRecord()
    result = await scan_and_record(FakeSession(), record, matcher=LocalMatcher())
    assert result.match_status == "not_configured"
    assert record.license_status == "not_configured"


@pytest.mark.asyncio
async def test_scan_and_record_writes_match_status():
    """scan_and_record must set license_status='match' for GPL-matching code."""

    class FakeRecord:
        inserted_code = _GPL_SNIPPET
        license_status = None
        license_match_license = None
        license_similarity = None
        provenance_payload = {}

    class FakeSession:
        def add(self, obj): pass

    record = FakeRecord()
    result = await scan_and_record(FakeSession(), record, matcher=_matcher())
    assert result.match_status == "match"
    assert record.license_status == "match"
    assert record.license_match_license == "GPL-3.0"


@pytest.mark.asyncio
async def test_scan_uses_proxy_supplied_shingles():
    """When provenance_payload contains licenseScan.shingles they are used directly."""

    class FakeRecord:
        inserted_code = ""  # empty — would be clean without proxy shingles
        license_status = None
        license_match_license = None
        license_similarity = None
        provenance_payload = {"licenseScan": {"shingles": _GPL_SHINGLES, "k": 5}}

    class FakeSession:
        def add(self, obj): pass

    record = FakeRecord()
    # Use a local matcher with corpus so the injected shingles can hit GPL-3.0
    result = await scan_and_record(FakeSession(), record, matcher=_matcher())
    # The proxy-supplied shingles are identical to GPL — should match
    assert result.match_status == "match"
    assert record.license_status == "match"


def test_certificate_gate_statuses():
    """The route gates cert issuance on CLEAN_STATES; confirm scan returns gate-correct statuses."""
    from app.services.license_match_service import CLEAN_STATES

    assert _matcher().scan(_GPL_SNIPPET).match_status == "match"
    assert _matcher().scan("x = 1").match_status in CLEAN_STATES
    # not_configured must NOT be a clean state — no corpus, no certification.
    assert LocalMatcher().scan("x = 1").match_status not in CLEAN_STATES
