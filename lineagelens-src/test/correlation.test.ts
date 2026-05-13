import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import {
  calculateLevenshteinSimilarity,
  correlateInsertionWithProxyRequest,
  resetCorrelationState
} from '../correlation';
import type { LocalLlmProxyRuntime, ProxyCapabilityReport, RequestResponsePair } from '../proxy';

beforeEach(() => {
  resetCorrelationState();
});

test('calculateLevenshteinSimilarity returns 1 for identical snippets', () => {
  const snippet = 'function add(a, b) {\n  return a + b;\n}';
  const score = calculateLevenshteinSimilarity(snippet, snippet);

  assert.equal(score, 1);
});

test('calculateLevenshteinSimilarity stays bounded for unrelated snippets', () => {
  const score = calculateLevenshteinSimilarity(
    'SELECT * FROM users WHERE id = 42;',
    'def fib(n):\n    return n if n < 2 else fib(n - 1) + fib(n - 2)'
  );

  assert.ok(score >= 0 && score <= 1);
  assert.ok(score < 0.5);
});

test('correlateInsertionWithProxyRequest reports proxy unavailable with configured window', async () => {
  const result = await correlateInsertionWithProxyRequest({
    insertionTimestampIso: new Date().toISOString(),
    filePath: 'src/example.ts',
    insertedCode: 'console.log("hello");',
    localProxy: undefined,
    correlationWindowMs: 15_000,
    similarityThreshold: 0.8
  });

  assert.equal(result.promptStatus, 'not-captured');
  assert.equal(result.reason, 'local-proxy-unavailable');
  assert.equal(result.captureStatus, 'unavailable');
  assert.equal(result.correlationWindowMs, 15_000);
  assert.equal(result.similarityThreshold, 0.8);
});

test('correlateInsertionWithProxyRequest redacts request headers', async () => {
  const requestId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const pair = createPair({
    requestId,
    requestHeaders: {
      authorization: 'Bearer secret-token',
      cookie: 'session=secret',
      'user-agent': 'Cursor/1.0',
      'content-type': 'application/json'
    },
    responseBody: 'const answer = 42;'
  });

  const result = await correlateInsertionWithProxyRequest({
    insertionTimestampIso: '2026-04-18T10:00:01.000Z',
    filePath: 'src/example.ts',
    insertedCode: 'const answer = 42;',
    localProxy: createProxyRuntime([pair]),
    correlationWindowMs: 15_000,
    similarityThreshold: 0.8
  });

  assert.equal(result.promptStatus, 'captured');
  assert.equal(result.requestUuid, requestId);
  assert.equal(result.requestHeaders?.authorization, undefined);
  assert.equal(result.requestHeaders?.cookie, undefined);
  assert.equal(result.requestHeaders?.['user-agent'], 'Cursor/1.0');
});

test('correlateInsertionWithProxyRequest does not match author inside auth context tokens', async () => {
  const result = await correlateInsertionWithProxyRequest({
    insertionTimestampIso: '2026-04-18T10:00:01.000Z',
    filePath: 'src/auth.ts',
    insertedCode: 'const value = 1;',
    localProxy: createProxyRuntime([
      createPair({
        requestId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
        requestMessages: [{ role: 'user', content: 'Review the author metadata here.' }],
        requestParameters: { note: 'author' },
        responseBody: 'const value = 1;'
      })
    ]),
    correlationWindowMs: 15_000,
    similarityThreshold: 0.8
  });

  assert.equal(result.promptStatus, 'captured');
  assert.equal(result.fileContextMatched, false);
});

test('correlateInsertionWithProxyRequest resolves ambiguous timing using similarity', async () => {
  const insertedCode = 'const answer = 42;';
  const matchingRequestId = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';

  const result = await correlateInsertionWithProxyRequest({
    insertionTimestampIso: '2026-04-18T10:00:02.000Z',
    filePath: 'src/example.ts',
    insertedCode,
    localProxy: createProxyRuntime([
      createPair({
        requestId: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
        requestTimestampIso: '2026-04-18T10:00:00.700Z',
        responseTimestampIso: '2026-04-18T10:00:00.700Z',
        requestMessages: [{ role: 'user', content: 'Add the answer constant.' }],
        requestParameters: { task: 'answer' },
        responseBody: 'console.log("nope");'
      }),
      createPair({
        requestId: matchingRequestId,
        requestTimestampIso: '2026-04-18T10:00:01.000Z',
        responseTimestampIso: '2026-04-18T10:00:01.000Z',
        requestMessages: [{ role: 'user', content: 'Add the answer constant.' }],
        requestParameters: { task: 'answer' },
        responseBody: insertedCode
      })
    ]),
    correlationWindowMs: 15_000,
    similarityThreshold: 0.8
  });

  assert.equal(result.promptStatus, 'captured');
  assert.equal(result.requestUuid, matchingRequestId);
  assert.equal(result.contentSimilarityApplied, true);
  assert.equal(result.ambiguityResolvedByContent, true);
  assert.ok((result.correlationConfidence ?? 0) > 0.5);
});

test('correlateInsertionWithProxyRequest resets claims for proxy restarts', async () => {
  const pair = createPair({
    requestId: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
    responseBody: 'const answer = 42;'
  });
  const proxy = createProxyRuntime([pair]);

  const first = await correlateInsertionWithProxyRequest({
    insertionTimestampIso: '2026-04-18T10:00:01.000Z',
    filePath: 'src/example.ts',
    insertedCode: 'const answer = 42;',
    localProxy: proxy,
    correlationWindowMs: 15_000,
    similarityThreshold: 0.8
  });

  const second = await correlateInsertionWithProxyRequest({
    insertionTimestampIso: '2026-04-18T10:00:01.000Z',
    filePath: 'src/example.ts',
    insertedCode: 'const answer = 42;',
    localProxy: proxy,
    correlationWindowMs: 15_000,
    similarityThreshold: 0.8
  });

  resetCorrelationState();

  const third = await correlateInsertionWithProxyRequest({
    insertionTimestampIso: '2026-04-18T10:00:01.000Z',
    filePath: 'src/example.ts',
    insertedCode: 'const answer = 42;',
    localProxy: proxy,
    correlationWindowMs: 15_000,
    similarityThreshold: 0.8
  });

  assert.equal(first.promptStatus, 'captured');
  assert.equal(second.promptStatus, 'not-captured');
  assert.equal(second.reason, 'prompt-already-associated');
  assert.equal(third.promptStatus, 'captured');
});

test('metadata-only capture reduces correlation confidence', async () => {
  const fullResult = await correlateInsertionWithProxyRequest({
    insertionTimestampIso: '2026-04-18T10:00:01.000Z',
    filePath: 'src/example.ts',
    insertedCode: 'const answer = 42;',
    localProxy: createProxyRuntime([
      createPair({
        requestId: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
        captureStatus: 'full',
        responseBody: 'const answer = 42;'
      })
    ]),
    correlationWindowMs: 15_000,
    similarityThreshold: 0.8
  });

  const metadataOnlyResult = await correlateInsertionWithProxyRequest({
    insertionTimestampIso: '2026-04-18T10:00:01.000Z',
    filePath: 'src/example.ts',
    insertedCode: 'const answer = 42;',
    localProxy: createProxyRuntime([
      createPair({
        requestId: '99999999-9999-4999-8999-999999999999',
        captureStatus: 'metadata_only',
        responseBody: 'const answer = 42;'
      })
    ]),
    correlationWindowMs: 15_000,
    similarityThreshold: 0.8
  });

  assert.equal(fullResult.promptStatus, 'captured');
  assert.equal(metadataOnlyResult.promptStatus, 'captured');
  assert.ok((metadataOnlyResult.correlationConfidence ?? 0) < (fullResult.correlationConfidence ?? 0));
  assert.equal(metadataOnlyResult.captureStatus, 'metadata_only');
});

test('tunnel-only captures use a real timestamp instead of epoch fallback', async () => {
  const responseTimestampIso = '2026-04-18T10:00:01.000Z';
  const result = await correlateInsertionWithProxyRequest({
    insertionTimestampIso: '2026-04-18T10:00:01.500Z',
    filePath: 'src/example.ts',
    insertedCode: 'const answer = 42;',
    localProxy: createProxyRuntime([
      createPair({
        requestId: '12121212-1212-4121-8121-121212121212',
        captureStatus: 'tunnel_only',
        tunnelEndedAtIso: responseTimestampIso,
        requestMessages: [{ role: 'user', content: 'Tunnel only request.' }],
        requestParameters: { mode: 'tunnel' }
      })
    ]),
    correlationWindowMs: 15_000,
    similarityThreshold: 0.8
  });

  assert.equal(result.promptStatus, 'captured');
  assert.equal(result.proxyResponseTimestampIso, responseTimestampIso);
});

function createProxyRuntime(pairs: RequestResponsePair[]): LocalLlmProxyRuntime {
  return {
    port: 8080,
    getRecentPairs: () => pairs,
    getCapabilityReport: () => createCapabilityReport(),
    stop: async () => undefined
  };
}

function createCapabilityReport(): ProxyCapabilityReport {
  return {
    port: 8080,
    retentionMs: 300_000,
    allowlist: ['api.openai.com'],
    observedRequests: 1,
    capturedRequests: 1,
    fullCaptures: 1,
    metadataOnlyCaptures: 0,
    tunnelOnlyCaptures: 0,
    unavailableCaptures: 0,
    recentHosts: [],
    notes: []
  };
}

function createPair(overrides: {
  requestId?: string;
  requestTimestampIso?: string;
  responseTimestampIso?: string;
  captureStatus?: 'full' | 'metadata_only' | 'tunnel_only';
  requestHeaders?: Record<string, string>;
  requestMessages?: unknown;
  requestParameters?: Record<string, unknown>;
  responseBody?: string;
  targetHost?: string;
  filePath?: string;
  tunnelEndedAtIso?: string;
} = {}): RequestResponsePair {
  const requestId = overrides.requestId ?? '11111111-1111-4111-8111-111111111111';
  const captureStatus = overrides.captureStatus ?? 'full';
  const requestTimestampIso = overrides.requestTimestampIso ?? '2026-04-18T10:00:00.000Z';
  const responseTimestampIso = overrides.responseTimestampIso ?? '2026-04-18T10:00:00.500Z';
  const targetHost = overrides.targetHost ?? 'api.openai.com';
  const requestHeaders = overrides.requestHeaders ?? {
    'user-agent': 'Cursor/1.0',
    'content-type': 'application/json',
    authorization: 'Bearer secret-token'
  };
  const requestMessages = overrides.requestMessages ?? [
    { role: 'user', content: 'Add the answer constant.' }
  ];
  const requestParameters = overrides.requestParameters ?? { temperature: 0.2 };
  const responseBody = overrides.responseBody ?? 'const answer = 42;';
  const requestBodyText = JSON.stringify({ messages: requestMessages });
  const requestBodyBase64 = Buffer.from(requestBodyText, 'utf8').toString('base64');
  const requestBody =
    captureStatus === 'full'
      ? {
          rawBodyUtf8: requestBodyText,
          rawBodyBase64: requestBodyBase64,
          payload: { messages: requestMessages },
          messages: requestMessages,
          model: 'gpt-4o-mini',
          temperature: 0.2,
          systemPrompt: 'You are a coding assistant.',
          parameters: requestParameters
        }
      : null;
  const tunnelEndedAtIso = overrides.tunnelEndedAtIso ?? '2026-04-18T10:00:00.800Z';
  const requestMetadata = {
    method: captureStatus === 'tunnel_only' ? 'CONNECT' : 'POST',
    targetUrl:
      captureStatus === 'tunnel_only'
        ? targetHost + ':443'
        : 'https://' + targetHost + '/v1/chat/completions',
    targetHost,
    targetPort: 443,
    path: captureStatus === 'tunnel_only' ? '/' : '/v1/chat/completions',
    headers: requestHeaders,
    userAgent: requestHeaders['user-agent'] ?? null,
    captureStatus,
    captureReason:
      captureStatus === 'full'
        ? null
        : captureStatus === 'metadata_only'
          ? 'Allowlisted request captured with metadata only because the body was not captured.'
          : 'HTTPS payload is encrypted inside the tunnel.'
  };

  return {
    id: requestId,
    createdAtMs: Date.parse(requestTimestampIso),
    updatedAtMs: Date.parse(responseTimestampIso),
    request: {
      id: requestId,
      timestampIso: requestTimestampIso,
      method: captureStatus === 'tunnel_only' ? 'CONNECT' : 'POST',
      targetUrl:
        captureStatus === 'tunnel_only'
          ? targetHost + ':443'
          : 'https://' + targetHost + '/v1/chat/completions',
      targetHost,
      targetPort: 443,
      headers: requestHeaders,
      captureStatus,
      captureReason:
        captureStatus === 'full'
          ? null
          : captureStatus === 'metadata_only'
            ? 'Allowlisted request captured with metadata only because the body was not captured.'
            : 'HTTPS payload is encrypted inside the tunnel.',
      requestMetadata,
      requestBody,
      tunnelMetadata:
        captureStatus === 'tunnel_only'
          ? {
              targetHost,
              targetPort: 443,
              clientAddress: '127.0.0.1',
              serverAddress: '127.0.0.1',
              startedAtIso: requestTimestampIso,
              endedAtIso: tunnelEndedAtIso,
              durationMs: Date.parse(tunnelEndedAtIso) - Date.parse(requestTimestampIso),
              bytesUpstream: 0,
              bytesDownstream: 0,
              connectionCount: 1
            }
          : null,
      rawBodyUtf8: requestBody?.rawBodyUtf8 ?? '',
      rawBodyBase64: requestBody?.rawBodyBase64 ?? '',
      payload: requestBody?.payload,
      messages: requestBody?.messages,
      model: requestBody?.model,
      temperature: requestBody?.temperature,
      systemPrompt: requestBody?.systemPrompt,
      parameters: requestBody?.parameters
    },
    response:
      captureStatus === 'tunnel_only'
        ? undefined
        : {
            timestampIso: responseTimestampIso,
            statusCode: 200,
            headers: {
              'content-type': 'application/json'
            },
            rawBodyUtf8: responseBody,
            rawBodyBase64: Buffer.from(responseBody, 'utf8').toString('base64')
          }
  };
}
