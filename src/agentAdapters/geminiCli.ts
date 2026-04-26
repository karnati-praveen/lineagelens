import type { AgentAdapter, AgentAdapterInput, AgentEvidence, NormalizedAgentContext } from './types';
import {
  buildDetectionPayloadBlob,
  buildSessionSignature,
  clampConfidence,
  classifyOperationType,
  findHeaderValue,
  hashContext,
  normalizeModelName,
  normalizeUserAgent,
  pushEvidence,
  sumEvidenceWeights
} from './shared';

export function createGeminiCliAdapter(): AgentAdapter {
  return {
    name: 'gemini-cli',
    order: 50,
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
        normalizeUserAgent(findHeaderValue(headers, 'x-goog-api-client'));
      const targetHost = input.correlation.targetHost?.trim().toLowerCase() ?? null;
      const payloadBlob = buildDetectionPayloadBlob({
        fullPromptMessages: input.correlation.fullPromptMessages,
        parameters: input.correlation.parameters,
        systemPrompt: input.correlation.systemPrompt,
        insertedText: input.insertedText
      });

      const googApiClient = findHeaderValue(headers, 'x-goog-api-client');
      const googRequestReason = findHeaderValue(headers, 'x-goog-request-reason');
      const googRequestId = findHeaderValue(headers, 'x-goog-request-id');

      const isGoogleHost =
        targetHost?.includes('generativelanguage.googleapis.com') ||
        targetHost?.includes('aiplatform.googleapis.com');

      const matched =
        userAgent?.toLowerCase().includes('gemini') ||
        googApiClient?.toLowerCase().includes('gemini') ||
        googApiClient?.toLowerCase().includes('google-generativeai') ||
        isGoogleHost ||
        payloadBlob.includes('gemini') ||
        payloadBlob.includes('google generative') ||
        payloadBlob.includes('generativelanguage');

      if (!matched) {
        return undefined;
      }

      const modelName = normalizeModelName(input.correlation.modelName);
      const provider = 'Google';
      const operationType = classifyOperationType({
        insertedText: input.insertedText,
        promptBlob: payloadBlob,
        modelName
      });

      const sessionId =
        googRequestId ||
        findHeaderValue(headers, 'x-goog-session-id') ||
        findHeaderValue(headers, 'x-request-id') ||
        `gemini-${hashContext([targetHost, userAgent, modelName, input.workspaceHint, input.timestampIso])}`;

      const conversationId =
        findHeaderValue(headers, 'x-goog-conversation-id') ||
        sessionId;

      const runId =
        findHeaderValue(headers, 'x-goog-run-id') ||
        null;

      const evidence: AgentEvidence[] = [];
      pushEvidence(evidence, userAgent?.toLowerCase().includes('gemini') ?? false, 'user-agent', 'user-agent', userAgent, 0.26, 'Gemini CLI user agent matched.');
      pushEvidence(evidence, Boolean(isGoogleHost), 'header', 'targetHost', targetHost, 0.25, 'Google Generative Language API host matched.');
      pushEvidence(evidence, googApiClient !== null, 'header', 'x-goog-api-client', googApiClient, 0.2, 'Google API client header present.');
      pushEvidence(evidence, googRequestReason !== null, 'header', 'x-goog-request-reason', googRequestReason, 0.1, 'Google request reason header present.');
      pushEvidence(evidence, payloadBlob.includes('gemini') || payloadBlob.includes('google generative') || payloadBlob.includes('generativelanguage'), 'payload', 'prompt', payloadBlob.slice(0, 200), 0.1, 'Payload fingerprint consistent with Gemini API traffic.');

      return {
        toolName: 'Gemini CLI',
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
        adapterName: 'gemini-cli',
        matchSource: 'adapter',
        sessionKind: 'cli',
        host: targetHost,
        sessionSignature: buildSessionSignature({
          toolName: 'Gemini CLI',
          provider,
          modelName: modelName || null,
          sessionKind: 'cli',
          sessionId,
          conversationId,
          runId
        }),
        detectedAtIso: input.timestampIso
      };
    }
  };
}
