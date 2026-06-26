from __future__ import annotations

"""Typed evidence / claim model (PART 1 #7).

A self-issued signature proves *integrity* (the bytes did not change), not
*truth* (that a claim about the world is correct). To stop the UI/exports from
collapsing very different kinds of evidence into one green check, every
trust-relevant fact is tagged with a claim class:

    observed   — directly recorded by our own instrumentation (file change,
                 inserted text, timing, hash-chain linkage).
    correlated — inferred by matching/joining signals (e.g. similarity match,
                 lineage descendant via the graph).
    declared   — asserted by a party with no independent proof (client-supplied
                 review engagement, manual outcome, model name from the caller).
    derived    — computed by our own formula from other inputs (risk score,
                 durability score, confidence).
    unknown    — not available (prompt unavailable, source unconfirmed).

Consumers MUST render these distinctly and never show a single aggregate
"verified" state.
"""

import hashlib
from dataclasses import asdict, dataclass, field

OBSERVED = "observed"
CORRELATED = "correlated"
DECLARED = "declared"
DERIVED = "derived"
UNKNOWN = "unknown"

CLAIM_CLASSES = frozenset({OBSERVED, CORRELATED, DECLARED, DERIVED, UNKNOWN})


@dataclass(frozen=True)
class Claim:
    """One typed claim about a record/artifact."""
    field: str
    value: object
    claim_class: str
    source: str
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["claimClass"] = d.pop("claim_class")
        return d


def classify_record_claims(record) -> list[dict]:
    """Return the typed claims for a provenance record.

    Pure/defensive (reads via getattr) so it works on ORM rows and test fakes.
    """
    claims: list[Claim] = []

    # Inserted code + file path are directly observed by our capture.
    claims.append(Claim("inserted_code", "present", OBSERVED, "capture"))
    claims.append(Claim("file_path", getattr(record, "file_path", None), OBSERVED, "capture"))

    # Model name is asserted by the caller/proxy; we did not independently verify it.
    model_name = getattr(record, "model_name", None)
    claims.append(Claim(
        "model_name", model_name,
        DECLARED if model_name else UNKNOWN,
        "proxy_or_client",
        [] if model_name else ["model not reported"],
    ))

    # Prompt: observed if we still hold it; redaction/deletion makes it unknown,
    # though the committed digest remains.
    lifecycle = getattr(record, "lifecycle_state", "active") or "active"
    has_prompt = getattr(record, "prompt_messages", None) is not None
    if has_prompt:
        claims.append(Claim("prompt", "present", OBSERVED, "capture"))
    elif getattr(record, "prompt_sha256", None):
        claims.append(Claim(
            "prompt", "committed_digest_only", CORRELATED, "commitment",
            ["raw prompt removed by redaction/deletion; only its digest is retained"]
            if lifecycle != "active" else ["prompt not captured; only a digest is available"],
        ))
    else:
        claims.append(Claim("prompt", None, UNKNOWN, "capture", ["prompt unavailable"]))

    # Hash-chain linkage is observed by us; it is tamper-evident, not proof of truth.
    if getattr(record, "record_hash", None):
        claims.append(Claim(
            "hash_chain", "linked", OBSERVED, "integrity_service",
            ["tamper-evident only; a full-DB rewrite could recompute the chain"],
        ))

    # Risk score is derived by our heuristic formula.
    risk = getattr(record, "risk_score", None)
    if risk is not None:
        claims.append(Claim("risk_score", risk, DERIVED, "risk_service",
                            ["heuristic; not a SAST result"]))

    # License status is derived from corpus matching; absence of a corpus is unknown.
    ls = getattr(record, "license_status", None)
    if ls in (None, "not_configured", "insufficient_corpus", "not_scanned"):
        claims.append(Claim("license_status", ls or "not_scanned", UNKNOWN, "license_match",
                            ["no corpus checked; not evidence of cleanliness"]))
    else:
        claims.append(Claim("license_status", ls, DERIVED, "license_match"))

    # PART 2 #17 — cross-check the proxy's client-side commitment against what
    # the backend actually stored, so the proxy/DB can't silently disagree.
    commitment_claim = _commitment_claim(record)
    if commitment_claim is not None:
        claims.append(commitment_claim)

    return [c.to_dict() for c in claims]


def _commitment_claim(record) -> "Claim | None":
    pp = getattr(record, "provenance_payload", None) or {}
    commitments = pp.get("commitments") or (pp.get("rawPayload") or {}).get("commitments")
    if not isinstance(commitments, dict):
        return None

    committed = commitments.get("insertedTextSha256")
    # Prefer the committed column; fall back to hashing the live code (active rows).
    backend_sha = getattr(record, "content_sha256", None)
    if backend_sha is None:
        code = getattr(record, "inserted_code", None)
        backend_sha = hashlib.sha256(code.encode()).hexdigest() if code else None

    if committed and backend_sha:
        status = "matched" if committed == backend_sha else "mismatch"
        limitations = [] if status == "matched" else [
            "proxy commitment disagrees with stored content — possible tampering or modification"
        ]
    else:
        status = "unverified"
        limitations = ["insufficient data to compare client commitment"]

    return Claim(
        field="client_commitment",
        value=status,
        claim_class=CORRELATED,
        source=commitments.get("committedBy", "proxy"),
        limitations=limitations,
    )
