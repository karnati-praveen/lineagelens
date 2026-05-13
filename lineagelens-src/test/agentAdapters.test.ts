import assert from 'node:assert/strict';
import test from 'node:test';
import {
  AgentAdapter,
  AgentAdapterInput,
  AgentAdapterRegistry,
  createAiderAdapter,
  createClaudeCodeAdapter,
  createCursorAdapter,
  createDefaultAgentAdapterRegistry
} from '../agentAdapters';
import type { PromptCorrelationResult } from '../correlation';

test('registry prefers lower-order adapters when confidence ties', () => {
  const first: AgentAdapter = {
    name: 'first',
    order: 5,
    capabilities: ['tool-name'],
    detect: () => makeContext('First', 'first', 0.8)
  };
  const second: AgentAdapter = {
    name: 'second',
    order: 10,
    capabilities: ['tool-name'],
    detect: () => makeContext('Second', 'second', 0.8)
  };

  const registry = new AgentAdapterRegistry([second, first]);
  const detected = registry.detect(buildInput({
    targetHost: 'api.example.com',
    userAgent: 'example-agent'
  }));

  assert.equal(detected?.toolName, 'First');
  assert.equal(detected?.adapterName, 'first');
});

test('registry exposes capability matrix for adapter contract checks', () => {
  const matrix = createDefaultAgentAdapterRegistry().getCompatibilityMatrix();
  const cursor = matrix.find((entry) => entry.adapterName === 'cursor');

  assert.ok(cursor);
  assert.ok(cursor.capabilities.includes('prompt-body'));
  assert.ok(cursor.capabilities.includes('file-diff'));
});

test('cursor adapter detects cursor-style metadata and operation type', () => {
  const detected = createDefaultAgentAdapterRegistry().detect(
    buildInput({
      targetHost: 'api.openai.com',
      userAgent: 'Cursor/1.0.0',
      systemPrompt: 'You are Cursor. Refactor this component and explain the changes.',
      promptMessages: [
        { role: 'system', content: 'You are Cursor.' },
        { role: 'user', content: 'Refactor this file and explain the changes.' }
      ],
      modelName: 'gpt-4o-mini'
    })
  );

  assert.equal(detected?.toolName, 'Cursor');
  assert.equal(detected?.adapterName, 'cursor');
  assert.equal(detected?.operationType, 'refactor');
  assert.ok((detected?.confidence ?? 0) > 0.8);
  assert.ok((detected?.evidence ?? []).length > 0);
});

test('claude code adapter detects anthropic-style prompts', () => {
  const detected = createClaudeCodeAdapter().detect(
    buildInput({
      targetHost: 'api.anthropic.com',
      userAgent: 'Claude-Code/0.9',
      systemPrompt: 'You are Claude Code. Fix the failing tests.',
      promptMessages: [
        { role: 'system', content: 'You are Claude Code.' },
        { role: 'user', content: 'Fix the failing tests in this workspace.' }
      ],
      modelName: 'claude-3.5-sonnet'
    })
  );

  assert.equal(detected?.toolName, 'Claude Code');
  assert.equal(detected?.adapterName, 'claude-code');
  assert.equal(detected?.operationType, 'test-fix');
});

test('aider adapter detects pair-programming style prompts', () => {
  const detected = createAiderAdapter().detect(
    buildInput({
      targetHost: 'api.openai.com',
      userAgent: 'aider/0.34',
      systemPrompt: 'Aider is helping with a multi-file refactor.',
      promptMessages: [
        { role: 'system', content: 'Aider session active.' },
        { role: 'user', content: 'Refactor this module across multiple files.' }
      ],
      modelName: 'gpt-4o-mini',
      insertedText: 'const transformed = true;\nconst other = false;'
    })
  );

  assert.equal(detected?.toolName, 'Aider');
  assert.equal(detected?.adapterName, 'aider');
  assert.equal(detected?.operationType, 'multi-file-run');
});

test('copilot adapter detects GitHub Copilot traffic via host and user-agent', () => {
  const detected = createDefaultAgentAdapterRegistry().detect(
    buildInput({
      targetHost: 'api.githubcopilot.com',
      userAgent: 'GitHubCopilot/1.0',
      promptMessages: [{ role: 'user', content: 'Use Copilot to add this helper.' }],
      modelName: 'gpt-4o-mini'
    })
  );

  assert.equal(detected?.adapterName, 'copilot');
  assert.equal(detected?.matchSource, 'adapter');
  assert.equal(detected?.sessionKind, 'assistant');
  assert.equal(detected?.toolName, 'GitHub Copilot');
});

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
      overrides.promptMessages ??
      [
        { role: 'system', content: 'You are a coding assistant.' },
        { role: 'user', content: 'Refactor this function.' }
      ],
    modelName: overrides.modelName ?? 'gpt-4o-mini',
    parameters: {
      temperature: 0.2,
      ...(overrides.parameters ?? {})
    },
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

function makeContext(toolName: string, adapterName: string, confidence: number) {
  return {
    toolName,
    provider: 'OpenAI',
    sessionId: 'session-id',
    conversationId: 'conversation-id',
    runId: 'run-id',
    modelName: 'gpt-4o-mini',
    userAgent: 'test-agent',
    workspaceHint: 'workspace',
    operationType: 'edit' as const,
    confidence,
    evidence: [],
    adapterName,
    matchSource: 'adapter' as const,
    sessionKind: 'agentic' as const,
    host: 'api.openai.com',
    sessionSignature: toolName + '|session',
    detectedAtIso: '2026-04-18T10:00:00.000Z'
  };
}
