import assert from 'node:assert/strict';
import test from 'node:test';

import { PROVENANCE_EVENT_SCHEMA_VERSION } from '../eventSchema';
import { buildLightweightProvenanceRecord } from '../lightweightRecord';

test('lightweight record helper builds a VS Code-free diff-only event', () => {
  const event = buildLightweightProvenanceRecord({
    eventId: '11111111-1111-4111-8111-111111111111',
    timestampIso: '2026-04-18T10:00:00.000Z',
    filePath: 'src/example.ts',
    fileUri: 'file:///workspace/src/example.ts',
    languageId: 'typescript',
    insertedText: 'const answer = 42;',
    captureStatus: 'unavailable',
    promptStatus: 'not-captured'
  });

  assert.equal(event.schemaVersion, PROVENANCE_EVENT_SCHEMA_VERSION);
  assert.equal(event.source.ide, null);
  assert.equal(event.source.shim, 'lightweight-adapter');
  assert.equal(event.capture.level, 'unavailable');
  assert.equal(event.capture.promptStatus, 'not-captured');
  assert.equal(event.diff.insertedText, 'const answer = 42;');
  assert.equal(event.extensions.operationType, 'unknown');
});

test('lightweight record helper synthesizes captured payloads', () => {
  const event = buildLightweightProvenanceRecord({
    eventId: '22222222-2222-4222-8222-222222222222',
    timestampIso: '2026-04-18T10:05:00.000Z',
    filePath: 'src/example.ts',
    fileUri: 'file:///workspace/src/example.ts',
    languageId: 'typescript',
    insertedText: 'const answer = 42;',
    captureStatus: 'metadata_only',
    promptStatus: 'captured',
    requestUuid: 'req-1',
    fullPromptMessages: [{ role: 'user', content: 'Add answer constant.' }],
    rawModelResponse: 'const answer = 42;',
    sourceIde: 'cli'
  });

  assert.equal(event.source.ide, 'cli');
  assert.equal(event.capture.level, 'metadata_only');
  assert.equal(event.capture.promptStatus, 'captured');
  assert.equal(event.session.requestId, 'req-1');
  assert.equal(event.prompt.body !== null, true);
  assert.equal(event.response.bodyBase64, Buffer.from('const answer = 42;', 'utf8').toString('base64'));
});
