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

export function createContinueAdapter(): AgentAdapter {
  return {
    name: 'continue',
    order: 35,
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

      const continueVersion = findHeaderValue(headers, 'x-continue-version');
      const continueUniqueId = findHeaderValue(headers, 'x-continue-unique-id');
      const continueClient = findHeaderValue(headers, 'x-continue-client');

      const matched =
        userAgent?.toLowerCase().includes('continue') ||
        continueVersion !== null ||
        continueUniqueId !== null ||
        continueClient !== null ||
        payloadBlob.includes('continuedev') ||
        payloadBlob.includes('continue.dev') ||
        payloadBlob.includes('"continue"');

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
        findHeaderValue(headers, 'x-continue-session-id') ||
        continueUniqueId ||
        findHeaderValue(headers, 'x-request-id') ||
        `continue-${hashContext([targetHost, userAgent, modelName, input.workspaceHint, input.timestampIso])}`;

      const conversationId =
        findHeaderValue(headers, 'x-continue-conversation-id') ||
        findHeaderValue(headers, 'x-continue-chat-id') ||
        sessionId;

      const runId =
        findHeaderValue(headers, 'x-continue-run-id') ||
        null;

      const evidence: AgentEvidence[] = [];
      pushEvidence(evidence, userAgent?.toLowerCase().includes('continue') ?? false, 'user-agent', 'user-agent', userAgent, 0.26, 'Continue.dev user agent matched.');
      pushEvidence(evidence, continueVersion !== null, 'header', 'x-continue-version', continueVersion, 0.22, 'Continue.dev version header present.');
      pushEvidence(evidence, continueUniqueId !== null, 'header', 'x-continue-unique-id', continueUniqueId, 0.18, 'Continue.dev unique ID header present.');
      pushEvidence(evidence, payloadBlob.includes('continuedev') || payloadBlob.includes('continue.dev') || payloadBlob.includes('"continue"'), 'payload', 'prompt', payloadBlob.slice(0, 200), 0.14, 'Payload fingerprint consistent with Continue.dev traffic.');

      return {
        toolName: 'Continue',
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
        adapterName: 'continue',
        matchSource: 'adapter',
        sessionKind: 'agentic',
        host: targetHost,
        sessionSignature: buildSessionSignature({
          toolName: 'Continue',
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
