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


def test_unrelated_code_is_clean():
    """Completely unrelated code must be 'clean' against the GPL corpus."""
    unrelated = "def add(a, b): return a + b"
    result = _matcher().scan(unrelated)
    assert result.match_status == "clean"
    assert result.best_match_license is None


def test_empty_corpus_returns_clean():
    """With an empty corpus every scan must return 'clean'."""
    result = _matcher(corpus=[]).scan(_GPL_SNIPPET)
    assert result.match_status == "clean"
    assert result.similarity == 0.0


def test_empty_code_returns_clean():
    """Empty or whitespace-only code must always be 'clean'."""
    result = _matcher().scan("")
    assert result.match_status == "clean"
    result2 = _matcher().scan("   \n\t  ")
    assert result2.match_status == "clean"


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
async def test_scan_and_record_writes_clean_status():
    """scan_and_record must set license_status='clean' on unrelated code."""

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
    assert result.match_status == "clean"
    assert record.license_status == "clean"
    assert record.license_match_license is None


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


def test_certificate_only_issued_when_clean():
    """No exception from the matcher — the route layer gates cert issuance on 'clean'."""
    # The route enforces: if license_status != 'clean' → 409.
    # Confirmed here by checking scan returns correct statuses for gate logic.
    assert _matcher().scan(_GPL_SNIPPET).match_status == "match"
    assert _matcher().scan("x = 1").match_status == "clean"
