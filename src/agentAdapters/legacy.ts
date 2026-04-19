import type { AgentAdapter, AgentAdapterInput, NormalizedAgentContext } from './types';
import {
  buildSessionSignature,
  clampConfidence,
  classifyOperationType,
  createEvidence,
  findHeaderValue,
  inferProvider,
  normalizeModelName,
  normalizeUserAgent,
  safeSerialize,
  toText
} from './shared';

export function createLegacyHeuristicAdapter(): AgentAdapter {
  return {
    name: 'legacy-heuristic',
    order: 1000,
    capabilities: [
      'tool-name',
      'provider',
      'model',
      'session-id',
      'request-id',
      'user-agent',
      'headers',
      'prompt-body',
      'file-context',
      'file-diff',
      'workspace'
    ],
    detect(input: AgentAdapterInput): NormalizedAgentContext | undefined {
      const targetHost = input.correlation.targetHost?.trim().toLowerCase() ?? null;
      const headers = input.correlation.requestHeaders ?? null;
      const userAgent =
        normalizeUserAgent(findHeaderValue(headers, 'user-agent')) ||
        normalizeUserAgent(findHeaderValue(headers, 'x-client-name'));
      const modelName = normalizeModelName(input.correlation.modelName);
      const rawContextBlob = [
        targetHost ?? '',
        userAgent ?? '',
        modelName,
        safeSerialize(input.correlation.parameters),
        safeSerialize(input.correlation.fullPromptMessages),
        toText(input.correlation.systemPrompt)
      ]
        .join('\n')
        .toLowerCase();

      let toolName: string | null = null;
      let sessionKind: NormalizedAgentContext['sessionKind'] = 'unknown';

      if (rawContextBlob.includes('cursor')) {
        toolName = 'Cursor';
        sessionKind = 'agentic';
      } else if (rawContextBlob.includes('claude-code') || rawContextBlob.includes('claude code')) {
        toolName = 'Claude Code';
        sessionKind = 'agentic';
      } else if (rawContextBlob.includes('aider')) {
        toolName = 'Aider';
        sessionKind = 'agentic';
      } else if (rawContextBlob.includes('copilot')) {
        toolName = 'GitHub Copilot';
        sessionKind = 'assistant';
      }

      const provider = inferProvider(targetHost, modelName, rawContextBlob);
      if (!toolName && !provider && !modelName) {
        return undefined;
      }

      const operationType = classifyOperationType({
        insertedText: input.insertedText,
        promptBlob: rawContextBlob,
        modelName
      });

      const sessionId = input.correlation.requestHeaders
        ? findHeaderValue(input.correlation.requestHeaders, 'x-request-id') ??
          findHeaderValue(input.correlation.requestHeaders, 'x-session-id') ??
          null
        : null;

      const confidence = clampConfidence(
        toolName ? 0.55 : provider ? 0.42 : 0.3
      );

      const evidence = [
        createEvidence('heuristic', 'rawContextBlob', rawContextBlob.slice(0, 240), 0.2, 'Legacy heuristic match.'),
        createEvidence('heuristic', 'toolName', toolName ?? 'unknown', 0.1, 'Tool inferred from prompt or routing fingerprints.'),
        createEvidence('heuristic', 'provider', provider ?? 'unknown', 0.1, 'Provider inferred from routing/model data.')
      ];

      return {
        toolName,
        provider,
        sessionId,
        conversationId: null,
        runId: null,
        modelName: modelName || null,
        userAgent,
        workspaceHint: input.workspaceHint,
        operationType,
        confidence,
        evidence,
        adapterName: 'legacy-heuristic',
        matchSource: 'heuristic',
        sessionKind,
        host: targetHost,
        sessionSignature: buildSessionSignature({
          toolName,
          provider,
          modelName: modelName || null,
          sessionKind,
          sessionId,
          conversationId: null,
          runId: null
        }),
        detectedAtIso: input.timestampIso
      };
    }
  };
}
