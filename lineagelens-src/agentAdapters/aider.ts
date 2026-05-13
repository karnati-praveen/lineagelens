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
      const payloadBlob = buildDetectionPayloadBlob({
        fullPromptMessages: input.correlation.fullPromptMessages,
        parameters: input.correlation.parameters,
        systemPrompt: input.correlation.systemPrompt,
        insertedText: input.insertedText
      });

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

      const evidence: AgentEvidence[] = [];
      pushEvidence(evidence, userAgent?.toLowerCase().includes('aider') ?? false, 'user-agent', 'user-agent', userAgent, 0.28, 'Aider user agent matched.');
      pushEvidence(evidence, payloadBlob.includes('aider') || payloadBlob.includes('pair programming'), 'payload', 'prompt', payloadBlob.slice(0, 240), 0.2, 'Prompt payload contains Aider fingerprints.');
      pushEvidence(evidence, Boolean(modelName), 'routing', 'modelName', modelName || 'unknown', 0.1, 'Model metadata observed.');
      pushEvidence(evidence, Boolean(targetHost), 'header', 'targetHost', targetHost ?? 'unknown', 0.1, 'Target host was consistent with AI request routing.');

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
        confidence: clampConfidence(0.5 + sumEvidenceWeights(evidence) * 0.6),
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
