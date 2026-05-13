from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from app.services.team_service import build_team_member_stats


def test_build_team_member_stats_formats_iso_and_counts() -> None:
    users = [
        SimpleNamespace(
            id=UUID("11111111-1111-4111-8111-111111111111"),
            username="alice",
            role="admin",
            created_at=datetime(2026, 4, 18, 10, 30, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=UUID("22222222-2222-4222-8222-222222222222"),
            username="bob",
            role="member",
            created_at=datetime(2026, 4, 18, 11, 45, tzinfo=UTC),
        ),
    ]

    stats = build_team_member_stats(users, {str(users[0].id): 7})

    assert stats[0].record_count == 7
    assert stats[0].joined_at_iso == "2026-04-18T10:30:00+00:00"
    assert stats[1].record_count == 0
    assert stats[1].role == "member"