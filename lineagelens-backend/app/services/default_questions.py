from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SavedQuery

_SYSTEM_USER = "__system__"

_DEFAULT_QUESTIONS: list[tuple[str, dict]] = [
    (
        "Unreviewed AI code (last 90 days)",
        {"reviewStatus": "unreviewed", "relativeDays": 90},
    ),
    (
        "AI-written authentication logic",
        {"category": "auth"},
    ),
    (
        "AI code touching secrets",
        {"category": "secrets"},
    ),
    (
        "High-risk and never reviewed",
        {"riskMin": 65, "reviewStatus": "unreviewed"},
    ),
    (
        "What did AI write this week?",
        {"relativeDays": 7},
    ),
]


async def seed_default_questions(session: AsyncSession, workspace_id: str) -> None:
    """Add default saved queries for a workspace. Idempotent: skips names that already exist."""
    for name, query in _DEFAULT_QUESTIONS:
        existing = await session.scalar(
            select(SavedQuery.id).where(
                and_(
                    SavedQuery.workspace_id == workspace_id,
                    SavedQuery.name == name,
                )
            )
        )
        if existing is None:
            session.add(
                SavedQuery(
                    workspace_id=workspace_id,
                    user_id=_SYSTEM_USER,
                    name=name,
                    query=query,
                )
            )
