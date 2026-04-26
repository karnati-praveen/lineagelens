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

export function createCodyAdapter(): AgentAdapter {
  return {
    name: 'cody',
    order: 40,
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
        normalizeUserAgent(findHeaderValue(headers, 'x-sourcegraph-client'));
      const targetHost = input.correlation.targetHost?.trim().toLowerCase() ?? null;
      const payloadBlob = buildDetectionPayloadBlob({
        fullPromptMessages: input.correlation.fullPromptMessages,
        parameters: input.correlation.parameters,
        systemPrompt: input.correlation.systemPrompt,
        insertedText: input.insertedText
      });

      const sourcegraphClient = findHeaderValue(headers, 'x-sourcegraph-client');
      const codyGateway = findHeaderValue(headers, 'x-cody-gateway-auth');
      const sourcegraphToken = findHeaderValue(headers, 'x-sourcegraph-token');

      const matched =
        userAgent?.toLowerCase().includes('cody') ||
        userAgent?.toLowerCase().includes('sourcegraph') ||
        targetHost?.includes('sourcegraph.com') ||
        targetHost?.includes('cody-gateway.sourcegraph.com') ||
        sourcegraphClient !== null ||
        codyGateway !== null ||
        sourcegraphToken !== null ||
        payloadBlob.includes('cody') ||
        payloadBlob.includes('sourcegraph');

      if (!matched) {
        return undefined;
      }

      const modelName = normalizeModelName(input.correlation.modelName);
      const provider = inferProvider(targetHost, modelName, payloadBlob) ?? 'Sourcegraph';
      const operationType = classifyOperationType({
        insertedText: input.insertedText,
        promptBlob: payloadBlob,
        modelName
      });

      const sessionId =
        findHeaderValue(headers, 'x-cody-session-id') ||
        findHeaderValue(headers, 'x-sourcegraph-session-id') ||
        findHeaderValue(headers, 'x-request-id') ||
        `cody-${hashContext([targetHost, userAgent, modelName, input.workspaceHint, input.timestampIso])}`;

      const conversationId =
        findHeaderValue(headers, 'x-cody-conversation-id') ||
        findHeaderValue(headers, 'x-sourcegraph-interaction-id') ||
        sessionId;

      const runId =
        findHeaderValue(headers, 'x-cody-run-id') ||
        null;

      const evidence: AgentEvidence[] = [];
      pushEvidence(evidence, Boolean(userAgent?.toLowerCase().includes('cody') || userAgent?.toLowerCase().includes('sourcegraph')), 'user-agent', 'user-agent', userAgent, 0.27, 'Sourcegraph Cody user agent matched.');
      pushEvidence(evidence, Boolean(targetHost?.includes('sourcegraph.com') || targetHost?.includes('cody-gateway.sourcegraph.com')), 'header', 'targetHost', targetHost, 0.22, 'Sourcegraph Cody gateway host matched.');
      pushEvidence(evidence, sourcegraphClient !== null, 'header', 'x-sourcegraph-client', sourcegraphClient, 0.2, 'Sourcegraph client header present.');
      pushEvidence(evidence, payloadBlob.includes('cody') || payloadBlob.includes('sourcegraph'), 'payload', 'prompt', payloadBlob.slice(0, 200), 0.12, 'Payload fingerprint consistent with Cody traffic.');

      return {
        toolName: 'Sourcegraph Cody',
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
        adapterName: 'cody',
        matchSource: 'adapter',
        sessionKind: 'agentic',
        host: targetHost,
        sessionSignature: buildSessionSignature({
          toolName: 'Sourcegraph Cody',
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
