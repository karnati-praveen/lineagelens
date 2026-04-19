from app.services.ingest_normalizer import normalize_ingest_payload


WORKSPACE_ID = 'workspace-alpha'


def test_normalize_ingest_payload_preserves_existing_extension_normalized_event() -> None:
    payload = {
        'id': '11111111-1111-4111-8111-111111111111',
        'timestampIso': '2026-04-18T10:00:00.000Z',
        'workspaceId': WORKSPACE_ID,
        'filePath': 'src/example.ts',
        'fileUri': 'file:///workspace/src/example.ts',
        'insertedText': 'const answer = 42;',
        'prompt': {
            'fullMessages': [{'role': 'user', 'content': 'Add an answer constant.'}],
            'rawModelResponse': 'const answer = 42;'
        },
        'insertion': {
            'insertedChunks': [
                {
                    'text': 'const answer = 42;',
                    'start': {'line': 1, 'column': 1},
                    'end': {'line': 1, 'column': 20},
                    'addedLines': 1,
                    'removedLines': 0,
                }
            ]
        },
        'normalizedEvent': {
            'schemaVersion': 'lineagelens.provenance-event.v1',
            'eventId': '11111111-1111-4111-8111-111111111111',
            'capture': {'level': 'full', 'promptStatus': 'captured'},
            'source': {'shim': 'vscode-extension'},
            'file': {
                'path': 'src/example.ts',
                'uri': 'file:///workspace/src/example.ts',
                'languageId': 'typescript',
                'workspace': WORKSPACE_ID,
            },
            'diff': {
                'insertedText': 'const answer = 42;',
                'chunks': [],
                'netAddedLines': 1,
            },
        },
        'metadata': {
            'captureStatus': 'full',
            'agentContext': {'toolName': 'Cursor'},
        },
    }

    normalized = normalize_ingest_payload(payload, workspace_id=WORKSPACE_ID)

    assert str(normalized.record_uuid) == '11111111-1111-4111-8111-111111111111'
    assert normalized.workspace_id == WORKSPACE_ID
    assert normalized.capture_status == 'full'
    assert normalized.prompt_status == 'captured'
    assert normalized.warnings == []
    assert normalized.normalized_event['capture']['level'] == 'full'
    assert normalized.normalized_event['source']['shim'] == 'vscode-extension'
    assert normalized.provenance_payload['normalizedEvent']['capture']['level'] == 'full'
    assert normalized.provenance_payload['insertion']['insertedChunks'][0]['text'] == 'const answer = 42;'


def test_normalize_ingest_payload_infers_file_diff_for_lightweight_payload() -> None:
    payload = {
        'id': '22222222-2222-4222-8222-222222222222',
        'timestampIso': '2026-04-18T10:05:00.000Z',
        'filePath': 'src/lightweight.ts',
        'fileUri': 'file:///workspace/src/lightweight.ts',
        'insertedText': 'const token = process.env.API_KEY;',
        'netAddedLines': 1,
        'source': {
            'shim': 'lightweight-adapter',
        },
    }

    normalized = normalize_ingest_payload(payload, workspace_id='workspace-beta')

    assert str(normalized.record_uuid) == '22222222-2222-4222-8222-222222222222'
    assert normalized.capture_status == 'file_diff'
    assert normalized.prompt_status == 'not-captured'
    assert normalized.warnings == [
        'Captured file-diff-only provenance without prompt or response evidence.'
    ]
    assert normalized.normalized_event['capture']['level'] == 'file_diff'
    assert normalized.normalized_event['source']['shim'] == 'lightweight-adapter'
    assert normalized.provenance_payload['insertion']['insertedChunks'][0]['text'] == (
        'const token = process.env.API_KEY;'
    )
