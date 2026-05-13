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

export function createCursorAdapter(): AgentAdapter {
  return {
    name: 'cursor',
    order: 10,
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
        normalizeUserAgent(findHeaderValue(headers, 'x-client-name')) ||
        normalizeUserAgent(findHeaderValue(headers, 'x-cursor-client-name'));

      const targetHost = input.correlation.targetHost?.trim().toLowerCase() ?? null;
      const payloadBlob = buildDetectionPayloadBlob({
        fullPromptMessages: input.correlation.fullPromptMessages,
        parameters: input.correlation.parameters,
        systemPrompt: input.correlation.systemPrompt,
        insertedText: input.insertedText
      });

      const cursorSignals = [
        userAgent?.toLowerCase().includes('cursor') ?? false,
        targetHost?.includes('cursor') ?? false,
        payloadBlob.includes('cursor'),
        payloadBlob.includes('composer'),
        payloadBlob.includes('inline completion')
      ];

      if (!cursorSignals.some(Boolean)) {
        return undefined;
      }

      const modelName = normalizeModelName(input.correlation.modelName);
      const provider = inferProvider(targetHost, modelName, payloadBlob) ?? 'Cursor';
      const operationType = classifyOperationType({
        insertedText: input.insertedText,
        promptBlob: payloadBlob,
        modelName
      });

      const sessionId =
        findHeaderValue(headers, 'x-cursor-session-id') ||
        findHeaderValue(headers, 'x-cursor-chat-id') ||
        findHeaderValue(headers, 'x-request-id') ||
        `cursor-${hashContext([targetHost, userAgent, modelName, input.workspaceHint, input.timestampIso])}`;

      const conversationId =
        findHeaderValue(headers, 'x-cursor-conversation-id') ||
        findHeaderValue(headers, 'x-cursor-thread-id') ||
        sessionId;

      const runId =
        findHeaderValue(headers, 'x-cursor-run-id') ||
        findHeaderValue(headers, 'x-cursor-operation-id') ||
        null;

      const evidence: AgentEvidence[] = [];
      pushEvidence(evidence, userAgent?.toLowerCase().includes('cursor') ?? false, 'user-agent', 'user-agent', userAgent, 0.26, 'Cursor user agent matched.');
      pushEvidence(evidence, targetHost?.includes('cursor') ?? false, 'header', 'targetHost', targetHost, 0.18, 'Cursor traffic target host matched.');
      pushEvidence(evidence, payloadBlob.includes('cursor') || payloadBlob.includes('composer') || payloadBlob.includes('inline completion'), 'payload', 'prompt', payloadBlob.slice(0, 200), 0.22, 'Cursor prompt or payload fingerprint matched.');
      pushEvidence(evidence, Boolean(modelName), 'routing', 'modelName', modelName || 'unknown', 0.08, 'Model routing metadata observed.');

      return {
        toolName: 'Cursor',
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
        adapterName: 'cursor',
        matchSource: 'adapter',
        sessionKind: 'agentic',
        host: targetHost,
        sessionSignature: buildSessionSignature({
          toolName: 'Cursor',
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
