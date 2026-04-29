/**
 * Diff-based verification tests.
 *
 * Every entry in BEHAVIORAL_SNAPSHOT fixes a known-good input → output mapping.
 * If a refactor causes ANY of these to change — adapter name, tool name, confidence
 * range, operation type, session kind, match source, or provider — the test fails
 * and prints exactly which field changed and for which scenario.
 *
 * Adding a new adapter or changing detection logic REQUIRES updating this file,
 * which makes regressions visible before they hit production.
 */

import assert from 'node:assert/strict';
import test from 'node:test';
import { AgentAdapterInput, createDefaultAgentAdapterRegistry } from '../agentAdapters';
import type { PromptCorrelationResult } from '../correlation';
import type { AgentOperationType, AgentSessionKind, NormalizedAgentContext } from '../agentAdapters/types';

type BehavioralExpectation = {
  scenario: string;
  input: Partial<FixtureOverrides>;
  expected: {
    adapterName: string;
    toolName: string | null;
    confidenceMin: number;
    confidenceMax: number;
    operationType: AgentOperationType;
    sessionKind: AgentSessionKind;
    matchSource: 'adapter' | 'heuristic';
    provider: string | null;
  };
};

// ---------------------------------------------------------------------------
// Fixture helpers
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
// Behavioral snapshot
// ---------------------------------------------------------------------------

const BEHAVIORAL_SNAPSHOT: BehavioralExpectation[] = [
  {
    scenario: 'Cursor — full signal set',
    input: {
      targetHost: 'api.cursor.sh',
      userAgent: 'Cursor/1.0.0',
      systemPrompt: 'You are Cursor, an AI coding assistant.',
      promptMessages: [
        { role: 'system', content: 'You are Cursor.' },
        { role: 'user', content: 'Refactor this module.' }
      ],
      modelName: 'gpt-4o'
    },
    expected: {
      adapterName: 'cursor',
      toolName: 'Cursor',
      confidenceMin: 0.8,
      confidenceMax: 1.0,
      operationType: 'refactor',
      sessionKind: 'agentic',
      matchSource: 'adapter',
      // inferProvider matches gpt-4o → OpenAI before the cursor-host fallback fires
      provider: 'OpenAI'
    }
  },
  {
    scenario: 'Claude Code — anthropic host + cli headers',
    input: {
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
    },
    expected: {
      adapterName: 'claude-code',
      toolName: 'Claude Code',
      confidenceMin: 0.9,
      confidenceMax: 1.0,
      operationType: 'test-fix',
      sessionKind: 'cli',
      matchSource: 'adapter',
      provider: 'Anthropic'
    }
  },
  {
    scenario: 'GitHub Copilot — githubcopilot.com + token + user-agent',
    input: {
      targetHost: 'api.githubcopilot.com',
      userAgent: 'GitHubCopilot/1.155.0',
      promptMessages: [{ role: 'user', content: 'Use GitHub Copilot to add a helper.' }],
      modelName: 'gpt-4o',
      requestHeaders: {
        'x-github-token': 'ghp_test_token',
        'editor-version': 'vscode/1.85.0'
      }
    },
    expected: {
      adapterName: 'copilot',
      toolName: 'GitHub Copilot',
      confidenceMin: 0.9,
      confidenceMax: 1.0,
      operationType: 'edit',
      sessionKind: 'assistant',
      matchSource: 'adapter',
      // inferProvider matches gpt-4o → OpenAI before the githubcopilot-host fallback fires
      provider: 'OpenAI'
    }
  },
  {
    scenario: 'Aider — user-agent + multi-file prompt',
    input: {
      targetHost: 'api.openai.com',
      userAgent: 'aider/0.50.0',
      systemPrompt: 'You are an aider pair-programming assistant.',
      promptMessages: [{ role: 'user', content: 'Refactor this module across multiple files.' }],
      modelName: 'gpt-4o',
      insertedText: 'const a = 1;\nconst b = 2;\nconst c = 3;\nconst d = 4;'
    },
    expected: {
      adapterName: 'aider',
      toolName: 'Aider',
      confidenceMin: 0.7,
      confidenceMax: 1.0,
      operationType: 'multi-file-run',
      sessionKind: 'agentic',
      matchSource: 'adapter',
      provider: 'OpenAI'
    }
  },
  {
    scenario: 'Codeium — codeium.com host + api-key + user-agent',
    input: {
      targetHost: 'codeium.com',
      userAgent: 'Codeium/1.8.0',
      promptMessages: [{ role: 'user', content: 'Add a helper function.' }],
      modelName: 'codeium-model',
      requestHeaders: {
        'x-codeium-api-key': 'test-api-key-123'
      }
    },
    expected: {
      adapterName: 'codeium',
      toolName: 'Codeium',
      confidenceMin: 0.85,
      confidenceMax: 1.0,
      operationType: 'edit',
      sessionKind: 'agentic',
      matchSource: 'adapter',
      provider: 'Codeium'
    }
  },
  {
    scenario: 'Continue — version header + unique-id + user-agent',
    input: {
      targetHost: 'api.openai.com',
      userAgent: 'Continue/0.9.220',
      promptMessages: [{ role: 'user', content: 'continuedev: refactor this.' }],
      modelName: 'gpt-4o-mini',
      requestHeaders: {
        'x-continue-version': '0.9.220',
        'x-continue-unique-id': 'continue-uid-abc123'
      }
    },
    expected: {
      adapterName: 'continue',
      toolName: 'Continue',
      confidenceMin: 0.85,
      confidenceMax: 1.0,
      operationType: 'refactor',
      sessionKind: 'agentic',
      matchSource: 'adapter',
      provider: 'OpenAI'
    }
  },
  {
    scenario: 'Sourcegraph Cody — cody-gateway host + sourcegraph-client + user-agent',
    input: {
      targetHost: 'cody-gateway.sourcegraph.com',
      userAgent: 'Cody/1.20.0',
      systemPrompt: 'You are Sourcegraph Cody.',
      promptMessages: [{ role: 'user', content: 'Use cody to explain this code.' }],
      modelName: 'claude-3-5-sonnet',
      requestHeaders: {
        'x-sourcegraph-client': 'cody-vscode',
        'x-sourcegraph-token': 'sg_test_token'
      }
    },
    expected: {
      adapterName: 'cody',
      toolName: 'Sourcegraph Cody',
      confidenceMin: 0.85,
      confidenceMax: 1.0,
      operationType: 'explain',
      sessionKind: 'agentic',
      matchSource: 'adapter',
      // inferProvider matches claude-3-5-sonnet → Anthropic before the sourcegraph-host fallback fires
      provider: 'Anthropic'
    }
  },
  {
    scenario: 'Amazon Q Developer — AWS host + amz-request-id + user-agent',
    input: {
      targetHost: 'q.us-east-1.amazonaws.com',
      userAgent: 'AmazonQ/1.0',
      promptMessages: [{ role: 'user', content: 'Use amazon-q to add a helper.' }],
      modelName: 'amazon-q-dev',
      requestHeaders: {
        'x-amz-request-id': 'amz-req-12345',
        'x-amz-target': 'CodeWhispererService.amazonq'
      }
    },
    expected: {
      adapterName: 'amazon-q',
      toolName: 'Amazon Q Developer',
      confidenceMin: 0.9,
      confidenceMax: 1.0,
      operationType: 'edit',
      sessionKind: 'assistant',
      matchSource: 'adapter',
      provider: 'AWS'
    }
  },
  {
    scenario: 'Gemini CLI — googleapis.com host + goog-api-client + user-agent',
    input: {
      targetHost: 'generativelanguage.googleapis.com',
      userAgent: 'gemini-cli/1.0',
      promptMessages: [{ role: 'user', content: 'Use gemini generativelanguage to help.' }],
      modelName: 'gemini-1.5-pro',
      requestHeaders: {
        'x-goog-api-client': 'gemini-node/1.0',
        'x-goog-request-reason': 'code-generation'
      }
    },
    expected: {
      adapterName: 'gemini-cli',
      toolName: 'Gemini CLI',
      confidenceMin: 0.9,
      confidenceMax: 1.0,
      operationType: 'edit',
      sessionKind: 'cli',
      matchSource: 'adapter',
      provider: 'Google'
    }
  },
  {
    scenario: 'OpenAI Codex CLI — openai-codex user-agent + o4-mini model',
    input: {
      targetHost: 'api.openai.com',
      userAgent: 'openai-codex/0.1.2511',
      promptMessages: [{ role: 'user', content: 'Edit this file using codex-cli.' }],
      modelName: 'o4-mini',
      requestHeaders: {
        'openai-organization': 'org-test123'
      }
    },
    expected: {
      adapterName: 'codex-cli',
      toolName: 'OpenAI Codex CLI',
      confidenceMin: 0.85,
      confidenceMax: 1.0,
      operationType: 'edit',
      sessionKind: 'cli',
      matchSource: 'adapter',
      provider: 'OpenAI'
    }
  },
  {
    scenario: 'Legacy — provider-only (OpenAI model, no tool signals)',
    input: {
      targetHost: 'api.openai.com',
      userAgent: 'python-http/1.0',
      systemPrompt: 'You are a helpful assistant.',
      promptMessages: [{ role: 'user', content: 'Write a function.' }],
      modelName: 'gpt-4o-mini'
    },
    expected: {
      adapterName: 'legacy-heuristic',
      toolName: null,
      confidenceMin: 0.42,
      confidenceMax: 0.42,
      operationType: 'edit',
      sessionKind: 'unknown',
      matchSource: 'heuristic',
      provider: 'OpenAI'
    }
  }
];

// ---------------------------------------------------------------------------
// Diff engine: compare actual vs expected and report field-level diffs
// ---------------------------------------------------------------------------

function diffResult(
  scenario: string,
  actual: NormalizedAgentContext,
  expected: BehavioralExpectation['expected']
): string[] {
  const diffs: string[] = [];

  if (actual.adapterName !== expected.adapterName) {
    diffs.push(`  adapterName: expected "${expected.adapterName}", got "${actual.adapterName}"`);
  }
  if (actual.toolName !== expected.toolName) {
    diffs.push(`  toolName: expected ${JSON.stringify(expected.toolName)}, got ${JSON.stringify(actual.toolName)}`);
  }
  if (actual.confidence < expected.confidenceMin || actual.confidence > expected.confidenceMax) {
    diffs.push(
      `  confidence: expected [${expected.confidenceMin}, ${expected.confidenceMax}], got ${actual.confidence}`
    );
  }
  if (actual.operationType !== expected.operationType) {
    diffs.push(`  operationType: expected "${expected.operationType}", got "${actual.operationType}"`);
  }
  if (actual.sessionKind !== expected.sessionKind) {
    diffs.push(`  sessionKind: expected "${expected.sessionKind}", got "${actual.sessionKind}"`);
  }
  if (actual.matchSource !== expected.matchSource) {
    diffs.push(`  matchSource: expected "${expected.matchSource}", got "${actual.matchSource}"`);
  }
  if (actual.provider !== expected.provider) {
    diffs.push(`  provider: expected ${JSON.stringify(expected.provider)}, got ${JSON.stringify(actual.provider)}`);
  }

  return diffs.map((line) => `[${scenario}] ${line}`);
}

// ---------------------------------------------------------------------------
// Run snapshot suite
// ---------------------------------------------------------------------------

test('diff-based verification: all behavioral snapshots match', () => {
  const registry = createDefaultAgentAdapterRegistry();
  const allDiffs: string[] = [];

  for (const entry of BEHAVIORAL_SNAPSHOT) {
    const actual = registry.detect(buildInput(entry.input));

    if (!actual) {
      allDiffs.push(`[${entry.scenario}] registry returned null — expected adapter "${entry.expected.adapterName}"`);
      continue;
    }

    const diffs = diffResult(entry.scenario, actual, entry.expected);
    allDiffs.push(...diffs);
  }

  if (allDiffs.length > 0) {
    assert.fail(
      `Behavioral snapshot regressions detected (${allDiffs.length} field change${allDiffs.length !== 1 ? 's' : ''}):\n\n` +
      allDiffs.join('\n') +
      '\n\nIf these changes are intentional, update the BEHAVIORAL_SNAPSHOT in src/test/diffVerification.test.ts.'
    );
  }
});

test('diff-based verification: no adapter returns confidence > 1.0 for any snapshot input', () => {
  const registry = createDefaultAgentAdapterRegistry();

  for (const entry of BEHAVIORAL_SNAPSHOT) {
    const actual = registry.detect(buildInput(entry.input));
    if (!actual) continue;

    assert.ok(
      actual.confidence <= 1.0,
      `[${entry.scenario}] confidence ${actual.confidence} exceeds 1.0 — clampConfidence is broken`
    );
    assert.ok(
      actual.confidence >= 0,
      `[${entry.scenario}] confidence ${actual.confidence} is negative — clampConfidence is broken`
    );
  }
});

test('diff-based verification: no adapter returns null for its own canonical input', () => {
  const registry = createDefaultAgentAdapterRegistry();

  for (const entry of BEHAVIORAL_SNAPSHOT) {
    const actual = registry.detect(buildInput(entry.input));
    assert.ok(
      actual !== null,
      `[${entry.scenario}] registry returned null — at minimum the legacy adapter should fire`
    );
  }
});
