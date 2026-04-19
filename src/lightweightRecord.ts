import type { NormalizedAgentContext } from './agentAdapters';
import type { CaptureEvidence, PromptCorrelationResult } from './correlation';
import type { ContextSnapshot } from './contextSnapshot';
import {
  buildProviderAgnosticProvenanceEvent,
  type ProviderAgnosticProvenanceEvent
} from './eventSchema';
import type { ProvenanceInsertedChunk } from './provenance';
import type { CaptureStatus } from './proxy';

export type LightweightPromptStatus = 'captured' | 'not-captured';

export type LightweightProvenanceRecordInput = {
  eventId: string;
  timestampIso?: string;
  filePath: string;
  fileUri: string;
  languageId: string;
  workspaceHint?: string | null;
  gitBranch?: string | null;
  insertedText: string;
  insertedChunks?: ProvenanceInsertedChunk[];
  netAddedLines?: number;
  surroundingContext?: {
    before?: string;
    after?: string;
    tokenWindow?: number;
  };
  contextSnapshot?: ContextSnapshot | null;
  promptStatus?: LightweightPromptStatus;
  captureStatus?: CaptureStatus;
  requestUuid?: string | null;
  timingDifferenceMs?: number | null;
  correlationConfidence?: number;
  fullPromptMessages?: unknown;
  modelName?: unknown;
  modelParameters?: Record<string, unknown> | null;
  rawModelResponse?: string | null;
  rawModelResponseBase64?: string | null;
  requestHeaders?: Record<string, string | string[]> | null;
  targetHost?: string | null;
  systemPrompt?: unknown;
  captureEvidence?: CaptureEvidence | null;
  agentContext?: NormalizedAgentContext | null;
  sourceIde?: string | null;
  sourceShim?: string;
  extraExtensions?: Record<string, unknown>;
};

export function buildLightweightProvenanceRecord(
  input: LightweightProvenanceRecordInput
): ProviderAgnosticProvenanceEvent {
  const timestampIso = input.timestampIso ?? new Date().toISOString();
  const promptStatus = input.promptStatus ?? inferPromptStatus(input);
  const correlation = buildSyntheticCorrelation(input, timestampIso, promptStatus);

  return buildProviderAgnosticProvenanceEvent({
    eventId: input.eventId,
    timestampIso,
    filePath: input.filePath,
    fileUri: input.fileUri,
    languageId: input.languageId,
    workspaceHint: input.workspaceHint ?? null,
    gitBranch: input.gitBranch ?? null,
    sourceIde: input.sourceIde ?? null,
    insertedText: input.insertedText,
    insertedChunks: input.insertedChunks ?? [],
    netAddedLines: input.netAddedLines ?? 0,
    surroundingContext: {
      before: input.surroundingContext?.before ?? '',
      after: input.surroundingContext?.after ?? '',
      tokenWindow: input.surroundingContext?.tokenWindow ?? 0
    },
    contextSnapshot: input.contextSnapshot ?? null,
    correlation,
    agentContext: input.agentContext ?? null,
    shimName: input.sourceShim ?? 'lightweight-adapter',
    extensions: input.extraExtensions
  });
}

function inferPromptStatus(input: LightweightProvenanceRecordInput): LightweightPromptStatus {
  if (
    input.fullPromptMessages !== undefined ||
    (typeof input.rawModelResponse === 'string' && input.rawModelResponse.trim().length > 0) ||
    (typeof input.rawModelResponseBase64 === 'string' && input.rawModelResponseBase64.trim().length > 0)
  ) {
    return 'captured';
  }

  return 'not-captured';
}

function buildSyntheticCorrelation(
  input: LightweightProvenanceRecordInput,
  timestampIso: string,
  promptStatus: LightweightPromptStatus
): PromptCorrelationResult {
  if (promptStatus === 'captured') {
    const rawModelResponse = input.rawModelResponse ?? '';
    const rawModelResponseBase64 =
      input.rawModelResponseBase64 ??
      Buffer.from(rawModelResponse, 'utf8').toString('base64');
    const effectiveCaptureStatus: Extract<
      CaptureStatus,
      'full' | 'metadata_only' | 'tunnel_only' | 'hook'
    > =
      input.captureStatus === 'full' ||
      input.captureStatus === 'hook' ||
      input.captureStatus === 'metadata_only' ||
      input.captureStatus === 'tunnel_only'
        ? input.captureStatus
        : 'metadata_only';

    return {
      promptStatus: 'captured',
      captureStatus: effectiveCaptureStatus,
      requestUuid: input.requestUuid ?? input.eventId,
      timingDifferenceMs: input.timingDifferenceMs ?? 0,
      correlationWindowMs: 15_000,
      similarityThreshold: 0.7,
      correlationConfidence: input.correlationConfidence ?? 0.75,
      fileContextMatched: Boolean(input.insertedText.trim().length > 0),
      matchedFileContextTokens: [],
      contentSimilarityApplied: false,
      ambiguityResolvedByContent: false,
      contentSimilarityScore: null,
      proxyResponseTimestampIso: timestampIso,
      proxyRequestTimestampIso: timestampIso,
      fullPromptMessages: input.fullPromptMessages ?? null,
      modelName: input.modelName ?? null,
      parameters: input.modelParameters ?? undefined,
      targetHost: input.targetHost ?? null,
      requestHeaders: input.requestHeaders ?? null,
      systemPrompt: input.systemPrompt ?? null,
      rawModelResponse,
      rawModelResponseBase64,
      captureEvidence: input.captureEvidence ?? null
    };
  }

  return {
    promptStatus: 'not-captured',
    captureStatus: input.captureStatus ?? 'unavailable',
    reason: inferNotCapturedReason(input.captureStatus),
    requestUuid: null,
    timingDifferenceMs: null,
    correlationWindowMs: 15_000,
    similarityThreshold: 0.7,
    correlationConfidence: 0,
    fileContextMatched: false,
    matchedFileContextTokens: [],
    contentSimilarityApplied: false,
    ambiguityResolvedByContent: false,
    contentSimilarityScore: null,
    proxyResponseTimestampIso: null,
    proxyRequestTimestampIso: null,
    fullPromptMessages: null,
    modelName: null,
    parameters: null,
    targetHost: null,
    requestHeaders: null,
    systemPrompt: null,
    rawModelResponse: null,
    rawModelResponseBase64: null,
    captureEvidence: input.captureEvidence ?? null
  };
}

function inferNotCapturedReason(
  captureStatus: CaptureStatus | undefined
): 'local-proxy-unavailable' | 'metadata-only-capture' | 'tunnel-only-capture' {
  if (captureStatus === 'metadata_only') {
    return 'metadata-only-capture';
  }

  if (captureStatus === 'tunnel_only') {
    return 'tunnel-only-capture';
  }

  return 'local-proxy-unavailable';
}
