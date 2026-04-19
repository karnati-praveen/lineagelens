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

export function createAiderAdapter(): AgentAdapter {
  return {
    name: 'aider',
    order: 30,
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

      const matched =
        userAgent?.toLowerCase().includes('aider') ||
        payloadBlob.includes('aider') ||
        payloadBlob.includes('pair programming');

      if (!matched) {
        return undefined;
      }

      const modelName = normalizeModelName(input.correlation.modelName);
      const provider = inferProvider(targetHost, modelName, payloadBlob) ?? 'OpenAI';
      const operationType = classifyOperationType({
        insertedText: input.insertedText,
        promptBlob: payloadBlob,
        modelName
      });

      const sessionId =
        findHeaderValue(headers, 'x-aider-session-id') ||
        findHeaderValue(headers, 'x-aider-run-id') ||
        findHeaderValue(headers, 'x-request-id') ||
        `aider-${hashContext([targetHost, userAgent, modelName, input.workspaceHint, input.timestampIso])}`;

      const conversationId =
        findHeaderValue(headers, 'x-aider-conversation-id') ||
        findHeaderValue(headers, 'x-conversation-id') ||
        sessionId;

      const runId =
        findHeaderValue(headers, 'x-aider-run-id') ||
        findHeaderValue(headers, 'x-session-id') ||
        sessionId;

      const evidence = [
        createEvidence('user-agent', 'user-agent', userAgent ?? 'aider', 0.28, 'Aider user agent matched.'),
        createEvidence('payload', 'prompt', payloadBlob.slice(0, 240), 0.2, 'Prompt payload contains Aider fingerprints.'),
        createEvidence('routing', 'modelName', modelName || 'unknown', 0.1, 'Model metadata observed.'),
        createEvidence('header', 'targetHost', targetHost ?? 'unknown', 0.1, 'Target host was consistent with AI request routing.')
      ];

      return {
        toolName: 'Aider',
        provider,
        sessionId,
        conversationId,
        runId,
        modelName: modelName || null,
        userAgent,
        workspaceHint: input.workspaceHint,
        operationType,
        confidence: clampConfidence(0.86 + evidence.reduce((sum, entry) => sum + entry.weight, 0) * 0.06),
        evidence,
        adapterName: 'aider',
        matchSource: 'adapter',
        sessionKind: 'agentic',
        host: targetHost,
        sessionSignature: buildSessionSignature({
          toolName: 'Aider',
          provider,
          modelName: modelName || null,
          sessionKind: 'agentic',
          sessionId,
          conversationId,
          runId
        }),
        detectedAtIso: input.timestampIso
      };
    }
  };
}
