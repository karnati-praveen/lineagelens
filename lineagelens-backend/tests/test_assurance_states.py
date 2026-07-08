"""Tests for the assurance-state catalogue (PART 5 #58)."""
from __future__ import annotations

from app.core.assurance_states import (
    BlastRadiusState,
    ChainState,
    EmbeddingState,
    LicenseState,
    PromptAvailabilityState,
    all_known_states,
    prompt_availability_state,
)


def test_chain_state_values_are_stable():
    assert ChainState.FULLY_AVAILABLE.value == "fully_available"
    assert ChainState.LOCALLY_TAMPER_EVIDENT.value == "locally_tamper_evident"
    assert ChainState.TAMPERED.value == "tampered"
    assert ChainState.UNVERIFIABLE.value == "unverifiable"


def test_blast_radius_state_includes_partial():
    values = {s.value for s in BlastRadiusState}
    assert "blast_radius_partial" in values
    assert {"checked", "checked_empty", "unavailable", "failed"} <= values


def test_prompt_availability_state_includes_unavailable():
    values = {s.value for s in PromptAvailabilityState}
    assert values == {"available", "committed_digest_only", "prompt_unavailable"}


def test_all_known_states_covers_every_family():
    families = all_known_states()
    assert set(families) == {"chain", "blastRadius", "promptAvailability", "license", "embedding"}
    for values in families.values():
        assert len(values) == len(set(values)), "duplicate values within a family"


def test_no_duplicate_values_across_families():
    families = all_known_states()
    seen: dict[str, str] = {}
    for family, values in families.items():
        for v in values:
            assert v not in seen, f"{v!r} appears in both {seen.get(v)!r} and {family!r}"
            seen[v] = family


def test_license_state_matches_license_match_service():
    from app.services.license_match_service import (
        STATUS_CLEAN_WITHIN_CORPUS,
        STATUS_MATCH,
        STATUS_NOT_CONFIGURED,
    )

    assert LicenseState.NOT_CONFIGURED.value == STATUS_NOT_CONFIGURED
    assert LicenseState.CLEAN_WITHIN_CORPUS.value == STATUS_CLEAN_WITHIN_CORPUS
    assert LicenseState.MATCH.value == STATUS_MATCH


def test_embedding_state_semantic_unavailable_value():
    assert EmbeddingState.SEMANTIC_SEARCH_UNAVAILABLE.value == "semantic_search_unavailable"


class _FakeRecord:
    def __init__(self, prompt_messages=None, prompt_sha256=None):
        self.prompt_messages = prompt_messages
        self.prompt_sha256 = prompt_sha256


def test_prompt_availability_state_available():
    rec = _FakeRecord(prompt_messages=[{"role": "user", "content": "hi"}])
    assert prompt_availability_state(rec) == PromptAvailabilityState.AVAILABLE.value


def test_prompt_availability_state_committed_digest_only():
    rec = _FakeRecord(prompt_messages=None, prompt_sha256="a" * 64)
    assert prompt_availability_state(rec) == PromptAvailabilityState.COMMITTED_DIGEST_ONLY.value


def test_prompt_availability_state_unavailable():
    rec = _FakeRecord(prompt_messages=None, prompt_sha256=None)
    assert prompt_availability_state(rec) == PromptAvailabilityState.PROMPT_UNAVAILABLE.value
