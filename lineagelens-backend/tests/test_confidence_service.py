"""Tests for the evidence-weighted confidence engine.

All tests are pure-function — no DB, no IO.

Run with:
    cd lineagelens-backend && pytest tests/test_confidence_service.py -v
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.confidence_service import (
    ConfidenceInputs,
    ConfidenceResult,
    compute_confidence,
    trigram_jaccard,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _inputs(**overrides) -> ConfidenceInputs:
    """Build a minimal ConfidenceInputs with sensible defaults, then apply overrides."""
    defaults = dict(
        capture_status="full",
        request_uuid_present=True,
        request_uuid_matches_capture=True,
        prompt_timestamp=_ts("2026-01-01T10:00:00"),
        insertion_timestamp=_ts("2026-01-01T10:00:01"),  # Δt = 1 s
        raw_model_response="def hello(): pass",
        inserted_text="def hello(): pass",
        tool_name="claude-code",
        user_agent=None,
        provider="anthropic",
    )
    defaults.update(overrides)
    return ConfidenceInputs(**defaults)


# ── Case 1: Pure-proxy capture, matching UUID, Δt=1s, sim~1.0, known tool ───

def test_case1_very_high_confidence() -> None:
    """Full proxy capture + confirmed UUID + instant + identical text + known tool → very_high."""
    result = compute_confidence(_inputs())
    assert isinstance(result, ConfidenceResult)
    assert result.value > 0.90, f"expected > 0.90, got {result.value}"
    assert result.level == "very_high"
    assert result.method == "weighted_evidence_v1"


# ── Case 2: File-diff only, no uuid, no timestamp, no response, no tool ──────

def test_case2_very_low_or_low_confidence() -> None:
    """Worst-case inputs → value < 0.30, level 'low' or 'very_low'."""
    result = compute_confidence(_inputs(
        capture_status="file_diff",
        request_uuid_present=False,
        request_uuid_matches_capture=False,
        prompt_timestamp=None,
        raw_model_response=None,
        inserted_text="some text",
        tool_name=None,
        user_agent=None,
        provider=None,
    ))
    assert result.value < 0.30, f"expected < 0.30, got {result.value}"
    assert result.level in ("low", "very_low")


# ── Case 3: Editor capture, uuid present not matched, Δt=120s, sim~0.45 ──────

def test_case3_medium_confidence() -> None:
    """Partial evidence → medium level.

    The model returned a short functional snippet but the developer replaced it
    with a completely different class-based implementation, so the inserted text
    and the raw model response share very few trigrams (Jaccard ≈ 0.10).
    Combined with metadata_only capture, an unconfirmed UUID, and a 2-minute
    delay, the total evidence score lands in the medium tier.
    """
    response = "def process_data(items):\n    return [x * 2 for x in items]\n"
    # Developer discarded the suggestion and wrote a class instead — low similarity.
    inserted = (
        "class EventProcessor:\n"
        "    def __init__(self, queue):\n"
        "        self.queue = queue\n\n"
        "    def handle_next(self):\n"
        "        event = self.queue.pop()\n"
        "        return self._dispatch(event)\n"
    )
    result = compute_confidence(_inputs(
        capture_status="metadata_only",
        request_uuid_present=True,
        request_uuid_matches_capture=False,
        prompt_timestamp=_ts("2026-01-01T10:00:00"),
        insertion_timestamp=_ts("2026-01-01T10:02:00"),  # Δt = 120 s
        raw_model_response=response,
        inserted_text=inserted,
        tool_name="cursor",
        user_agent=None,
        provider="openai",
    ))
    assert result.level == "medium", (
        f"expected medium, got {result.level} (value={result.value})"
    )


# ── Case 4: Trigram Jaccard sanity check ─────────────────────────────────────

def test_case4_trigram_jaccard_known_pair() -> None:
    """Verify the trigram Jaccard implementation on a fixed small pair."""
    a = "hello world"
    b = "hello wonderful world"
    # Trigrams of "hello world":    {"hel","ell","llo","lo ","o w"," wo","wor","orl","rld"}
    # Trigrams of "hello wonderful world":
    #   "hel","ell","llo","lo ","o w"," wo","won","ond","nde","der","erf","rfu","ful","ul ","l w","wor","orl","rld"
    # Intersection = {"hel","ell","llo","lo ","o w"," wo","wor","orl","rld"} = 9
    # Union = 9 + (18 - 9) = 18
    # Jaccard = 9/18 = 0.5
    sim = trigram_jaccard(a, b)
    assert abs(sim - 0.5) < 1e-9, f"expected 0.5, got {sim}"


# ── Case 5: contributions sum to value ───────────────────────────────────────

@pytest.mark.parametrize("overrides", [
    {},
    {"capture_status": "tunnel_only", "request_uuid_present": False, "request_uuid_matches_capture": False},
    {"prompt_timestamp": None, "raw_model_response": None, "tool_name": None, "provider": None},
])
def test_case5_contributions_sum_to_value(overrides: dict) -> None:
    """Sum of evidence contributions must equal the reported value (float precision)."""
    result = compute_confidence(_inputs(**overrides))
    total = sum(e.contribution for e in result.evidence)
    assert abs(round(total, 3) - result.value) < 1e-9, (
        f"contributions sum {total} ≠ value {result.value}"
    )


# ── Case 6: Evidence always exactly 5 items in stable order ──────────────────

_EXPECTED_SIGNALS = [
    "capture_layer",
    "request_uuid_match",
    "time_correlation",
    "content_similarity",
    "tool_fingerprint",
]


@pytest.mark.parametrize("overrides", [
    {},
    {"capture_status": "unavailable", "tool_name": None, "provider": None},
    {"prompt_timestamp": None, "raw_model_response": None},
])
def test_case6_exactly_five_evidence_items_stable_order(overrides: dict) -> None:
    """Evidence list is always exactly 5 items in the documented stable order."""
    result = compute_confidence(_inputs(**overrides))
    assert len(result.evidence) == 5, f"expected 5 items, got {len(result.evidence)}"
    for item, expected_signal in zip(result.evidence, _EXPECTED_SIGNALS):
        assert item.signal == expected_signal, (
            f"expected signal '{expected_signal}', got '{item.signal}'"
        )


# ── Additional signal-level spot checks ───────────────────────────────────────

def test_capture_layer_full_score() -> None:
    r = compute_confidence(_inputs(capture_status="full"))
    cap = next(e for e in r.evidence if e.signal == "capture_layer")
    assert abs(cap.contribution - 0.30) < 1e-9


def test_capture_layer_file_diff_score() -> None:
    r = compute_confidence(_inputs(capture_status="file_diff"))
    cap = next(e for e in r.evidence if e.signal == "capture_layer")
    assert abs(cap.contribution - 0.35 * 0.30) < 1e-9


def test_uuid_match_score() -> None:
    r = compute_confidence(_inputs(request_uuid_matches_capture=True))
    uuid_ev = next(e for e in r.evidence if e.signal == "request_uuid_match")
    assert abs(uuid_ev.contribution - 0.25) < 1e-9


def test_uuid_present_not_matched() -> None:
    r = compute_confidence(_inputs(request_uuid_present=True, request_uuid_matches_capture=False))
    uuid_ev = next(e for e in r.evidence if e.signal == "request_uuid_match")
    assert abs(uuid_ev.contribution - 0.50 * 0.25) < 1e-9


def test_no_uuid_score_zero() -> None:
    r = compute_confidence(_inputs(request_uuid_present=False, request_uuid_matches_capture=False))
    uuid_ev = next(e for e in r.evidence if e.signal == "request_uuid_match")
    assert uuid_ev.contribution == 0.0


def test_time_correlation_instant() -> None:
    r = compute_confidence(_inputs(
        prompt_timestamp=_ts("2026-01-01T10:00:00"),
        insertion_timestamp=_ts("2026-01-01T10:00:01"),
    ))
    tc = next(e for e in r.evidence if e.signal == "time_correlation")
    assert abs(tc.contribution - 0.15) < 1e-9


def test_time_correlation_none_timestamp() -> None:
    r = compute_confidence(_inputs(prompt_timestamp=None))
    tc = next(e for e in r.evidence if e.signal == "time_correlation")
    assert abs(tc.contribution - 0.20 * 0.15) < 1e-9


def test_tool_fingerprint_known() -> None:
    r = compute_confidence(_inputs(tool_name="cursor"))
    fp = next(e for e in r.evidence if e.signal == "tool_fingerprint")
    assert abs(fp.contribution - 0.10) < 1e-9


def test_tool_fingerprint_unknown_tool() -> None:
    r = compute_confidence(_inputs(tool_name="my-custom-tool"))
    fp = next(e for e in r.evidence if e.signal == "tool_fingerprint")
    assert abs(fp.contribution - 0.60 * 0.10) < 1e-9


def test_tool_fingerprint_none_provider_only() -> None:
    r = compute_confidence(_inputs(tool_name=None, provider="anthropic"))
    fp = next(e for e in r.evidence if e.signal == "tool_fingerprint")
    assert abs(fp.contribution - 0.40 * 0.10) < 1e-9


def test_tool_fingerprint_none_all() -> None:
    r = compute_confidence(_inputs(tool_name=None, provider=None, user_agent=None))
    fp = next(e for e in r.evidence if e.signal == "tool_fingerprint")
    assert fp.contribution == 0.0


def test_level_bucketing() -> None:
    """Spot-check that level bucketing matches documented thresholds."""
    # very_high: value >= 0.85
    r = compute_confidence(_inputs())
    assert r.value >= 0.85
    assert r.level == "very_high"

    # very_low: worst possible (file_diff, no uuid, no ts, no response, no tool)
    r_low = compute_confidence(_inputs(
        capture_status="unavailable",
        request_uuid_present=False, request_uuid_matches_capture=False,
        prompt_timestamp=None, raw_model_response=None, inserted_text="x",
        tool_name=None, user_agent=None, provider=None,
    ))
    assert r_low.level == "very_low"
