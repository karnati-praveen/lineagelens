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

export function createCodeiumAdapter(): AgentAdapter {
  return {
    name: 'codeium',
    order: 25,
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

      const codeiumApiKey = findHeaderValue(headers, 'x-codeium-api-key');
      const codeiumChecksum = findHeaderValue(headers, 'x-codeium-checksum-algorithm');
      const windsurfHeader = findHeaderValue(headers, 'x-windsurf-client');

      const matched =
        userAgent?.toLowerCase().includes('codeium') ||
        userAgent?.toLowerCase().includes('windsurf') ||
        targetHost?.includes('codeium.com') ||
        targetHost?.includes('windsurf.codeium.com') ||
        codeiumApiKey !== null ||
        codeiumChecksum !== null ||
        windsurfHeader !== null ||
        payloadBlob.includes('codeium') ||
        payloadBlob.includes('windsurf');

      if (!matched) {
        return undefined;
      }

      const isWindsurf =
        userAgent?.toLowerCase().includes('windsurf') ||
        windsurfHeader !== null ||
        targetHost?.includes('windsurf') ||
        payloadBlob.includes('windsurf');

      const toolName = isWindsurf ? 'Windsurf' : 'Codeium';

      const modelName = normalizeModelName(input.correlation.modelName);
      const provider = inferProvider(targetHost, modelName, payloadBlob) ?? 'Codeium';
      const operationType = classifyOperationType({
        insertedText: input.insertedText,
        promptBlob: payloadBlob,
        modelName
      });

      const sessionId =
        findHeaderValue(headers, 'x-codeium-session-id') ||
        findHeaderValue(headers, 'x-windsurf-session-id') ||
        findHeaderValue(headers, 'x-request-id') ||
        `codeium-${hashContext([targetHost, userAgent, modelName, input.workspaceHint, input.timestampIso])}`;

      const conversationId =
        findHeaderValue(headers, 'x-codeium-conversation-id') ||
        findHeaderValue(headers, 'x-windsurf-chat-id') ||
        sessionId;

      const runId =
        findHeaderValue(headers, 'x-codeium-run-id') ||
        findHeaderValue(headers, 'x-codeium-request-id') ||
        null;

      const evidence: AgentEvidence[] = [];
      pushEvidence(evidence, Boolean(userAgent?.toLowerCase().includes('codeium') || userAgent?.toLowerCase().includes('windsurf')), 'user-agent', 'user-agent', userAgent, 0.27, `${toolName} user agent matched.`);
      pushEvidence(evidence, Boolean(targetHost?.includes('codeium.com') || targetHost?.includes('windsurf.codeium.com')), 'header', 'targetHost', targetHost, 0.22, `${toolName} API host matched.`);
      pushEvidence(evidence, codeiumApiKey !== null, 'header', 'x-codeium-api-key', codeiumApiKey, 0.2, 'Codeium API key header present.');
      pushEvidence(evidence, payloadBlob.includes('codeium') || payloadBlob.includes('windsurf'), 'payload', 'prompt', payloadBlob.slice(0, 200), 0.12, `Payload fingerprint consistent with ${toolName} traffic.`);

      return {
        toolName,
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
        adapterName: 'codeium',
        matchSource: 'adapter',
        sessionKind: 'agentic',
        host: targetHost,
        sessionSignature: buildSessionSignature({
          toolName,
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
