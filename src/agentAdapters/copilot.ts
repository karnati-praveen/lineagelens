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

export function createCopilotAdapter(): AgentAdapter {
  return {
    name: 'copilot',
    order: 15,
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

      const githubToken = findHeaderValue(headers, 'x-github-token');
      const copilotIntegration = findHeaderValue(headers, 'x-github-copilot-integration-id');
      const githubClientId = findHeaderValue(headers, 'github-client-id') || findHeaderValue(headers, 'x-github-client-id');
      const editorVersion = findHeaderValue(headers, 'editor-version') || findHeaderValue(headers, 'x-editor-version');

      const matched =
        userAgent?.toLowerCase().includes('copilot') ||
        targetHost?.includes('githubcopilot.com') ||
        targetHost?.includes('copilot-proxy.githubusercontent.com') ||
        targetHost?.includes('api.githubcopilot.com') ||
        githubToken !== null ||
        copilotIntegration !== null ||
        githubClientId !== null ||
        payloadBlob.includes('copilot') ||
        payloadBlob.includes('github copilot');

      if (!matched) {
        return undefined;
      }

      const modelName = normalizeModelName(input.correlation.modelName);
      const provider = inferProvider(targetHost, modelName, payloadBlob) ?? 'GitHub';
      const operationType = classifyOperationType({
        insertedText: input.insertedText,
        promptBlob: payloadBlob,
        modelName
      });

      const sessionId =
        findHeaderValue(headers, 'x-github-copilot-session-id') ||
        findHeaderValue(headers, 'x-request-id') ||
        findHeaderValue(headers, 'x-vsc-request-id') ||
        `copilot-${hashContext([targetHost, userAgent, modelName, input.workspaceHint, input.timestampIso])}`;

      const conversationId =
        findHeaderValue(headers, 'x-github-copilot-chat-id') ||
        findHeaderValue(headers, 'x-conversation-id') ||
        sessionId;

      const runId =
        findHeaderValue(headers, 'x-github-copilot-run-id') ||
        copilotIntegration ||
        null;

      const evidence: AgentEvidence[] = [];
      pushEvidence(evidence, userAgent?.toLowerCase().includes('copilot') ?? false, 'user-agent', 'user-agent', userAgent, 0.28, 'GitHub Copilot user agent matched.');
      pushEvidence(evidence, Boolean(targetHost?.includes('githubcopilot.com') || targetHost?.includes('copilot-proxy.githubusercontent.com') || targetHost?.includes('api.githubcopilot.com')), 'header', 'targetHost', targetHost, 0.22, 'GitHub Copilot proxy host matched.');
      pushEvidence(evidence, githubToken !== null, 'header', 'x-github-token', githubToken, 0.2, 'GitHub authentication token header present.');
      pushEvidence(evidence, editorVersion !== null, 'header', 'editor-version', editorVersion, 0.1, 'Editor version header emitted by Copilot plugin.');
      pushEvidence(evidence, payloadBlob.includes('copilot') || payloadBlob.includes('github copilot'), 'payload', 'prompt', payloadBlob.slice(0, 200), 0.12, 'Payload fingerprint consistent with Copilot traffic.');

      return {
        toolName: 'GitHub Copilot',
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
        adapterName: 'copilot',
        matchSource: 'adapter',
        sessionKind: 'assistant',
        host: targetHost,
        sessionSignature: buildSessionSignature({
          toolName: 'GitHub Copilot',
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
