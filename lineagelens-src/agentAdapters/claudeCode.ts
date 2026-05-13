import type { AgentAdapter, AgentAdapterInput, AgentEvidence, NormalizedAgentContext } from './types';
import {
  buildDetectionPayloadBlob,
  buildSessionSignature,
  clampConfidence,
  classifyOperationType,
  findHeaderValue,
  hashContext,
  inferProvider,
  normalizeModelName,
  normalizeUserAgent,
  pushEvidence,
  sumEvidenceWeights
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
      const payloadBlob = buildDetectionPayloadBlob({
        fullPromptMessages: input.correlation.fullPromptMessages,
        parameters: input.correlation.parameters,
        systemPrompt: input.correlation.systemPrompt,
        insertedText: input.insertedText
      });

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

      const evidence: AgentEvidence[] = [];
      pushEvidence(evidence, userAgent?.toLowerCase().includes('claude') ?? false, 'user-agent', 'user-agent', userAgent, 0.3, 'Claude Code user agent matched.');
      pushEvidence(evidence, targetHost?.includes('anthropic.com') ?? false, 'header', 'targetHost', targetHost, 0.18, 'Anthropic API host matched.');
      pushEvidence(evidence, anthropicVersion !== null, 'header', 'anthropic-version', anthropicVersion, 0.12, 'Anthropic SDK version header present.');
      pushEvidence(evidence, xApp !== null, 'header', 'x-app', xApp, 0.15, 'CLI application marker header.');
      pushEvidence(evidence, typeof input.correlation.systemPrompt === 'string' && /claude/i.test(input.correlation.systemPrompt), 'system-prompt', 'systemPrompt', input.correlation.systemPrompt, 0.18, 'Claude-style system prompt fingerprint observed.');
      pushEvidence(evidence, Array.isArray(input.correlation.fullPromptMessages) && input.correlation.fullPromptMessages.length > 0, 'payload', 'messages', input.correlation.fullPromptMessages, 0.12, 'Prompt payload shape matched Claude Code traffic.');

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
        confidence: clampConfidence(0.5 + sumEvidenceWeights(evidence) * 0.6),
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
