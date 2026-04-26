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

export function createCodexCliAdapter(): AgentAdapter {
  return {
    name: 'codex-cli',
    order: 55,
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

      const openaiOrg = findHeaderValue(headers, 'openai-organization') || findHeaderValue(headers, 'x-openai-org-id');
      const openaiProject = findHeaderValue(headers, 'openai-project');

      // Codex CLI is OpenAI's agentic CLI — distinct from Cursor or generic API calls.
      // Signals: codex in user-agent, openai-codex in user-agent, or model name starts with codex/o1/o3/o4.
      const codexModelMatch =
        /\b(codex|o1|o3|o4[-\s]?mini|o4)\b/.test(normalizeModelName(input.correlation.modelName).toLowerCase());

      const isOpenAIHost =
        targetHost?.includes('api.openai.com') ||
        targetHost?.includes('openai.com');

      const matched =
        userAgent?.toLowerCase().includes('openai-codex') ||
        userAgent?.toLowerCase().includes('codex-cli') ||
        userAgent?.toLowerCase().includes('@openai/codex') ||
        (isOpenAIHost && codexModelMatch && !payloadBlob.includes('cursor') && !payloadBlob.includes('aider')) ||
        payloadBlob.includes('openai-codex') ||
        payloadBlob.includes('codex-cli');

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
        findHeaderValue(headers, 'x-codex-session-id') ||
        findHeaderValue(headers, 'x-openai-session-id') ||
        findHeaderValue(headers, 'x-request-id') ||
        `codex-${hashContext([targetHost, userAgent, modelName, input.workspaceHint, input.timestampIso])}`;

      const conversationId =
        findHeaderValue(headers, 'x-codex-conversation-id') ||
        findHeaderValue(headers, 'x-conversation-id') ||
        sessionId;

      const runId =
        findHeaderValue(headers, 'x-codex-run-id') ||
        findHeaderValue(headers, 'x-invocation-id') ||
        null;

      const evidence: AgentEvidence[] = [];
      pushEvidence(evidence, Boolean(userAgent?.toLowerCase().includes('openai-codex') || userAgent?.toLowerCase().includes('codex-cli') || userAgent?.toLowerCase().includes('@openai/codex')), 'user-agent', 'user-agent', userAgent, 0.3, 'OpenAI Codex CLI user agent matched.');
      pushEvidence(evidence, Boolean(isOpenAIHost), 'header', 'targetHost', targetHost, 0.18, 'OpenAI API host matched.');
      pushEvidence(evidence, openaiOrg !== null || openaiProject !== null, 'header', 'openai-organization', openaiOrg ?? openaiProject, 0.12, 'OpenAI organization or project header present.');
      pushEvidence(evidence, codexModelMatch, 'routing', 'modelName', modelName || 'unknown', 0.15, 'Model name consistent with Codex CLI (o-series or codex).');
      pushEvidence(evidence, payloadBlob.includes('openai-codex') || payloadBlob.includes('codex-cli'), 'payload', 'prompt', payloadBlob.slice(0, 200), 0.1, 'Payload fingerprint consistent with Codex CLI traffic.');

      return {
        toolName: 'OpenAI Codex CLI',
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
        adapterName: 'codex-cli',
        matchSource: 'adapter',
        sessionKind: 'cli',
        host: targetHost,
        sessionSignature: buildSessionSignature({
          toolName: 'OpenAI Codex CLI',
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
