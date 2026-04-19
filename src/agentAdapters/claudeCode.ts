import type { AgentAdapter, AgentAdapterInput, NormalizedAgentContext } from './types';
import {
  buildSessionSignature,
  clampConfidence,
  classifyOperationType,
  createEvidence,
  findHeaderValue,
  hashContext,
  inferProvider,
  normalizeModelName,
  normalizeUserAgent,
  safeSerialize,
  toText
} from './shared';

export function createClaudeCodeAdapter(): AgentAdapter {
  return {
    name: 'claude-code',
    order: 20,
    capabilities: [
      'tool-name',
      'provider',
      'model',
      'session-id',
      'conversation-id',
      'run-id',
      'request-id',
      'user-agent',
      'headers',
      'prompt-body',
      'response-body',
      'file-context',
      'file-diff',
      'workspace'
    ],
    detect(input: AgentAdapterInput): NormalizedAgentContext | undefined {
      const headers = input.correlation.requestHeaders;
      const userAgent =
        normalizeUserAgent(findHeaderValue(headers, 'user-agent')) ||
        normalizeUserAgent(findHeaderValue(headers, 'x-client-name'));
      const targetHost = input.correlation.targetHost?.trim().toLowerCase() ?? null;
      const payloadBlob = [
        safeSerialize(input.correlation.fullPromptMessages),
        safeSerialize(input.correlation.parameters),
        toText(input.correlation.systemPrompt),
        toText(input.correlation.rawModelResponse),
        input.insertedText
      ]
        .join('\n')
        .toLowerCase();

      // CLI-specific headers sent by the Anthropic SDK and Claude Code CLI
      const anthropicVersion = findHeaderValue(headers, 'anthropic-version');
      const xApp = findHeaderValue(headers, 'x-app');
      const stainlessRuntime = findHeaderValue(headers, 'x-stainless-runtime');
      const claudeCodeVersionMatch = /claude[\s-]?code\/[\d.]+/i.exec(userAgent ?? '');
      const claudeCodeVersion = claudeCodeVersionMatch?.[0] ?? null;

      // isCliMode is true when the request originates from the Claude Code terminal CLI
      const isCliMode =
        xApp?.toLowerCase() === 'cli' ||
        stainlessRuntime?.toLowerCase() === 'node' ||
        claudeCodeVersion !== null;

      const matched =
        userAgent?.toLowerCase().includes('claude') ||
        isCliMode ||
        anthropicVersion !== null ||
        targetHost?.includes('anthropic.com') ||
        payloadBlob.includes('claude code') ||
        payloadBlob.includes('claude-code');

      if (!matched) {
        return undefined;
      }

      const modelName = normalizeModelName(input.correlation.modelName);
      const provider = inferProvider(targetHost, modelName, payloadBlob) ?? 'Anthropic';
      const operationType = classifyOperationType({
        insertedText: input.insertedText,
        promptBlob: payloadBlob,
        modelName
      });

      const sessionId =
        findHeaderValue(headers, 'x-claude-code-session-id') ||
        findHeaderValue(headers, 'x-claude-session-id') ||
        findHeaderValue(headers, 'x-request-id') ||
        findHeaderValue(headers, 'request-id') ||
        `claude-${hashContext([targetHost, userAgent, modelName, input.workspaceHint, input.timestampIso])}`;

      const conversationId =
        findHeaderValue(headers, 'x-claude-conversation-id') ||
        findHeaderValue(headers, 'x-conversation-id') ||
        sessionId;

      const runId =
        findHeaderValue(headers, 'x-claude-code-run-id') ||
        findHeaderValue(headers, 'x-run-id') ||
        findHeaderValue(headers, 'x-invocation-id') ||
        null;

      const sessionKind = isCliMode ? 'cli' : 'agentic';

      const evidence = [
        createEvidence('user-agent', 'user-agent', userAgent ?? 'claude-code', 0.3, 'Claude Code user agent matched.'),
        createEvidence('header', 'targetHost', targetHost ?? 'unknown', 0.18, 'Anthropic API host matched.'),
        createEvidence('header', 'anthropic-version', anthropicVersion ?? '', 0.12, 'Anthropic SDK version header present.'),
        createEvidence('header', 'x-app', xApp ?? '', 0.15, 'CLI application marker header.'),
        createEvidence('system-prompt', 'systemPrompt', input.correlation.systemPrompt, 0.18, 'Claude-style system prompt fingerprint observed.'),
        createEvidence('payload', 'messages', input.correlation.fullPromptMessages, 0.12, 'Prompt payload shape matched Claude Code traffic.')
      ];

      return {
        toolName: 'Claude Code',
        provider,
        sessionId,
        conversationId,
        runId,
        modelName: modelName || null,
        userAgent,
        workspaceHint: input.workspaceHint,
        operationType,
        confidence: clampConfidence(0.9 + evidence.reduce((sum, entry) => sum + entry.weight, 0) * 0.05),
        evidence,
        adapterName: 'claude-code',
        matchSource: 'adapter',
        sessionKind,
        host: targetHost,
        sessionSignature: buildSessionSignature({
          toolName: 'Claude Code',
          provider,
          modelName: modelName || null,
          sessionKind,
          sessionId,
          conversationId,
          runId
        }),
        detectedAtIso: input.timestampIso
      };
    }
  };
}
