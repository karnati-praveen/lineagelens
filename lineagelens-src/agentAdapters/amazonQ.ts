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

export function createAmazonQAdapter(): AgentAdapter {
  return {
    name: 'amazon-q',
    order: 45,
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

      const amzRequestId = findHeaderValue(headers, 'x-amz-request-id') || findHeaderValue(headers, 'x-amzn-requestid');
      const amzTarget = findHeaderValue(headers, 'x-amz-target');
      const amzUserAgent = findHeaderValue(headers, 'x-amz-user-agent');

      const isCodeWhispererHost =
        targetHost?.includes('codewhisperer.') ||
        targetHost?.includes('q.us-east-1.amazonaws.com') ||
        targetHost?.includes('amazonq.');

      const matched =
        userAgent?.toLowerCase().includes('codewhisperer') ||
        userAgent?.toLowerCase().includes('amazon-q') ||
        userAgent?.toLowerCase().includes('amazonq') ||
        amzUserAgent?.toLowerCase().includes('codewhisperer') ||
        amzUserAgent?.toLowerCase().includes('amazon-q') ||
        (isCodeWhispererHost && amzRequestId !== null) ||
        amzTarget?.toLowerCase().includes('codewhisperer') ||
        amzTarget?.toLowerCase().includes('amazonq') ||
        payloadBlob.includes('codewhisperer') ||
        payloadBlob.includes('amazon q') ||
        payloadBlob.includes('amazon-q');

      if (!matched) {
        return undefined;
      }

      const modelName = normalizeModelName(input.correlation.modelName);
      const provider = 'AWS';
      const operationType = classifyOperationType({
        insertedText: input.insertedText,
        promptBlob: payloadBlob,
        modelName
      });

      const sessionId =
        amzRequestId ||
        findHeaderValue(headers, 'x-amzn-trace-id') ||
        `amazon-q-${hashContext([targetHost, userAgent, modelName, input.workspaceHint, input.timestampIso])}`;

      const conversationId =
        findHeaderValue(headers, 'x-amzn-conversation-id') ||
        findHeaderValue(headers, 'x-conversation-id') ||
        sessionId;

      const runId =
        findHeaderValue(headers, 'x-amz-execution-run-id') ||
        null;

      const evidence: AgentEvidence[] = [];
      pushEvidence(evidence, Boolean(userAgent?.toLowerCase().includes('codewhisperer') || userAgent?.toLowerCase().includes('amazon-q') || userAgent?.toLowerCase().includes('amazonq') || amzUserAgent?.toLowerCase().includes('codewhisperer') || amzUserAgent?.toLowerCase().includes('amazon-q')), 'user-agent', 'user-agent', userAgent ?? amzUserAgent, 0.27, 'Amazon Q / CodeWhisperer user agent matched.');
      pushEvidence(evidence, Boolean(isCodeWhispererHost), 'header', 'targetHost', targetHost, 0.22, 'AWS CodeWhisperer or Amazon Q host matched.');
      pushEvidence(evidence, amzRequestId !== null, 'header', 'x-amz-request-id', amzRequestId, 0.18, 'AWS request ID header present.');
      pushEvidence(evidence, Boolean(amzTarget?.toLowerCase().includes('codewhisperer') || amzTarget?.toLowerCase().includes('amazonq')), 'header', 'x-amz-target', amzTarget, 0.15, 'AWS API target header consistent with Q / CodeWhisperer.');
      pushEvidence(evidence, payloadBlob.includes('codewhisperer') || payloadBlob.includes('amazon q') || payloadBlob.includes('amazon-q'), 'payload', 'prompt', payloadBlob.slice(0, 200), 0.1, 'Payload fingerprint consistent with Amazon Q traffic.');

      return {
        toolName: 'Amazon Q Developer',
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
        adapterName: 'amazon-q',
        matchSource: 'adapter',
        sessionKind: 'assistant',
        host: targetHost,
        sessionSignature: buildSessionSignature({
          toolName: 'Amazon Q Developer',
          provider,
          modelName: modelName || null,
          sessionKind: 'assistant',
          sessionId,
          conversationId,
          runId
        }),
        detectedAtIso: input.timestampIso
      };
    }
  };
}
