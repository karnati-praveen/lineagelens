from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mode_guard import require_non_solo
from app.core.security import AuthContext, get_current_auth_context
from app.db.session import get_db_session
from app.services.outcome_service import (
    _VALID_OUTCOME_TYPES,
    _VALID_SOURCES,
    compute_durability,
    get_record_outcome_timeline,
    ingest_git_outcome,
    record_outcome,
)

router = APIRouter(
    prefix="/trust",
    tags=["trust"],
    dependencies=[Depends(require_non_solo)],
)
logger = logging.getLogger(__name__)


# ─── Schemas ──────────────────────────────────────────────────────────────────

class OutcomeEvent(BaseModel):
    recordUuid: str = Field(alias="recordUuid")
    outcomeType: str = Field(alias="outcomeType")
    source: str
    observedAt: datetime | None = Field(default=None, alias="observedAt")
    detailJson: dict | None = Field(default=None, alias="detailJson")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("outcomeType")
    @classmethod
    def _validate_outcome_type(cls, v: str) -> str:
        if v not in _VALID_OUTCOME_TYPES:
            raise ValueError(f"outcomeType must be one of {sorted(_VALID_OUTCOME_TYPES)}")
        return v

    @field_validator("source")
    @classmethod
    def _validate_source(cls, v: str) -> str:
        if v not in _VALID_SOURCES:
            raise ValueError(f"source must be one of {sorted(_VALID_SOURCES)}")
        return v


class OutcomeBatch(BaseModel):
    events: list[OutcomeEvent]

    @field_validator("events")
    @classmethod
    def _validate_events(cls, v: list[OutcomeEvent]) -> list[OutcomeEvent]:
        if not v:
            raise ValueError("events must be non-empty")
        if len(v) > 200:
            raise ValueError("Maximum 200 events per batch")
        return v


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/outcomes")
async def ingest_outcomes(
    batch: OutcomeBatch,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Accept a batch of outcome events from CI, extension, or git integration."""
    created_count = 0
    skipped_count = 0
    errors: list[str] = []

    for event in batch.events:
        try:
            _, created = await record_outcome(
                session,
                workspace_id=auth.workspace_id,
                record_uuid=event.recordUuid,
                outcome_type=event.outcomeType,
                source=event.source,
                observed_at=event.observedAt,
                detail_json=event.detailJson,
                user_id=auth.subject,
            )
            if created:
                created_count += 1
            else:
                skipped_count += 1
        except ValueError as exc:
            errors.append(f"{event.recordUuid}: {exc}")

    return {
        "createdCount": created_count,
        "skippedCount": skipped_count,
        "errors": errors,
    }


@router.get("/durability")
async def get_durability(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    group_by: str = Query(default="model", alias="groupBy"),
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
) -> dict:
    """Durability leaderboard grouped by model, prompt_pattern, or developer."""
    try:
        results = await compute_durability(
            session,
            auth.workspace_id,
            group_by=group_by,
            date_from=date_from,
            date_to=date_to,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {"items": results, "groupBy": group_by}


@router.get("/record/{record_uuid}")
async def get_record_trust(
    record_uuid: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return outcome timeline and durability context for a specific AI block."""
    from app.services.provenance_service import get_provenance_by_uuid, serialize_provenance_record

    record = await get_provenance_by_uuid(session, record_uuid, auth.workspace_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found.")

    timeline = await get_record_outcome_timeline(session, auth.workspace_id, record_uuid)

    model_name = record.model_name
    if model_name:
        all_durability = await compute_durability(session, auth.workspace_id, group_by="model")
        model_row = next(
            (r for r in all_durability if r["groupValue"] == model_name), None
        )
        model_durability_score = model_row["durabilityScore"] if model_row else None
    else:
        model_durability_score = None

    return {
        "record": serialize_provenance_record(record, model_durability=model_durability_score),
        "outcomesTimeline": timeline,
        "modelDurabilityScore": model_durability_score,
    }
