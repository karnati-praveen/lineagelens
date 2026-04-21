from __future__ import annotations

from typing import Any

from app.schemas.team import TeamMemberStats


def build_team_member_stats(
    users: list[Any],
    record_counts: dict[str, int],
) -> list[TeamMemberStats]:
    members: list[TeamMemberStats] = []

    for user in users:
        user_id = str(getattr(user, "id", ""))
        created_at = getattr(user, "created_at", None)
        joined_at_iso = created_at.isoformat() if hasattr(created_at, "isoformat") else None

        members.append(
            TeamMemberStats(
                id=user_id,
                username=str(getattr(user, "username", "")),
                role=getattr(user, "role", None) or "member",
                record_count=int(record_counts.get(user_id, 0)),
                joined_at_iso=joined_at_iso,
            )
        )

    return members