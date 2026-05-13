import assert from 'node:assert/strict';
import test from 'node:test';
import {
  AgentAdapterInput,
  AgentAdapterRegistry,
  createAiderAdapter,
  createAmazonQAdapter,
  createClaudeCodeAdapter,
  createCodeiumAdapter,
  createCodexCliAdapter,
  createCodyAdapter,
  createContinueAdapter,
  createCopilotAdapter,
  createCursorAdapter,
  createDefaultAgentAdapterRegistry,
  createGeminiCliAdapter,
  createLegacyHeuristicAdapter
} from '../agentAdapters';
import { assertContextInvariants } from '../agentAdapters/invariants';
import type { NormalizedAgentContext } from '../agentAdapters/types';
import type { PromptCorrelationResult } from '../correlation';

// ---------------------------------------------------------------------------
// Fixture helpers (mirrors agentAdapters.test.ts for consistency)
// ---------------------------------------------------------------------------

type FixtureOverrides = {
  timestampIso: string;
  filePath: string;
  fileLanguageId: string;
  workspaceHint: string;
  insertedText: string;
  requestUuid: string;
  targetHost: string;
  userAgent: string;
  systemPrompt: string;
  promptMessages: unknown;
  modelName: string;
  parameters: Record<string, unknown>;
  requestHeaders: Record<string, string | string[]>;
  rawModelResponse: string;
};

function buildInput(overrides: Partial<FixtureOverrides> = {}): AgentAdapterInput {
  return {
    timestampIso: overrides.timestampIso ?? '2026-04-18T10:00:00.000Z',
    filePath: overrides.filePath ?? 'src/example.ts',
    fileLanguageId: overrides.fileLanguageId ?? 'typescript',
    workspaceHint: overrides.workspaceHint ?? 'Lineagelens',
    insertedText: overrides.insertedText ?? 'const result = computeValue();',
    correlation: buildCapturedCorrelation(overrides)
  };
}

function buildCapturedCorrelation(
  overrides: Partial<FixtureOverrides> = {}
): Extract<PromptCorrelationResult, { promptStatus: 'captured' }> {
  return {
    promptStatus: 'captured',
    captureStatus: 'full',
    requestUuid: overrides.requestUuid ?? '11111111-1111-4111-8111-111111111111',
    timingDifferenceMs: 150,
    correlationWindowMs: 15000,
    similarityThreshold: 0.7,
    correlationConfidence: 0.86,
    fileContextMatched: true,
    matchedFileContextTokens: ['example'],
    contentSimilarityApplied: false,
    ambiguityResolvedByContent: false,
    contentSimilarityScore: null,
    proxyResponseTimestampIso: '2026-04-18T10:00:01.000Z',
    proxyRequestTimestampIso: '2026-04-18T09:59:59.000Z',
    fullPromptMessages:
      overrides.promptMessages ?? [
        { role: 'system', content: 'You are a coding assistant.' },
        { role: 'user', content: 'Edit this function.' }
      ],
    modelName: overrides.modelName ?? 'gpt-4o-mini',
    parameters: { temperature: 0.2, ...(overrides.parameters ?? {}) },
    targetHost: overrides.targetHost ?? 'api.openai.com',
    requestHeaders: {
      'user-agent': overrides.userAgent ?? 'test-agent/1.0',
      'x-request-id': overrides.requestUuid ?? '11111111-1111-4111-8111-111111111111',
      ...(overrides.requestHeaders ?? {})
    },
    systemPrompt: overrides.systemPrompt ?? 'You are a coding assistant.',
    rawModelResponse: overrides.rawModelResponse ?? 'Generated response',
    rawModelResponseBase64: Buffer.from(
      overrides.rawModelResponse ?? 'Generated response',
      'utf8'
    ).toString('base64'),
    captureEvidence: null
  };
}

// ---------------------------------------------------------------------------
// Per-adapter golden path tests
// ---------------------------------------------------------------------------

test('golden path: cursor adapter — all signals fire, confidence in expected range', () => {
  const result = createCursorAdapter().detect(
    buildInput({
      targetHost: 'api.cursor.sh',
      userAgent: 'Cursor/1.0.0',
      systemPrompt: 'You are Cursor, an AI coding assistant.',
      promptMessages: [
        { role: 'system', content: 'You are Cursor.' },
        { role: 'user', content: 'Refactor this module.' }
      ],
      modelName: 'gpt-4o'
    })
  );

  assert.ok(result, 'cursor adapter must detect');
  assert.equal(result.adapterName, 'cursor');
  assert.equal(result.toolName, 'Cursor');
  assert.equal(result.matchSource, 'adapter');
  assert.equal(result.sessionKind, 'agentic');
  // userAgent(0.26)+host(0.18)+payload(0.22 for "cursor")+model(0.08) = 0.74 → 0.5+0.74*0.6 ≈ 0.944
  assert.ok(result.confidence >= 0.8, `confidence ${result.confidence} should be >= 0.8`);
  assert.ok(result.confidence <= 1.0, `confidence ${result.confidence} should be <= 1.0`);
  assert.ok(result.evidence.length >= 3, 'at least 3 evidence entries expected');
});

test('golden path: cursor adapter — detects "composer" payload without ua match', () => {
  const result = createCursorAdapter().detect(
    buildInput({
      targetHost: 'api.openai.com',
      userAgent: 'test-agent/1.0',
      systemPrompt: 'You are a composer assistant for cursor IDE.',
      promptMessages: [{ role: 'user', content: 'Use the cursor composer to refactor.' }],
      modelName: 'gpt-4o-mini'
    })
  );

  assert.ok(result, 'cursor adapter must detect via payload "composer"');
  assert.equal(result.adapterName, 'cursor');
  // Only payload(0.22)+model(0.08) = 0.30 → 0.5+0.30*0.6 = 0.68
  assert.ok(result.confidence >= 0.5, 'confidence must be ≥ 0.5');
  assert.ok(result.confidence <= 0.75, 'confidence must be ≤ 0.75 for weak signals only');
});

test('golden path: claude code adapter — full signal set yields max confidence', () => {
  const result = createClaudeCodeAdapter().detect(
    buildInput({
      targetHost: 'api.anthropic.com',
      userAgent: 'Claude-Code/1.5.0',
      systemPrompt: 'You are Claude Code. Fix the failing tests.',
      promptMessages: [
        { role: 'system', content: 'You are Claude Code.' },
        { role: 'user', content: 'Fix failing tests.' }
      ],
      modelName: 'claude-3-5-sonnet-20241022',
      requestHeaders: {
        'anthropic-version': '2023-06-01',
        'x-app': 'cli'
      }
    })
  );

  assert.ok(result, 'claude-code adapter must detect');
  assert.equal(result.adapterName, 'claude-code');
  assert.equal(result.toolName, 'Claude Code');
  assert.equal(result.matchSource, 'adapter');
  assert.equal(result.sessionKind, 'cli');
  // ua(0.30)+host(0.18)+anthropic-version(0.12)+x-app(0.15)+system(0.18)+msgs(0.12) = 1.05 → clamped to 1.0
  assert.ok(result.confidence >= 0.9, `confidence ${result.confidence} should be >= 0.9`);
  assert.ok(result.confidence <= 1.0, 'confidence must not exceed 1.0');
  assert.equal(result.operationType, 'test-fix');
});

test('golden path: claude code adapter — agentic mode when user-agent does not match CLI pattern', () => {
  // Use an SDK-style user-agent that doesn't match /claude[\s-]?code\/[\d.]+/i
  // (no version suffix) so isCliMode stays false and sessionKind becomes 'agentic'.
  const result = createClaudeCodeAdapter().detect(
    buildInput({
      targetHost: 'api.anthropic.com',
      userAgent: 'anthropic-sdk/1.0',
      systemPrompt: 'You are Claude Code.',
      promptMessages: [{ role: 'user', content: 'Refactor this module.' }],
      modelName: 'claude-3-5-sonnet-20241022',
      requestHeaders: {
        'anthropic-version': '2023-06-01'
      }
    })
  );

  assert.ok(result);
  assert.equal(result.adapterName, 'claude-code');
  assert.equal(result.sessionKind, 'agentic');
});

test('golden path: copilot adapter — full signal set yields max confidence', () => {
  const result = createCopilotAdapter().detect(
    buildInput({
      targetHost: 'api.githubcopilot.com',
      userAgent: 'GitHubCopilot/1.155.0',
      promptMessages: [{ role: 'user', content: 'Use GitHub Copilot to add a helper.' }],
      modelName: 'gpt-4o',
      requestHeaders: {
        'x-github-token': 'ghp_test_token',
        'editor-version': 'vscode/1.85.0'
      }
    })
  );

  assert.ok(result, 'copilot adapter must detect');
  assert.equal(result.adapterName, 'copilot');
  assert.equal(result.toolName, 'GitHub Copilot');
  assert.equal(result.matchSource, 'adapter');
  assert.equal(result.sessionKind, 'assistant');
  // ua(0.28)+host(0.22)+token(0.20)+editor-ver(0.10)+payload(0.12) = 0.92 → clamped 1.0
  assert.ok(result.confidence >= 0.9, `confidence ${result.confidence} should be >= 0.9`);
  assert.ok(result.confidence <= 1.0);
});

test('golden path: aider adapter — user-agent + payload + model + host', () => {
  const result = createAiderAdapter().detect(
    buildInput({
      targetHost: 'api.openai.com',
      userAgent: 'aider/0.50.0',
      systemPrompt: 'You are an aider pair-programming assistant.',
      promptMessages: [{ role: 'user', content: 'Refactor this module across multiple files.' }],
      modelName: 'gpt-4o',
      insertedText: 'const refactored = true;\nconst b = 2;\nconst c = 3;\nconst d = 4;'
    })
  );

  assert.ok(result, 'aider adapter must detect');
  assert.equal(result.adapterName, 'aider');
  assert.equal(result.toolName, 'Aider');
  assert.equal(result.matchSource, 'adapter');
  assert.equal(result.sessionKind, 'agentic');
  // ua(0.28)+payload(0.20)+model(0.10)+host(0.10) = 0.68 → 0.5+0.68*0.6 ≈ 0.908
  assert.ok(result.confidence >= 0.7, `confidence ${result.confidence} should be >= 0.7`);
  assert.ok(result.confidence <= 1.0);
  assert.equal(result.operationType, 'multi-file-run');
});

test('golden path: aider adapter — pair programming payload detection', () => {
  const result = createAiderAdapter().detect(
    buildInput({
      targetHost: 'api.openai.com',
      userAgent: 'python-requests/2.31',
      systemPrompt: 'You are a pair programming assistant for aider sessions.',
      promptMessages: [{ role: 'user', content: 'Fix the pair programming code.' }],
      modelName: 'gpt-4o-mini'
    })
  );

  assert.ok(result, 'aider must detect "pair programming" in payload');
  assert.equal(result.adapterName, 'aider');
});

test('golden path: codeium adapter — api-key header + host + user-agent', () => {
  const result = createCodeiumAdapter().detect(
    buildInput({
      targetHost: 'codeium.com',
      userAgent: 'Codeium/1.8.0',
      promptMessages: [{ role: 'user', content: 'Add a helper function.' }],
      modelName: 'codeium-model',
      requestHeaders: {
        'x-codeium-api-key': 'test-api-key-123'
      }
    })
  );

  assert.ok(result, 'codeium adapter must detect');
  assert.equal(result.adapterName, 'codeium');
  assert.equal(result.toolName, 'Codeium');
  assert.equal(result.matchSource, 'adapter');
  // ua(0.27)+host(0.22)+api-key(0.20)+payload(0) = 0.69 → 0.5+0.69*0.6 ≈ 0.914
  assert.ok(result.confidence >= 0.85, `confidence ${result.confidence} should be >= 0.85`);
  assert.ok(result.confidence <= 1.0);
});

test('golden path: codeium adapter — windsurf variant is detected as Windsurf', () => {
  const result = createCodeiumAdapter().detect(
    buildInput({
      targetHost: 'windsurf.codeium.com',
      userAgent: 'Windsurf/1.0',
      promptMessages: [{ role: 'user', content: 'Use windsurf to refactor.' }],
      modelName: 'windsurf-model'
    })
  );

  assert.ok(result, 'codeium adapter must detect windsurf variant');
  assert.equal(result.adapterName, 'codeium');
  assert.equal(result.toolName, 'Windsurf');
});

test('golden path: continue adapter — version header + unique-id + user-agent', () => {
  const result = createContinueAdapter().detect(
    buildInput({
      targetHost: 'api.openai.com',
      userAgent: 'Continue/0.9.220',
      promptMessages: [{ role: 'user', content: 'continuedev: refactor this function.' }],
      modelName: 'gpt-4o-mini',
      requestHeaders: {
        'x-continue-version': '0.9.220',
        'x-continue-unique-id': 'continue-uid-abc123'
      }
    })
  );

  assert.ok(result, 'continue adapter must detect');
  assert.equal(result.adapterName, 'continue');
  assert.equal(result.toolName, 'Continue');
  assert.equal(result.matchSource, 'adapter');
  assert.equal(result.sessionKind, 'agentic');
  // ua(0.26)+version(0.22)+unique-id(0.18)+payload(0.14) = 0.80 → 0.5+0.80*0.6 = 0.98
  assert.ok(result.confidence >= 0.85, `confidence ${result.confidence} should be >= 0.85`);
  assert.ok(result.confidence <= 1.0);
});

test('golden path: cody adapter — sourcegraph host + token + user-agent', () => {
  const result = createCodyAdapter().detect(
    buildInput({
      targetHost: 'cody-gateway.sourcegraph.com',
      userAgent: 'Cody/1.20.0',
      systemPrompt: 'You are Sourcegraph Cody.',
      promptMessages: [{ role: 'user', content: 'Use cody to explain this code.' }],
      modelName: 'claude-3-5-sonnet',
      requestHeaders: {
        'x-sourcegraph-client': 'cody-vscode',
        'x-sourcegraph-token': 'sg_test_token'
      }
    })
  );

  assert.ok(result, 'cody adapter must detect');
  assert.equal(result.adapterName, 'cody');
  assert.equal(result.toolName, 'Sourcegraph Cody');
  assert.equal(result.matchSource, 'adapter');
  // ua(0.27)+host(0.22)+sg-client(0.20)+payload(0.12) = 0.81 → 0.5+0.81*0.6 ≈ 0.986
  assert.ok(result.confidence >= 0.85, `confidence ${result.confidence} should be >= 0.85`);
  assert.ok(result.confidence <= 1.0);
  assert.equal(result.operationType, 'explain');
});

test('golden path: amazon-q adapter — host + amz-request-id + user-agent + target', () => {
  const result = createAmazonQAdapter().detect(
    buildInput({
      targetHost: 'q.us-east-1.amazonaws.com',
      userAgent: 'AmazonQ/1.0',
      promptMessages: [{ role: 'user', content: 'Use amazon-q to add a helper.' }],
      modelName: 'amazon-q-dev',
      requestHeaders: {
        'x-amz-request-id': 'amz-req-12345',
        'x-amz-target': 'CodeWhispererService.amazonq'
      }
    })
  );

  assert.ok(result, 'amazon-q adapter must detect');
  assert.equal(result.adapterName, 'amazon-q');
  assert.equal(result.toolName, 'Amazon Q Developer');
  assert.equal(result.matchSource, 'adapter');
  assert.equal(result.sessionKind, 'assistant');
  assert.equal(result.provider, 'AWS');
  // ua(0.27)+host(0.22)+amz-request-id(0.18)+amz-target(0.15)+payload(0.10) = 0.92 → clamped 1.0
  assert.ok(result.confidence >= 0.9, `confidence ${result.confidence} should be >= 0.9`);
  assert.ok(result.confidence <= 1.0);
});

test('golden path: gemini-cli adapter — google host + goog-api-client + user-agent', () => {
  const result = createGeminiCliAdapter().detect(
    buildInput({
      targetHost: 'generativelanguage.googleapis.com',
      userAgent: 'gemini-cli/1.0',
      promptMessages: [{ role: 'user', content: 'Use gemini generativelanguage to help.' }],
      modelName: 'gemini-1.5-pro',
      requestHeaders: {
        'x-goog-api-client': 'gemini-node/1.0',
        'x-goog-request-reason': 'code-generation'
      }
    })
  );

  assert.ok(result, 'gemini-cli adapter must detect');
  assert.equal(result.adapterName, 'gemini-cli');
  assert.equal(result.toolName, 'Gemini CLI');
  assert.equal(result.matchSource, 'adapter');
  assert.equal(result.sessionKind, 'cli');
  assert.equal(result.provider, 'Google');
  // ua(0.26)+host(0.25)+goog-api-client(0.20)+request-reason(0.10)+payload(0.10) = 0.91 → clamped 1.0
  assert.ok(result.confidence >= 0.9, `confidence ${result.confidence} should be >= 0.9`);
  assert.ok(result.confidence <= 1.0);
});

test('golden path: codex-cli adapter — user-agent + openai host + o-series model', () => {
  const result = createCodexCliAdapter().detect(
    buildInput({
      targetHost: 'api.openai.com',
      userAgent: 'openai-codex/0.1.2511',
      promptMessages: [{ role: 'user', content: 'Edit this file using codex-cli.' }],
      modelName: 'o4-mini',
      requestHeaders: {
        'openai-organization': 'org-test123'
      }
    })
  );

  assert.ok(result, 'codex-cli adapter must detect');
  assert.equal(result.adapterName, 'codex-cli');
  assert.equal(result.toolName, 'OpenAI Codex CLI');
  assert.equal(result.matchSource, 'adapter');
  assert.equal(result.sessionKind, 'cli');
  // ua(0.30)+host(0.18)+org(0.12)+model(0.15)+payload(0) = 0.75 → 0.5+0.75*0.6 = 0.95
  assert.ok(result.confidence >= 0.85, `confidence ${result.confidence} should be >= 0.85`);
  assert.ok(result.confidence <= 1.0);
});

test('golden path: legacy adapter — tool detected via raw blob', () => {
  const result = createLegacyHeuristicAdapter().detect(
    buildInput({
      targetHost: 'api.openai.com',
      userAgent: 'python-http/1.0',
      systemPrompt: 'You are a coding assistant for Cursor.',
      promptMessages: [{ role: 'user', content: 'Cursor: edit this function.' }],
      modelName: 'gpt-4o-mini'
    })
  );

  assert.ok(result, 'legacy adapter must detect cursor via blob');
  assert.equal(result.adapterName, 'legacy-heuristic');
  assert.equal(result.matchSource, 'heuristic');
  assert.equal(result.toolName, 'Cursor');
  assert.equal(result.confidence, 0.55, 'toolName match should yield exactly 0.55');
});

test('golden path: legacy adapter — provider-only match yields 0.42', () => {
  const result = createLegacyHeuristicAdapter().detect(
    buildInput({
      targetHost: 'api.openai.com',
      userAgent: 'python-http/1.0',
      systemPrompt: 'You are a helpful assistant.',
      promptMessages: [{ role: 'user', content: 'Write a function.' }],
      modelName: 'gpt-4o-mini'
    })
  );

  assert.ok(result, 'legacy adapter must detect via provider');
  assert.equal(result.adapterName, 'legacy-heuristic');
  assert.equal(result.toolName, null);
  assert.equal(result.confidence, 0.42, 'provider-only match should yield exactly 0.42');
});

// ---------------------------------------------------------------------------
// Operation type classification
// ---------------------------------------------------------------------------

test('golden path: operation type — refactor keyword', () => {
  const result = createClaudeCodeAdapter().detect(
    buildInput({
      targetHost: 'api.anthropic.com',
      userAgent: 'Claude-Code/1.5',
      systemPrompt: 'You are Claude Code.',
      promptMessages: [{ role: 'user', content: 'Refactor this service module.' }],
      modelName: 'claude-3-5-sonnet-20241022'
    })
  );
  assert.equal(result?.operationType, 'refactor');
});

test('golden path: operation type — test-fix keyword', () => {
  const result = createClaudeCodeAdapter().detect(
    buildInput({
      targetHost: 'api.anthropic.com',
      userAgent: 'Claude-Code/1.5',
      systemPrompt: 'You are Claude Code.',
      promptMessages: [{ role: 'user', content: 'Fix the failing tests in the jest suite.' }],
      modelName: 'claude-3-5-sonnet-20241022'
    })
  );
  assert.equal(result?.operationType, 'test-fix');
});

test('golden path: operation type — explain keyword', () => {
  const result = createClaudeCodeAdapter().detect(
    buildInput({
      targetHost: 'api.anthropic.com',
      userAgent: 'Claude-Code/1.5',
      systemPrompt: 'You are Claude Code.',
      promptMessages: [{ role: 'user', content: 'Explain why this algorithm is O(n log n).' }],
      modelName: 'claude-3-5-sonnet-20241022'
    })
  );
  assert.equal(result?.operationType, 'explain');
});

test('golden path: operation type — multi-file-run keyword', () => {
  const result = createClaudeCodeAdapter().detect(
    buildInput({
      targetHost: 'api.anthropic.com',
      userAgent: 'Claude-Code/1.5',
      systemPrompt: 'You are Claude Code.',
      promptMessages: [{ role: 'user', content: 'Apply this change across multiple files in the workspace.' }],
      modelName: 'claude-3-5-sonnet-20241022'
    })
  );
  assert.equal(result?.operationType, 'multi-file-run');
});

test('golden path: operation type — default edit', () => {
  const result = createClaudeCodeAdapter().detect(
    buildInput({
      targetHost: 'api.anthropic.com',
      userAgent: 'Claude-Code/1.5',
      systemPrompt: 'You are Claude Code.',
      promptMessages: [{ role: 'user', content: 'Add a null check here.' }],
      modelName: 'claude-3-5-sonnet-20241022'
    })
  );
  assert.equal(result?.operationType, 'edit');
});

// ---------------------------------------------------------------------------
// Conflict resolution
// ---------------------------------------------------------------------------

test('golden path: conflict resolution — specialist beats legacy at equal confidence', () => {
  // This tests the fallback invariant: legacy (order=1000, confidence=0.55)
  // must lose to a specialist with ≥ 0.55 confidence.
  // Cursor min evidence (payload only) → 0.5 + 0.22*0.6 = 0.632 > 0.55
  const result = createDefaultAgentAdapterRegistry().detect(
    buildInput({
      targetHost: 'api.openai.com',
      userAgent: 'python-requests/2.31',
      systemPrompt: 'You are Cursor. Edit this file.',
      promptMessages: [{ role: 'user', content: 'Use cursor to add this helper function.' }],
      modelName: 'gpt-4o-mini'
    })
  );

  assert.ok(result, 'must produce a result');
  assert.equal(result.adapterName, 'cursor', `cursor (specialist) must win over legacy (got: ${result.adapterName})`);
});

test('golden path: conflict resolution — higher order adapter loses when confidence is lower', () => {
  // Two custom adapters where the higher-confidence lower-order one wins.
  const registry = new AgentAdapterRegistry([
    {
      name: 'slow-low-confidence',
      order: 5,
      capabilities: ['tool-name'],
      detect: () => ({
        toolName: 'LowConf',
        provider: null,
        sessionId: null,
        conversationId: null,
        runId: null,
        modelName: null,
        userAgent: null,
        workspaceHint: null,
        operationType: 'edit' as const,
        confidence: 0.5,
        evidence: [],
        adapterName: 'slow-low-confidence',
        matchSource: 'adapter' as const,
        sessionKind: 'unknown' as const,
        host: null,
        sessionSignature: 'low|sig',
        detectedAtIso: '2026-04-18T10:00:00.000Z'
      })
    },
    {
      name: 'fast-high-confidence',
      order: 100,
      capabilities: ['tool-name'],
      detect: () => ({
        toolName: 'HighConf',
        provider: null,
        sessionId: null,
        conversationId: null,
        runId: null,
        modelName: null,
        userAgent: null,
        workspaceHint: null,
        operationType: 'edit' as const,
        confidence: 0.9,
        evidence: [],
        adapterName: 'fast-high-confidence',
        matchSource: 'adapter' as const,
        sessionKind: 'unknown' as const,
        host: null,
        sessionSignature: 'high|sig',
        detectedAtIso: '2026-04-18T10:00:00.000Z'
      })
    }
  ]);

  const result = registry.detect(buildInput());
  assert.equal(result?.toolName, 'HighConf', 'higher confidence must win regardless of order');
  assert.equal(result?.adapterName, 'fast-high-confidence');
});

// ---------------------------------------------------------------------------
// Invariant enforcement
// ---------------------------------------------------------------------------

test('invariant: duplicate adapter orders are rejected at construction', () => {
  assert.throws(
    () =>
      new AgentAdapterRegistry([
        createCursorAdapter(),   // order=10
        createCursorAdapter()    // order=10 again
      ]),
    /duplicate adapter order/i
  );
});

test('invariant: confidence out of bounds — assertContextInvariants throws directly', () => {
  // normalizeAgentContext clamps confidence before the registry calls assertContextInvariants,
  // so we test the assertion function directly with an already-normalized-looking context
  // where confidence is manually set outside [0,1].
  const badContext: NormalizedAgentContext = {
    toolName: 'Bad',
    provider: null,
    sessionId: null,
    conversationId: null,
    runId: null,
    modelName: null,
    userAgent: null,
    workspaceHint: null,
    operationType: 'edit',
    confidence: 1.5,
    evidence: [],
    adapterName: 'bad-confidence',
    matchSource: 'adapter',
    sessionKind: 'unknown',
    host: null,
    sessionSignature: 'bad|sig',
    detectedAtIso: '2026-04-18T10:00:00.000Z'
  };

  const registered = new Set(['bad-confidence']);
  assert.throws(
    () => assertContextInvariants(badContext, registered),
    /confidence out of bounds/i
  );
});

test('invariant: negative confidence — assertContextInvariants throws', () => {
  const badContext: NormalizedAgentContext = {
    toolName: null,
    provider: null,
    sessionId: null,
    conversationId: null,
    runId: null,
    modelName: null,
    userAgent: null,
    workspaceHint: null,
    operationType: 'edit',
    confidence: -0.1,
    evidence: [],
    adapterName: 'bad-neg',
    matchSource: 'adapter',
    sessionKind: 'unknown',
    host: null,
    sessionSignature: 'neg|sig',
    detectedAtIso: '2026-04-18T10:00:00.000Z'
  };

  assert.throws(
    () => assertContextInvariants(badContext, new Set(['bad-neg'])),
    /confidence out of bounds/i
  );
});

test('invariant: unregistered adapter name throws from registry', () => {
  const spoofedAdapter = {
    name: 'my-adapter',
    order: 998,
    capabilities: [] as const,
    detect: () => ({
      toolName: 'Spoofed',
      provider: null,
      sessionId: null,
      conversationId: null,
      runId: null,
      modelName: null,
      userAgent: null,
      workspaceHint: null,
      operationType: 'edit' as const,
      confidence: 0.8,
      evidence: [],
      adapterName: 'not-my-adapter',
      matchSource: 'adapter' as const,
      sessionKind: 'unknown' as const,
      host: null,
      sessionSignature: 'spoof|sig',
      detectedAtIso: '2026-04-18T10:00:00.000Z'
    })
  };

  const registry = new AgentAdapterRegistry([spoofedAdapter]);
  assert.throws(() => registry.detect(buildInput()), /not in registry/i);
});

test('invariant: legacy-heuristic cannot be selected over specialist at same confidence', () => {
  const specialistAdapter = {
    name: 'specialist',
    order: 5,
    capabilities: [] as const,
    detect: () => ({
      toolName: 'SpecialistTool',
      provider: null,
      sessionId: null,
      conversationId: null,
      runId: null,
      modelName: null,
      userAgent: null,
      workspaceHint: null,
      operationType: 'edit' as const,
      confidence: 0.55,
      evidence: [],
      adapterName: 'specialist',
      matchSource: 'adapter' as const,
      sessionKind: 'agentic' as const,
      host: null,
      sessionSignature: 'spec|sig',
      detectedAtIso: '2026-04-18T10:00:00.000Z'
    })
  };

  // Add legacy as well — it will return 0.55 for a toolName match
  const registry = new AgentAdapterRegistry([specialistAdapter, createLegacyHeuristicAdapter()]);
  // The specialist has order=5 and confidence=0.55; legacy would also return 0.55 for a toolName match.
  // Candidates sorted: specialist (confidence=0.55, order=5) comes first — legacy (confidence=0.55, order=1000) loses.
  // The invariant only fires if legacy-heuristic ends up as `best`, so this should pass fine.
  const result = registry.detect(buildInput({
    targetHost: 'api.openai.com',
    userAgent: 'generic-agent',
    promptMessages: [{ role: 'user', content: 'Edit this.' }],
    modelName: 'gpt-4o-mini'
  }));

  assert.equal(result?.adapterName, 'specialist', 'specialist must win at equal confidence via lower order');
});
