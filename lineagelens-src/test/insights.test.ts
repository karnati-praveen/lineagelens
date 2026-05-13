import assert from 'node:assert/strict';
import test from 'node:test';
import { PROVENANCE_EVENT_SCHEMA_VERSION } from '../eventSchema';
import { assessProvenanceRisk, buildInsightsDashboard } from '../insights';
import type { ProvenanceRecord } from '../provenance';

function createRecord(overrides: Partial<ProvenanceRecord> = {}): ProvenanceRecord {
  return {
    schemaVersion: overrides.schemaVersion ?? PROVENANCE_EVENT_SCHEMA_VERSION,
    uuid: overrides.uuid ?? '11111111-1111-4111-8111-111111111111',
    requestUuid: overrides.requestUuid ?? null,
    timestampIso: overrides.timestampIso ?? '2026-04-18T10:00:00.000Z',
    insertionTimestampIso: overrides.insertionTimestampIso ?? '2026-04-18T10:00:00.000Z',
    promptStatus: overrides.promptStatus ?? 'captured',
    prompt: overrides.prompt ?? {
      fullMessages: [{ role: 'user', content: 'Add auth middleware' }],
      modelName: 'gpt-4o-mini',
      parameters: { temperature: 0.2 },
      rawModelResponse: 'middleware code',
      rawModelResponseBase64: null
    },
    insertion: overrides.insertion ?? {
      extractedInsertedCodeBlock: 'const token = process.env.API_KEY;\nexec(command);',
      insertedChunks: [],
      netAddedLines: 22,
      cursorPosition: { line: 1, column: 1 },
      surroundingContext: {
        before: 'import { exec } from "child_process";',
        after: 'export default middleware;',
        tokenWindow: 50
      }
    },
    file: overrides.file ?? {
      path: 'src/auth/middleware.ts',
      uri: 'file:///workspace/src/auth/middleware.ts',
      languageId: 'typescript'
    },
    repository: overrides.repository ?? {
      gitBranch: 'main'
    },
    contextSnapshot: overrides.contextSnapshot ?? null,
    normalizedEvent: overrides.normalizedEvent ?? {
      schemaVersion: PROVENANCE_EVENT_SCHEMA_VERSION,
      eventId: overrides.uuid ?? '11111111-1111-4111-8111-111111111111',
      timestamps: {
        observedAtIso: overrides.timestampIso ?? '2026-04-18T10:00:00.000Z',
        insertedAtIso: overrides.insertionTimestampIso ?? '2026-04-18T10:00:00.000Z',
        requestAtIso: '2026-04-18T09:59:59.000Z',
        responseAtIso: '2026-04-18T10:00:00.000Z'
      },
      source: {
        ide: 'vscode',
        shim: 'test',
        toolName: 'Cursor',
        provider: 'OpenAI',
        adapterName: 'cursor'
      },
      capture: {
        level: 'full',
        promptStatus: 'captured',
        capabilities: [],
        evidence: []
      },
      session: {
        sessionId: 'session',
        conversationId: null,
        runId: null,
        requestId: '22222222-2222-4222-8222-222222222222',
        signature: 'Cursor|OpenAI|gpt-4o-mini|agentic|session'
      },
      model: {
        name: 'gpt-4o-mini',
        parameters: { temperature: 0.2 }
      },
      prompt: {
        body: [{ role: 'user', content: 'Add auth middleware' }],
        system: 'You are Cursor.'
      },
      response: {
        body: 'middleware code',
        bodyBase64: null
      },
      file: {
        path: 'src/auth/middleware.ts',
        uri: 'file:///workspace/src/auth/middleware.ts',
        languageId: 'typescript',
        workspace: 'Lineagelens',
        gitBranch: 'main'
      },
      diff: {
        insertedText: 'const token = process.env.API_KEY;\nexec(command);',
        chunks: [],
        netAddedLines: 22
      },
      context: {
        snapshot: null,
        before: 'import { exec } from "child_process";',
        after: 'export default middleware;'
      },
      correlation: {
        confidence: 0.82,
        timingDifferenceMs: 250,
        windowMs: 15000,
        contentSimilarityScore: null,
        fileContextMatched: true
      },
      confidence: {
        agent: 0.9,
        correlation: 0.82
      },
      extensions: {}
    },
    rawData: overrides.rawData ?? {
      detectionPayload: {},
      proxyRequest: null,
      proxyResponse: null
    },
    embeddings: overrides.embeddings ?? {},
    astSnapshot: overrides.astSnapshot ?? {
      parserEngine: 'tree-sitter',
      normalizationVersion: 'node-type-sequence-v1',
      languageDetected: 'typescript',
      rootNodeType: 'program',
      normalizedNodeTypes: ['program', 'call_expression'],
      nodeCount: 2,
      parseSucceeded: true,
      parseError: null,
      createdAtIso: '2026-04-18T10:00:00.000Z'
    },
    correlation: overrides.correlation ?? {
      promptStatus: 'captured',
      captureStatus: 'full',
      requestUuid: '22222222-2222-4222-8222-222222222222',
      timingDifferenceMs: 250,
      correlationWindowMs: 15000,
      similarityThreshold: 0.7,
      correlationConfidence: 0.82,
      fileContextMatched: true,
      matchedFileContextTokens: ['middleware'],
      contentSimilarityApplied: false,
      ambiguityResolvedByContent: false,
      contentSimilarityScore: null,
      proxyResponseTimestampIso: '2026-04-18T10:00:00.000Z',
      proxyRequestTimestampIso: '2026-04-18T09:59:59.000Z',
      fullPromptMessages: [{ role: 'user', content: 'Add auth middleware' }],
      modelName: 'gpt-4o-mini',
      parameters: {
        client: 'cursor',
        task: 'implement auth middleware'
      },
      targetHost: 'api.openai.com',
      requestHeaders: {
        'user-agent': 'cursor-agent'
      },
      systemPrompt: 'You are Cursor.',
      rawModelResponse: 'middleware code',
      rawModelResponseBase64: '',
      captureEvidence: {
        requestId: '22222222-2222-4222-8222-222222222222',
        targetHost: 'api.openai.com',
        targetPort: 443,
        userAgent: 'cursor-agent',
        captureStatus: 'full',
        tunnelDurationMs: null,
        captureReason: null
      }
    },
    metadata: overrides.metadata ?? {
      similarityThreshold: 0.7,
      correlationWindowMs: 15000,
      timingDifferenceMs: 250,
      correlationConfidence: 0.82,
      featureVersion: 'test'
    }
  };
}

test('assessProvenanceRisk escalates risky generated code', () => {
  const risk = assessProvenanceRisk(createRecord());

  assert.equal(risk.level === 'high' || risk.level === 'critical', true);
  assert.equal(risk.reasons.length > 0, true);
});

test('buildInsightsDashboard groups detected agent sessions and summary counts', () => {
  const first = createRecord();
  const second = createRecord({
    uuid: '33333333-3333-4333-8333-333333333333',
    timestampIso: '2026-04-18T10:12:00.000Z',
    insertionTimestampIso: '2026-04-18T10:12:00.000Z',
    metadata: {
      similarityThreshold: 0.7,
      correlationWindowMs: 15000,
      timingDifferenceMs: 250,
      correlationConfidence: 0.9,
      featureVersion: 'test'
    }
  });

  const dashboard = buildInsightsDashboard(
    [first, second],
    'local',
    {
      dateFrom: '',
      dateTo: '',
      currentFileOnly: false
    }
  );

  assert.equal(dashboard.summary.totalRecords, 2);
  assert.equal(dashboard.agentSessions.length >= 1, true);
  assert.equal(dashboard.summary.uniqueAgentSessions >= 1, true);
});
