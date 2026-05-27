from app.services.ingest_normalizer import normalize_ingest_payload


WORKSPACE_ID = 'workspace-alpha'
_PROXY_WORKSPACE_ID = 'workspace-proxy'


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


# ── Round-trip: full-proxy payload → confidence engine fires ─────────────────

def test_full_proxy_payload_produces_high_confidence() -> None:
    """Full proxy capture with UUID match, Δt=1 s, identical text, known tool.

    Old code returned a hardcoded 0.5 for any tool-bearing payload.
    The evidence-weighted engine should score > 0.7 for this best-case input.
    """
    payload = {
        'id': '33333333-3333-4333-8333-333333333333',
        # insertion timestamp — 1 second after the request
        'timestampIso': '2026-01-01T10:00:01.000Z',
        # request UUID and the "proxy + editor agreed" flag
        'requestUuid': '44444444-4444-4444-8444-444444444444',
        'requestUuidMatchesCapture': True,
        # prompt timestamp — picked up via timestamps.requestAtIso
        'timestamps': {
            'requestAtIso': '2026-01-01T10:00:00.000Z',
        },
        'workspaceId': _PROXY_WORKSPACE_ID,
        'filePath': 'src/proxy_gen.py',
        'insertedText': 'def hello(): pass',
        'prompt': {
            'rawModelResponse': 'def hello(): pass',
        },
        'metadata': {
            'captureStatus': 'full',
        },
        'source': {
            'toolName': 'claude-code',
            'provider': 'anthropic',
            'shim': 'lineagelens-proxy',
        },
        'capture': {
            'level': 'full',
        },
    }

    normalized = normalize_ingest_payload(payload, workspace_id=_PROXY_WORKSPACE_ID)

    # Basic shape checks.
    assert str(normalized.record_uuid) == '33333333-3333-4333-8333-333333333333'
    assert normalized.capture_status == 'full'
    assert normalized.prompt_status == 'captured'

    # Confidence breakdown is always present when the engine fires.
    assert normalized.confidence_breakdown is not None
    assert len(normalized.confidence_breakdown) == 5, (
        f"expected 5 evidence items, got {len(normalized.confidence_breakdown)}"
    )

    # agent_context is populated and the confidence value beats the old 0.5 ceiling.
    assert isinstance(normalized.agent_context, dict)
    conf_value = normalized.agent_context['confidence']
    assert conf_value is not None, "confidence should not be None"
    assert conf_value > 0.7, (
        f"expected confidence > 0.7 (old code gave 0.5), got {conf_value}"
    )

    # The full result dict is also embedded in normalizedEvent.
    conf_obj = normalized.normalized_event.get('confidence', {})
    assert conf_obj.get('method') == 'weighted_evidence_v1'
    assert conf_obj.get('level') in ('very_high', 'high'), (
        f"expected very_high or high, got {conf_obj.get('level')!r}"
    )
