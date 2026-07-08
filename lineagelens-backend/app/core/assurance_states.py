from __future__ import annotations

"""Central catalogue of "honest assurance state" strings (PART 5 #58).

No silent green: every capability that can be unavailable, partial, or
degraded must say so with an explicit, machine-readable state rather than
collapsing to a fake success value (a bare `true`/`100`/`[]`).

Several of these states already existed, scattered across services
(license_match_service, embedding_service, recall_service). This module is a
*catalogue*, not a new source of truth — it re-exports those existing
families so there is exactly one place to import any assurance-state string
from, and it adds the states the independence report flagged as still
missing: `blast_radius_partial`, `prompt_unavailable` (as a dedicated
PromptAvailabilityState), and `locally_tamper_evident` (as a machine-readable
ChainState, not just prose).
"""

from enum import Enum

from app.services.embedding_service import SEMANTIC_UNAVAILABLE_WARNING  # noqa: F401
from app.services.license_match_service import (
    STATUS_CLEAN_WITHIN_CORPUS,
    STATUS_INSUFFICIENT_CORPUS,
    STATUS_MATCH,
    STATUS_NOT_CONFIGURED,
    STATUS_REVIEW,
    STATUS_SCAN_ERROR,
)


class ChainState(str, Enum):
    """Top-level /integrity/verify outcome (PART 4 #33 privacy-lifecycle states)."""

    FULLY_AVAILABLE = "fully_available"
    VALIDLY_REDACTED = "validly_redacted"
    VALIDLY_DELETED = "validly_deleted"
    LOCALLY_TAMPER_EVIDENT = "locally_tamper_evident"
    UNAVAILABLE_KEY_DESTROYED = "unavailable_key_destroyed"
    TAMPERED = "tampered"
    UNVERIFIABLE = "unverifiable"


class BlastRadiusState(str, Enum):
    """Mirrors recall_service.BlastRadiusResult.coverage_status, plus PARTIAL."""

    CHECKED = "checked"
    CHECKED_EMPTY = "checked_empty"
    BLAST_RADIUS_PARTIAL = "blast_radius_partial"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class PromptAvailabilityState(str, Enum):
    """Whether a record's raw prompt can still be inspected."""

    AVAILABLE = "available"
    COMMITTED_DIGEST_ONLY = "committed_digest_only"
    PROMPT_UNAVAILABLE = "prompt_unavailable"


class LicenseState(str, Enum):
    """Re-export of license_match_service's honest match states (PART 1 #2)."""

    NOT_CONFIGURED = STATUS_NOT_CONFIGURED
    INSUFFICIENT_CORPUS = STATUS_INSUFFICIENT_CORPUS
    CLEAN_WITHIN_CORPUS = STATUS_CLEAN_WITHIN_CORPUS
    REVIEW = STATUS_REVIEW
    MATCH = STATUS_MATCH
    SCAN_ERROR = STATUS_SCAN_ERROR


class EmbeddingState(str, Enum):
    """Re-export of embedding_service's semantic-availability state (PART 3 #18)."""

    SEMANTIC_ACTIVE = "semantic_active"
    SEMANTIC_SEARCH_UNAVAILABLE = "semantic_search_unavailable"


def prompt_availability_state(record: object) -> str:
    """Return the PromptAvailabilityState value for a provenance record.

    Mirrors the prompt branch of evidence.classify_record_claims — kept as a
    small, independently-callable function so routes can surface a
    machine-readable `promptState` field without re-deriving the full claim
    list.
    """
    if getattr(record, "prompt_messages", None) is not None:
        return PromptAvailabilityState.AVAILABLE.value
    if getattr(record, "prompt_sha256", None):
        return PromptAvailabilityState.COMMITTED_DIGEST_ONLY.value
    return PromptAvailabilityState.PROMPT_UNAVAILABLE.value


def all_known_states() -> dict[str, list[str]]:
    """Return every known assurance-state family -> its member values.

    Used by tests to guard against a new scattered "state" string constant
    being introduced elsewhere instead of added here.
    """
    return {
        "chain": [s.value for s in ChainState],
        "blastRadius": [s.value for s in BlastRadiusState],
        "promptAvailability": [s.value for s in PromptAvailabilityState],
        "license": [s.value for s in LicenseState],
        "embedding": [s.value for s in EmbeddingState],
    }
