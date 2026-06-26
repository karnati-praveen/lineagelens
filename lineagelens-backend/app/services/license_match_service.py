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
#   [0,  CLEAN)   → clean_within_corpus — no meaningful overlap with known corpus
#   [CLEAN, MATCH) → review             — partial overlap, manual check recommended
#   [MATCH, 1]    → match               — substantial overlap, treat as contaminated
_CLEAN_THRESHOLD: float = 0.10
_MATCH_THRESHOLD: float = 0.35

# Bumped whenever the fingerprint algorithm changes so a stale cert is detectable.
_SCANNER_VERSION = "kgram-shingle-1"

# PART 1 #2 — honest match states. "nothing checked" must never read as "clean".
#   not_configured       — no corpus is configured (we scanned nothing).
#   insufficient_corpus  — a corpus is configured but empty / too small to trust.
#   clean_within_corpus  — scanned against a real corpus, no meaningful overlap.
#   review               — partial overlap; needs human review.
#   match                — substantial overlap with a restricted license.
#   scan_error           — the scan itself failed.
# (A record never scanned carries license_status = NULL, surfaced as not_scanned.)
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_INSUFFICIENT_CORPUS = "insufficient_corpus"
STATUS_CLEAN_WITHIN_CORPUS = "clean_within_corpus"
STATUS_REVIEW = "review"
STATUS_MATCH = "match"
STATUS_SCAN_ERROR = "scan_error"

# States that an eligibility/clean-room gate may treat as "no restricted match
# found against a real corpus". Note: not_configured / insufficient_corpus are
# deliberately excluded — absence of a corpus is not evidence of cleanliness.
CLEAN_STATES = frozenset({"clean", STATUS_CLEAN_WITHIN_CORPUS})


@dataclass(frozen=True, slots=True)
class MatchResult:
    match_status: str
    best_match_license: str | None
    similarity: float
    # Provenance of the scan so a certificate can state exactly what was checked.
    scanner_version: str = _SCANNER_VERSION
    corpus_id: str | None = None
    corpus_digest: str | None = None
    corpus_size: int = 0
    match_threshold: float = _MATCH_THRESHOLD
    review_threshold: float = _CLEAN_THRESHOLD

    @property
    def coverage(self) -> str:
        """Human-readable coverage state for surfacing in certs/UI."""
        if self.match_status in (STATUS_NOT_CONFIGURED, STATUS_INSUFFICIENT_CORPUS):
            return "no_corpus"
        if self.match_status == STATUS_SCAN_ERROR:
            return "scan_failed"
        return "scanned"


def _corpus_digest(corpus: list[dict]) -> str | None:
    """Stable short digest of the corpus identity (sorted entry ids)."""
    if not corpus:
        return None
    ids = sorted(str(entry.get("id", "")) for entry in corpus)
    return hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:16]


def _classify(
    *,
    configured: bool,
    corpus: list[dict],
    best_sim: float,
    best_id: str | None,
) -> MatchResult:
    """Map (corpus presence, similarity) to an honest MatchResult."""
    digest = _corpus_digest(corpus)
    common = dict(
        corpus_digest=digest,
        corpus_size=len(corpus),
    )
    if not configured:
        return MatchResult(STATUS_NOT_CONFIGURED, None, 0.0, **common)
    if not corpus:
        return MatchResult(STATUS_INSUFFICIENT_CORPUS, None, 0.0, **common)
    if best_sim >= _MATCH_THRESHOLD:
        status, lic = STATUS_MATCH, best_id
    elif best_sim >= _CLEAN_THRESHOLD:
        status, lic = STATUS_REVIEW, best_id
    else:
        status, lic = STATUS_CLEAN_WITHIN_CORPUS, None
    return MatchResult(status, lic, round(best_sim, 4), corpus_id=lic, **common)


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

    Uses a stable SHA-256-derived hash truncated to 64 bits so the shingles
    are reproducible across Python processes and versions (hash() is not stable
    across restarts). 64 bits keeps birthday collisions negligible for large
    codebases. This must stay in sync with the proxy implementation
    (lineagelens-proxy/ingest.py _compute_shingles).
    """
    tokens = _normalize(code)
    if not tokens:
        return set()
    if len(tokens) < k:
        gram_str = " ".join(tokens)
        h = int(hashlib.sha256(gram_str.encode()).hexdigest()[:16], 16)
        return {h}
    shingles: set[int] = set()
    for i in range(len(tokens) - k + 1):
        gram_str = " ".join(tokens[i : i + k])
        h = int(hashlib.sha256(gram_str.encode()).hexdigest()[:16], 16)
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

    Returns [] if the path is unset or the file does not exist. Whether a corpus
    was *configured* (vs simply empty) is tracked separately via
    _is_corpus_configured so "nothing checked" is never reported as "clean".
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


def _is_corpus_configured() -> bool:
    """True if an operator has configured a corpus source (env path set)."""
    return bool(os.environ.get("LICENSE_FINGERPRINT_PATH", "").strip())


def _best_match(code_shingles: set[int], corpus: list[dict]) -> tuple[float, str | None]:
    best_sim = 0.0
    best_id: str | None = None
    for entry in corpus:
        entry_shingles: set[int] = set(entry.get("shingles", []))
        sim = _jaccard(code_shingles, entry_shingles)
        if sim > best_sim:
            best_sim = sim
            best_id = entry.get("id")
    return best_sim, best_id


def _corpus_match(code_shingles: set[int]) -> MatchResult:
    """Compare *code_shingles* against the env-configured corpus (honest states)."""
    corpus = _load_corpus()
    best_sim, best_id = _best_match(code_shingles, corpus) if corpus else (0.0, None)
    return _classify(
        configured=_is_corpus_configured(),
        corpus=corpus,
        best_sim=best_sim,
        best_id=best_id,
    )


# ── Matcher implementations ───────────────────────────────────────────────────

class LocalMatcher:
    """Deterministic offline k-gram shingle matcher.

    Works fully without network access — suitable for air-gapped environments
    and reproducible CI checks.
    """

    def __init__(self, corpus: list[dict] | None = None) -> None:
        # Injected corpus overrides the env-var path (used by tests). An injected
        # corpus (even []) counts as "configured" — the operator supplied it.
        self._corpus = corpus

    def _effective_corpus(self) -> list[dict]:
        return self._corpus if self._corpus is not None else _load_corpus()

    def _configured(self) -> bool:
        return self._corpus is not None or _is_corpus_configured()

    def scan(self, code: str) -> MatchResult:
        return self.scan_shingles(compute_shingles(code))

    def scan_shingles(self, code_shingles: set[int]) -> MatchResult:
        """Match pre-computed shingles against this matcher's corpus."""
        corpus = self._effective_corpus()
        best_sim, best_id = _best_match(code_shingles, corpus) if corpus else (0.0, None)
        return _classify(
            configured=self._configured(),
            corpus=corpus,
            best_sim=best_sim,
            best_id=best_id,
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

    try:
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
    except Exception as exc:  # never let a scan failure masquerade as "clean"
        logger.error("License scan failed for record — recording scan_error: %s", exc)
        result = MatchResult(STATUS_SCAN_ERROR, None, 0.0)

    record.license_status = result.match_status
    record.license_match_license = result.best_match_license
    record.license_similarity = result.similarity
    # Persist the scan provenance so a certificate can state exactly what was
    # checked (corpus digest, scanner version, thresholds, coverage).
    if isinstance(record.provenance_payload, dict):
        record.provenance_payload = {
            **record.provenance_payload,
            "licenseScanResult": {
                "status": result.match_status,
                "scannerVersion": result.scanner_version,
                "corpusDigest": result.corpus_digest,
                "corpusSize": result.corpus_size,
                "matchThreshold": result.match_threshold,
                "reviewThreshold": result.review_threshold,
                "coverage": result.coverage,
            },
        }
    session.add(record)

    return result
