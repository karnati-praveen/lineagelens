from __future__ import annotations

"""Privacy lifecycle for provenance records (PART 2 #10 & #11).

Redaction and deletion used to mutate or physically remove rows, which made the
tamper-evident hash chain report a false ``tampered`` (redaction nulled
``prompt_messages`` without re-committing) and broke ``prev_hash`` linkage
(hard deletes left dangling references).

This module replaces both with a *non-destructive lifecycle*:

  * compute a content commitment (digests of the fields being removed),
  * emit a signed, append-only ``RecordLifecycleEvent`` (Ed25519),
  * scrub the plaintext but **keep the row as a tombstone** so the chain link
    survives,
  * mark ``lifecycle_state`` = ``redacted`` | ``deleted``.

The offline verifier then reports ``validly_redacted`` / ``validly_deleted``
instead of ``tampered``.  Content scrubbed *without* a matching signed event is
the real tamper signal.
"""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attestation import (
    SignedAttestation,
    build_attestation,
    sign_attestation,
    verify_attestation,
)
from app.db.models import ProvenanceRecord, RecordLifecycleEvent
from app.services.integrity_service import (
    compute_content_sha256,
    compute_prompt_sha256,
)

# Fields cleared by each operation. Redaction keeps inserted_code (the code must
# remain reviewable); deletion is a full tombstone.
_REDACTION_FIELDS = (
    "prompt_messages",
    "raw_model_response",
    "surrounding_context",
    "context_snapshot",
)
_DELETION_FIELDS = (
    "prompt_messages",
    "raw_model_response",
    "surrounding_context",
    "context_snapshot",
    "inserted_code",
    "embeddings",
    "ast_snapshot",
    "embedding_vector",
)


def _build_commitment(record: ProvenanceRecord, fields_removed: tuple[str, ...]) -> dict[str, Any]:
    """Commit to the content prior to scrubbing so it stays attestable.

    Prefers any commitment already captured at hash-chain time; otherwise
    computes one from the live content (back-fill for legacy / non-chained rows).
    """
    prompt_sha = record.prompt_sha256 or compute_prompt_sha256(record.prompt_messages)
    content_sha = record.content_sha256 or compute_content_sha256(record.inserted_code)
    return {
        "prompt_sha256": prompt_sha,
        "content_sha256": content_sha,
        "fields_removed": list(fields_removed),
    }


def _scrub(record: ProvenanceRecord, fields: tuple[str, ...]) -> None:
    # inserted_code is NOT NULL in the schema — tombstone it with an empty string.
    for field in fields:
        if field == "inserted_code":
            record.inserted_code = ""
        else:
            setattr(record, field, None)


async def apply_lifecycle_event(
    session: AsyncSession,
    record: ProvenanceRecord,
    *,
    event_type: str,
    authorized_by: str | None,
    reason: str | None = None,
    policy_ref: str | None = None,
) -> RecordLifecycleEvent:
    """Record a signed lifecycle event and scrub the record in place.

    event_type: "redaction" | "deletion". Caller is responsible for committing
    the session.  Does not itself decide authorisation — the route enforces that.
    """
    if event_type not in ("redaction", "deletion"):
        raise ValueError(f"Invalid lifecycle event_type: {event_type!r}")

    fields = _REDACTION_FIELDS if event_type == "redaction" else _DELETION_FIELDS
    commitment = _build_commitment(record, fields)

    # Back-fill the commitment columns so the verifier can validate the chain
    # hash of a redacted record using the committed prompt digest (not the now
    # null prompt_messages).
    if record.prompt_sha256 is None:
        record.prompt_sha256 = commitment["prompt_sha256"]
    if record.content_sha256 is None:
        record.content_sha256 = commitment["content_sha256"]

    statement = build_attestation(
        subject_type="record_lifecycle",
        subject_id=str(record.uuid),
        claims={
            "event_type": event_type,
            "reason": reason,
            "policy_ref": policy_ref,
            "authorized_by": authorized_by,
            "commitment": commitment,
        },
        workspace_id=record.workspace_id,
        prev_hash=record.record_hash,
    )
    signed: SignedAttestation = sign_attestation(statement)

    event = RecordLifecycleEvent(
        workspace_id=record.workspace_id,
        record_uuid=str(record.uuid),
        event_type=event_type,
        reason=reason,
        policy_ref=policy_ref,
        authorized_by=authorized_by,
        content_commitment=commitment,
        statement_json=json.dumps(signed.statement, sort_keys=True, default=str),
        signature=signed.signature,
        public_key_id=signed.public_key_id,
        prev_hash=record.record_hash,
    )
    session.add(event)

    # Scrub plaintext and mark the lifecycle state. record_hash / prev_hash are
    # deliberately left untouched so chain linkage survives.
    _scrub(record, fields)
    record.is_redacted = True
    record.lifecycle_state = "deleted" if event_type == "deletion" else "redacted"
    session.add(record)

    return event


async def get_latest_event(
    session: AsyncSession,
    workspace_id: str,
    record_uuid: str,
) -> RecordLifecycleEvent | None:
    result = await session.execute(
        select(RecordLifecycleEvent)
        .where(
            RecordLifecycleEvent.workspace_id == workspace_id,
            RecordLifecycleEvent.record_uuid == record_uuid,
        )
        .order_by(RecordLifecycleEvent.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def verify_event_signature(event: RecordLifecycleEvent) -> bool:
    """Verify the Ed25519 signature on a lifecycle event. Never raises."""
    try:
        statement = json.loads(event.statement_json)
    except (TypeError, ValueError):
        return False
    return verify_attestation(
        SignedAttestation(
            statement=statement,
            signature=event.signature,
            public_key_id=event.public_key_id,
        )
    )


async def list_events_for_workspace(
    session: AsyncSession,
    workspace_id: str,
    *,
    date_from=None,
    date_to=None,
) -> list[RecordLifecycleEvent]:
    """All lifecycle (redaction/deletion) events for a workspace, optionally date-bounded.

    Used by the evidence capsule (PART 5 #51) to bundle the privacy lifecycle
    alongside the records it acted on.
    """
    filters = [RecordLifecycleEvent.workspace_id == workspace_id]
    if date_from is not None:
        filters.append(RecordLifecycleEvent.created_at >= date_from)
    if date_to is not None:
        filters.append(RecordLifecycleEvent.created_at <= date_to)
    result = await session.execute(
        select(RecordLifecycleEvent).where(*filters).order_by(RecordLifecycleEvent.id.asc())
    )
    return list(result.scalars().all())


def commitment_matches_record(event: RecordLifecycleEvent, record: ProvenanceRecord) -> bool:
    """Confirm the event's committed digests match the record's stored commitments.

    Guards against a DBA swapping the commitment columns after the fact: the
    signed event froze the digests, so they must still agree.
    """
    commitment = event.content_commitment or {}
    if record.prompt_sha256 is not None and commitment.get("prompt_sha256") != record.prompt_sha256:
        return False
    if record.content_sha256 is not None and commitment.get("content_sha256") != record.content_sha256:
        return False
    return True
