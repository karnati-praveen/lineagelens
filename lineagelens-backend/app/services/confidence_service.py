"""Evidence-weighted confidence engine for provenance records.

Replaces the legacy hardcoded 0.5 / 0.25 confidence assignments with a
deterministic five-signal scorer.  No ML, no IO, no external dependencies.

Public API
----------
compute_confidence(evidence_inputs: ConfidenceInputs) -> ConfidenceResult
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


# ── Public dataclasses ────────────────────────────────────────────────────────

@dataclass(slots=True)
class ConfidenceInputs:
    """Raw fields extracted by the ingest normalizer."""

    capture_status: str          # "full" | "metadata_only" | "tunnel_only" | "file_diff" | "unavailable"
    request_uuid_present: bool
    request_uuid_matches_capture: bool   # True only when proxy & editor both agree
    prompt_timestamp: datetime | None
    insertion_timestamp: datetime
    raw_model_response: str | None
    inserted_text: str
    tool_name: str | None
    user_agent: str | None
    provider: str | None


@dataclass(slots=True)
class EvidenceItem:
    """One signal's contribution to the composite confidence score."""

    signal: str
    value: object
    weight: float
    contribution: float
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "value": self.value,
            "weight": self.weight,
            "contribution": self.contribution,
            "rationale": self.rationale,
        }


@dataclass(slots=True)
class ConfidenceResult:
    """Composite confidence score with full evidence trail."""

    value: float                              # 0.0 – 1.0, rounded to 3 decimals
    level: Literal["very_high", "high", "medium", "low", "very_low"]
    method: str                               # always "weighted_evidence_v1"
    evidence: list[EvidenceItem]             # always exactly 5 items, stable order

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "level": self.level,
            "method": self.method,
            "evidence": [e.to_dict() for e in self.evidence],
        }


# ── Signal tables ─────────────────────────────────────────────────────────────

_CAPTURE_LAYER_SCORES: dict[str, float] = {
    "full": 1.00,
    "metadata_only": 0.80,
    "tunnel_only": 0.70,
    "file_diff": 0.35,
    "unavailable": 0.10,
}

_KNOWN_TOOL_MARKERS: frozenset[str] = frozenset({
    "anthropic", "claude-code", "cursor", "openai-codex", "gemini-cli",
    "copilot", "continue", "aider", "goose", "windsurf", "cline", "sourcegraph",
})

# Text capping constants for content similarity
_SIM_CAP = 50_000
_SIM_HALF = _SIM_CAP // 2   # 25 000


# ── Internal helpers ──────────────────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Lowercase + collapse all whitespace to a single space."""
    return re.sub(r"\s+", " ", text.lower()).strip()


def _cap_text(text: str) -> str:
    """Cap to _SIM_CAP chars: first 25k + last 25k if longer."""
    if len(text) <= _SIM_CAP:
        return text
    return text[:_SIM_HALF] + text[-_SIM_HALF:]


def _trigram_set(text: str) -> frozenset[str]:
    """Build the set of all 3-char shingles from *text*."""
    return frozenset(text[i : i + 3] for i in range(len(text) - 2))


def trigram_jaccard(a: str, b: str) -> float:
    """Trigram Jaccard similarity — exported for tests."""
    a = _cap_text(_normalize_text(a))
    b = _cap_text(_normalize_text(b))
    sa = _trigram_set(a)
    sb = _trigram_set(b)
    if not sa or not sb:
        return 0.0
    intersection = len(sa & sb)
    union = len(sa | sb)
    return intersection / union if union > 0 else 0.0


def _score_similarity(sim: float) -> float:
    """Map a Jaccard similarity value to [0.1, 1.0] using linear interpolation.

    Anchor points: (0.2 → 0.4), (0.4 → 0.7), (0.7 → 1.0).
    Below 0.2 → 0.1; at or above 0.7 → 1.0.
    """
    if sim >= 0.7:
        return 1.0
    if sim >= 0.4:
        t = (sim - 0.4) / (0.7 - 0.4)
        return 0.7 + t * (1.0 - 0.7)
    if sim >= 0.2:
        t = (sim - 0.2) / (0.4 - 0.2)
        return 0.4 + t * (0.7 - 0.4)
    return 0.1


def _value_to_level(
    value: float,
) -> Literal["very_high", "high", "medium", "low", "very_low"]:
    if value >= 0.85:
        return "very_high"
    if value >= 0.65:
        return "high"
    if value >= 0.40:
        return "medium"
    if value >= 0.20:
        return "low"
    return "very_low"


# ── Main scorer ───────────────────────────────────────────────────────────────

def compute_confidence(inputs: ConfidenceInputs) -> ConfidenceResult:
    """Compute a five-signal evidence-weighted confidence score.

    Returns a ConfidenceResult whose .evidence list always contains exactly
    five EvidenceItems in stable order:
      capture_layer, request_uuid_match, time_correlation,
      content_similarity, tool_fingerprint.
    """
    evidence: list[EvidenceItem] = []

    # ── Signal 1: capture_layer (weight 0.30) ─────────────────────────────
    cap_score = _CAPTURE_LAYER_SCORES.get(inputs.capture_status, 0.10)
    evidence.append(EvidenceItem(
        signal="capture_layer",
        value=inputs.capture_status,
        weight=0.30,
        contribution=cap_score * 0.30,
        rationale=f"capture_status='{inputs.capture_status}' → layer score {cap_score:.2f}",
    ))

    # ── Signal 2: request_uuid_match (weight 0.25) ────────────────────────
    if inputs.request_uuid_matches_capture:
        uuid_score = 1.00
        uuid_rationale = "Request UUID confirmed matched between proxy and editor"
    elif inputs.request_uuid_present:
        uuid_score = 0.50
        uuid_rationale = "Request UUID present but match not confirmed"
    else:
        uuid_score = 0.00
        uuid_rationale = "No request UUID available"
    evidence.append(EvidenceItem(
        signal="request_uuid_match",
        value=inputs.request_uuid_matches_capture if inputs.request_uuid_matches_capture
              else inputs.request_uuid_present,
        weight=0.25,
        contribution=uuid_score * 0.25,
        rationale=uuid_rationale,
    ))

    # ── Signal 3: time_correlation (weight 0.15) ──────────────────────────
    if inputs.prompt_timestamp is None:
        time_score = 0.20
        delta_t: float | None = None
        time_rationale = "Prompt timestamp unavailable — timing unknown"
    else:
        delta_t = abs(
            (inputs.insertion_timestamp - inputs.prompt_timestamp).total_seconds()
        )
        if delta_t <= 2:
            time_score = 1.00
            time_rationale = f"Δt={delta_t:.1f}s — near-instant insertion"
        elif delta_t <= 10:
            time_score = 0.85
            time_rationale = f"Δt={delta_t:.1f}s — very fast insertion"
        elif delta_t <= 60:
            time_score = 0.60
            time_rationale = f"Δt={delta_t:.1f}s — within 1 minute"
        elif delta_t <= 300:
            time_score = 0.30
            time_rationale = f"Δt={delta_t:.1f}s — within 5 minutes"
        else:
            time_score = 0.10
            time_rationale = f"Δt={delta_t:.1f}s — long delay, weak correlation"
    evidence.append(EvidenceItem(
        signal="time_correlation",
        value=round(delta_t, 1) if delta_t is not None else None,
        weight=0.15,
        contribution=time_score * 0.15,
        rationale=time_rationale,
    ))

    # ── Signal 4: content_similarity (weight 0.20) ────────────────────────
    if not inputs.raw_model_response or not inputs.inserted_text.strip():
        sim_score = 0.20
        sim_value: float | None = None
        sim_rationale = "Response or inserted text unavailable — similarity unknown"
    else:
        sim_value = round(trigram_jaccard(inputs.raw_model_response, inputs.inserted_text), 4)
        sim_score = _score_similarity(sim_value)
        sim_rationale = f"Trigram Jaccard similarity = {sim_value:.4f}"
    evidence.append(EvidenceItem(
        signal="content_similarity",
        value=sim_value,
        weight=0.20,
        contribution=sim_score * 0.20,
        rationale=sim_rationale,
    ))

    # ── Signal 5: tool_fingerprint (weight 0.10) ──────────────────────────
    tool_lower = (inputs.tool_name or "").lower()
    if inputs.tool_name and any(m in tool_lower for m in _KNOWN_TOOL_MARKERS):
        fp_score = 1.00
        fp_rationale = f"Known tool marker matched: '{inputs.tool_name}'"
        fp_value: object = inputs.tool_name
    elif inputs.tool_name:
        fp_score = 0.60
        fp_rationale = f"Tool name present, no known marker: '{inputs.tool_name}'"
        fp_value = inputs.tool_name
    elif inputs.provider:
        fp_score = 0.40
        fp_rationale = f"Provider present, tool name absent: '{inputs.provider}'"
        fp_value = inputs.provider
    elif inputs.user_agent:
        fp_score = 0.30
        fp_rationale = "User-agent present; tool name and provider absent"
        fp_value = inputs.user_agent
    else:
        fp_score = 0.00
        fp_rationale = "No tool name, provider, or user-agent"
        fp_value = None
    evidence.append(EvidenceItem(
        signal="tool_fingerprint",
        value=fp_value,
        weight=0.10,
        contribution=fp_score * 0.10,
        rationale=fp_rationale,
    ))

    value = round(sum(e.contribution for e in evidence), 3)
    return ConfidenceResult(
        value=value,
        level=_value_to_level(value),
        method="weighted_evidence_v1",
        evidence=evidence,
    )
