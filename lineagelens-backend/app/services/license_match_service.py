from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

logger = logging.getLogger(__name__)

# k-gram window used for shingle fingerprinting — must match the proxy.
_SHINGLE_K = 5

# Similarity thresholds (Jaccard):
#   [0,  CLEAN)   → clean   — no meaningful overlap with known restricted corpus
#   [CLEAN, MATCH) → review  — partial overlap, manual check recommended
#   [MATCH, 1]    → match   — substantial overlap, treat as contaminated
_CLEAN_THRESHOLD: float = 0.10
_MATCH_THRESHOLD: float = 0.35


@dataclass(frozen=True, slots=True)
class MatchResult:
    match_status: str  # "clean" | "review" | "match"
    best_match_license: str | None
    similarity: float


class Matcher(Protocol):
    def scan(self, code: str) -> MatchResult: ...


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalize(code: str) -> list[str]:
    """Strip line/block comments, lowercase, tokenize on word boundaries."""
    code = re.sub(r"//[^\n]*", " ", code)
    code = re.sub(r"/\*.*?\*/", " ", code, flags=re.DOTALL)
    code = re.sub(r"#[^\n]*", " ", code)
    return re.findall(r"\w+", code.lower())


def compute_shingles(code: str, k: int = _SHINGLE_K) -> set[int]:
    """Return the set of k-gram shingle hashes for *code*.

    Uses a stable SHA-256-derived hash truncated to 32 bits so the shingles
    are reproducible across Python processes and versions (hash() is not stable
    across restarts). This must stay in sync with the proxy implementation.
    """
    tokens = _normalize(code)
    if not tokens:
        return set()
    if len(tokens) < k:
        gram_str = " ".join(tokens)
        h = int(hashlib.sha256(gram_str.encode()).hexdigest()[:8], 16)
        return {h}
    shingles: set[int] = set()
    for i in range(len(tokens) - k + 1):
        gram_str = " ".join(tokens[i : i + k])
        h = int(hashlib.sha256(gram_str.encode()).hexdigest()[:8], 16)
        shingles.add(h)
    return shingles


def _jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


# ── Corpus loading ────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_corpus() -> list[dict]:
    """Load license fingerprint corpus from LICENSE_FINGERPRINT_PATH.

    Expected JSON format:
        [{"id": "GPL-3.0", "name": "...", "shingles": [int, ...], "restrictive": true}, ...]

    Returns [] if the path is unset or the file does not exist — callers treat
    an empty corpus as "no matches" (all scans return clean).
    """
    path = os.environ.get("LICENSE_FINGERPRINT_PATH", "").strip()
    if not path:
        return []
    if not os.path.isfile(path):
        logger.warning("LICENSE_FINGERPRINT_PATH %r does not exist — license matching disabled.", path)
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.error("Failed to load license fingerprint corpus from %r: %s", path, exc)
        return []


def _corpus_match(code_shingles: set[int]) -> MatchResult:
    """Compare *code_shingles* against every corpus entry and return the best match."""
    corpus = _load_corpus()
    if not corpus:
        return MatchResult("clean", None, 0.0)

    best_sim = 0.0
    best_id: str | None = None

    for entry in corpus:
        entry_shingles: set[int] = set(entry.get("shingles", []))
        sim = _jaccard(code_shingles, entry_shingles)
        if sim > best_sim:
            best_sim = sim
            best_id = entry.get("id")

    if best_sim >= _MATCH_THRESHOLD:
        status = "match"
    elif best_sim >= _CLEAN_THRESHOLD:
        status = "review"
    else:
        status, best_id = "clean", None

    return MatchResult(
        match_status=status,
        best_match_license=best_id,
        similarity=round(best_sim, 4),
    )


# ── Matcher implementations ───────────────────────────────────────────────────

class LocalMatcher:
    """Deterministic offline k-gram shingle matcher.

    Works fully without network access — suitable for air-gapped environments
    and reproducible CI checks.
    """

    def __init__(self, corpus: list[dict] | None = None) -> None:
        # Injected corpus overrides the env-var path (used by tests).
        self._corpus = corpus

    def _effective_corpus(self) -> list[dict]:
        return self._corpus if self._corpus is not None else _load_corpus()

    def scan(self, code: str) -> MatchResult:
        return self.scan_shingles(compute_shingles(code))

    def scan_shingles(self, code_shingles: set[int]) -> MatchResult:
        """Match pre-computed shingles against this matcher's corpus."""
        corpus = self._effective_corpus()
        if not corpus:
            return MatchResult("clean", None, 0.0)
        best_sim = 0.0
        best_id: str | None = None
        for entry in corpus:
            entry_shingles: set[int] = set(entry.get("shingles", []))
            sim = _jaccard(code_shingles, entry_shingles)
            if sim > best_sim:
                best_sim = sim
                best_id = entry.get("id")
        if best_sim >= _MATCH_THRESHOLD:
            status = "match"
        elif best_sim >= _CLEAN_THRESHOLD:
            status = "review"
        else:
            status, best_id = "clean", None
        return MatchResult(
            match_status=status,
            best_match_license=best_id,
            similarity=round(best_sim, 4),
        )


# Extension point for an external IP-scan provider.
# class ExternalMatcher:
#     """Call an external IP-scan service (e.g. FOSSA, Black Duck) for deeper analysis.
#
#     Must implement the Matcher protocol: scan(code: str) -> MatchResult.
#     Switch from LocalMatcher by setting _default_matcher = ExternalMatcher(api_key=...).
#     """
#     async def scan(self, code: str) -> MatchResult: ...


_default_matcher = LocalMatcher()


# ── Main service entry point ──────────────────────────────────────────────────

async def scan_and_record(session, record, *, matcher: Matcher | None = None) -> MatchResult:
    """Scan *record* for license contamination, persist the result, and return it.

    If the ingest payload already carries proxy-computed shingles they are used
    directly to avoid re-fingerprinting.  Falls back to fingerprinting
    record.inserted_code.
    """
    m = matcher or _default_matcher
    code = record.inserted_code or ""

    proxy_scan = None
    if record.provenance_payload:
        proxy_scan = record.provenance_payload.get("licenseScan")

    if (
        proxy_scan
        and isinstance(proxy_scan, dict)
        and isinstance(proxy_scan.get("shingles"), list)
    ):
        code_shingles = set(int(s) for s in proxy_scan["shingles"])
        if isinstance(m, LocalMatcher):
            # Use the matcher's effective corpus (injected or env-var) with proxy shingles.
            result = m.scan_shingles(code_shingles)
        else:
            result = _corpus_match(code_shingles)
    else:
        result = m.scan(code)

    record.license_status = result.match_status
    record.license_match_license = result.best_match_license
    record.license_similarity = result.similarity
    session.add(record)

    return result
