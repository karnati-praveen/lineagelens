import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
import uuid as uuid_pkg

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.schemas.provenance import SearchRequest
from app.services.ingest_normalizer import NormalizedIngestPayload, normalize_ingest_payload
from app.services.provenance_service import ingest_provenance_event, search_provenance_records
import app.services.provenance_service as provenance_service


BASE_SETTINGS = {
    'APP_ENV': 'test',
    'JWT_SECRET_KEY': 'c' * 40,
    'BACKEND_CORS_ORIGINS': 'http://localhost:3000',
}


class FakeScalarResult:
    def __init__(self, records: list[object]):
        self._records = records

    def all(self) -> list[object]:
        return self._records


class FakeExecuteResult:
    def __init__(self, records: list[object]):
        self._records = records

    def scalars(self) -> FakeScalarResult:
        return FakeScalarResult(self._records)

    def scalar_one_or_none(self) -> object | None:
        return self._records[0] if self._records else None


class FakeSession:
    def __init__(self, records: list[object]):
        self.records = records
        self.statements: list[object] = []
        self.add_calls = 0
        self.flush_calls = 0
        self.commit_calls = 0
        self.refresh_calls = 0

    async def execute(self, statement: object) -> FakeExecuteResult:
        self.statements.append(statement)
        await asyncio.sleep(0)
        return FakeExecuteResult(self.records)

    def add(self, record: object) -> None:
        self.add_calls += 1

    async def flush(self) -> None:
        self.flush_calls += 1
        await asyncio.sleep(0)

    async def commit(self) -> None:
        self.commit_calls += 1
        await asyncio.sleep(0)

    async def refresh(self, record: object) -> None:
        self.refresh_calls += 1
        await asyncio.sleep(0)


def build_settings(**overrides: object) -> Settings:
    return Settings.model_validate({**BASE_SETTINGS, **overrides})


def make_record(
    uuid: str,
    *,
    timestamp_iso: datetime,
    file_path: str,
    inserted_code: str,
    prompt_messages: object | None = None,
    model_name: str | None = 'gpt-4o-mini',
    provenance_payload: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        uuid=uuid,
        timestamp_iso=timestamp_iso,
        file_path=file_path,
        inserted_code=inserted_code,
        prompt_messages=prompt_messages,
        model_name=model_name,
        provenance_payload=provenance_payload or {},
    )


def test_search_provenance_records_keyword_fallback_returns_warnings() -> None:
    settings = build_settings(VECTOR_SEARCH_ENABLED=False, BACKEND_MODE='basic')
    session = FakeSession(
        [
            make_record(
                '11111111-1111-4111-8111-111111111111',
                timestamp_iso=datetime.fromisoformat('2026-04-18T10:00:00+00:00'),
                file_path='src/api/auth.ts',
                inserted_code='const apiKey = process.env.API_KEY;',
                provenance_payload={'filePath': 'src/api/auth.ts'},
            ),
            make_record(
                '22222222-2222-4222-8222-222222222222',
                timestamp_iso=datetime.fromisoformat('2026-04-18T09:00:00+00:00'),
                file_path='src/other.ts',
                inserted_code='const value = 1;',
                provenance_payload={'filePath': 'src/other.ts'},
            ),
        ]
    )
    search = SearchRequest(query='API key', limit=10)

    rows, warnings, total = asyncio.run(
        search_provenance_records(
            session=cast(AsyncSession, session),
            search=search,
            workspace_id='workspace-alpha',
            settings=settings,
        )
    )

    assert warnings == ['Vector search is disabled; using keyword fallback search.']
    assert total == 1
    assert len(rows) == 1
    assert rows[0][0].uuid == '11111111-1111-4111-8111-111111111111'
    assert rows[0][1] is not None
    assert rows[0][1] > 0


def test_ingest_provenance_event_is_idempotent_for_duplicate_request_uuid(monkeypatch) -> None:
    workspace_id = 'workspace-alpha'
    duplicate_request_uuid = uuid_pkg.UUID('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')
    existing_record = SimpleNamespace(
        uuid=uuid_pkg.UUID('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
        request_uuid=duplicate_request_uuid,
        workspace_id=workspace_id,
        lineage_node_id='lineage-123',
    )

    payload = normalize_ingest_payload(
        {
            'id': 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
            'requestUuid': str(duplicate_request_uuid),
            'timestampIso': '2026-04-18T10:00:00.000Z',
            'workspaceId': workspace_id,
            'filePath': 'src/example.ts',
            'fileUri': 'file:///workspace/src/example.ts',
            'insertedText': 'const answer = 42;',
            'prompt': {
                'fullMessages': [{'role': 'user', 'content': 'Add an answer constant.'}],
                'rawModelResponse': 'const answer = 42;'
            }
        },
        workspace_id=workspace_id,
    )

    session = FakeSession([existing_record])

    async def fail_generate_embedding(*args: object, **kwargs: object) -> None:
        raise AssertionError('generate_embedding should not run for duplicate ingest payloads.')

    monkeypatch.setattr(provenance_service, 'generate_embedding', fail_generate_embedding)

    outcome = asyncio.run(
        ingest_provenance_event(
            session=cast(AsyncSession, session),
            payload=payload,
            auth=SimpleNamespace(workspace_id=workspace_id),  # type: ignore[arg-type]
            settings=build_settings(VECTOR_SEARCH_ENABLED=False, BACKEND_MODE='basic'),
            neo4j_service=None,
        )
    )

    assert outcome.record is existing_record
    assert session.add_calls == 0
    assert session.flush_calls == 0
    assert session.commit_calls == 0
    assert session.refresh_calls == 0
