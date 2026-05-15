import type { PromptCorrelationResult } from './correlation';
import type { ContextSnapshot } from './contextSnapshot';
import type { NormalizedAgentContext } from './agentAdapters';
import type { ProvenanceInsertedChunk } from './provenance';
import type { CaptureStatus } from './proxy';

export const PROVENANCE_EVENT_SCHEMA_VERSION = 'lineagelens.provenance-event.v1';

export type ProvenanceCaptureLevel = CaptureStatus;

export type ProvenanceEventCapabilityStatus = 'provided' | 'missing' | 'unknown';

export type ProvenanceEventCapability = {
  name: string;
  status: ProvenanceEventCapabilityStatus;
};

export type ProviderAgnosticProvenanceEvent = {
  schemaVersion: typeof PROVENANCE_EVENT_SCHEMA_VERSION;
  eventId: string;
  timestamps: {
    observedAtIso: string;
    insertedAtIso: string;
    requestAtIso: string | null;
    responseAtIso: string | null;
  };
  source: {
    ide: string | null;
    shim: string;
    toolName: string | null;
    provider: string | null;
    adapterName: string | null;
  };
  capture: {
    level: ProvenanceCaptureLevel;
    promptStatus: 'captured' | 'not-captured';
    capabilities: ProvenanceEventCapability[];
    evidence: unknown[];
  };
  session: {
    sessionId: string | null;
    conversationId: string | null;
    runId: string | null;
    requestId: string | null;
    signature: string | null;
  };
  model: {
    name: unknown;
    parameters: Record<string, unknown> | null;
  };
  prompt: {
    body: unknown;
    system: unknown;
  };
  response: {
    body: string | null;
    bodyBase64: string | null;
  };
  file: {
    path: string;
    uri: string;
    languageId: string;
    workspace: string | null;
    gitBranch: string | null;
  };
  diff: {
    insertedText: string;
    chunks: ProvenanceInsertedChunk[];
    netAddedLines: number;
  };
  context: {
    snapshot: ContextSnapshot | null;
    before: string;
    after: string;
  };
  correlation: {
    confidence: number;
    timingDifferenceMs: number | null;
    windowMs: number;
    contentSimilarityScore: number | null;
    fileContextMatched: boolean;
  };
  confidence: {
    agent: number | null;
    correlation: number;
  };
  extensions: Record<string, unknown>;
};

export type BuildProviderAgnosticProvenanceEventInput = {
  eventId: string;
  timestampIso: string;
  filePath: string;
  fileUri: string;
  languageId: string;
  workspaceHint: string | null;
  gitBranch: string | null;
  sourceIde?: string | null;
  insertedText: string;
  insertedChunks: ProvenanceInsertedChunk[];
  netAddedLines: number;
  surroundingContext: {
    before: string;
    after: string;
    tokenWindow: number;
  };
  contextSnapshot: ContextSnapshot | null;
  correlation: PromptCorrelationResult;
  agentContext: NormalizedAgentContext | null;
  shimName: string;
  extensions?: Record<string, unknown>;
};

export function buildProviderAgnosticProvenanceEvent(
  input: BuildProviderAgnosticProvenanceEventInput
): ProviderAgnosticProvenanceEvent {
  const captured = input.correlation.promptStatus === 'captured';
  const agentContext = input.agentContext;

  return {
    schemaVersion: PROVENANCE_EVENT_SCHEMA_VERSION,
    eventId: input.eventId,
    timestamps: {
      observedAtIso: input.timestampIso,
      insertedAtIso: input.timestampIso,
      requestAtIso: input.correlation.proxyRequestTimestampIso,
      responseAtIso: input.correlation.proxyResponseTimestampIso
    },
    source: {
      ide: input.sourceIde === undefined ? 'vscode' : input.sourceIde,
      shim: input.shimName,
      toolName: agentContext?.toolName ?? null,
      provider: agentContext?.provider ?? null,
      adapterName: agentContext?.adapterName ?? null
    },
    capture: {
      level: input.correlation.captureStatus,
      promptStatus: captured ? 'captured' : 'not-captured',
      capabilities: buildCapabilityStatuses(input.correlation, agentContext),
      evidence: agentContext?.evidence ?? []
    },
    session: {
      sessionId: agentContext?.sessionId ?? null,
      conversationId: agentContext?.conversationId ?? null,
      runId: agentContext?.runId ?? null,
      requestId: input.correlation.requestUuid,
      signature: agentContext?.sessionSignature ?? null
    },
    model: {
      name: input.correlation.modelName,
      parameters: input.correlation.parameters ?? null
    },
    prompt: {
      body: input.correlation.fullPromptMessages,
      system: input.correlation.systemPrompt
    },
    response: {
      body: input.correlation.rawModelResponse,
      bodyBase64: input.correlation.rawModelResponseBase64
    },
    file: {
      path: input.filePath,
      uri: input.fileUri,
      languageId: input.languageId,
      workspace: input.workspaceHint,
      gitBranch: input.gitBranch
    },
    diff: {
      insertedText: input.insertedText,
      chunks: input.insertedChunks,
      netAddedLines: input.netAddedLines
    },
    context: {
      snapshot: input.contextSnapshot,
      before: input.surroundingContext.before,
      after: input.surroundingContext.after
    },
    correlation: {
      confidence: input.correlation.correlationConfidence,
      timingDifferenceMs: input.correlation.timingDifferenceMs,
      windowMs: input.correlation.correlationWindowMs,
      contentSimilarityScore: input.correlation.contentSimilarityScore,
      fileContextMatched: input.correlation.fileContextMatched
    },
    confidence: {
      agent: agentContext?.confidence ?? null,
      correlation: input.correlation.correlationConfidence
    },
    extensions: {
      operationType: agentContext?.operationType ?? 'unknown',
      sessionKind: agentContext?.sessionKind ?? 'unknown',
      host: agentContext?.host ?? input.correlation.targetHost,
      matchedFileContextTokens: input.correlation.matchedFileContextTokens,
      captureEvidence: input.correlation.captureEvidence,
      ...input.extensions
    }
  };
}

function buildCapabilityStatuses(
  correlation: PromptCorrelationResult,
  agentContext: NormalizedAgentContext | null
): ProvenanceEventCapability[] {
  return [
    { name: 'prompt-body', status: correlation.fullPromptMessages ? 'provided' : 'missing' },
    { name: 'response-body', status: correlation.rawModelResponse ? 'provided' : 'missing' },
    { name: 'headers', status: correlation.requestHeaders ? 'provided' : 'missing' },
    { name: 'request-id', status: correlation.requestUuid ? 'provided' : 'missing' },
    { name: 'session-id', status: agentContext?.sessionId ? 'provided' : 'missing' },
    { name: 'model', status: correlation.modelName ? 'provided' : 'missing' },
    { name: 'user-agent', status: agentContext?.userAgent ? 'provided' : 'missing' },
    { name: 'file-diff', status: 'provided' },
    { name: 'file-context', status: correlation.fileContextMatched ? 'provided' : 'unknown' },
    { name: 'workspace', status: agentContext?.workspaceHint ? 'provided' : 'missing' }
  ];
}
