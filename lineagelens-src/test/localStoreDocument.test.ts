import assert from 'node:assert/strict';
import test from 'node:test';

import { PROVENANCE_EVENT_SCHEMA_VERSION } from '../eventSchema';
import { buildLightweightProvenanceRecord } from '../lightweightRecord';
import { createEmptyStore, sanitizeStoreDocument } from '../storage/localStoreDocument';
import type { PromptCorrelationResult } from '../correlation';
import type { ProvenanceRecord } from '../provenance';

test('local store sanitization preserves lightweight file-diff provenance records', () => {
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

  const store = createEmptyStore();
  store.records.push({
    uuid: event.eventId.toLowerCase(),
    record: toStoredRecord(event),
    searchText: '',
    storedAtIso: '2026-04-18T10:00:00.000Z',
    updatedAtIso: '2026-04-18T10:00:00.000Z',
    lineage: {
      parentUuid: null,
      relationshipType: 'INITIAL',
      similarity: null,
      commitHash: null,
      updatedAtIso: '2026-04-18T10:00:00.000Z'
    }
  });

  const sanitized = sanitizeStoreDocument(store);

  assert.equal(sanitized.schemaVersion, 1);
  assert.equal(sanitized.records.length, 1);
  assert.equal(sanitized.records[0].uuid, '11111111-1111-4111-8111-111111111111');
  assert.equal(sanitized.records[0].record.schemaVersion, PROVENANCE_EVENT_SCHEMA_VERSION);
  assert.equal(sanitized.records[0].record.normalizedEvent.diff.insertedText, 'const answer = 42;');
  assert.equal(sanitized.records[0].record.promptStatus, 'not-captured');
  assert.equal(sanitized.records[0].searchText.includes('src/example.ts'), true);
  assert.equal(sanitized.records[0].searchText.includes('const answer = 42;'), true);
});

function toStoredRecord(event: ReturnType<typeof buildLightweightProvenanceRecord>): ProvenanceRecord {
  return {
    schemaVersion: event.schemaVersion,
    uuid: event.eventId,
    requestUuid: event.session.requestId,
    timestampIso: event.timestamps.observedAtIso,
    insertionTimestampIso: event.timestamps.insertedAtIso,
    promptStatus: event.capture.promptStatus,
    prompt: {
      fullMessages: event.prompt.body,
      modelName: event.model.name,
      parameters: event.model.parameters,
      rawModelResponse: event.response.body,
      rawModelResponseBase64: event.response.bodyBase64
    },
    insertion: {
      extractedInsertedCodeBlock: event.diff.insertedText,
      insertedChunks: event.diff.chunks,
      netAddedLines: event.diff.netAddedLines,
      cursorPosition: {
        line: 0,
        column: 0
      },
      surroundingContext: {
        before: event.context.before,
        after: event.context.after,
        tokenWindow: 0
      }
    },
    file: {
      path: event.file.path,
      uri: event.file.uri,
      languageId: event.file.languageId
    },
    repository: {
      gitBranch: event.file.gitBranch
    },
    contextSnapshot: event.context.snapshot,
    normalizedEvent: event,
    rawData: {
      detectionPayload: {
        sourceShim: event.source.shim
      },
      proxyRequest: null,
      proxyResponse: null
    },
    embeddings: {},
    astSnapshot: {
      parserEngine: 'tree-sitter',
      normalizationVersion: 'node-type-sequence-v1',
      languageDetected: event.file.languageId,
      rootNodeType: null,
      normalizedNodeTypes: [],
      nodeCount: 0,
      parseSucceeded: true,
      parseError: null,
      createdAtIso: event.timestamps.observedAtIso
    },
    correlation: lightweightCorrelation(event),
    metadata: {
      similarityThreshold: 0.7,
      correlationWindowMs: 15000,
      timingDifferenceMs: 0,
      featureVersion: 'local-storage-test',
      captureStatus: event.capture.level
    }
  };
}

function lightweightCorrelation(
  event: ReturnType<typeof buildLightweightProvenanceRecord>
): PromptCorrelationResult {
  if (event.capture.promptStatus === 'captured') {
    const captureStatus:
      | Extract<PromptCorrelationResult, { promptStatus: 'captured' }>['captureStatus']
      =
      event.capture.level === 'full' ||
      event.capture.level === 'metadata_only' ||
      event.capture.level === 'tunnel_only'
        ? event.capture.level
        : 'metadata_only';

    return {
      promptStatus: 'captured',
      captureStatus,
      requestUuid: event.session.requestId ?? event.eventId,
      timingDifferenceMs: 0,
      correlationWindowMs: 15000,
      similarityThreshold: 0.7,
      correlationConfidence: 0.75,
      fileContextMatched: true,
      matchedFileContextTokens: [],
      contentSimilarityApplied: false,
      ambiguityResolvedByContent: false,
      contentSimilarityScore: null,
      proxyResponseTimestampIso: event.timestamps.responseAtIso ?? event.timestamps.observedAtIso,
      proxyRequestTimestampIso: event.timestamps.requestAtIso ?? event.timestamps.observedAtIso,
      fullPromptMessages: event.prompt.body,
      modelName: event.model.name,
      parameters: event.model.parameters ?? undefined,
      targetHost: null,
      requestHeaders: null,
      systemPrompt: event.prompt.system,
      rawModelResponse: event.response.body ?? '',
      rawModelResponseBase64:
        event.response.bodyBase64 ?? Buffer.from(event.response.body ?? '', 'utf8').toString('base64'),
      captureEvidence: null
    };
  }

  return {
    promptStatus: 'not-captured',
    captureStatus: event.capture.level,
    reason: 'local-proxy-unavailable',
    requestUuid: null,
    timingDifferenceMs: null,
    correlationWindowMs: 15000,
    similarityThreshold: 0.7,
    correlationConfidence: 0,
    fileContextMatched: false,
    matchedFileContextTokens: [],
    contentSimilarityApplied: false,
    ambiguityResolvedByContent: false,
    contentSimilarityScore: null,
    proxyResponseTimestampIso: null,
    proxyRequestTimestampIso: null,
    fullPromptMessages: null,
    modelName: null,
    parameters: null,
    targetHost: null,
    requestHeaders: null,
    systemPrompt: null,
    rawModelResponse: null,
    rawModelResponseBase64: null,
    captureEvidence: null
  };
}