import assert from 'node:assert/strict';
import test from 'node:test';
import {
  PROVENANCE_EVENT_SCHEMA_VERSION,
  buildProviderAgnosticProvenanceEvent
} from '../eventSchema';
import type { PromptCorrelationResult } from '../correlation';
import type { NormalizedAgentContext } from '../agentAdapters';

test('provider-agnostic event schema preserves full prompt and response capture', () => {
  const event = buildProviderAgnosticProvenanceEvent({
    eventId: '11111111-1111-4111-8111-111111111111',
    timestampIso: '2026-04-18T10:00:00.000Z',
    filePath: 'src/example.ts',
    fileUri: 'file:///workspace/src/example.ts',
    languageId: 'typescript',
    workspaceHint: 'Lineagelens',
    gitBranch: 'main',
    insertedText: 'export const answer = 42;',
    insertedChunks: [],
    netAddedLines: 1,
    surroundingContext: {
      before: 'const before = true;',
      after: 'const after = true;',
      tokenWindow: 50
    },
    contextSnapshot: null,
    correlation: capturedCorrelation('full'),
    agentContext: agentContext(),
    shimName: 'vscode-extension'
  });

  assert.equal(event.schemaVersion, PROVENANCE_EVENT_SCHEMA_VERSION);
  assert.equal(event.capture.level, 'full');
  assert.equal(event.capture.promptStatus, 'captured');
  assert.equal(event.source.toolName, 'Cursor');
  assert.equal(event.session.requestId, '22222222-2222-4222-8222-222222222222');
  assert.equal(event.prompt.body !== null, true);
  assert.equal(event.response.body, 'export const answer = 42;');
});

test('provider-agnostic event schema keeps diff-only value when capture is opaque', () => {
  const event = buildProviderAgnosticProvenanceEvent({
    eventId: '11111111-1111-4111-8111-111111111111',
    timestampIso: '2026-04-18T10:00:00.000Z',
    filePath: 'src/example.ts',
    fileUri: 'file:///workspace/src/example.ts',
    languageId: 'typescript',
    workspaceHint: null,
    gitBranch: null,
    insertedText: 'export const opaque = true;',
    insertedChunks: [],
    netAddedLines: 1,
    surroundingContext: {
      before: '',
      after: '',
      tokenWindow: 50
    },
    contextSnapshot: null,
    correlation: notCapturedCorrelation('tunnel_only'),
    agentContext: null,
    shimName: 'vscode-extension'
  });

  const fileDiffCapability = event.capture.capabilities.find((item) => item.name === 'file-diff');

  assert.equal(event.capture.level, 'tunnel_only');
  assert.equal(event.capture.promptStatus, 'not-captured');
  assert.equal(fileDiffCapability?.status, 'provided');
  assert.equal(event.diff.insertedText, 'export const opaque = true;');
});

test('provider-agnostic event schema treats metadata-only correlation as captured', () => {
  const event = buildProviderAgnosticProvenanceEvent({
    eventId: '11111111-1111-4111-8111-111111111111',
    timestampIso: '2026-04-18T10:00:00.000Z',
    filePath: 'src/example.ts',
    fileUri: 'file:///workspace/src/example.ts',
    languageId: 'typescript',
    workspaceHint: 'Lineagelens',
    gitBranch: 'main',
    insertedText: 'export const metadataOnly = true;',
    insertedChunks: [],
    netAddedLines: 1,
    surroundingContext: {
      before: '',
      after: '',
      tokenWindow: 50
    },
    contextSnapshot: null,
    correlation: capturedCorrelation('metadata_only'),
    agentContext: null,
    shimName: 'vscode-extension'
  });

  assert.equal(event.capture.level, 'metadata_only');
  assert.equal(event.capture.promptStatus, 'captured');
  assert.equal(event.session.requestId, '22222222-2222-4222-8222-222222222222');
});

function capturedCorrelation(
  captureStatus: 'full' | 'metadata_only' | 'tunnel_only'
): Extract<PromptCorrelationResult, { promptStatus: 'captured' }> {
  return {
    promptStatus: 'captured',
    captureStatus,
    requestUuid: '22222222-2222-4222-8222-222222222222',
    timingDifferenceMs: 100,
    correlationWindowMs: 15000,
    similarityThreshold: 0.7,
    correlationConfidence: 0.9,
    fileContextMatched: true,
    matchedFileContextTokens: ['example'],
    contentSimilarityApplied: false,
    ambiguityResolvedByContent: false,
    contentSimilarityScore: null,
    proxyResponseTimestampIso: '2026-04-18T10:00:00.000Z',
    proxyRequestTimestampIso: '2026-04-18T09:59:59.000Z',
    fullPromptMessages: [{ role: 'user', content: 'Add an answer constant.' }],
    modelName: 'gpt-4o-mini',
    parameters: { temperature: 0.2 },
    targetHost: 'api.openai.com',
    requestHeaders: {
      'user-agent': 'Cursor/1.0'
    },
    systemPrompt: 'You are Cursor.',
    rawModelResponse: 'export const answer = 42;',
    rawModelResponseBase64: Buffer.from('export const answer = 42;', 'utf8').toString('base64'),
    captureEvidence: null
  };
}

function notCapturedCorrelation(captureStatus: 'tunnel_only' | 'metadata_only' | 'unavailable'): Extract<PromptCorrelationResult, { promptStatus: 'not-captured' }> {
  return {
    promptStatus: 'not-captured',
    captureStatus,
    reason: captureStatus === 'tunnel_only' ? 'tunnel-only-capture' : 'no-proxy-response-within-window',
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

function agentContext(): NormalizedAgentContext {
  return {
    toolName: 'Cursor',
    provider: 'OpenAI',
    sessionId: 'session',
    conversationId: 'conversation',
    runId: 'run',
    modelName: 'gpt-4o-mini',
    userAgent: 'Cursor/1.0',
    workspaceHint: 'Lineagelens',
    operationType: 'edit',
    confidence: 0.94,
    evidence: [],
    adapterName: 'cursor',
    matchSource: 'adapter',
    sessionKind: 'agentic',
    host: 'api.openai.com',
    sessionSignature: 'Cursor|OpenAI|gpt-4o-mini|agentic|session',
    detectedAtIso: '2026-04-18T10:00:00.000Z'
  };
}
